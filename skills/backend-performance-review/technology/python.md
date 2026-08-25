# Python (CPython)

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file assumes **CPython**, the reference implementation almost every backend Python service
runs on. It contains only what those two files do not give you — the general blocking/non-
blocking taxonomy and generic GC reasoning are covered there. Where PyPy or another
implementation is in use, see §6 before applying anything here.

---

## 1. Detection signals

`requirements.txt`, `pyproject.toml`, `poetry.lock`/`Pipfile.lock`, `setup.py`, or a `.venv`
directory. The interpreter version: a `.python-version` file, `python_requires` in
`pyproject.toml`/`setup.py`, or the base image tag in a `Dockerfile` — record it, since several
behaviors below are version-dependent.

**Determine the concurrency model before anything else**, from the actual process manager
config rather than assuming: Gunicorn/uWSGI worker class (`sync`, `gthread`, `gevent`,
`eventlet`, or an ASGI worker like `uvicorn.workers.UvicornWorker`), or a direct `uvicorn`/
`hypercorn`/`daphne` invocation. This single fact determines which half of §2 applies.

---

## 2. What differs from the universal runtime baseline

### The concurrency model changes the severity of the identical code pattern

The same blocking call in a request handler has a completely different cost depending on the
model in use — the single most consequential distinction to get right before scoring anything:

| Model | Typical stack | A blocking call in a handler costs... |
|:--|:--|:--|
| **Sync, thread/process-per-request (WSGI)** | Flask, classic Django, Gunicorn `sync`/`gthread` workers | One worker slot, for the duration — a capacity cost, bounded by worker count |
| **Async, event-loop (ASGI)** | FastAPI, Starlette, Django async views, `uvicorn`/`hypercorn` | The **entire event loop for that worker process** — every other concurrent request on it stalls, the same severe pattern `application/async-and-blocking.md` describes for any single-threaded event loop |
| **Cooperative, monkey-patched (greenlets)** | `gevent`/`eventlet` workers | Depends entirely on whether the blocking call's library was actually patched — see the monkey-patching subsection below |

**Do not apply async-event-loop severity reasoning to a WSGI/sync deployment, or vice versa.**
The same `requests.get()` call inside a handler is a minor, bounded cost under sync workers and
a `Critical`, system-wide-for-that-worker finding under ASGI.

A single codebase can mix models — Django supports sync and async views side by side, bridged
by `sync_to_async`/`async_to_sync` — and that bridge has its own real, non-zero per-call cost,
worth checking when a hot path crosses it repeatedly.

### The GIL is the concrete mechanism behind this runtime's parallelism dimension

`runtimes/universal.md` asks whether a runtime can execute application code on multiple cores in
one process. For CPython, the concrete answer is the **Global Interpreter Lock**: only one
thread executes Python bytecode at a time, in a single process, regardless of core count.

- **Threading does not provide CPU parallelism for pure-Python code.** Adding threads to
  CPU-bound Python work and expecting a multi-core speedup is a specific, checkable mistake —
  the GIL prevents it structurally, and "we added threads and saw no improvement" is the
  predictable outcome, not a bug to hunt for elsewhere.
- **Threading does provide real concurrency for I/O-bound work**, because the GIL is released
  during I/O waits (and during many C-extension calls that don't touch Python objects — numeric
  libraries, some compression and hashing operations). This is why thread pools remain a
  reasonable tool for I/O-bound offload in Python specifically, even though they would not help
  a CPU-bound equivalent.
- **Genuine CPU parallelism requires a separate process** (`multiprocessing`, a process-based
  worker pool, or the deployment's own multi-process worker model) or moving the work into a C
  extension that releases the GIL for its duration. Recommending "add threads" for a CPU-bound
  bottleneck here is on the same cargo-cult footing as anywhere else, and additionally
  structurally incapable of helping.

**Version note:** Python 3.13 introduced an experimental build with the GIL removable
(free-threaded CPython, PEP 703) — opt-in, not the default build most deployments use. Confirm
the specific interpreter build before assuming the GIL doesn't apply merely because the major
version is 3.13 or later.

### Reference counting is the primary memory-reclamation mechanism, not just generational GC

Most objects are reclaimed immediately via reference counting the instant their count hits
zero, with no collector pass required. A supplemental generational, cycle-detecting collector
runs periodically only to catch reference cycles that counting alone cannot free.

Consequences: most allocation/deallocation churn does **not** produce GC-pause-style latency
spikes the way a pure tracing collector's cycle does, but the cycle-detecting collector can
still produce a pause-like cost walking a large object graph, and a codebase generating many
long-lived cyclic structures can trigger this more often than expected. `gc.disable()` with
manual, deliberate collection is a real technique some high-throughput services use — a
trade-off (cycles accumulate until manually collected) worth surfacing, not recommending
by default.

### Cooperative concurrency via monkey-patching has a specific, sharp failure mode

Where a deployment uses greenlet-based cooperative concurrency (workers that patch the standard
library so ordinarily-blocking calls yield cooperatively instead of blocking the OS thread), the
entire model depends on **every blocking call actually being patched**. A library performing
blocking I/O through a path the patching doesn't cover — a C extension making its own syscalls,
a database driver with native socket handling — blocks the whole worker exactly as if no
patching existed, and this is easy to miss because most of the codebase behaves cooperatively
and this one dependency does not. Checking which network-touching dependencies are
confirmed-compatible with the patching approach in use is a legitimate, concrete review step,
not something to assume is fine because the framework claims general compatibility.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `py-spy` | Sampling CPU profile / flame graph, **attaches to a running process with no code changes and negligible overhead** | **safe-on-production** — usually the right first tool precisely because it needs no restart |
| `cProfile` | Deterministic call-level profile | **not-safe-on-production** for anything but light use — instrumentation overhead is non-trivial |
| `scalene` | Combined CPU, memory, and (where relevant) GPU profiling, with line-level attribution | Overhead higher than `py-spy`; treat as a staging/targeted-production tool, not a default |
| `tracemalloc` (built-in) | Memory allocation tracking with source-line attribution | **safe-on-production** at a basic sampling level; higher detail settings add overhead |
| `asyncio` debug mode (`PYTHONASYNCIODEBUG=1`, or `asyncio.run(..., debug=True)`) | Flags slow callbacks directly — the most targeted way to confirm an ASGI blocking-call finding | **not-safe-on-production** — meaningful overhead by design; use in staging under representative load |
| `gc.get_stats()` / `gc.set_debug()` | Collector pass counts and timing | **safe-on-production** for `get_stats()`; `set_debug()` verbosity levels can be costly |

`py-spy` deserves the same emphasis given to `perf_hooks` in the Node reference: it is usually
the fastest path from a suspected CPU or blocking finding to real, `Confirmed`-grade evidence,
specifically because it requires no code change and no restart.

---

## 4. Common failure modes and their symptoms

| Symptom | Python-specific cause to check first |
|:--|:--|
| An ASGI endpoint's latency spikes and drags down concurrent, unrelated requests | A synchronous blocking call (a sync DB driver, `requests`, `time.sleep`, blocking file I/O) inside an `async def` handler — the direct Python analogue of Node's event-loop-blocking finding |
| "We added threads for a CPU-bound job and got no speedup" | The GIL — expected, not a bug; the fix is multiprocessing or a GIL-releasing C extension, not more threads |
| A cooperative (`gevent`/`eventlet`) worker occasionally stalls entirely under load | An unpatched blocking dependency — check which network-touching libraries are confirmed compatible with the monkey-patching in use |
| Periodic latency spikes with no obvious cause, worse on services with many long-lived interlinked objects | The cycle-detecting collector walking a large object graph; check `gc` stats before assuming it's unrelated |
| Slow application startup | Expensive module-level code executed at import time — connecting to services, building large structures eagerly rather than lazily |
| Mixed sync/async Django code is slower than expected on a hot path | Repeated `sync_to_async`/`async_to_sync` bridging, which has a real per-call cost |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Worker count (Gunicorn/uWSGI `--workers`) | More concurrent request capacity versus memory — each worker is a full separate process and interpreter, so memory multiplies directly by count |
| Threads per worker (`--threads`, `gthread` worker class) | More I/O-bound concurrency per process versus GIL contention among those threads for any CPU-bound portion of the work |
| ASGI server worker count (`uvicorn --workers`, or a process manager in front of it) | A single event loop is still one core; running only one ASGI worker process on a multi-core host leaves cores idle, the same "no process-level parallelism configured" finding as the Node reference describes |
| Worker class choice (`sync` vs `gthread` vs `gevent`/`eventlet` vs an ASGI worker) | Determines which half of the concurrency-model table above applies; changing it changes correctness assumptions about blocking calls throughout the codebase, not just performance |
| `gc` thresholds / `gc.disable()` | Fewer cycle-collection pauses versus cycles accumulating unreclaimed until a manual or process-restart collection |

---

## 6. Version differences worth knowing

- **Python 3.11** shipped substantial interpreter speed improvements (the specializing adaptive
  interpreter); baseline CPU-cost assumptions from significantly older versions may not hold.
- **Python 3.13** introduced an experimental free-threaded (no-GIL) build — opt-in, not default;
  confirm the actual interpreter build before assuming this file's GIL reasoning doesn't apply.
- `asyncio.TaskGroup` (3.11+) and various `asyncio` API changes across versions affect how
  structured concurrency is idiomatically written; older code using bare `asyncio.gather` with
  no error-group handling is not wrong, just dated.
- **PyPy** and other alternative implementations have a JIT and different GIL/memory
  characteristics entirely. Nothing in this file's §2 should be assumed to hold for them —
  confirm the interpreter (`sys.implementation.name`, or the deployment's stated runtime) before
  applying it at all; if it is not CPython, apply `runtimes/universal.md` and mark specifics as
  unknown per the skill's graceful-degradation rule.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Framework-specific routing, middleware, or ORM behavior (Django, Flask, FastAPI internals;
  Django ORM, SQLAlchemy query generation) — see `application/api.md`, `application/data-access.md`,
  and the relevant `databases/`/`technology/` files.
- Data-science or numerical-computing performance (NumPy/Pandas vectorization, GPU offload) —
  out of scope for a backend-service performance review.
- `multiprocessing` internals beyond noting it as the answer to CPU-bound parallelism; its
  IPC/serialization costs are a real topic but not covered here in depth.
- PyPy, Cython, or other non-CPython implementations in any depth — see §6.
- Specific numeric recommendations for any setting (worker counts, thread-pool sizes); recommend
  the measurement that determines it.
