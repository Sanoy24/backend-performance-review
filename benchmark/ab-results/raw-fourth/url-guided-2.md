# Performance Review: URLshortener

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec backend-performance-review/2.0)
**Commit:** `34b4cb38ca972ebcc831ddc72395f979e1672b8a`

---

## 1. Decision summary

### Overall assessment

This is a small, thin service. The hot path (redirect) does one Redis `GET`, which is about as cheap as it gets, and static analysis found **no current bottleneck on it**. The material risks are at the edges. The HTTP server has no limit on how long a connection can stay open or how large a request body can be (PERF-001). Clients, not the service, decide how many Redis keys exist and for how long (PERF-002). The health check fans out through loopback HTTP using a client with no timeout (PERF-003). The repository has no instrumentation, so every finding here comes from reading the code: none is `Confirmed`, and anything that depends on workload is capped at `Medium` confidence. **Review confidence: Medium.** I read the whole codebase, but no runtime evidence or workload answers were available.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Set `ReadHeaderTimeout`/`ReadTimeout`/`WriteTimeout`/`IdleTimeout` on the `http.Server`, and wrap request bodies in `http.MaxBytesReader` | Without them, any slow or oversized client can tie up process resources with no limit. It's a few lines of config and a quick win. |
| 2 | **PERF-002 (P2)** | Reject negative `exp` on creation, cap `exp` server-side, and make `/ui/generate` a POST | Today, clients and crawlers decide how big the Redis keyspace gets. Check `INFO keyspace` first. |
| 3 | **PERF-003 (P2)** | Give the self-test a dedicated `http.Client` with a timeout and the request context, and split out a cheap liveness check | During any slowdown the health check hangs instead of failing, and every probe makes 4 more internal requests. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is there a reverse proxy enforcing client timeouts and body limits? What is the peak request rate? | PERF-001 could fall from P1 to P3, or rise to `High` confidence. Also decides whether PERF-004 matters. | Proxy/ingress config; access-log counts per route |
| Redis `maxmemory`/policy, persistence mode, key count, production `TokenLength`/`DefaultExp` | PERF-002 could move between Low and High. A short `TokenLength` would raise PERF-005. | `redis-cli INFO memory`, `INFO keyspace`, deployment env |
| Who probes `/api/v1/healthcheck`, how often, and is it public? | Severity of PERF-003 | Monitor/orchestrator configuration |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-002 | `safe-on-production` | `redis-cli INFO keyspace` | If `keys` is much larger than `expires`, keys with no TTL exist and PERF-002's growth mechanism is live |
| PERF-002 | `safe-on-production` | `redis-cli INFO memory` | `used_memory` against `maxmemory` and `maxmemory_policy` tells you how close the store is to refusing writes |
| PERF-001 / PERF-003 | `not-safe-on-production` | `go test -race -run Test10Service ./...` (local Redis via `URLSHORTENER_REDISADDRS`) | Confirms that the timeout changes keep token creation and the health check working |

---

## 2. Scope and method

**Reviewed:** All non-test Go source (`service.go`, `dataBaseRedis.go`, `shortToken.go`, `tools.go`, `urlshortener.go`), `go.mod`/`go.sum` (via detector), `dockerfile`, `docker-compose.yml`, `build.sh`, `redisDockerRun.sh`, `.github/workflows/go.yml` and `README.md`. I read test files only for benchmarks and configuration.
**Not reviewed:** `.env_sample` was checked for variable names only, never values. Production Redis and proxy configuration are not in the repository. I did not open any vendored or module-cache source, so go-redis v7 option defaults are not verified.

**Evidence available:** Uninstrumented. There are no metrics, tracing, pprof or request-duration logging. Benchmarks exist (`Benchmark00ST00Create*` in `shortToken_test.go`, `Benchmark05DBR10set`/`Benchmark05DBR00del` in `dataBaseRedis_test.go`), but no results are committed. There are no load tests and no SLOs.

**Ranking method:** Structural signals only. The ranking is inference.

**Reference depth:** Go, Redis and Docker all have deep references. `detect_stack.py` also reported gRPC from a transitive `protobuf` entry in `go.sum`. There is no gRPC code, so that reference was not applied.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All application source and deployment files were read in full (listed above) |
| **Critical paths** | 8 / 8 |
| **Shared resources** | 5 / 5 (Redis instance, go-redis client pool, global `log` logger / stderr, HTTP server goroutines and file descriptors, `http.DefaultClient` used by the self-test) |
| **Technology support** | Go: Deep, Redis: Deep, Docker: Deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is separate from finding confidence. I read the whole repository, but I had no runtime data and no workload answers.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Request rate and route mix | No evidence exists | Access-log counts per route at peak, or a request-rate metric |
| Whether a reverse proxy bounds timeouts and body size | No evidence exists | Proxy/ingress configuration |
| Redis memory limit, eviction policy, persistence, key count | No evidence exists | `redis-cli INFO memory`, `INFO persistence`, `INFO keyspace` |
| Effective go-redis pool size and Redis dial/read/write/pool timeouts | No evidence exists (the repository sets none, so library defaults apply) | go-redis v7.4.1 option defaults, or exported `PoolStats()` |
| Latency of any path | No evidence exists | Per-route duration metric or access log with duration |

---

## 3. Architecture overview

A single Go binary (`net/http`, custom mux in `ServeHTTP`) backed by one Redis deployment (single node, Sentinel or Cluster via `redis.NewUniversalClient`). It is shipped as a `FROM scratch` image, compressed with UPX, and run as one container with `network_mode: host` and no resource limits.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Service | Go `net/http` | Go 1.24.0 (`go.mod`) | Deep | All HTTP routes |
| Datastore | Redis via `github.com/go-redis/redis/v7` | client v7.4.1; server version not in repo (test script uses 5.0.7) | Deep | Primary, durable storage of token → long URL (BGSAVE on close) |
| Container | Docker, compose | compose v3 | Deep | Single container, host network |

**Shared resources:** the single Redis instance (single-threaded command execution, shared by every route); the go-redis client pool (library-default size); the global `log` logger and stderr; the process's goroutines, sockets and memory (no server limits); `http.DefaultClient` used for loopback health-check calls.

---

## 4. Workload model

Nobody was available to answer workload questions. I asked the questions below in one batch, recorded them as unanswered, and continued under the stated assumptions.

**Known**

- One container, host networking, no replica count or CPU/memory limits. Source: `docker-compose.yml`, `dockerfile`.
- Go 1.24.0, go-redis v7.4.1. Source: `go.mod`.
- `http.Server` has only `Addr` and `Handler` set. Source: `service.go:611-614`.
- POST bodies are read with `io.ReadAll` and no size limit. Source: `service.go:143-149`.
- Each request logs an access line with all headers plus a result line, using `Lshortfile`. Source: `service.go:98`, `urlshortener.go:30`.
- Token creation retries `SETNX` until success or `URLSHORTENER_TIMEOUT` runs out (default 500 ms). Source: `service.go:455-525`, `tools.go:23,48`.
- The health check makes loopback POST token, GET token (following the redirect) and POST expire calls using `http.DefaultClient`. Source: `service.go:227-337`.
- TTL is `exp` days with no upper cap. A negative `exp` on creation is stored with no TTL. Source: `dataBaseRedis.go:50-55`, `service.go:473`.
- `GET /ui/generate?s=` creates a token on every GET. Source: `service.go:152-183`.
- No metrics, tracing or pprof exist. Benchmarks exist but have no committed results. Source: repo-wide search, `go.mod`.
- Route shape: 1 redirect read, 2 POST writes, 1 GET that writes, 1 self-test that writes, and 2 static GETs. Source: `service.go:97-140`.

**Assumed**

- No reverse proxy enforces header/read timeouts or a body-size limit. Affects PERF-001. Confidence is held at Medium because the `localhost:8080` default bind suggests a proxy may exist.
- Some clients send large or negative `exp`, or something crawls `/ui/generate?s=`, at a meaningful rate. Affects PERF-002.
- Stderr goes to a Docker log driver with no application-side buffering. Affects PERF-004.
- An external monitor probes the health check periodically. Affects PERF-003.

**Unknown**

- Request rate and route distribution.
- Proxy presence and its limits.
- Redis `maxmemory`, policy, persistence, key count.
- Health-check probe source and frequency.
- Production `TokenLength` and `DefaultExp`.
- Log sink and its write latency.

**Derived**

- Token keyspace = 64^TokenLength. At the default of 6 that is 64^6 = 68,719,476,736. From `shortToken.go:33,49` and `tools.go:22,47`.
- Expected `SETNX` attempts per creation ≈ 1/(1 − f), where f = active tokens / keyspace, assuming crypto-random uniform tokens. From `shortToken.go:38-50` and `service.go:506-521`.
- One health check in mode 0 makes 4 loopback HTTP requests (POST token, GET token, followed GET `/favicon.ico`, POST expire), 3 Redis commands (`SETNX` ≥ 1, `GET`, `EXPIRE`) and at least 5 access-log lines. From `service.go:227-337`.
- Stored keys ≈ creation rate × mean TTL for keys with a TTL, plus every key ever created with a negative `exp`. From `dataBaseRedis.go:50-55`.

**Measured**

- None. No benchmark output, profile, trace or metrics export was supplied or committed. This is why no finding is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is there a reverse proxy enforcing client header/read timeouts and a body-size limit? What is the peak request rate on redirect vs. creation? | severity / confidence / recommendation | With a proxy enforcing both, PERF-001 drops to Low/P3. Without one, it stays P1 at High confidence. The rate decides whether PERF-004 needs action. |
| 2 | high | Redis `maxmemory`/policy, persistence mode, key count and memory, plus production `TokenLength`/`DefaultExp`? | severity / confidence | Many keys with no TTL against a non-evicting limit raise PERF-002 to High. A small, TTL-bounded keyspace lowers it to Low. A short `TokenLength` raises PERF-005. |
| 3 | high | What probes `/api/v1/healthcheck`, how often, and is it public? | severity / recommendation | A frequent or public probe makes the 4x fan-out in PERF-003 a real load multiplier. |
| 4 | medium | Where does stderr go in production, and has log volume or latency been an issue? | severity / confidence | A slow or blocking sink makes PERF-004 a shared-resource stall. A fast one makes it Low. |
| 5 | medium | Was there a specific complaint (slow redirects, 408s, memory growth) behind this review? | recommendation | The review would be reorganized around confirming or refuting that complaint. |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `POST /api/v1/token` | yes | `SETNX` × attempts (≥1) | Time-bounded (500 ms default); body size unbounded | no | PERF-001, PERF-002, PERF-005 |
| 2 | `GET /api/v1/healthcheck` | yes (prober) | 3 Redis cmds via 4 loopback HTTP calls | No client timeout | no | PERF-003 |
| 3 | `GET /ui/generate?s=` | yes | `SETNX` × attempts | Time-bounded | no | Creates a token on every GET (PERF-002) |
| 4 | `GET /<token>` (redirect) | yes | 1 `GET` | yes, O(1) point lookup | no | Hottest path for a shortener. Only per-request logging is notable (PERF-004). |
| 5 | `POST /api/v1/expire` | yes | 1 `EXPIRE` | Body size unbounded | no | PERF-001 |
| 6 | `GET /` | yes | none | yes | no | Static template |
| 7 | `GET /favicon.ico` | yes | none | yes | no | Embedded bytes |
| 8 | Startup self-test | blocks startup | same as #2 | No client timeout | no | Fixed 300 ms sleep, then health check (`urlshortener.go:77`) |

**Amplification points:** one health check → 4 internal HTTP requests and 3 Redis commands. Token creation → 1/(1 − f) `SETNX` round trips, negligible at the default length. Every request → at least 2 synchronous log writes.

**Paths deliberately not analyzed in depth:** `GET /` and `GET /favicon.ico`. They do constant work with no I/O beyond the response write.

---

## 6. Layer analysis

### 6.1 Application

Handlers are thin. The token generator uses `crypto/rand` and base64. Its cost is per attempt and constant, and benchmarks for it already exist. A deferred goroutine is spawned per token creation to compute statistics. It is short-lived and bounded by request concurrency, so it is not material. `time.After` per call is fine on Go 1.24 (timers are collectable). Logging is the only per-request cost beyond Redis (PERF-004).

### 6.2 API

The server has no timeouts and no body limit (PERF-001). One state-changing operation is exposed as GET (`/ui/generate`, PERF-002). The health check is expensive and public (PERF-003). Two POST endpoints are unauthenticated (SEC-002).

### 6.3 Data access and datastore

Every Redis access is a single-key O(1) command (`SETNX`, `GET`, `EXPIRE`, `DEL`). There are no multi-key, scan or collection operations, so nothing on the request path can stall the single-threaded instance by size. Keyspace growth is the datastore concern (PERF-002). The client uses default pool and timeout settings, which the repo neither states nor exposes (considered, not reported).

### 6.6 Infrastructure

One container with host networking and no CPU or memory limits. That means no GOMAXPROCS/quota mismatch and no memory-limit mismatch is possible from the repository's own config. The UPX-compressed binary affects only startup.

### 6.7 Observability

There is none beyond an access log without durations, plus a last-attempt-rate figure on the home page (PERF-006). Instrumentation should come before acting on PERF-004.

---

## 7. Findings

### PERF-001: HTTP server has no connection timeouts or request-body limit

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `service.go:611` (`NewHandler`); also `service.go:144` (`readBody`) |
| **Tags** | quick-win |

**Problem**
`http.Server` is built with only `Addr` and `Handler`. It sets no `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout` or `IdleTimeout`. POST bodies are read with `io.ReadAll` and no size limit. A slow or oversized client holds a goroutine, a socket and, for bodies, memory with no limit.

**Performance principle**
Every resource a request can hold needs both a time limit and a size limit. Otherwise the slowest or largest caller decides how much of the process's resources get used.

**Evidence**
- `service.go:611-614`: server with no timeout fields.
- `service.go:144`: `io.ReadAll(r.Body)` with no `http.MaxBytesReader`. Used by `POST /api/v1/token` and `POST /api/v1/expire`.
- `docker-compose.yml:9`: `network_mode: host`. No proxy or ingress config in the repo.
- No runtime evidence exists.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in the number of slow or large concurrent connections. Blast radius: service-wide. Once file descriptors or memory run out, every route fails.

**Conditions**
This matters when clients reach the Go server directly, with no proxy enforcing header/read timeouts and a body limit, and some clients are slow, stalled or send large bodies. Assumed: no such proxy. The deployment is not in the repo.

**Counter-evidence**
- The default bind `localhost:8080` (`tools.go:24,49`) suggests a front proxy, but nothing proves it. **Lowers confidence**, from High to Medium.
- Goroutines are cheap and `net/http` caps header size by default. The binding resources are file descriptors and body memory, not CPU. **Bounds impact**, so severity is held at High rather than Critical.
- I searched for `SetReadDeadline`, `MaxBytesReader`, `TimeoutHandler` and limit middleware. None found. No effect.

**Why this might not matter**
If a proxy already enforces client timeouts and a body limit, the Go server only ever sees well-behaved proxy connections, and this becomes defence in depth.

**Recommendation**
Set the four server timeouts. Derive their values from the slowest legitimate request, and keep them below the proxy's timeouts. Note that `WriteTimeout` must exceed `URLSHORTENER_TIMEOUT` and the health check's duration. Wrap bodies in `http.MaxBytesReader`, sized to the largest legitimate request. For reference, the UI caps URLs at 1024 characters (`service.go:55`). This removes the unbounded hold instead of adding capacity to absorb it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Server timeouts + `MaxBytesReader` in the service | **preferred** | Fixes it where the resource is owned, regardless of deployment, in a few lines |
| Rely on a reverse proxy | | Valid only if a proxy is guaranteed, and the repo guarantees none |
| More replicas / higher fd limits | | Slow clients can use up the extra capacity just the same |

**Trade-offs**
Timeouts that are too short cut off legitimate slow clients. A body limit set too low rejects valid long URLs with 413.

**Validation**
In staging, hold N connections open without sending headers, and post large bodies. Watch goroutine count, open sockets and RSS. Expected after the fix: stalled connections close after `ReadHeaderTimeout`, oversized bodies get 413, and goroutine count returns to baseline. Falsifier: if stalled connections are already closed within a bounded time in the real deployment, the conditions don't hold. `not-safe-on-production`.

---

### PERF-002: Redis keyspace size is controlled by clients, not the service

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `dataBaseRedis.go:55` (`tokenDBR.Set`) |
| **Tags** | scalability-risk, quick-win |

**Problem**
`exp` has no upper limit. A negative `exp` is clamped to 0 and stored with **no TTL**. `GET /ui/generate?s=` creates a token on every GET. Redis is treated as durable (`BGSAVE` on close, `dataBaseRedis.go:92`), and nothing in the repo limits memory growth.

**Performance principle**
A store that is written on every user action needs a retention limit the system enforces. Growth controlled only by caller input is unbounded growth.

**Evidence**
- `dataBaseRedis.go:51-55`: clamp to 0, then `SETNX` with a 0 duration, which means no expiry.
- `service.go:473`: `DefaultExp` is substituted only when `exp == 0`.
- `service.go:164`: a GET with `s=` creates a token.
- `dataBaseRedis.go:92`: `BGSAVE`, a fork-based snapshot whose cost scales with dataset size.
- Derivation: stored keys ≈ creation rate × mean TTL, plus every key created with a negative `exp`.
- No runtime evidence of key count or memory exists.

**Impact**
Position: critical path (write path). Frequency: per request. Growth: O(n) in tokens created, over time. Blast radius: system-wide, because one Redis serves every route. Each request is O(1). The cost builds up in storage, and in `BGSAVE` time, as the keyspace grows.

**Conditions**
This matters if clients send large or negative `exp`, or crawlers hit `/ui/generate?s=`, at a rate that makes the keyspace large relative to Redis `maxmemory`. Assumed: a finite `maxmemory` with a non-evicting policy, which is correct for durable data. When memory fills, `SETNX` fails and token creation returns 500. Key count, limits and creation rate are unknown.

**Counter-evidence**
- The default `exp` is 1 day (`tools.go:50`), so clients that omit it self-limit. **Bounds impact.**
- Each key is small (a token and a URL), so growth only matters at large counts. **Bounds impact.**
- I searched for a sweeper or `exp` validation. None found. No effect.

**Why this might not matter**
If clients omit `exp` or send small positive values, the keyspace stays around creation rate × 1 day and never gets near memory limits.

**Recommendation**
Make retention a server-side decision. Reject negative `exp`, or map it to `DefaultExp`. Cap `exp` at a configured maximum. Make UI generation a POST.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Validate/cap `exp`; POST for UI generation | **preferred** | Removes the source of growth at the entry point with a small change |
| `maxmemory` with an eviction policy | | Silently deletes live links in a durable store |
| Periodic sweeper | | Needs a full `SCAN` on a single-threaded instance, and permanent keys can still be created in between sweeps |

**Trade-offs**
Changes API behavior for clients that rely on very long-lived or permanent links. Existing keys with no TTL stay until they are migrated. Moving the UI form to POST is a user-visible change.

**Validation**
Baseline with `redis-cli INFO keyspace` (`keys` vs `expires`) and `INFO memory`, both `safe-on-production`. Expected after the fix: no new key without a TTL, and the TTL-bearing key count levels off. Falsifier: if `expires` ≈ `keys` and `used_memory` stays flat for weeks, this drops to Low.

---

### PERF-003: Health check fans out through loopback HTTP using a client with no timeout

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `service.go:257` (`serviceHandler.healthCheck`) |
| **Tags** | quick-win |

**Problem**
Each `GET /api/v1/healthcheck` runs a full self-test over loopback HTTP: POST token, GET token (with the redirect followed to `/favicon.ico`), and POST expire. That is 4 internal requests and 3 Redis commands, all through `http.DefaultClient`, which has no timeout. The request context is not passed along. When the server or Redis is slow, probes hang, and each new probe adds another stuck goroutine and connection.

**Performance principle**
A liveness check must be cheap, finish within a time limit, and not multiply load on the system it is checking.

**Evidence**
- `service.go:257`, `:297`, `:324`: `http.Post`/`http.Get` calls (default client, no timeout).
- `urlshortener.go:77`: the same self-test runs at startup after a fixed 300 ms sleep.
- Derivation: 1 probe → 4 HTTP requests, 3 Redis commands and at least 5 log lines (`service.go:227-337`).

**Impact**
Position: critical path (the prober waits). Frequency: per probe, rare in normal operation. Growth: O(1). Blast radius: service-wide. Amplification: 4x.

**Conditions**
The missing timeout matters whenever the service or Redis degrades. The fan-out matters in proportion to how often the probe runs. The endpoint is unauthenticated, so anyone can trigger it. Probe frequency and exposure are unknown.

**Counter-evidence**
- Probers usually time out on their own side, but the server-side goroutine keeps going because the context is not passed through. No effect.
- Low probe frequency limits the fan-out cost. **Bounds impact**, so severity is Medium, not High.
- Redis calls may be bounded by go-redis default timeouts that the repo doesn't state. The HTTP legs are still unbounded. No effect.

**Why this might not matter**
If only an internal monitor probes every few tens of seconds, the fan-out is negligible, and the hang only shows up during an incident that is already under way.

**Recommendation**
Use a dedicated `http.Client` with a `Timeout` shorter than the prober's, and pass `r.Context()`. Split out a cheap liveness endpoint (process up plus Redis `PING`), and restrict or rate-limit the full self-test.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Bounded client + context + cheap liveness endpoint | **preferred** | Bounds the worst case and stops adding load to an already degraded system, while keeping the end-to-end test available |
| Call internal functions instead of loopback HTTP | | No longer tests the real HTTP path, which is the point of the self-test |
| Cache the health result briefly | | Reports stale health. Only worth it if a high probe rate is shown. |

**Trade-offs**
A timeout that is too short makes probes fail during brief slowdowns and can cause restarts. Splitting endpoints means updating the probes.

**Validation**
In staging, pause Redis or saturate the listener, then watch health-check duration and goroutine count. Expected: before the fix, probes hang and goroutines grow. After it, probes fail within the client timeout and goroutines stay flat. Falsifier: if probes already return in bounded time while Redis is paused, the hang part is weaker than stated. `not-safe-on-production`. Re-run the existing test: `go test -race -run Test10Service03CheckHealthCheck ./...` (needs a local Redis).

---

### PERF-004: Synchronous, verbose per-request logging through one global logger lock

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `service.go:98` (`serviceHandler.ServeHTTP`) |
| **Tags** | needs-measurement |

**Problem**
Every request writes at least two log lines synchronously: an access line that formats the **whole header map**, then a result line. The standard logger serializes all writers on one mutex, which it holds while writing to stderr. `Lshortfile` adds a caller lookup to every line.

**Performance principle**
Don't hold a process-wide lock across I/O on the request path. Per-request diagnostic work should not serialize independent requests.

**Evidence**
`service.go:98` (access line with `r.Header`), `service.go:374` (redirect result line with the full URL), `urlshortener.go:30` (`Lshortfile`). No runtime evidence exists.

**Impact**
Position: critical path. Frequency: per request. Growth: O(1). Blast radius: service-wide, because every goroutine shares the logger lock. Amplification: 2x log writes per request.

**Conditions**
This matters at request rates where log writes become a noticeable share of redirect time, or whenever the stderr consumer (Docker log driver or journald) is slow. In that case every request queues on the logger mutex. Rate and sink are unknown.

**Counter-evidence**
The redirect otherwise does a single Redis `GET`, so logging is probably a modest constant cost when the sink is healthy. **Bounds impact.** I searched for buffered or async writers, sampling or log levels. None found.

**Why this might not matter**
With a fast sink and modest traffic, log writes are dwarfed by the Redis round trip, and this log is the service's only observability.

**Recommendation**
Measure first, with CPU and mutex/block profiles under a redirect load test. If logging shows up, emit one structured line per request (method, path, status, duration), drop the header map and `Lshortfile`, and use a bounded buffered writer.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| One structured access line with duration | **preferred** | Less work, and it adds the missing latency signal |
| Async/buffered writer | | Decouples request latency from sink latency, but can drop or delay lines |
| Disable access logging | | Loses the only runtime evidence the service has |

**Trade-offs**
Less forensic detail. A buffered writer can lose its last lines on a crash and needs a bounded buffer.

**Validation**
Compare a CPU profile and a mutex profile under a redirect-only load in staging, before and after (`not-safe-on-production`). Falsifier: if profiles show logging as a negligible share, drop this to Low. Guard: a `ServeHTTP` redirect benchmark with a discard logger vs the production logger.

---

### PERF-005: Token creation retries SETNX until a time budget runs out

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `service.go:506` (`serviceHandler.generateToken`) |
| **Tags** | scalability-risk |

**Problem**
Creation loops `SETNX` with a fresh random token until it succeeds or `URLSHORTENER_TIMEOUT` (default 500 ms, `tools.go:48`) runs out. There is no attempt cap and no check for client disconnect. Expected attempts grow as 1/(1 − f) as the keyspace fills, so each request sends more sequential writes to the shared Redis.

**Performance principle**
Retry loops against a shared resource need a count limit and should stop when the caller is gone.

**Evidence**
`service.go:506-521`. Derivation: keyspace 64^6 = 68,719,476,736 at the default length (`shortToken.go:33,49`, `tools.go:47`). The README expects that operating at 70–80% fill is possible.

**Impact**
Position: critical path. Frequency: per request. Growth: unknown, because it depends on fill fraction. Blast radius: endpoint. Amplification: Nx.

**Conditions**
This only matters when active tokens are a large fraction of 64^TokenLength. In practice that means a short configured `TokenLength`. Production `TokenLength` is unknown.

**Counter-evidence**
At the default length, f is negligible and the loop exits after one attempt. **Bounds impact.** The wall-clock budget also bounds the duration. **Bounds impact.**

**Recommendation**
Leave it as is at the default length. If short tokens are used, add an attempt cap and a check on `r.Context().Done()`, and alert on the existing attempt warnings.

**Trade-offs**
An attempt cap may return 408 earlier when the keyspace is nearly full.

**Validation**
Attempts per creation should stay at 1 at the default length. `go test -run '^$' -bench 'Benchmark00ST00Create' ./...` (`safe-on-production`, no Redis needed) gives the generator's per-attempt CPU cost. Falsifier: if attempt warnings never appear, the mechanism is dormant.

---

### PERF-006: No latency, Redis-pool or runtime instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `service.go:98` (`serviceHandler.ServeHTTP`) |
| **Tags** | quick-win |

**Problem**
There are no metrics, no request durations in logs, no pprof and no Redis `PoolStats`. None of the findings above can be confirmed, and no fix can be validated in production.

**Performance principle**
You can't tune what isn't measured. Every optimization needs a baseline on the critical path first.

**Evidence**
The access line has no duration or status (`service.go:98`). `go.mod` lists only go-redis, godotenv and testify. A search for prometheus/expvar/pprof/otel found nothing. Benchmarks exist without committed results.

**Impact**
Position: critical path. Frequency: per request. Growth: O(1). Blast radius: service-wide.

**Conditions**
Applies at any traffic level.

**Counter-evidence**
The home page shows the last computed attempt rate (`service.go:197`), a crude occupancy signal. **Bounds impact.**

**Recommendation**
Add duration and status to the access log, or a per-route latency histogram. Export go-redis `PoolStats` and Go runtime metrics. Serve `net/http/pprof` on a non-public listener. Sequence this before PERF-004.

**Trade-offs**
Adds a dependency and an extra listener. pprof must never be public.

**Validation**
Per-route p50/p99 and pool hits/misses/timeouts become visible (`safe-on-production`). Falsifier: if equivalent metrics already exist at a proxy, this reduces to Redis-client and runtime visibility only.

---

### Remaining findings

None. All six findings are written out in full above.

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-001 | High | Medium | P1 | `service.go:611` | No server timeouts or body limit |
| PERF-002 | Medium | Medium | P2 | `dataBaseRedis.go:55` | Clients control keyspace size; negative `exp` means no TTL |
| PERF-003 | Medium | High | P2 | `service.go:257` | Health check makes 4 loopback calls with no client timeout |
| PERF-004 | Medium | Medium | P2 | `service.go:98` | Synchronous verbose logging through a global lock |
| PERF-005 | Low | Medium | P3 | `service.go:506` | `SETNX` retry loop grows with keyspace fill |
| PERF-006 | Low | High | P3 | `service.go:98` | No instrumentation |

### Considered and not reported

| Observation | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| The Redis client uses default pool size and dial/read/write/pool timeouts, with none configured or exposed | go-redis pool shared by all routes | `dataBaseRedis.go:36-39`, `tools.go`, `go.mod` | The defaults aren't stated in the repo, and there's no worker count or Redis connection limit to compare them with. Kept as an unknown. | `PoolStats` show timeouts or misses, or Redis latency rises |
| GOMAXPROCS vs container CPU quota (Go 1.24, no automaxprocs) | Go scheduler | `docker-compose.yml`, `dockerfile`, `go.mod` | No CPU limit is declared, so there's nothing to mismatch | Deployed with a CPU limit below the host core count |
| `upx --best` compression adds startup time and memory that can't be shared | Startup | `build.sh:4`, `dockerfile` | Startup-only cost for a long-running container | Frequent restarts or autoscaling |

### Adjacent findings: outside performance scope

### SEC-001: Full request headers, long URLs and raw bodies are logged

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `service.go:98` |

**Problem** Every request's header map, which may include `Cookie` or `Authorization`, is logged, along with full long URLs (which may carry tokens in query strings) and raw bodies on bad requests.
**Evidence** `service.go:98` (`r.Header`), `service.go:374` (long URL), `service.go:417` (raw body).
**Impact** Credentials or personal data can end up in log storage, where retention and access are usually looser than for the data itself.
**Recommendation** Log an allow-list of fields only. Never log auth or cookie headers or raw bodies. Consider redacting query strings.
**Trade-offs** Less forensic detail.
**Validation** Grep the logs from a staging run for `Cookie`/`Authorization`.
**Would need** A privacy/security logging review against the data-handling policy.

### SEC-002: Token creation and expiry are unauthenticated

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `service.go:533` |

**Problem** Anyone who knows or guesses a token can expire (delete) someone else's link. Anyone can create tokens and trigger the write-heavy health check.
**Evidence** `TODO: check some authorization ???` at `service.go:396` and `service.go:533`. Expiring with `exp ≤ 0` removes the key (`dataBaseRedis.go:67-71`).
**Impact** Links can be deleted by third parties, and the write endpoints can be abused at no cost.
**Recommendation** Require an owner credential for expire (for example a per-token secret returned at creation). Authenticate or rate-limit creation and the full health check.
**Trade-offs** API change for existing clients.
**Validation** A test showing expire without the credential is rejected.
**Would need** A security review / threat model of the public API.

### COR-001: Negative `exp` creates a permanent link instead of being rejected

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `dataBaseRedis.go:51` |

**Problem** On creation, `exp < 0` is clamped to 0 and stored with no expiry. On expire, the same value means delete now. The README does not document permanent links.
**Evidence** `dataBaseRedis.go:51-55` (`SETNX` with a 0 duration) and `dataBaseRedis.go:67-71` (`EXPIRE` with 0).
**Impact** API behavior is inconsistent and links unexpectedly become permanent. This also feeds PERF-002.
**Recommendation** Return 400 for `exp < 0` on creation (or apply `DefaultExp`), and add a test.
**Trade-offs** Clients relying on the quirk will break.
**Validation** A unit test on `POST /api/v1/token` with `exp: -1`.
**Would need** A correctness-focused API input-validation test suite.

---

## 8. Prioritized action plan

### P1: High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-006 | P3 | Small | **Sequenced early because it is cheap and is a prerequisite.** Adding a duration to the access log and exporting pool stats gives every later change a baseline. Its priority stays P3. |
| 2 | PERF-001 | P1 | Small | Highest impact: removes the unbounded per-connection hold |

### P2: Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 3 | PERF-002 | P2 | Small | Run `INFO keyspace` first. Fix `exp` validation together with COR-001. |
| 4 | PERF-003 | P2 | Small | Bounded client plus context, then split the liveness endpoint |
| 5 | PERF-004 | P2 | Small–Medium | Only after PERF-006 makes its cost measurable |

### P3: Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 6 | PERF-005 | P3 | Small | Only if a short `TokenLength` is used in production |

**If only one thing is done:** add server timeouts and `http.MaxBytesReader` (PERF-001).

---

## 9. Validation plan

### PERF-001

- **Baseline:** In staging, open N connections that never send headers, and post large bodies. Record goroutine count, open sockets and RSS. `safe-on-production: no`
- **Change:** Server timeouts plus `MaxBytesReader`.
- **Measurement:** The same metrics, under the same scenario.
- **Expectation:** Stalled connections close after `ReadHeaderTimeout`, oversized bodies get 413, and goroutines return to baseline.
- **Falsifier:** Stalled connections were already closed in a bounded time in the real deployment.
- **Guard:** Tests asserting 413 for oversized bodies and closure of header-less connections.
- **Commands:**
  - `not-safe-on-production` `go test -race -run Test10Service ./...`: regression check of service paths (needs local Redis).

### PERF-002

- **Baseline:** `keys` vs `expires` and `used_memory` vs `maxmemory`. `safe-on-production: yes`
- **Change:** Reject or cap `exp`; POST for UI generation.
- **Measurement:** New keys without a TTL; growth of TTL-bearing keys.
- **Expectation:** Zero new keys without a TTL, and the key count levels off at about creation rate × maximum `exp`.
- **Falsifier:** `expires` ≈ `keys` and memory flat for weeks. Downgrade to Low.
- **Guard:** Unit test for `exp < 0`; alert on `used_memory` / `maxmemory`.
- **Commands:**
  - `safe-on-production` `redis-cli INFO keyspace`: compare total keys with keys that have an expiry.
  - `safe-on-production` `redis-cli INFO memory`: memory against the limit and the policy.

### PERF-003

- **Baseline:** In staging, health-check duration and goroutine count while Redis is paused. `safe-on-production: no`
- **Change:** Bounded `http.Client` with request context; separate liveness endpoint.
- **Measurement:** Probe duration and goroutine count.
- **Expectation:** Probe fails within the client timeout, and goroutines stay flat.
- **Falsifier:** Probes already return in bounded time while Redis is paused.
- **Guard:** Test running `healthCheck` against a stub that never responds.
- **Commands:**
  - `not-safe-on-production` `go test -race -run Test10Service03CheckHealthCheck ./...`: happy-path regression (needs local Redis).

### PERF-004

- **Baseline:** CPU and mutex profiles under a redirect-only load in staging (after PERF-006 adds pprof). `safe-on-production: no` (load test)
- **Change:** One structured access line; drop the header map and `Lshortfile`; bounded buffered writer.
- **Measurement:** CPU share in `log`/`fmt`; mutex wait on the logger.
- **Expectation:** Both fall if logging was material. Otherwise no change is needed.
- **Falsifier:** Logging is a negligible share in the profiles.
- **Guard:** A `ServeHTTP` redirect benchmark tracked over time.

### PERF-005

- **Baseline:** Attempts per creation (already computed in `generateToken`). `safe-on-production: yes`
- **Commands:**
  - `safe-on-production` `go test -run '^$' -bench 'Benchmark00ST00Create' ./...`: CPU cost of one token-generation attempt.
- **Falsifier:** Attempts stay at 1, so no change is needed.

### Instrumentation gaps to close first

This service is uninstrumented. Before optimizing anything, add request duration and status to the access log (or a latency histogram per route), export go-redis `PoolStats` and Go runtime metrics, and serve `net/http/pprof` on a non-public listener (PERF-006).

---

## 10. Machine-readable output

The machine-readable review is in `url-guided-2.json`, alongside this report. It conforms to `schemas/review.schema.json` and passes `scripts/validate_review.py` ("is a valid review (6 finding(s))"). Each finding's `stable_id` was computed with `scripts/compute_stable_id.py`. If the two files ever disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are graded by evidence. No finding is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated or assumed. The keyspace size (64^6), the expected-attempts formula and the health-check fan-out count are derivations, labelled as such where they are used.
- Every recommendation states its trade-offs and how to validate it. Timeout and limit values are deliberately not specified, because they need measured latency.
