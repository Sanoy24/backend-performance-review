# Concurrency and contention

Concurrency exists to keep resources busy while something waits. Contention is what happens
when concurrent work collides over something shared. Nearly every concurrency finding in a
backend is really a contention finding.

This file is model-independent: it applies to threads, processes, coroutines, event loops,
green threads, and actor systems alike. Runtime-specific mechanics live in
`runtimes/universal.md` and per-runtime technology files.

---

## 1. Identify the concurrency model before analyzing anything

The same code has different failure modes under different models. Establish which one
applies before forming any opinion.

| Model | Parallelism | Dominant failure mode |
|:--|:--|:--|
| Process per request | Yes | Memory per process; connection count multiplies |
| Thread per request | Usually | Context switching, lock contention, stack memory |
| Event loop, single-threaded | No | One blocking call stalls everything on that loop |
| Event loop + worker pool | Partial | Blocking work saturates the auxiliary pool |
| Coroutines on a multi-threaded scheduler | Yes | Blocking a scheduler thread; synchronization cost |
| Actor / message passing | Varies | Mailbox growth; a single hot actor serializes |

Two facts to establish for any runtime: **can it execute application code in parallel**,
and **what happens when a unit of work blocks**. Those two answers determine most of the
analysis.

Multiple models frequently coexist in one process — an async server with a thread pool for
blocking calls, or a multi-process server whose workers each run an event loop. Connection
and memory arithmetic must account for the product, not one layer.

---

## 2. Blocking

Blocking means occupying a unit of concurrency while doing nothing but waiting.

The severity of a block is determined by what is blocked:

- **Blocking one thread in a large pool** — a capacity cost.
- **Blocking the only event loop** — a total stall. Every other in-flight task on that loop
  waits, including ones with no relationship to the blocking work.
- **Blocking a scheduler thread in a work-stealing runtime** — reduces effective
  parallelism and can deadlock if all scheduler threads block.

Operations that block and are easy to miss because they look local:

- Filesystem I/O, including logging to a file and reading config at request time.
- DNS resolution — frequently synchronous even in otherwise async stacks.
- Cryptographic work: password hashing, key derivation, large signature operations.
- Compression and decompression of large payloads.
- Synchronous datastore or HTTP clients used inside asynchronous handlers.
- CPU-heavy pure computation: large sorts, JSON handling of large documents, regex
  backtracking, image or PDF processing.
- `sleep` in a synchronous form inside asynchronous code.

**A synchronous client inside an asynchronous handler is one of the highest-value static
findings available**, because it is unambiguous from the code and its blast radius is the
entire loop. Confidence `High`, blast radius `system-wide`.

---

## 3. Bounded resources and their queues

Every bounded resource has an implicit queue in front of it. The queue is where latency
hides.

| Resource | Bound | What waiting looks like |
|:--|:--|:--|
| Connection pool | Pool size | Requests wait to acquire; latency rises with no datastore load |
| Thread pool | Thread count | Tasks queue; loop-adjacent work stalls |
| Worker slots | Processes × threads | Requests queue at the server or load balancer |
| Semaphore | Permit count | Deliberate throttling — the good case |
| Rate limiter | Configured rate | Rejections or waits |
| Event loop | One | Everything after a blocking call |

For each bounded resource in the system, ask three questions:

1. **Is the bound consistent with the demand it will see?** (concurrency ≈ arrival rate ×
   hold time)
2. **Is wait time observable?** An unmetered pool is a blind spot.
3. **What happens at the bound — wait, reject, or fail?** Waiting forever is the worst of
   the three, and is often the default.

Unbounded concurrency is not the fix for a bounded resource. Removing the bound moves the
saturation downstream, usually to something less able to defend itself.

---

## 4. Locks and critical sections

Everything inside a lock is serialized. Throughput through a lock is bounded by
1 / critical-section duration, regardless of how many workers exist.

Findings to look for, in order of value:

- **A lock held across I/O.** A network call inside a critical section serializes everything
  contending for that lock for the duration of a remote round trip. High value, easy to spot,
  and often severe.
- **Lock granularity too coarse.** One lock protecting unrelated state.
- **Lock ordering inconsistency.** A deadlock risk, and deadlock is a performance event.
- **A hot single lock.** Effective parallelism collapses to one.
- **Distributed locks on a hot path.** Every acquisition is a network round trip, and lock
  lease timeouts interact badly with slow work.

Alternatives worth considering before recommending finer locking: partition the state so
contenders do not overlap, make the operation idempotent and drop the lock, use an atomic
primitive, or move the serialization into a datastore that already does it well.

Note that lock-free is not free: atomics under heavy contention have their own costs, and
correctness risk rises sharply. Do not recommend lock-free structures casually.

---

## 5. Contention that is not a lock

- **Shared cache lines.** Independent counters on the same cache line serialize at the
  hardware level. Rarely the top finding in an I/O-bound service, but real in hot loops.
- **Allocator contention.** Many threads allocating heavily contend on the allocator.
- **Garbage collector.** A collection affects the whole process, so allocation pressure in
  one path becomes latency in every path.
- **Datastore-side contention.** Row locks, page latches, index hot spots — application-side
  concurrency does not help when the constraint is a single row every request updates.
- **The hot-key problem.** A monotonically increasing key, or one tenant dominating traffic,
  concentrates work on one partition, shard, or leader regardless of total capacity.

---

## 6. Backpressure

Backpressure is the signal that a producer must slow down. Without it, a system under
overload accumulates work until it fails.

Absence of backpressure appears as:

- Unbounded in-memory queues or channels.
- Accepting requests faster than they can be processed, with no admission control.
- Consumers fetching more work than they can process within its visibility window.
- Retries that increase load on a degraded dependency.

**Rejecting work quickly is usually better than accepting it slowly.** A fast rejection
lets a caller retry elsewhere or degrade gracefully; a slow acceptance consumes resources
on both sides and often ends in a timeout that wasted the work anyway.

See `distributed/retries-and-backpressure.md` for the cross-service form.

---

## 7. What to look for in a review

- What is the concurrency model, and where do models compose?
- Is any blocking operation on an event loop or on a scheduler thread?
- For each bounded resource: is the bound consistent with demand, is wait time visible,
  and what happens at the bound?
- Is any lock held across I/O?
- Is there unbounded concurrency, unbounded queueing, or unbounded buffering?
- Is there a hot key, hot partition, or hot row?
- Does anything provide backpressure, or does the system simply accept everything?

## 8. What not to conclude

- Do not recommend more concurrency without identifying what the extra workers will wait on.
- Do not recommend removing a bound; recommend a bound that fits, plus visibility.
- Do not assume async is faster. Async improves resource efficiency under I/O wait; it does
  nothing for CPU-bound work and adds real complexity.
- Do not assume parallel execution is available — several widely used runtimes restrict
  in-process parallelism for application code.
