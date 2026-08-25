# Node.js

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file contains **only what those files do not give you**. The single-threaded-event-loop
concurrency model, the general blocking/non-blocking taxonomy, and generic GC reasoning are
covered there and are not repeated here.

---

## 1. Detection signals

`package.json` and `node_modules` at the repository root — but see §7 of the registry's `node`
signal notes: these alone cannot distinguish a Node.js **backend** from front-end build tooling
sitting in the same repository. Confirm the actual backend entry point: a `main`/`start` script
in `package.json` pointing at a server file, a framework dependency actually imported in server
code (not just declared), or a process manager config (`pm2`, a `Procfile`, a container `CMD`)
that names a Node entry file.

Runtime version: `.nvmrc`, `engines.node` in `package.json`, or the base image tag in a
`Dockerfile`. Record it — several behaviors below are version-dependent.

---

## 2. What differs from the universal runtime baseline

### The event loop has phases, and where a callback runs is not interchangeable

`runtimes/universal.md` covers what happens when *something* blocks the loop. Node's loop has a
specific structure worth knowing when reading code that reasons about ordering: timers, pending
I/O callbacks, an idle/prepare phase, a poll phase (where most I/O callbacks actually fire),
`setImmediate` callbacks, and close callbacks — with `process.nextTick` and resolved-Promise
microtasks draining **completely** between every phase, not just once per loop iteration.

The practical consequence: **recursive or unbounded `process.nextTick`/Promise scheduling
starves the loop entirely** — I/O callbacks in the poll phase never get a turn, because the
microtask queue is fully drained before the loop advances. This produces a distinctive symptom —
the process looks alive (it's doing CPU work) but stops servicing any I/O — that is easy to
misdiagnose as a hang. It is a different failure from "one synchronous call blocked the loop
once"; it is the loop being denied its turn indefinitely.

### The libuv thread pool is small, bounded, and used for more than you'd expect

Node offloads a specific set of operations to a fixed-size worker pool (libuv's thread pool),
**default size 4**, regardless of CPU core count: most filesystem operations, `dns.lookup`
(though not `dns.resolve`, which is not the same call), some `crypto` functions (`pbkdf2`,
`scrypt`, `randomBytes` in async form), and `zlib` compression.

This pool is shared across the whole process for **all** of these operations together. A
handler that does a lot of filesystem access or async crypto is competing with every other
concurrent request's filesystem and crypto calls for the same four slots — a bounded-resource
saturation exactly in the shape `principles/concurrency-and-contention.md` §3 describes, but
concrete here: **the pool size (`UV_THREADPOOL_SIZE`) is a specific, checkable environment
variable**, and its absence from configuration, combined with concurrent load involving any of
the operations above, is a legitimate finding — not a guess, since the default is well-known and
the affected operations are enumerable.

### Achieving parallelism is an explicit, visible architectural choice

A single Node process runs application JavaScript on one thread. Universal runtime reasoning
already establishes "parallelism: no, or restricted" as a dimension to check (`runtimes/
universal.md` §1); for Node the concrete mechanisms are:

- **The `cluster` module or an external process manager** forking multiple processes that share
  listening ports — the standard way to use more than one core. Each forked process has its own
  event loop, its own memory, and its own libuv thread pool; connection and memory arithmetic
  must account for the process count, the same multiplication `application/async-and-blocking.md`
  §4 describes generically.
- **`worker_threads`** — genuine OS threads with their own V8 isolate, for offloading CPU-bound
  JavaScript work without spawning a whole new process. Distinct from the libuv pool: worker
  threads run arbitrary JS you write, the libuv pool runs specific built-in operations you don't
  control the implementation of.

**No process-level parallelism configured at all** (a single process, no cluster, no worker
threads, no external multi-instance orchestration) on a host with multiple cores available is a
directly checkable, concrete finding: the deployment is not using the hardware's core count, not
merely "single-threaded" in the abstract sense every Node process always is.

### Garbage collection has a default memory ceiling worth checking against the container limit

V8's default old-space heap ceiling has historically been on the order of one to two gigabytes
per isolate unless explicitly raised via `--max-old-space-size`. This is exactly the
container-memory-limit interaction `runtimes/universal.md` §4 warns about in the abstract, made
concrete: a container memory limit set without checking this flag can mean V8 doesn't even know
to stay under it (if the flag is unset and the default ceiling is *below* the container limit,
V8 self-limits conservatively, wasting available memory instead of causing an OOM kill — the
opposite problem, and also worth flagging as a missed-capacity finding). Either mismatch —
V8's ceiling above the container limit, or well below it — is checkable directly from the
startup flags and the container spec, with no runtime data required.

---

## 3. Diagnostics

| Command / API | What it shows | Production safety |
|:--|:--|:--|
| `node --prof` and the accompanying log processor | CPU profile via V8's built-in sampling profiler | **safe-on-production** — moderate overhead, standard practice |
| `node --cpu-prof` | CPU profile written directly to a loadable `.cpuprofile` file | **safe-on-production** |
| `node --inspect` + Chrome DevTools / `node --inspect-brk` | Interactive profiling and debugging | **not-safe-on-production** for `--inspect-brk` (pauses execution); `--inspect` alone is lower-risk but still exposes a debugging port that must be network-restricted |
| `perf_hooks.monitorEventLoopDelay()` | Event-loop lag as a running metric, built into Node itself | **safe-on-production** — this is the direct way to confirm or refute a suspected blocking-call finding with real data |
| `--heapsnapshot-signal=SIGUSR2` or `v8.writeHeapSnapshot()` | A heap snapshot for retention analysis | **not-safe-on-production without care** — pauses the process and the snapshot may contain sensitive data, same caution as any heap dump |
| `--max-old-space-size` (startup flag) | The configured heap ceiling | **safe-on-production** (it's just a flag to read) |
| `UV_THREADPOOL_SIZE` (environment variable) | The configured libuv pool size | **safe-on-production** |
| `process.memoryUsage()` | RSS, heap used/total, external memory, at a point in time | **safe-on-production** |

`perf_hooks.monitorEventLoopDelay()` deserves emphasis: it is the built-in, zero-dependency way
to convert a suspected "something is blocking the loop" finding from `High` confidence (code
reading) to `Confirmed` (measured lag), and recommending it costs nothing to add.

---

## 4. Common failure modes and their symptoms

| Symptom | Node-specific cause to check first |
|:--|:--|
| Latency spikes correlated with large request/response bodies | Synchronous `JSON.parse`/`JSON.stringify` on a large payload, blocking the loop for the duration |
| The process is "busy" but stops responding to any request | Recursive `process.nextTick` or an unbounded Promise chain starving the poll phase (§2) |
| Filesystem-heavy or crypto-heavy endpoints slow down together under load, even though they're unrelated | libuv thread pool exhaustion — both compete for the same 4 default slots |
| A memory leak that grows slowly and steadily | A closure or `EventEmitter` listener capturing request-scoped data in a module-level or long-lived cache, never released |
| `MaxListenersExceededWarning` in logs | A real diagnostic, not noise — listeners are being added repeatedly without removal, usually per-request on a long-lived emitter |
| A CPU-bound endpoint blocks unrelated concurrent requests | Synchronous computation (large sort, regex backtracking, image/data processing) with no `worker_threads` offload |
| Outbound HTTP calls to the same host are slower than expected under concurrency | Default `http(s).Agent` socket limits not tuned for the actual concurrent call volume, or `keepAlive` not enabled, defeating connection reuse (`application/connection-pools.md` §5) |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `UV_THREADPOOL_SIZE` | More concurrent fs/crypto/DNS/zlib operations versus more OS threads context-switching; default 4 is conservative for I/O-heavy workloads using these operations concurrently |
| `--max-old-space-size` | Must be set with the container memory limit in mind — too high risks an OOM kill outside V8's own awareness; too low wastes available memory and triggers GC more often than necessary |
| Cluster/worker process count | More parallelism versus per-process memory multiplication and more total connections to any shared downstream (`application/connection-pools.md` §6) |
| `http.Agent` `keepAlive` and `maxSockets` | Connection reuse and concurrency to a given host versus resource use per agent; the default agent historically favors correctness over throughput and is worth revisiting for high-volume outbound calls |
| `worker_threads` pool size (if a pool wrapper is used) | CPU offload capacity versus memory — each worker is a separate V8 isolate with its own overhead |

---

## 6. Version differences worth knowing

- Native `fetch` (stable from Node 18) removes the need for a third-party HTTP client for many
  cases; code still carrying a heavier client dependency for simple calls is not a performance
  problem by itself but is worth noting as unnecessary surface.
- `worker_threads` (stable since Node 12, matured further since) is the modern answer to
  CPU-bound offload; advice assuming it is unavailable is dated.
- V8 version (tied to the Node major version) changes GC pause characteristics and JIT
  optimization behavior release to release; a specific claim about GC pause duration should be
  tied to the actual major version in use, not asserted generically.
- ECMAScript modules (ESM) versus CommonJS affects module-loading cost and, in mixed codebases,
  can introduce interop overhead; check which is actually in use rather than assuming CommonJS.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them from general knowledge that may predate the current LTS line.

---

## 7. What this file does NOT cover

- Framework-specific routing or middleware overhead (Express, Fastify, Koa, NestJS internals) —
  see `application/api.md` for the framework-agnostic reasoning; this file is about the runtime
  underneath any of them.
- ORM- or driver-specific query behavior — see `application/data-access.md` and the relevant
  `databases/` and `technology/` files for whichever datastore is in use.
- Bundler or build-tool performance (webpack, esbuild, etc.) — that is a build-time concern, not
  a running-service performance concern this skill addresses.
- Deno or Bun — different runtimes with their own event-loop and threading implementations;
  applying this file's specifics to them would be exactly the kind of unverified assumption the
  skill's evidence rules forbid. Apply `runtimes/universal.md` and mark specifics as unknown.
- Specific numeric recommendations for any setting; recommend the measurement that determines it.
