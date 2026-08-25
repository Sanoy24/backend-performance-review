# Rust

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file covers backend services written in Rust. Unlike every other runtime this skill has a
dedicated file for, Rust ships no built-in async executor — "async Rust" means a specific
third-party runtime, almost always Tokio, and this file's async-specific reasoning assumes it
unless the evidence says otherwise (see §1).

---

## 1. Detection signals

`Cargo.toml` and `Cargo.lock`, or a `Dockerfile` with a `rust` base image. **Identify the async
runtime actually in use** from `Cargo.toml` dependencies (`tokio`, `async-std`, `smol`) before
applying any of the async-specific reasoning in §2 — a synchronous, thread-per-request Rust
service (no async runtime dependency at all) does not have an executor to stall in the first
place, and the relevant failure modes are the plain OS-thread ones `runtimes/universal.md`
already covers.

**Confirm the build profile actually deployed** (`cargo build --release` vs. a debug build) before
drawing any conclusion from a profile, benchmark, or reported latency. A debug build has
essentially no optimization applied and is commonly 10–30x slower than release — a real,
recurring evaluation mistake, not a hypothetical one.

---

## 2. What differs from the universal runtime baseline

### There is no garbage collector — memory is reclaimed deterministically, but reference cycles still leak

Rust's ownership and borrowing system enforces memory safety at compile time; memory is freed
deterministically the instant a value goes out of scope (RAII, via the `Drop` trait), with no
runtime collector and no GC pauses to find. This removes an entire category of finding this
skill covers for every other runtime in its `deep` tier — there is no pause-time, no
generational-heap, no collector-algorithm-choice question to ask.

This does **not** mean memory growth or leaks are impossible:

- **`Rc`/`Arc` reference cycles are never reclaimed.** Rust's ownership model prevents most
  leaks by construction, but a cycle of reference-counted pointers (an `Rc<RefCell<Node>>` graph
  with parent/child pointers going both ways, for example) keeps its reference count above zero
  forever, and Rust has no cycle-detecting collector to catch it the way CPython's supplemental
  GC does (`technology/python.md` §2). A steadily growing heap in a service using `Rc`/`Arc`
  extensively is the concrete signature to check for this specific pattern, most often via `Weak`
  references that should have broken the cycle but weren't used.
- **`Arc` itself has real, non-zero overhead** — cloning it and dropping the last reference are
  atomic operations, not free, and contention on the atomic counter is a measurable cost under
  very high-frequency cloning across threads.
- Unbounded collections (`Vec`, `HashMap`) still grow without limit if the application logic
  doesn't bound them — the absence of a GC changes nothing about this category of finding, which
  `principles/resources.md` already covers generically.

### Async execution depends entirely on which runtime is chosen, and blocking has a narrower blast radius than a single-threaded event loop

Because async support isn't in the standard library, "this service is async" is not a complete
fact — it names an executor with its own scheduling model. **Tokio**, the dominant runtime,
defaults to a **multi-threaded, work-stealing scheduler** with one worker thread per CPU core
(configurable). This changes the shape of the blocking-call finding relative to a genuinely
single-threaded event loop (Node, a JVM reactive stack): a synchronous, CPU-heavy, or blocking-I/O
call inside an `async fn` stalls the **one worker thread** executing it — the tasks scheduled on
that worker stop making progress, but tasks on the runtime's other worker threads continue. The
severity is real but narrower than the "the entire process stops serving requests" shape a truly
single-threaded event loop produces; do not apply that most-severe framing here without
confirming the runtime is actually configured single-threaded (Tokio's `current_thread` flavor,
sometimes chosen deliberately for lower overhead in a low-concurrency service).

Tokio provides `spawn_blocking` specifically to move blocking work off the async worker pool onto
a separate, dedicated blocking-thread pool — the direct, idiomatic fix, playing the same role
Node's `worker_threads`/thread-pool offload or a JVM virtual thread's carrier-unmounting plays
elsewhere in this skill's runtime coverage. A blocking call inside `async fn` that was never moved
to `spawn_blocking` is the single highest-value thing to check first in an async Rust service.

### Static dispatch is the default; dynamic dispatch is an explicit, checkable opt-in

Generic functions are monomorphized — the compiler generates a specialized copy per concrete type
used, rather than using type erasure — trading compile time and binary size for call-site
performance close to hand-written code. Dynamic dispatch (`dyn Trait`, `Box<dyn Trait>`) is
available but must be requested explicitly, trading a vtable-call indirection and a heap
allocation (for `Box<dyn Trait>`) for reduced code size and faster compiles. Pervasive `dyn Trait`
use specifically inside a demonstrated hot loop is a real, checkable finding — Rust programmers
coming from a language with pervasive dynamic dispatch by default sometimes reach for it out of
habit where static dispatch was available and idiomatic.

### Compile-time data-race prevention applies only to safe Rust

The ownership/borrowing system prevents data races at compile time for code outside `unsafe`
blocks — a real, structural guarantee `principles/concurrency-and-contention.md`'s generic race-
condition reasoning can mostly be set aside for. This guarantee does **not** extend inside
`unsafe` blocks, which exist specifically to let the programmer bypass it — treat a codebase's
`unsafe` usage as the boundary where classic race-condition and memory-safety review still
applies in full, and where a correctness finding (per `SKILL.md` rule 8) is more likely to
originate than anywhere else in a Rust codebase.

### No JIT means no warm-up, and the allocator is a real, swappable choice

Rust compiles ahead-of-time to native code; there is no JIT tier and no warm-up period the way
the JVM, .NET, or Node's V8 have — a genuine structural advantage for a cold-start-sensitive
deployment target, achieved without needing an alternative build mode the way the JVM needs
GraalVM native image or .NET needs Native AOT.

The global allocator is a pluggable, application-level choice — swapping the system default for
`jemalloc` or `mimalloc` is a well-documented, real lever for allocation-heavy workloads, unlike
most languages in this skill's coverage where the allocator isn't something application code
routinely chooses. Worth checking as configuration, not assuming as fixed.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `perf` (Linux) / `cargo flamegraph` | Sampling CPU profile / flame graph, works on any native binary including a release-build Rust service | **safe-on-production** — low overhead by design, the usual first tool |
| `tokio-console` | Live view of Tokio task polling times, busy/idle time, and slow-to-poll tasks — the direct way to spot a task stalling a worker | **safe-on-production** at low overhead once instrumented, but requires the `console-subscriber` crate compiled in ahead of time — not retrofittable onto a running process without a redeploy |
| `heaptrack` / `valgrind` (`massif`) | Detailed heap allocation tracking and leak detection | **not-safe-on-production** — substantial instrumentation overhead |
| `dhat` | Heap profiling with allocation-site attribution | **not-safe-on-production** — requires a special build; a staging/targeted tool |
| `cargo bench` / Criterion | Micro-benchmark harness with statistical rigor | Not a production tool at all — a development-time measurement; still only meaningful against a release build |

`perf`/`cargo flamegraph` deserve the same emphasis given to the always-on tools in the other
runtime references: no code changes required, negligible overhead, and usually the fastest path
from a suspected finding to `Confirmed`-grade evidence. `tokio-console` is the more targeted tool
specifically for confirming a suspected worker-blocking finding, at the cost of needing to be
built in ahead of time.

---

## 4. Common failure modes and their symptoms

| Symptom | Rust-specific cause to check first |
|:--|:--|
| One Tokio worker's tasks stall while others continue, under a multi-threaded runtime | A synchronous or blocking-I/O call inside an `async fn` that was never moved to `spawn_blocking` |
| The entire service stops serving requests under a single blocking call | The Tokio runtime is configured single-threaded (`current_thread`) — confirm the runtime flavor before treating this as unusually severe for "just one blocking call" |
| Heap usage grows steadily with no obvious cause, in a codebase using `Rc`/`Arc` for shared ownership | A reference cycle — check for parent/child pointers that should use `Weak` on one side |
| High contention or serialization under concurrent load despite "using async" | Coarse-grained `Arc<Mutex<T>>` protecting more state than necessary, serializing access that could be more finely partitioned |
| Reported benchmark numbers look far worse than expected for the workload | Confirm a `--release` build was actually used — a debug build's near-total lack of optimization is the first thing to rule out |
| A hot loop is slower than the equivalent statically-dispatched code | Pervasive `dyn Trait` use inside the loop where static dispatch (generics) was available |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Cargo release profile (`opt-level`, LTO, `codegen-units`) | Higher optimization and better runtime performance versus longer compile times — confirm the actual deployed build uses `--release` with the profile settings assumed, before drawing conclusions from any measurement |
| Tokio worker-thread count | More parallel task execution versus contention/scheduling overhead at high counts — defaults to core count, not always the right number for a workload dominated by blocking-offloaded work |
| `spawn_blocking` thread-pool size | More headroom for offloaded blocking work versus memory/thread overhead per additional thread |
| Global allocator choice (system default vs. `jemalloc`/`mimalloc`) | Different throughput/fragmentation trade-offs under allocation-heavy workloads — a real, application-level choice worth checking rather than assuming fixed |
| `Arc<Mutex<T>>` granularity | Simpler code with one coarse lock versus finer-grained locking (or partitioning) that reduces contention at the cost of complexity |

---

## 6. Version differences worth knowing

- **`async`/`await` stabilized in Rust 1.39** (2019). Code predating this uses now-legacy patterns
  (the `futures 0.1` ecosystem) — rare in an actively maintained codebase, but a real signal of
  significant technical debt if encountered.
- **`async fn` in traits** stabilized progressively, reaching a usable state around **Rust 1.75**.
  A codebase working around its earlier absence (via `async-trait`-crate boxing, for instance)
  may be carrying now-unnecessary dynamic-dispatch overhead if it hasn't revisited that choice
  since upgrading.
- The 2018/2021 **edition** setting in `Cargo.toml` is a real, checkable project setting, but its
  implications are almost entirely ergonomic rather than performance-related — worth noting, not
  worth treating as a performance finding on its own.
- **No version of Rust ships a built-in async runtime**, and none is expected to — this is a
  stable ecosystem convention, not a gap likely to close, unlike the version-dependent gaps in
  this skill's other runtime files.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Web-framework-specific behavior (Actix, Axum, Rocket routing/middleware/extractor internals) —
  see `application/api.md` and the relevant `technology/` files.
- `unsafe` code soundness analysis in depth — flagged in §2 as the boundary where this file's
  data-race guarantees stop applying, but a full soundness review is a correctness concern (see
  `SKILL.md` rule 8), not a performance one.
- Embedded or `no_std` Rust, which has no allocator or OS assumptions and a fundamentally
  different performance model from a backend service.
- Rust compiled to WebAssembly — a different execution environment with its own constraints.
- Exhaustive allocator or Cargo-profile numeric tuning; recommend the measurement (`perf`,
  `cargo flamegraph`, a release-build benchmark) that determines the right value rather than a
  specific number.
