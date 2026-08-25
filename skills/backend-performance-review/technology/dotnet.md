# .NET (CLR)

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file covers the modern, cross-platform CLR (.NET 5+, and its .NET Core predecessor). It
contains only what those two files do not give you — generic GC and blocking/non-blocking
reasoning are covered there. Where the legacy .NET Framework (pre-.NET-Core, Windows-only) is in
use, see §6 — several of the container-awareness and default-configuration claims below do not
hold for it.

---

## 1. Detection signals

A `.csproj`, `.sln`, or `.fsproj` file, or a `Dockerfile` with a `mcr.microsoft.com/dotnet`
base image. The target framework moniker inside the `.csproj` (`<TargetFramework>net8.0</
TargetFramework>`) or a `global.json`'s SDK pin records the version — note it, since §6 below is
version-sensitive, particularly around container-aware defaults and Native AOT availability.

**Determine whether the code awaits asynchronously throughout, or blocks synchronously on async
work (`.Result`, `.Wait()`, `.GetAwaiter().GetResult()`) anywhere on a hot path**, before scoring
anything — this single fact determines whether the concurrency-model section below applies
cleanly or whether the thread-pool-starvation failure mode is already active.

---

## 2. What differs from the universal runtime baseline

### Thread-pool starvation, not a single stalled event loop, is this runtime's core concurrency failure mode

ASP.NET Core's async model frees the handling thread back to a shared `ThreadPool` while a
request awaits I/O, rather than pinning one thread per request or running everything on a single
event-loop thread. This means a single blocking call does not stall *all* concurrent requests the
way it would under Node or a JVM reactive stack — but it does something distinctly its own:
**synchronous blocking on async work** (`.Result`, `.Wait()`, or `.GetAwaiter().GetResult()`
called on a `Task` instead of `await`ing it) occupies a thread-pool thread for the blocking
duration, and the thread pool only grows its thread count slowly, via a hill-climbing algorithm
tuned for steady-state efficiency rather than sudden bursts.

The result is a specific, recognizable symptom shape: **latency degrades progressively under
load rather than collapsing immediately** — the pool is still servicing requests, just with
growing queue delay as it waits for its own slow ramp-up to catch up with demand. This is
different enough from both the "event loop is stalled" pattern and the "thread pool is at a hard
capacity ceiling" pattern that it is worth naming as its own finding shape, not folding into
either.

### Sync-over-async can deadlock, depending on synchronization-context capture

Calling `.Result`/`.Wait()` on a `Task` from a context that captures a `SynchronizationContext`
(classic ASP.NET, WPF/WinForms UI threads) can deadlock outright: the blocked thread is waiting
for the async method's continuation to run, but that continuation is queued to run back on the
very context the blocked thread now occupies. **ASP.NET Core does not install a request
`SynchronizationContext`** by default, so this exact deadlock is less common in an ASP.NET Core
web request — but library code written to be portable across both classic ASP.NET and ASP.NET
Core still needs `ConfigureAwait(false)` to avoid depending on context-capture behavior at all,
and a mixed/older codebase can still exhibit this. Confirm which hosting model is actually in use
before ruling this failure mode in or out.

### Value types avoid heap allocation and GC pressure entirely — until they're boxed

C#'s value types (`struct`) are allocated inline — on the stack, or embedded directly inside
their containing object — rather than on the managed heap, so using them does not create GC
pressure the way an equivalent class instance would. This is a distinct, checkable lever that
Node's and the JVM's object models don't offer in the same form. **Boxing** — converting a value
type into a reference type, which happens implicitly when a struct is assigned to an `object`,
passed as an interface parameter, or added to a non-generic collection — allocates it on the heap
after all, silently reintroducing the GC pressure the value type was meant to avoid. A struct
used generically and correctly is a real optimization; the same struct captured into an
`IEnumerable<object>` or boxed in a hot loop is a specific, checkable regression, not a
hypothetical one.

`Span<T>`/`Memory<T>` extend the same idea to slicing: they represent a view over existing memory
(an array, a stack allocation) without copying or allocating, and are the concrete tool for
removing allocation from a hot parsing/serialization path.

### The GC has two operating modes with a real throughput/memory trade-off, and container awareness that (mostly) already works

The CLR's GC runs in **Workstation** mode (one heap, tuned for low footprint) or **Server** mode
(one heap per logical core, tuned for throughput) — ASP.NET Core defaults to Server GC, which
trades more memory for parallel collection and higher throughput. Since .NET Core 3.0, the
runtime reads the container's actual CPU and memory limits (cgroup-aware) to size the GC heap
count and thread-pool defaults by default — a real, positive counter-example to the JVM's and
Go's historical container-CPU-quota mismatches, though still worth confirming for the specific
version and hosting model in use, and **not true at all for the legacy .NET Framework**, which
has no such awareness.

Objects at or above 85,000 bytes are allocated on the **Large Object Heap (LOH)** instead of the
normal generational heaps, and the LOH is not compacted by default — a workload that repeatedly
allocates and frees large arrays or strings can fragment it in a way ordinary generational GC
does not fix, visible as memory usage that grows despite the objects themselves being
short-lived. `GCSettings.LargeObjectHeapCompactionMode` allows an on-demand compaction pass, at
the cost of a longer pause when triggered.

### Native AOT trades the JIT and its warm-up cost for compile-time constraints

Like GraalVM native image for the JVM, .NET's Native AOT (stable since .NET 7/8) compiles ahead of
time to a native binary, eliminating JIT warm-up and reducing startup time and memory footprint —
at the cost of reduced support for runtime reflection and dynamic code generation, which some
libraries (older serializers, some DI containers) rely on. Worth surfacing specifically for
short-lived, frequently-cold-started deployment targets (aggressive serverless scale-to-zero);
not a default recommendation otherwise.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `dotnet-counters` | Live metrics: GC heap size and collection counts per generation, thread-pool thread count and queue length, exception rate | **safe-on-production** — designed for continuous, low-overhead monitoring; thread-pool queue length is the direct signal for the starvation pattern in §2 |
| `dotnet-trace` | CPU and event-based profiling via `EventPipe`, viewable as a flame graph | **safe-on-production** at default sampling; a targeted, time-boxed capture is still the usual approach |
| `dotnet-gcdump` | A heap snapshot for object-graph and allocation analysis, without a full process dump | **not-safe-on-production** for a large heap — the capture itself pauses the process similarly to a JVM heap dump |
| `dotnet-dump` | A full process memory dump | **not-safe-on-production** — same caveat as `dotnet-gcdump`, more so |
| PerfView / `EventPipe` sessions | Deeper ETW-based analysis (GC internals, JIT events, contention) | **not-safe-on-production** for continuous use; a short, targeted session under representative load is the appropriate use |

`dotnet-counters`' thread-pool metrics deserve the same emphasis given to the equivalent
always-on tools in the other runtime references: no restart required, negligible overhead, and
usually the fastest way to turn a suspected thread-pool-starvation finding into `Confirmed`-grade
evidence — a queue length that grows under load while thread count ramps up slowly is the direct
signature.

---

## 4. Common failure modes and their symptoms

| Symptom | .NET-specific cause to check first |
|:--|:--|
| Latency degrades progressively under increasing load rather than failing sharply | Thread-pool starvation from sync-over-async (`.Result`, `.Wait()`, `.GetAwaiter().GetResult()`) somewhere on the hot path — check `dotnet-counters`' thread-pool queue length |
| A request hangs indefinitely rather than timing out, specifically in code that still runs under a captured `SynchronizationContext` | The classic sync-over-async deadlock — confirm the hosting model, since ASP.NET Core's default absence of a request `SynchronizationContext` makes this less likely there than in older or mixed codebases |
| Elevated Gen0 GC frequency with no obvious large-object cause | Boxing of value types in a hot path — a struct passed as an interface parameter, stored in a non-generic collection, or captured into an `object` |
| Memory usage grows despite short-lived objects, with generational GC not reclaiming it | Large Object Heap fragmentation from repeated large (≥85,000-byte) array or string allocation |
| Slow first-request latency after deploy or cold start | JIT warm-up — the direct .NET analogue of the JVM's warm-up cost; Native AOT is the relevant fix for a genuinely cold-start-sensitive deployment target, not a general recommendation |
| GC heap count or thread-pool sizing looks wrong under a container CPU limit | Confirm the actual .NET version and hosting configuration — pre-.NET-Core-3.0 and legacy .NET Framework have no cgroup awareness at all |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Server GC vs. Workstation GC | Higher throughput and more memory (one heap per core) versus a smaller footprint tuned for lower concurrency — ASP.NET Core defaults to Server GC |
| Concurrent/Background GC | Reduced pause time versus additional CPU and memory overhead for the concurrent collection thread |
| `ThreadPool.SetMinThreads` | Faster ramp-up under a sudden burst versus more idle threads reserved ahead of demonstrated need — a direct lever against the slow-hill-climbing starvation symptom in §2 |
| LOH compaction mode | Reduced fragmentation versus a longer pause when a compaction pass actually runs |
| Native AOT vs. JIT | Faster startup and lower memory versus reduced support for runtime reflection and dynamic code generation that some libraries depend on |

---

## 6. Version differences worth knowing

- **.NET Core 3.0** introduced container-aware GC heap count and thread-pool sizing by default
  (reading the container's actual cgroup CPU/memory limits) — a real capability gap before this
  version, and entirely absent in the legacy .NET Framework.
- **.NET 5** unified .NET Core and .NET Framework naming going forward; "the modern CLR" in this
  file means .NET Core-lineage runtimes (.NET 5 and later, and .NET Core 3.x), not classic .NET
  Framework.
- **ASP.NET Core**, since its inception, does not install a request `SynchronizationContext` —
  the classic sync-over-async deadlock described in §2 is correspondingly less likely there than
  under classic ASP.NET (System.Web) or a UI application, though the thread-pool-starvation
  pattern (a different failure mode) still applies fully.
- **Native AOT** reached general production stability in **.NET 7–8** — confirm it is actually
  the deployment model in use (versus a self-contained but JIT-based publish) before applying its
  startup-cost and reflection-constraint reasoning.
- **The legacy .NET Framework** (pre-.NET-Core) lacks the container-awareness described in §2 and
  has a different, Windows-only deployment model throughout — confirm which runtime family is
  actually in use before applying this file's specifics to it.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Framework-specific behavior (ASP.NET Core middleware pipeline, minimal APIs vs. controllers,
  Entity Framework Core query generation) — see `application/api.md`, `application/data-access.md`,
  and the relevant `databases/`/`technology/` files.
- Blazor (WebAssembly or Server) client-side or circuit-specific performance — a different
  execution model from a backend API service.
- Classic .NET Framework in depth beyond the container-awareness and `SynchronizationContext`
  caveats in §6; treat it closer to `runtimes/universal.md`'s generic reasoning where this file's
  specifics are flagged as not applying.
- Exhaustive GC or thread-pool numeric tuning; recommend the measurement (`dotnet-counters`,
  `dotnet-trace`) that determines the right value rather than a specific number.
