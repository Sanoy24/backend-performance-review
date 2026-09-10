# Ruby

Load after `runtimes/universal.md`.

This file contains **only what that file does not give you**. The five performance
dimensions, garbage collection generically, thread/stack cost, container-awareness, and
startup-cost reasoning are covered there and are not repeated here.

This file assumes **MRI/CRuby**, the dominant reference implementation, unless the code
indicates otherwise (§7). JRuby and TruffleRuby have fundamentally different concurrency
models and are out of scope.

---

## 1. Detection signals

Dependencies/manifests: `Gemfile`, `Gemfile.lock`, `config.ru` (a Rack application entry
point — present for Rails and every other Rack-based framework, not Rails-specific).

Other signals: `.rb` source files, `config/application.rb` (Rails specifically), `puma.rb`/
`config/puma.rb`, `unicorn.rb`/`config/unicorn.rb`, a `Procfile` invoking `puma`/`unicorn`/
`bundle exec sidekiq`, `ruby:*` container images, `sidekiq.yml`.

**Record which application server (Puma, Unicorn, Passenger) and which mode (process count,
thread count) is configured during discovery** — the sizing question in §2 depends on it
directly, and different servers expose the same underlying trade-off through different
settings.

---

## 2. What differs from the universal runtime baseline

### The GVL means threads give you I/O concurrency, not CPU parallelism — and that determines whether processes or threads fix a given bottleneck

MRI has a **Global VM Lock (GVL)**: only one thread executes Ruby bytecode at a time within
one process, regardless of how many OS threads exist. A thread performing I/O (a network
call, a database query, file access) releases the GVL while waiting, so **threaded
concurrency genuinely helps I/O-bound work** — but CPU-bound Ruby code (parsing,
serialization, in-memory computation) does not parallelize across threads in the same
process no matter how many are configured. This is the Ruby-specific form of the same
constraint Python's GIL imposes, and it has a direct, checkable consequence for this
runtime's dominant deployment shape:

- **True parallelism across CPU cores requires multiple OS processes**, not more threads in
  one process. Puma's *cluster mode* (multiple worker processes, each with its own GVL) is
  the standard mechanism; Unicorn is process-only by design (no threading at all).
- A deployment with many threads per worker but few worker processes leaves CPU cores idle
  under CPU-bound load — adding threads will not fix it. A deployment with many worker
  processes but each severely memory-constrained is trading the opposite way. Which lever
  actually helps depends on whether the workload's bottleneck is I/O wait or CPU work, a
  distinction worth establishing explicitly before recommending either.

### Forked workers can share memory via copy-on-write, but Ruby's GC has historically undermined it

Puma (and Unicorn) commonly fork worker processes from one preloaded parent
(`preload_app!` in Puma's configuration) so the OS's **copy-on-write (COW)** mechanism lets
workers share the memory pages of code and data loaded before the fork, rather than each
worker loading the full application independently. This substantially reduces aggregate
memory footprint — *when it works*. Ruby's garbage collector has historically undermined it:
because MRI's GC touches object header bits during mark-and-sweep, a GC cycle in a forked
worker can "dirty" pages that were meant to stay shared, defeating COW page by page over
the worker's lifetime. **GC compaction** (`GC.compact`, added in Ruby 2.7) and related
bitmap-marking improvements substantially reduce this effect, but the checkable review
questions are: is `preload_app!` (or its equivalent) actually enabled, and is the deployed
Ruby version new enough to retain COW sharing well under GC pressure — not merely "is
forking used."

### The generational GC has its own environment-variable tuning surface, distinct from JVM/Node's flags

MRI's GC is generational with incremental/lazy sweep. A common, checkable pattern: an
application that allocates a large object graph at boot (loading Rails' framework code,
routes, and initializers) forces the GC to grow the heap repeatedly in the minutes after
each deploy, since the heap starts small and expands as boot-time allocation pressure hits
it — visible as elevated GC time specifically in the period right after a restart.
`RUBY_GC_HEAP_INIT_SLOTS` (and related `RUBY_GC_HEAP_GROWTH_FACTOR`/
`RUBY_GC_HEAP_GROWTH_MAX_SLOTS` variables) let the initial heap be sized close to what boot
actually needs, avoiding that growth churn — a real, checkable, Ruby-specific tuning
question distinct from the universal file's generic GC guidance.

### Background-job concurrency inherits the same GVL constraint, and it is easy to size wrong

**Sidekiq**, the dominant Ruby background-job processor, runs multiple worker *threads*
within one process to process jobs concurrently. Per the GVL constraint above, this
concurrency setting genuinely helps I/O-bound jobs (the common case: HTTP calls, database
writes, sending email) but provides no parallelism benefit for CPU-bound jobs (image
processing, data transformation) — those need separate processes, not a higher Sidekiq
`concurrency` value. A CPU-bound job queue tuned by raising thread concurrency is a
checkable, common misconfiguration worth flagging directly, distinct from the ordinary
"is the queue backing up" question `distributed/retries-and-backpressure.md` already covers.

### ActiveRecord's lazy-loaded associations are the concrete, named form of the N+1 finding here

`application/data-access.md` already covers the N+1 pattern generically. What is
Rails/ActiveRecord-specific and worth naming concretely: association access
(`post.comments`) triggers a query lazily, on first access, by default — and the documented
fix has specific, checkable API names (`.includes`, `.eager_load`, `.preload`), each with a
different join/query strategy. The `bullet` gem is the standard development-time tool for
catching this pattern before it reaches production (§3).

---

## 3. Diagnostics

| Command / tool | What it shows | Production safety |
|:--|:--|:--|
| `GC.stat` | Detailed GC statistics: heap slots, live/free objects, minor/major GC counts | **safe-on-production** — a cheap runtime call |
| `GC.compact` | Explicitly triggers heap compaction (Ruby 2.7+) | **not safe-on-production as a reactive fix under load** — it pauses the process; useful for one-off measurement, not a live remediation |
| Puma's control/status app, or the `/stats` endpoint in cluster mode | Worker and thread-pool utilization: busy/idle threads per worker, backlog | **safe-on-production** |
| `pumactl status` | Equivalent worker/thread status from the command line | **safe-on-production** |
| `stackprof` | Low-overhead sampling profiler, CPU or wall-clock mode | **safe-on-production for sampling mode, time-boxed** — continuous profiling under heavy load still adds some overhead |
| `rack-mini-profiler` | Per-request timing breakdown, including SQL query count and timing | **safe-on-production in its default lightweight mode**; some of its deeper modes (full flame graphs) carry more overhead |
| `derailed_benchmarks` | Memory bloat and object-allocation analysis, specifically for finding what a boot or request allocates | **not safe-on-production** — designed for local/CI use against a representative workload |
| `bullet` gem | Flags N+1 queries and unused eager-loads during development/test requests | **development/test only** — not intended to run against production traffic |

Puma's status endpoint and `GC.stat` together answer the first question worth asking about
any Ruby latency complaint: whether workers/threads are saturated, and whether GC is a
material share of that time — before profiling application code at all.

---

## 4. Common failure modes and their symptoms

| Symptom | Ruby-specific cause to check first |
|:--|:--|
| CPU-bound work doesn't speed up no matter how many threads are configured | The GVL — only process count adds real CPU parallelism, not thread count |
| Memory use per worker is far higher than expected given forking | `preload_app!` not enabled, or GC activity defeating copy-on-write sharing |
| GC time is elevated specifically in the minutes after each deploy | Heap growing from a small initial size under boot-time allocation pressure; `RUBY_GC_HEAP_INIT_SLOTS` unset |
| A background job queue backs up despite raising Sidekiq concurrency | The jobs are CPU-bound; thread concurrency doesn't parallelize them under the GVL |
| A page is slow and query logs show many near-identical queries | An unloaded ActiveRecord association accessed in a loop; missing `.includes`/`.preload` |
| Requests queue at the app server while CPU looks idle | Too few worker processes for the offered concurrency, particularly under CPU-bound load |
| Behavior differs between a threads-only Puma config and a process-clustered one under identical load | GVL-bound CPU work only benefits from the process dimension, not the thread dimension |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Puma/Unicorn worker process count | CPU parallelism (bypassing the GVL) versus aggregate memory footprint |
| Puma threads-per-worker | I/O-bound concurrency within a process versus no CPU parallelism benefit and added GVL contention among threads |
| `preload_app!` (Puma) | Copy-on-write memory sharing across workers versus losing per-worker independence during a rolling restart |
| `RUBY_GC_HEAP_INIT_SLOTS` and related GC env vars | Less GC churn immediately after boot versus a larger fixed baseline heap |
| Sidekiq `concurrency` | More parallel I/O-bound job throughput per process versus memory per thread and no benefit for CPU-bound jobs |
| ActiveRecord eager-loading (`.includes`/`.eager_load`/`.preload`) | Fewer queries versus fetching data that may go unused, and (for `.eager_load`) a larger single join |

---

## 6. Version differences worth knowing

- **YJIT**, Ruby's just-in-time compiler, became available in 3.1, improved substantially
  through 3.2/3.3, and is **enabled by default starting in Ruby 3.3**. For an application on
  3.1 or 3.2 not explicitly enabling it (`--yjit` or `RUBY_YJIT_ENABLE=1`), turning it on is a
  genuinely low-effort, evidence-backed recommendation — check the deployed version and flag
  rather than assume it's already active.
- **GC compaction (`GC.compact`, 2.7+)** and subsequent GC improvements materially improved
  copy-on-write retention under forking (§2); a claim about COW memory savings should be
  checked against the actual deployed Ruby version rather than assumed uniform across
  versions.
- **Ractor** (3.0+) is an opt-in, actor-like primitive that provides genuine parallel
  execution outside the GVL for isolated units of work. It remains comparatively rarely
  adopted in ordinary application code as of most codebases reviewed — its *absence* is not
  itself a finding, but its presence changes which of the GVL constraints above actually
  apply to the Ractor-isolated portion of the code.
- **JRuby and TruffleRuby** have no GVL and a fundamentally different threading model; if
  either is in use (a distinct detection signal from MRI — check the Gemfile/CI for
  JRuby-specific gems or a `.jruby-version` file), the entire GVL-centered reasoning in this
  file does not apply, and JVM-style reasoning (`technology/jvm.md`) is the closer analogy
  for JRuby specifically.

**Confirm version- and implementation-specific claims against the current documentation for
the deployed Ruby version and implementation.**

---

## 7. What this file does NOT cover

- The five performance dimensions, garbage collection generically, thread/stack cost,
  container-awareness, and startup-cost reasoning — see `runtimes/universal.md`.
- Rails-specific concerns beyond ActiveRecord's lazy-loading pattern: view rendering, the
  asset pipeline, and Action Cable are out of scope here.
- JRuby's and TruffleRuby's own threading and JIT models in depth — noted above as a
  different architecture, not characterized.
- Sidekiq's Redis usage and queue mechanics — see `technology/redis.md` and
  `distributed/retries-and-backpressure.md`.
- Puma/Unicorn/Passenger's own deployment and process-supervision configuration beyond the
  worker/thread sizing trade-off named in §2.
- Security: gem-supply-chain concerns and `Gemfile.lock` integrity checking.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
