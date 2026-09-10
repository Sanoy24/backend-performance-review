# PHP

Load after `runtimes/universal.md`.

This file contains **only what that file does not give you**. The five performance
dimensions, garbage collection generically, thread/stack cost, container-awareness, and
startup-cost reasoning are covered there and are not repeated here.

---

## 1. Detection signals

Dependencies/manifests: `composer.json`, `composer.lock`, `artisan` (Laravel's CLI entry
point — a strong signal for that specific framework, not PHP generically).

Other signals: `.php` source files, `php-fpm.conf`/`www.conf`/`pool.d/*.conf` (PHP-FPM pool
configuration), `php.ini`, `php-fpm`/`php:*-fpm` container images, an `nginx`/`apache`
config referencing a `fastcgi_pass`/`ProxyPassMatch` to a PHP-FPM socket, `wp-config.php`
(WordPress).

**Record the execution model during discovery** (§2) — classic PHP-FPM (the default
assumption below), or a persistent-process runtime like Swoole, RoadRunner, or FrankenPHP.
This is the single fact everything else in this file is conditioned on.

---

## 2. What differs from the universal runtime baseline

### The default execution model is shared-nothing per request — and that changes what "startup cost" means

Classic PHP (PHP-FPM, mod_php, or the built-in CLI server) tears down almost all
application-level state — object instances, non-persistent connections, superglobals —
**after every single request**, even though the underlying OS worker process persists across
many requests. The universal file's framing ("long-lived processes amortize startup cost to
nothing," §5) does not apply the way it does for Node, the JVM, or Go: the *process* is
long-lived, but the *application state within it* is not. Consequences that follow directly:

- There is no in-process object cache that survives across requests by default. Caching
  that needs to survive a request must go through **APCu** (a local, per-worker shared-memory
  cache) or an external store (Redis/Memcached) — an in-process dictionary or class-level
  cache built the way it would be in a persistent-process runtime silently does nothing here.
- A database connection opened per request pays full connection-setup cost on every request
  unless **persistent connections** are deliberately used (`mysqli_pconnect`,
  PDO's `PDO::ATTR_PERSISTENT`) — and persistent connections carry a real, documented,
  correctness-adjacent risk (`SKILL.md` rule 8): a reused connection can carry an uncommitted
  transaction, a locked table, or a session variable left over from the *previous, unrelated*
  request that used it.

### OPcache is the single most consequential, most overlooked configuration fact in PHP

PHP compiles source to bytecode on every request unless a **compiled-bytecode cache**
(OPcache, bundled since PHP 5.5) is enabled and actually retaining what it compiles.
OPcache's own settings determine whether it is helping at all:

- `opcache.enable` off (or missing entirely) means every request pays full parse-and-compile
  cost for every included file — a large, checkable, single-setting performance defect.
- `opcache.validate_timestamps` on (the default) makes OPcache `stat()` every included file
  on every request to detect source changes — correct for development, a real per-request
  filesystem-call cost in production, where `opcache.validate_timestamps=0` (with a
  deployment process that clears the cache on deploy) is the documented production setting.
- `opcache.memory_consumption` too small for the codebase causes the cache to evict and
  recompile — visible directly in `opcache_get_status()`'s hit-rate and eviction-count
  fields (§3), not something to guess at.

### Composer's autoloader has a production mode, and running without it costs a lookup per class

Composer's default autoloader resolves class names to files at runtime. `composer install
--no-dev --optimize-autoloader` (or `--classmap-authoritative`) generates a static classmap
ahead of time, removing that per-class-load resolution cost. A deployment running the
development-mode autoloader in production — checkable directly from the deploy script or
`composer.json`'s scripts — is paying a real, avoidable cost on every class load across every
request.

### PHP-FPM's process manager is a worker-pool sizing problem, directly analogous to a connection pool

`pm.max_children` (and, for `pm = dynamic`, `pm.start_servers`/`pm.min_spare_servers`/
`pm.max_spare_servers`) sets how many PHP-FPM worker processes exist. Each worker holds a
full copy of the loaded interpreter, extensions, and OPcache-shared bytecode, plus
whatever memory the request in progress allocates. Two failure directions, both checkable
from configuration and a memory-per-worker measurement:

- **Too few workers**: requests queue at the FPM socket once every worker is busy, visible
  as rising `listen.backlog` queue depth and, past the backlog limit, connection refusals —
  the FPM-specific form of a saturated pool.
- **Too many workers for available memory**: `pm.max_children × (typical worker RSS)`
  exceeding the container/host memory limit produces OOM kills under load, exactly the
  container-awareness arithmetic the universal file (§4) describes generically, with PHP-FPM
  workers as the specific unit to multiply.

### Newer persistent-process runtimes invert the shared-nothing model, and reintroduce a class of bug PHP historically didn't have

**Swoole, RoadRunner, and FrankenPHP** run a PHP application inside a long-lived worker
process that serves many requests without tearing down application state between them —
much closer to how Node or the JVM behave, and for I/O-bound work, capable of real
performance gains the shared-nothing model cannot match. The trade: **code written assuming
per-request isolation can now leak state between unrelated requests** — a static property, a
module-level variable, or an object held in a container that was safe to leave "dirty" under
the classic model is a correctness bug under a persistent-worker model, since the next
request served by that worker inherits whatever was left behind. Confirm which model is
actually deployed (§1) before assuming either this risk or the classic per-request
performance ceiling applies.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `opcache_get_status()` | OPcache hit rate, memory usage, and eviction/restart counts | **safe-on-production** — a cheap runtime call |
| `opcache_get_configuration()` | Active OPcache settings, for confirming `validate_timestamps`/memory sizing | **safe-on-production** |
| PHP-FPM status page (`pm.status_path`, e.g. `/status`) | Active/idle worker counts, queue length, slow-request log | **safe-on-production** |
| PHP-FPM slow-request log (`request_slowlog_timeout`) | Stack trace for any request exceeding a configured threshold | **safe-on-production** — logging only, no execution overhead beyond the timer |
| Xdebug profiling mode | Full call-graph profiling with per-function timing | **not safe-on-production** — overhead is large, frequently an order of magnitude slowdown |
| Blackfire (or a comparable low-overhead profiler) | Call-graph profiling designed for low overhead | **safe-on-production only if explicitly validated for the deployed version and load** — confirm before recommending against a busy production path |
| `GC.stat`-equivalent: `memory_get_usage()` / `memory_get_peak_usage()` | Per-request memory footprint | **safe-on-production** |

Xdebug and a genuinely low-overhead profiler are not interchangeable recommendations —
naming Xdebug for a live production investigation is itself a finding worth flagging back,
not a neutral suggestion.

---

## 4. Common failure modes and their symptoms

| Symptom | PHP-specific cause to check first |
|:--|:--|
| Every request is slow in a way that scales with codebase size | OPcache disabled, or evicting due to undersized `opcache.memory_consumption` |
| Production is measurably slower than a local dev environment with "the same code" | `opcache.validate_timestamps` left at its development default, `stat()`-ing every file per request |
| Requests queue or are refused under load while CPU looks idle | `pm.max_children` too low for the concurrency being offered |
| The FPM pool is OOM-killed under load | `pm.max_children` sized without accounting for per-worker memory footprint against the container limit |
| Class loading is measurably slower in production than expected | Composer's development-mode autoloader deployed instead of `--optimize-autoloader` |
| A cache "works" in testing but never persists between real requests | An in-process cache (a class property, a static array) used as if it survives across requests under the classic shared-nothing model |
| Intermittent, hard-to-reproduce wrong data appears for unrelated users | Persistent DB connection reuse carrying state from a prior request, or (Swoole/RoadRunner/FrankenPHP) a leaked static/global between requests |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `opcache.enable` / `opcache.memory_consumption` | Compiled-bytecode reuse versus the shared-memory segment size allotted to it |
| `opcache.validate_timestamps` | Correctness under live source edits (development) versus a `stat()` call avoided per request (production) |
| `pm.max_children` and related pool settings | Concurrent request capacity versus total memory committed to PHP-FPM workers |
| `--optimize-autoloader` / `--classmap-authoritative` | Faster class resolution versus needing a `composer install` step on every deploy that changes autoload mappings |
| Persistent (`pconnect`) versus per-request DB connections | Avoided per-request connection setup versus the state-leakage risk across reused connections |
| `realpath_cache_size` / `realpath_cache_ttl` | Fewer filesystem `stat()` calls for resolved include paths versus memory held by the cache |
| Classic PHP-FPM versus a persistent-process runtime (Swoole/RoadRunner/FrankenPHP) | Per-request isolation (safer against state leakage) versus eliminating shared-nothing's per-request re-initialization cost |

---

## 6. Version differences worth knowing

- **JIT compilation (PHP 8.0+)**, configured via `opcache.jit`/`opcache.jit_buffer_size`,
  compiles hot code paths to machine code. It benefits CPU-bound work measurably; **most
  typical web request handling is I/O-bound (waiting on a database or an external call)**,
  where JIT provides little to no benefit — recommending JIT as a fix for a slow, I/O-bound
  endpoint is a cargo-cult recommendation regardless of PHP version.
- **PHP 7 was a substantial performance improvement over PHP 5** in interpreter efficiency
  and memory use; a codebase still running PHP 5 or early PHP 7 should have that treated as a
  material fact, not a detail.
- **ZTS (thread-safe) versus NTS (non-thread-safe) builds** matter specifically when PHP runs
  under a threaded SAPI (e.g. Apache's `mpm_worker`/`mpm_event` with `mod_php`) — mod_php
  under a threaded MPM requires a ZTS build, and using the wrong build is a real
  misconfiguration, not merely a performance nuance. PHP-FPM (the dominant modern deployment)
  sidesteps this entirely by using its own process-based worker model regardless of build.
- **Composer 2** materially improved autoloader and dependency-resolution performance over
  Composer 1; a lockfile format or CI step assuming Composer 1 behavior is worth flagging as
  dated.

**Confirm version- and deployment-specific claims against the current documentation for the
deployed PHP version and SAPI.**

---

## 7. What this file does NOT cover

- The five performance dimensions, garbage collection generically, thread/stack cost,
  container-awareness, and startup-cost reasoning — see `runtimes/universal.md`.
- Framework-specific behavior (Laravel's service container, Symfony's DI compilation,
  WordPress's plugin/hook architecture) beyond noting `artisan` as a detection signal.
- Apache/Nginx web-server tuning beyond the FastCGI/PHP-FPM socket relationship named above.
- Swoole/RoadRunner/FrankenPHP's own internal architecture beyond the state-leakage risk
  named in §2 — each has its own configuration surface this file does not cover.
- Security: `disable_functions`, `open_basedir`, and other hardening settings.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
