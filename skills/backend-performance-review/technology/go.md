# Go

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file covers the standard Go toolchain and runtime. It contains only what those two files
do not give you — generic GC and blocking/non-blocking reasoning are covered there. There is no
alternative mainstream Go implementation to caveat against, unlike the other per-runtime files
in this skill.

---

## 1. Detection signals

`go.mod` and `go.sum`, or a `Dockerfile` with a `golang` base image. The `go` directive inside
`go.mod` records the language version the module targets — note it, since §6 below is
version-sensitive, especially around goroutine preemption and memory-limit behavior.

**Identify the concurrency pattern actually in use before scoring anything**: unbounded
goroutine-per-request/per-item spawning, a bounded worker pool, or a channel-based pipeline.
This determines which failure mode in §2 and §4 is relevant — Go's runtime does not impose a
concurrency ceiling on its own, so the codebase's own patterns are the only thing that does.

---

## 2. What differs from the universal runtime baseline

### Goroutines give cheap concurrency *and* real parallelism, with a scheduler that mostly hides blocking cost — but not entirely

Go's M:N scheduler (goroutines multiplexed onto a small number of OS threads, governed by
`GOMAXPROCS`) is why a blocking call in Go does not have the same catastrophic, request-wide-stall
shape it has in a single-threaded event loop: when a goroutine blocks on a syscall, the runtime
hands its processor (`P`) off to another OS thread so other goroutines keep running. This is a
materially different mechanism from Node's or a JVM event loop's single shared thread, and from a
JVM thread-per-request model's fixed pool — it is closer to "concurrency is close to free," which
makes the failure modes that *do* exist easy to overlook precisely because the common ones don't
apply.

The two failure modes that survive this model:

- **A CPU-bound goroutine in a tight loop with no function calls or channel operations can starve
  other goroutines on the same `P`**, because the scheduler's preemption points historically
  depended on those operations. Go 1.14 added asynchronous, signal-based preemption specifically
  to close this gap — confirm the Go version before assuming a tight numeric loop cannot cause
  starvation; on older versions, it can.
- **Nothing in the language bounds how many goroutines get spawned.** A per-request or per-item
  goroutine with no worker-pool or semaphore limiting it is not "using Go's concurrency
  correctly" — it is an unbounded-growth pattern, the same shape as an unbounded queue anywhere
  else in this skill, except here it is trivial to write by accident because spawning one costs a
  single keyword (`go`).

### Goroutine leaks are this runtime's most distinctive resource-growth pattern

A goroutine blocked forever on a channel send or receive that will never be serviced — a common
result of a context not being cancelled, a consumer goroutine exiting before a producer finishes,
or a `select` missing a `case <-ctx.Done()`. — is never garbage collected while it exists, because
the runtime cannot prove it's unreachable: it's still a live, scheduled unit of execution, just a
permanently blocked one. This is the direct Go analogue of a connection-pool or memory leak
elsewhere in this skill, and `runtime.NumGoroutine()` climbing steadily under steady request
volume (not proportional to concurrent request count) is its concrete signature.

### GOMAXPROCS defaulting to host CPU count is a specific, checkable container-mismatch risk

`GOMAXPROCS` governs how many OS threads run goroutines simultaneously and defaults to the number
of CPUs the runtime detects. In a container with a CPU quota (`cpu.cfs_quota_us`/cgroup v2
equivalent) set lower than the host's actual core count, the Go runtime by default sizes itself
against the **host's** CPU count, not the container's quota — the same class of mismatch the JVM
had before its own container-awareness fixes, addressed in the Go ecosystem largely through a
third-party fix (`uber-go/automaxprocs`, which reads the cgroup limit and calls
`runtime.GOMAXPROCS()` explicitly) or manual configuration, rather than a stdlib-runtime default.
**Confirm the specific Go version's behavior and whether `automaxprocs` (or an equivalent
explicit `GOMAXPROCS` setting) is actually wired in** before assuming this is handled — an
over-provisioned `GOMAXPROCS` under a real CPU quota causes scheduler contention and CFS
throttling, visible as a service that looks CPU-starved despite apparently modest CPU usage
averaged over a longer window.

### The garbage collector is concurrent, non-generational, and tuned by budget rather than by algorithm choice

Go ships one collector — concurrent, tri-color mark-and-sweep, non-compacting, designed
specifically to keep stop-the-world pauses in the sub-millisecond range regardless of heap size.
Unlike the JVM's menu of collectors with different pause/throughput trade-offs (`technology/
jvm.md` §2), the Go GC is not a choice between algorithms; it is tuned by giving it a memory
*budget*:

- **`GOGC`** (default `100`) controls how much the heap is allowed to grow between collections,
  as a percentage of live heap size after the last collection — lower values collect more often
  and use less memory; higher values collect less often and use more.
- **`GOMEMLIMIT`** (a soft memory cap) works alongside `GOGC` to make the collector react to an
  absolute ceiling — the more directly useful knob in a container with a fixed memory limit, since
  `GOGC` alone has no notion of the container's actual memory budget.

A latency complaint attributed to "GC pauses" in Go is less often about pause *duration* (which is
usually already small) and more often about collection *frequency* driven by allocation rate —
check allocation rate and `GOGC`/`GOMEMLIMIT` configuration before assuming a pause-time problem
analogous to a JVM GC finding.

### Escape analysis decides stack-vs-heap allocation, and gets it wrong in checkable ways

Each goroutine has its own small, growable stack (a few KB initially). The compiler's escape
analysis decides at compile time whether a value can stay on the stack (cheap, no GC involvement)
or must escape to the heap (subject to GC). A value escapes when the compiler cannot prove its
lifetime is bounded by the function call — most commonly, returning a pointer to a local, storing
it in a longer-lived structure, or passing it through an interface. `go build -gcflags="-m"`
prints these decisions directly, making "why is this hot path allocating so much" a directly
answerable, evidence-backed question rather than a guess.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `net/http/pprof` | CPU, heap, goroutine, block, and mutex profiles via a built-in HTTP endpoint | **safe-on-production** — sampling-based, negligible overhead at default rates; the standard first tool |
| `go tool trace` | A detailed execution trace: scheduler decisions, GC events, goroutine state transitions over time | **not-safe-on-production** for continuous use — meaningfully higher overhead than `pprof`; capture a short window under representative load instead |
| Goroutine profile / `SIGQUIT` stack dump | Full goroutine count and stack traces at a point in time — the direct way to confirm a goroutine leak or a stuck channel operation | **safe-on-production** for an occasional capture |
| `runtime.ReadMemStats` / `expvar` | Heap size, GC pause history, goroutine count as live metrics | **safe-on-production** — cheap enough for continuous export |
| Race detector (`-race`) | Data races, at the cost of substantial CPU and memory overhead | **not-safe-on-production** — a testing/CI tool only, never run against a live workload |

`pprof`'s heap and goroutine profiles deserve the same emphasis given to JFR and `py-spy` in the
other runtime references: no restart required, low enough overhead to run continuously, and
usually the fastest path from a suspected finding to `Confirmed`-grade evidence.

---

## 4. Common failure modes and their symptoms

| Symptom | Go-specific cause to check first |
|:--|:--|
| Goroutine count climbs steadily under steady request volume, not proportional to concurrency | A goroutine leak — a channel operation with no cancellation path, or a producer/consumer pair where one side can exit while the other keeps waiting |
| CPU usage looks moderate on average but the service behaves as if starved under load, inside a container with a CPU limit | `GOMAXPROCS` sized against the host's CPU count rather than the container's quota, causing CFS throttling; check whether `automaxprocs` or an explicit `GOMAXPROCS` is actually configured |
| One goroutine appears to stall others despite Go's scheduler | A tight CPU-bound loop with no function calls or channel operations, on a pre-1.14 Go version without asynchronous preemption |
| GC-attributed latency, but individual pause durations are already small | Collection *frequency* driven by allocation rate, not pause duration — check `GOGC`/`GOMEMLIMIT` and the actual allocation rate before treating it as a JVM-style pause-time problem |
| A specific hot path allocates far more than expected | An escaping value the compiler couldn't keep on the stack — check with `go build -gcflags="-m"` before guessing |
| Unbounded memory growth under a workload with growing request volume | Per-request or per-item goroutine spawning with no worker-pool or semaphore bound |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `GOMAXPROCS` | More parallel execution versus scheduler/cache contention at high counts; must match the container's actual CPU quota, not just the host's core count |
| `GOGC` | Lower values collect more often and use less memory; higher values collect less often and use more — a direct memory-versus-CPU trade-off |
| `GOMEMLIMIT` | A soft ceiling that makes the collector react to an absolute memory budget, most useful precisely when running under a fixed container memory limit that `GOGC` alone can't see |
| Worker-pool / semaphore bound on goroutine spawning | Explicit concurrency ceiling versus throughput — since the runtime imposes none on its own, an application-level bound is the only thing standing between "concurrent" and "unbounded" |
| Buffered vs. unbuffered channels | A buffer decouples producer and consumer pacing at the cost of memory and a less immediate signal of backpressure; an unbuffered channel provides precise backpressure at the cost of a producer blocking exactly when a consumer isn't ready |

---

## 6. Version differences worth knowing

- **Go 1.5** introduced the concurrent GC and made `GOMAXPROCS` default to the host's CPU count
  (it defaulted to `1` before).
- **Go 1.14** added asynchronous, signal-based goroutine preemption — before this version, a
  tight loop with no function calls or channel operations could starve other goroutines on the
  same `P` indefinitely; confirm the deployed version before ruling this failure mode out.
- **Go 1.19** introduced `GOMEMLIMIT`, the soft memory-limit setting — a real gap in
  container-memory-aware configuration before this version.
- **Container CPU-quota awareness** (`GOMAXPROCS` reflecting a cgroup limit rather than the host's
  full core count) has historically required a third-party library (`uber-go/automaxprocs`) or
  manual configuration rather than being automatic in the stdlib runtime — confirm the specific
  version and deployment's actual configuration rather than assuming either the old or a fixed
  behavior.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Framework-specific behavior (Gin, Echo, Fiber routing/middleware internals) — see
  `application/api.md` and the relevant `technology/` files.
- `cgo` overhead and cross-language-boundary cost in depth, beyond noting it as a real but
  separate topic from pure-Go escape analysis and GC behavior.
- Build-time and compilation performance (build caching, cross-compilation) — out of scope for a
  running service's performance review.
- Generics' runtime performance characteristics in depth; they do not change the concurrency or
  GC model this file describes.
- Exhaustive `GOGC`/`GOMEMLIMIT` numeric tuning; recommend the measurement (allocation rate,
  `pprof` heap profile) that determines the right value rather than a specific number.
