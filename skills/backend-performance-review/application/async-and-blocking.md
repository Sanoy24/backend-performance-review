# Async and blocking

Asynchrony is a technique for keeping a unit of concurrency useful while it waits for I/O.
It is not a speed improvement, and treating it as one produces both bad recommendations and
bad code.

The findings in this file are among the most valuable available from static analysis,
because they are unambiguous from code and their blast radius is often the whole process.

---

## 1. What async does and does not do

**Does:** allows one thread to serve many concurrent I/O-bound operations, reducing memory
and context-switch overhead relative to thread-per-request.

**Does not:** make any individual operation faster; help CPU-bound work; add parallelism in
single-threaded runtimes; remove the need for bounded concurrency.

Consequences for review:

- Converting an I/O-bound service to async can raise concurrency substantially. Converting
  a CPU-bound one changes nothing and adds complexity — never recommend it.
- "Make it async" is on the cargo-cult list. Recommend it only with a stated mechanism:
  *this path spends most of its time waiting on I/O, and workers are the constraint.*

---

## 2. The critical finding: blocking in an async context

A synchronous, blocking operation inside an async execution context stalls the executor. In
a single-threaded event loop it stalls *everything* — including tasks with no relationship
to the blocking work, including health checks.

Operations that block, ordered roughly by how often they are missed in review:

| Operation | Why it is missed |
|:--|:--|
| A synchronous datastore or HTTP client in an async handler | Looks identical to the async one at the call site |
| DNS resolution | Frequently synchronous even in async stacks |
| Filesystem I/O, including config reads and file logging | Rarely thought of as I/O |
| Password hashing and key derivation | Deliberately expensive, by design |
| Compression, image processing, PDF generation | Obviously CPU work, but easy to overlook in a handler |
| Large JSON serialization or parsing | Scales with payload; fine until payloads grow |
| Regex with catastrophic backtracking | Input-dependent, so it passes tests |
| Synchronous `sleep` | Occasionally used for rate limiting or retry backoff |

**How to find them:** identify the async entry points, then look for calls into libraries
whose async variant exists but is not being used, and for CPU-heavy work with no offload.
A dependency list that contains both a sync and an async client for the same service is a
strong signal worth grepping.

Scoring: position depends on where it sits, but blast radius is `system-wide` when the
runtime is single-threaded. Confidence is `High` — this is a property of the code and does
not depend on workload.

The correct fix is to use the async variant, or to offload the blocking work to a thread or
process pool — and, when offloading, to bound that pool. Offloading to an unbounded
executor swaps a stall for a resource exhaustion.

---

## 3. Partial and accidental async

- **An async function that never awaits.** It runs synchronously and provides no benefit,
  while looking like it does.
- **Awaiting sequentially what could run concurrently.** Independent I/O calls awaited one
  after another add their latencies. This is a genuine and safe latency win when the calls
  are truly independent and the downstream can absorb the concurrency.
- **Fire-and-forget tasks** that are never awaited: errors vanish, and the work may be
  cancelled at shutdown. This is a correctness problem with performance symptoms —
  duplicated or lost work.
- **Unbounded task spawning.** One task per item with no semaphore is unbounded concurrency
  in disguise; it will saturate whatever it calls.
- **Sync/async bridges.** Repeatedly entering an event loop from synchronous code, or
  blocking on an async call from a sync context, is expensive and frequently deadlock-prone.

---

## 4. Mixed concurrency models

Many production deployments compose models: multiple processes, each running an event loop,
each with a thread pool for blocking work. Analysis must account for the composition:

- Connection arithmetic multiplies across every layer: pool × loops × processes × instances.
- The auxiliary thread pool is a bounded resource with its own queue, and it is usually
  small by default. Offloading heavy work there saturates it quickly.
- Per-process memory multiplies by process count against the container limit.

Establish the actual production start command — entrypoint script, `Procfile`, container
`CMD`, or process manager config — rather than inferring it from the framework. The worker
and thread counts there are frequently the most important numbers in the repository.

---

## 5. Background work

- Work deferred to a background task within the same process still consumes the same CPU,
  memory, and pool. Deferring improves *user-perceived* latency, not capacity. Say which one
  the recommendation targets.
- Work deferred to a separate worker process or queue genuinely moves the load, at the cost
  of a delivery guarantee to think about.
- Background work sharing a connection pool with request handling can starve it. Separate
  pools, or a bounded share, are the usual answer.
- A task scheduled per request with no coalescing can produce more background work than
  foreground work.

---

## 6. Cancellation

When a client disconnects or a timeout fires, does the work stop?

Uncancelled work after a client has given up is pure waste, and it is worst exactly when
the system is overloaded — the moment timeouts fire is the moment capacity matters most.
This turns a degradation into a spiral: slow responses cause timeouts, timeouts cause
retries, and the abandoned original work still consumes capacity.

Check that cancellation signals propagate from the entry point through to datastore calls
and outbound requests, rather than stopping at the first layer.

---

## 7. What to look for in a review

- The concurrency model, and the real production worker/thread configuration.
- Any blocking call in an async handler — the highest-value item in this file.
- Independent awaits issued serially.
- Unbounded task spawning; unbounded offload pools.
- Fire-and-forget work.
- Background work sharing pools with request handling.
- Whether cancellation propagates.

## 8. What not to conclude

- Do not recommend converting to async without showing the path is I/O-bound and that
  workers are the constraint.
- Do not recommend parallelising calls that share a saturated downstream.
- Do not assume a library is non-blocking because the codebase awaits it — check that the
  client itself is async.
- Do not treat thread-per-request as a defect. It is a valid model with different
  trade-offs, and it is frequently the right one.
