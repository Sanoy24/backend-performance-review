# .NET (CLR)

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file covers the modern, cross-platform CLR (.NET 5+, and its .NET Core predecessor). It
contains only what those two files do not give you: why a blocking call costs more here than
on a thread-per-request JVM, how to score it, and the CLR's own memory and deployment levers.
Where the legacy .NET Framework (pre-.NET-Core, Windows-only) is in use, see §6 first.

---

## 1. Detection signals

A `.csproj`, `.sln`, or `.fsproj` file, or a `Dockerfile` with a `mcr.microsoft.com/dotnet`
base image. The target framework moniker inside the `.csproj` (`<TargetFramework>net8.0</
TargetFramework>`) or a `global.json`'s SDK pin records the version — note it, since §6 is
version-sensitive.

**Before scoring anything, find every synchronous blocking call on a hot path of an async
handler.** There are two forms, and they cost the same:

- **Sync-over-async:** `.Result`, `.Wait()`, or `.GetAwaiter().GetResult()` on a `Task`.
- **A synchronous I/O method where an `*Async` one exists.** These look like ordinary calls,
  so they are the ones missed. In EF Core: `ToList()`, `Count()`, `Any()`, `First()`,
  `Single()`, `Find()`, `SaveChanges()`, `Database.BeginTransaction()` and the transaction's
  `Commit()`, against `ToListAsync()`, `CountAsync()`, and so on. In ADO.NET:
  `ExecuteReader()`/`ExecuteNonQuery()`/`ExecuteScalar()`. Elsewhere: `HttpClient.Send`,
  `Stream.Read`/`Write`, `File.ReadAllText` and its siblings.

Check shared code that runs on every request first: MediatR pipeline behaviors, middleware,
action filters and `DbContext` interceptors. One blocking call there is on every path.

---

## 2. What differs from the universal runtime baseline

### A blocking call holds a thread every other request needs

ASP.NET Core runs request handlers, and every continuation after an `await`, on one shared
`ThreadPool`. There is no request `SynchronizationContext`: when any request's awaited I/O
completes, its continuation waits in the pool's queue for a free thread. A thread blocked in
a synchronous call is unavailable for the whole call, to every request in the process. When
enough threads block at once, continuations queue behind them, and requests that never block
slow down too.

The pool grows past its minimum (by default, one thread per logical core) only gradually,
through a hill-climbing algorithm tuned for steady load, not bursts. So the symptom is
specific: **latency degrades progressively under load rather than failing sharply**, with
thread count climbing slowly while the pool's queue length grows. This is thread-pool
starvation. It is neither a stalled event loop nor a hard pool ceiling.

### Scoring: the pool is the shared resource

This is what separates .NET from a thread-per-request JVM, where `technology/jvm.md` scores the
same blocking JDBC call as a minor, bounded cost. There, a blocked thread delays only its own
request. Here, it delays the continuations of every other request.

| Where the blocking call sits | Blast radius | Reading off `rubrics.md` §2 |
|:--|:--|:--|
| Shared code on every request: a pipeline behavior, middleware, a filter | `system-wide` | `critical-path` + `system-wide` saturation → **Critical**: blocked threads grow with total concurrency |
| One endpoint's handler | `system-wide` | Usually **High**: saturation needs that endpoint to carry enough of the concurrency. **Critical** if it dominates traffic, or if the call can take unbounded time (an outbound HTTP call with no timeout) |
| Startup, a background service on its own thread, an offline job | Per `rubrics.md`; not the request pool | Follows position and frequency; usually Low |

Confidence is `High` for the code fact, as `application/async-and-blocking.md` §2 says. How soon
starvation appears depends on concurrency, so state the load at which it would show, or that it
is unknown, under `conditions`. Do not lower blast radius because the blocking call itself is
fast: a fast call shrinks the window, not the resource it holds.

### Sync-over-async can deadlock where a `SynchronizationContext` is captured

Under classic ASP.NET (System.Web) or a WPF/WinForms UI thread, `.Result`/`.Wait()` can deadlock
outright: the async method's continuation is queued back onto the context that the blocked
thread now occupies. ASP.NET Core installs no request `SynchronizationContext`, so this exact
deadlock is unlikely there. Library code shared with older hosts still needs
`ConfigureAwait(false)`. Confirm the hosting model before ruling this in or out.

### Value types avoid heap allocation — until they are boxed

A `struct` lives inline, on the stack or inside its containing object, so it creates no GC
pressure of its own. **Boxing** puts it on the heap after all: assigning it to `object`, passing
it as an interface, or storing it in a non-generic collection (`ArrayList`, `Hashtable`). A
struct boxed in a hot loop is a specific, checkable allocation source. `Span<T>`/`Memory<T>`
slice existing memory without copying, and are the concrete tool for removing allocation from
a hot parsing or serialization path.

### Server GC, container awareness, and the Large Object Heap

The GC runs in **Workstation** mode (one heap, small footprint) or **Server** mode (one heap per
logical core, more memory for higher throughput). ASP.NET Core defaults to Server GC. Since .NET
Core 3.0, the runtime reads the container's cgroup CPU and memory limits to size GC heaps and
thread-pool defaults — the check `runtimes/universal.md` §4 asks for usually passes here, but
not on legacy .NET Framework.

Objects of 85,000 bytes or more go on the **Large Object Heap (LOH)**, which is not compacted by
default. Repeatedly allocating large arrays or strings can fragment it, visible as memory that
grows although the objects are short-lived. `GCSettings.LargeObjectHeapCompactionMode` requests
an on-demand compaction, at the cost of a longer pause. Pooling buffers (`ArrayPool<T>`) avoids
the allocation entirely.

### Native AOT removes JIT warm-up, with constraints

Native AOT compiles ahead of time to a native binary: no JIT warm-up, faster startup, smaller
footprint. It restricts runtime reflection and dynamic code generation, which some serializers
and DI containers rely on. Surface it only for cold-start-sensitive targets, such as aggressive
serverless scale-to-zero.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `dotnet-counters` | Live metrics: GC heap size and collections per generation, thread-pool thread count and queue length, exception rate | **safe-on-production** — designed for continuous, low-overhead monitoring |
| `dotnet-trace` | CPU and event-based profiling via `EventPipe`, viewable as a flame graph | **safe-on-production** at default sampling; a targeted, time-boxed capture is still the usual approach |
| `dotnet-gcdump` | A heap snapshot for object-graph and allocation analysis, without a full process dump | **not-safe-on-production** for a large heap — the capture pauses the process |
| `dotnet-dump` | A full process memory dump | **not-safe-on-production** — same caveat as `dotnet-gcdump`, more so |
| PerfView / `EventPipe` sessions | Deeper ETW-based analysis (GC internals, JIT events, contention) | **not-safe-on-production** for continuous use; a short, targeted session under representative load is the appropriate use |

`dotnet-counters` needs no restart and is the fastest way to confirm starvation: under load,
`ThreadPool Queue Length` grows while `ThreadPool Thread Count` climbs slowly. Recommend it in a
starvation finding's `validation`.

---

## 4. Common failure modes and their symptoms

| Symptom | .NET-specific cause to check first |
|:--|:--|
| Latency degrades progressively under increasing load rather than failing sharply | Thread-pool starvation from a blocking call in either form in §1, especially in shared per-request code — check thread-pool queue length |
| A request hangs indefinitely rather than timing out, in code that runs under a captured `SynchronizationContext` | The sync-over-async deadlock in §2 — confirm the hosting model first |
| Elevated Gen0 GC frequency with no obvious large-object cause | Boxing of value types in a hot path |
| Memory grows despite short-lived objects, and generational GC does not reclaim it | LOH fragmentation from repeated large (≥ 85,000-byte) allocations |
| GC heap count or thread-pool sizing looks wrong under a container CPU limit | A pre-3.0 .NET Core or legacy .NET Framework runtime, which reads host resources, not cgroup limits |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Server GC vs. Workstation GC | Higher throughput and more memory (one heap per core) versus a smaller footprint for lower concurrency |
| Concurrent/Background GC | Shorter pauses versus extra CPU and memory for the concurrent collection thread |
| `ThreadPool.SetMinThreads` | Faster ramp-up under a burst versus idle threads reserved ahead of need. It masks starvation rather than fixing it: recommend the `*Async` call first |
| LOH compaction mode | Less fragmentation versus a longer pause when a compaction runs |
| Native AOT vs. JIT | Faster startup and lower memory versus reduced reflection and dynamic-code support |

---

## 6. Version differences worth knowing

- **.NET Core 3.0** made GC heap count and thread-pool sizing read cgroup limits by default.
- **.NET 5** dropped "Core" from the name. "The modern CLR" here means .NET Core 3.x and .NET 5
  and later, not .NET Framework.
- **ASP.NET Core** has never installed a request `SynchronizationContext`. Classic ASP.NET
  (System.Web) does.
- **Native AOT** became production-ready across .NET 7 and 8. Confirm it is the deployment model
  in use, not a self-contained JIT-based publish, before applying §2's reasoning about it.
- **Legacy .NET Framework** has none of the container awareness above, and a Windows-only
  deployment model. Thread-pool starvation still applies to it.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Framework-specific behavior (ASP.NET Core middleware pipeline, minimal APIs vs. controllers,
  Entity Framework Core query generation) — see `application/api.md`, `application/data-access.md`,
  and the relevant `databases/`/`technology/` files.
- Blazor (WebAssembly or Server) client-side or circuit-specific performance — a different
  execution model from a backend API service.
- Classic .NET Framework in depth beyond the caveats in §6; treat it closer to
  `runtimes/universal.md`'s generic reasoning where this file's specifics are flagged as not
  applying.
- Exhaustive GC or thread-pool numeric tuning; recommend the measurement (`dotnet-counters`,
  `dotnet-trace`) that determines the right value rather than a specific number.
