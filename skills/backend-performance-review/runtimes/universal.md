# Runtimes — universal

A runtime's performance characteristics follow from a small number of design choices. Rather
than memorizing per-language facts, **classify the runtime along these dimensions** and the
relevant failure modes follow.

This file is the taxonomy. Per-runtime specifics belong in `technology/<runtime>.md` files.
Where no such file exists, this taxonomy plus honest unknowns is the correct output — that
is the graceful-degradation path, not a gap to paper over.

---

## 1. The dimensions

Establish these before analyzing runtime behavior. Two or three of them explain most of what
a runtime will do under load.

### Execution model

Interpreted, bytecode-with-JIT, or compiled-ahead-of-time.

- **JIT runtimes are slow until warm.** Startup and the first requests to a new instance run
  unoptimized code. This is a tail-latency and cold-start concern, and it interacts badly
  with aggressive autoscaling and short-lived instances.
- **Interpreted runtimes** have higher constant overhead per operation, which matters in hot
  loops and rarely matters on I/O-bound paths.
- **Compiled runtimes** have predictable performance from the first request, and no warmup
  to reason about.

### Parallelism

Can the runtime execute application code on multiple cores within one process?

- **Yes** — CPU-bound work can be parallelized in-process; shared-state contention becomes a
  real concern.
- **No, or restricted** — CPU-bound work must be spread across processes or offloaded. This
  is a decisive constraint: it determines whether "add threads" is even a possible answer.
  It also means per-process memory multiplies by process count.

### Concurrency model

See `principles/concurrency-and-contention.md` §1 for the taxonomy and its failure modes.
Establish two things: what happens when a unit of work blocks, and how many units exist.

### Memory management

Manual, reference counting, tracing garbage collection, or ownership-based.

- **Tracing GC** — collection pauses are a whole-process event and therefore a tail-latency
  cause affecting paths unrelated to the allocating one. Allocation rate matters as much as
  retained size.
- **Reference counting** — costs are spread rather than batched, and cycles need separate
  handling; more predictable latency, more constant overhead.
- **Ownership/manual** — no collector pauses; costs move to allocation patterns and to the
  allocator itself under concurrency.

### Startup cost

Process start, dependency loading, framework initialization, connection establishment, JIT
warmup, and any at-boot work such as migrations. Dominant in serverless and in aggressively
autoscaled deployments; irrelevant in long-lived processes.

---

## 2. Garbage collection, generically

Where a tracing collector is present:

- **Allocation rate drives collection frequency.** Reducing garbage produced on a hot path is
  usually more effective than tuning the collector, and it is the recommendation with fewer
  ways to go wrong.
- **Collections affect the whole process.** A path that allocates heavily degrades the
  latency of every other path in the process. This makes allocation pressure a
  `system-wide` blast radius even when the allocating path is not on the critical path.
- **Heap size trades pause frequency against pause duration**, broadly: a larger heap means
  less frequent but potentially longer collections.
- **Configured heap size must fit inside the container memory limit**, with room for
  non-heap memory — thread stacks, native buffers, metadata, and the allocator's own
  overhead. When it does not, the process is killed rather than collected, which produces an
  abrupt failure with nothing useful in the application log. This is a checkable
  configuration inconsistency and a high-value static finding.
- **GC pauses are a tail phenomenon.** They will not appear in average latency and will
  appear at p99.

Common allocation-pressure sources: per-request object graphs, large intermediate
collections, string building in loops, boxing in hot paths, and deserializing large payloads
into full object models when a streaming or partial parse would do.

---

## 3. Threads, stacks, and context switching

- Each OS thread reserves stack space; thousands of threads is a memory decision as much as
  a scheduling one.
- Context switching costs rise with thread count, and threads that mostly wait are paying
  memory for idleness — which is the entire motivation for async and for lightweight
  concurrency primitives.
- Lightweight concurrency (coroutines, green threads, fibers) reduces per-unit cost but does
  not remove the need for bounded concurrency: unbounded cheap tasks still saturate whatever
  they call.
- Thread pool sizing depends on workload shape: CPU-bound work wants roughly the available
  parallelism; I/O-bound work wants more, bounded by what downstream systems can absorb.

---

## 4. Container-awareness

A recurring class of misconfiguration, worth checking in every containerized deployment:

- Does the runtime observe the container's CPU quota, or the host's core count? If the
  latter, it may size thread pools, GC threads, and parallelism for a machine it cannot use,
  producing throttling and context-switch overhead.
- Does it observe the memory limit, or host memory? If the latter, its heap or cache sizing
  is based on a number that is not available to it.
- Are limits set explicitly, or inherited? Inherited limits change when the node type
  changes, silently altering runtime behavior.

Modern versions of most runtimes handle this correctly. Older versions frequently do not,
which is why runtime **version** is worth recording during discovery.

---

## 5. Startup and cold paths

- Work performed at import/initialization time — reading files, building large structures,
  establishing connections, compiling regular expressions — is startup latency.
- Lazy initialization moves the cost to the first request that needs it, which makes it a
  tail-latency event rather than a startup event. Neither is automatically better; the right
  choice depends on whether the instance is long-lived.
- Connection pools with no minimum size mean the first requests after idle pay full setup
  cost.
- Migrations or schema checks at boot serialize deployments and delay readiness.

Long-lived processes amortize all of this to nothing. Short-lived ones pay it constantly.
Establish which the deployment is before treating startup cost as a finding.

---

## 6. Profiling by runtime family

Recommend by capability and say what the evidence would show. Never assume a tool is
installed or that the process can be restarted.

| Need | Generic form |
|:--|:--|
| CPU attribution | Sampling profiler producing a flame graph |
| Allocation attribution | Allocation profiler, not just heap snapshots |
| Retention analysis | Heap snapshot or dump — usually pauses the process, and may contain sensitive data |
| GC behavior | Collector logs or runtime event streams, giving pause frequency and duration |
| Blocking detection | Event-loop lag metric, or a scheduler-stall detector |
| Startup cost | Startup trace, or timing around initialization phases |

Prefer tools that attach to a running process without a restart: a restart destroys the state
you were trying to observe, and cannot be used on a production incident.

Concrete per-runtime tooling is listed in `methodology/validation.md` §3.

---

## 7. What to look for in a review

- The five dimensions, established explicitly, plus the runtime version.
- Configured heap or cache sizes versus container memory limits.
- Runtime-configured parallelism versus CPU quota.
- Allocation-heavy hot paths in garbage-collected runtimes.
- Thread pool sizes versus workload shape.
- Startup work, and whether the deployment model makes it matter.
- Whether GC or runtime pauses are observable at all.

## 8. What not to conclude

- Do not recommend a different language or runtime. It is on the cargo-cult list, it is
  almost never actionable, and the constraint is rarely the runtime.
- Do not tune GC parameters before reducing allocation — parameter tuning is easy to get
  wrong and hard to validate.
- Do not assume a runtime's default configuration; find it or state that you could not.
- Do not claim a runtime characteristic for an engine you have no reference for. Apply this
  taxonomy and mark the specifics unknown.
