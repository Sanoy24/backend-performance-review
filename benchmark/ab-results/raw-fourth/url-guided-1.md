# Performance Review: URLshortener

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0

---

## 1. Decision summary

### Overall assessment

URLshortener is a small, thin Go service. Each redirect is one Redis `GET` and each token creation is usually one `SETNX`. No superlinear data-access pattern exists. The material risks sit at the HTTP edge. The server has no time bounds on client connections (PERF-001) and no size bound on request bodies or stored URLs (PERF-002), and both endpoints are unauthenticated. Two growth risks then feed each other on the shared Redis. Keys can be written with no TTL (PERF-004), and a fuller keyspace makes the blind `SETNX` retry loop (PERF-003) issue more round trips. The service has no instrumentation of any kind (PERF-007), so every finding is ranked from code structure alone. **Review confidence: Medium.** All seven routes and every source file were read, but no runtime evidence exists and no workload answers were available. Whether a reverse proxy fronts the service would move PERF-001 and PERF-002 by one priority band.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Set `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout` and `IdleTimeout` on the `http.Server` in `NewHandler` | Unbounded hold time for every client connection; a few lines of code |
| 2 | **PERF-002 (P1)** | Wrap bodies in `http.MaxBytesReader` and cap URL length before storing | Unauthenticated callers control per-request memory and Redis value size |
| 3 | **PERF-007 (P2)**, sequenced early | Add per-route duration/status metrics and internal-port `pprof` | Without it no other change can be validated; also gives PERF-005 its replacement access line |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is there a reverse proxy enforcing client timeouts and body limits? | PERF-001/PERF-002 move between P1 and P2 | Read the production proxy/LB configuration |
| Active token count, configured `TokenLength`, and whether clients send `exp <= 0` | PERF-003/PERF-004 move between P2 and P1 | `redis-cli INFO keyspace` (keys vs expires) |
| Peak redirect and creation rates | Size of PERF-005 and PERF-003 | Route-level request metrics (PERF-007) |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | `safe-on-production` | `grep -nE 'ReadHeaderTimeout\|ReadTimeout\|WriteTimeout\|IdleTimeout' *.go` | Confirms no server timeout is set (currently empty) |
| PERF-004 / PERF-003 | `safe-on-production` | `redis-cli INFO keyspace` | `keys - expires` > 0 means TTL-less keys exist; `keys` / 64^TokenLength gives fill fraction |
| PERF-003 | `safe-on-production` | `redis-cli INFO commandstats \| grep -E 'cmdstat_(setnx\|get\|expire)'` | SETNX calls per created token = retry amplification |

---

## 2. Scope and method

**Reviewed:** All non-test Go sources in full (`urlshortener.go`, `service.go`, `dataBaseRedis.go`, `shortToken.go`, `tools.go`). Also `go.mod`, `dockerfile`, `docker-compose.yml`, `build.sh`, `redisDockerRun.sh`, `.github/workflows/go.yml` and `README.md`. Test files were read for benchmarks and intent only.
**Not reviewed:** `.env_sample` was noted by presence only and not opened, per the no-secrets rule. go-redis v7.4.1 library internals, including its default timeouts and pool size, were not read because they are outside the repository. Production Redis configuration is not in the repository.

**Evidence available:** Uninstrumented. There are no metrics, tracing, `pprof`, load tests, SLOs or dashboards. Benchmark sources exist (`shortToken_test.go:107-128`, `dataBaseRedis_test.go:176-200`), but no results are committed. Every workload-dependent finding is capped at `Medium` confidence.

**Ranking method:** Structural signals only. No runtime data was available.

**Reference depth:** Go, Redis and Docker have deep references. The detector also reported gRPC from `protobuf` in `go.sum`. No `.go` file imports gRPC, so that signal was discarded as a transitive-dependency false positive.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every production source file and every deployment/build file read in full |
| **Critical paths** | 7 / 7 (redirect, token POST, UI generate, expire, healthcheck, home, favicon) |
| **Shared resources** | 5 / 5 (Redis instance, Redis client pool, HTTP server connections/goroutines, global logger/stderr, process memory) |
| **Technology support** | Go: Deep · Redis: Deep · Docker: Deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not finding confidence. The code is small enough to have been read in full. The confidence ceiling comes from the absence of any runtime evidence or workload answers.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Request rates, active token count, latency targets | No evidence exists | Route metrics; `redis-cli INFO keyspace` |
| Whether a reverse proxy bounds client time and body size | No evidence exists | The proxy/LB configuration |
| go-redis v7 defaults in effect (pool size, timeouts) | Out of scope (library source not read) | Read go-redis v7.4.1 `Options` defaults; the repo sets only `Addrs` and `Password` |
| Production Redis persistence, `maxmemory`, eviction policy | No evidence exists | `redis-cli INFO persistence`; `CONFIG GET maxmemory*` |

---

## 3. Architecture overview

One Go process (`net/http`, goroutine per connection) uses a hand-written mux in `serviceHandler.ServeHTTP` (`service.go:97-140`). It talks to Redis through `go-redis/v7` `UniversalClient` (`dataBaseRedis.go:36-39`), which is a single node, Sentinel or Cluster depending on the address list. Redis is the **system of record**: token → long URL, with TTL in days. The deployment is one container from a `scratch` image with `network_mode: host`, no replica count and no resource limits (`docker-compose.yml`). The binary is UPX-packed (`build.sh:4`).

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Service | Go | go 1.24.0 (`go.mod:3`) | Deep | HTTP API, UI and redirects |
| Datastore | Redis via go-redis | client v7.4.1; server version unknown (test script uses 5.0.7, CI uses latest) | Deep | System of record for tokens |
| Container | Docker (`scratch`) | n/a | Deep | Single-container deployment |

**Shared resources:** the single Redis instance, whose commands execute one at a time; the go-redis connection pool; the HTTP server's connections and goroutines; the global `log` writer (stderr); and process memory.

---

## 4. Workload model

**Known**

- TokenLength default 6, token-creation timeout default 500 ms, default expiry 1 day; all overridable by environment variables. Source: `tools.go:22-27, 47-52`.
- One container with no resource limits or replicas, and no probe or autoscaling configuration. Source: `docker-compose.yml:1-9`.
- Read/write shape: 1 pure-read route (redirect: 1 `GET`); 3 Redis-writing routes (`POST /api/v1/token` and `GET /ui/generate` run the `SETNX` loop; `POST /api/v1/expire` runs `EXPIRE`); the healthcheck writes, reads and expires. Source: `service.go:97-140`.
- No authentication or rate limiting on any route. Source: `service.go:396, 533`.
- Request bodies are unbounded (`service.go:144`). Negative `exp` yields keys with no TTL (`dataBaseRedis.go:51-55`).

**Assumed**

- No proxy-level body-size or client-timeout limit is guaranteed. Affects: PERF-001, PERF-002.
- Redirects dominate traffic. Affects: PERF-005.
- The keyspace fill fraction is currently low. Affects: PERF-003.

**Unknown**

- Peak request rate per route; active token count; proxy limits; healthcheck poll interval; log sink; Redis topology, persistence and memory policy.

**Derived**

- Token keyspace = 64^6 = 68,719,476,736 at the default length. From: `tools.go:47` and the 64-symbol alphabet in `shortToken.go:61-69`.
- Expected `SETNX` attempts per created token = 1 / (1 − p), where p is the occupied fraction of the keyspace. From: uniform random tokens (`shortToken.go:38-50`) and retry-until-success (`service.go:506-522`).
- Redis commands per healthcheck in mode 0 = 1 `SETNX` + 1 `GET` + 1 `EXPIRE`, plus 3 loopback HTTP requests. From: `service.go:257, 297, 324`.

**Measured**

- None. No benchmark output, trace, profile or metrics export was supplied or committed.

### Questions that would change the ranking

No one was available to answer these. They are recorded as asked and unanswered, and the review proceeded under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is the service behind a reverse proxy enforcing client read/idle timeouts and a max body size? | severity / confidence / recommendation | Yes, with sane limits: PERF-001 and PERF-002 drop to P2 as defence in depth. No proxy: both stay P1 at High confidence |
| 2 | high | Roughly how many active tokens exist, at what `TokenLength`, and do clients send `exp <= 0` or very large `exp`? | severity / confidence | High fill or non-positive `exp` in use would raise PERF-003 and PERF-004 to P1 |
| 3 | high | Peak redirect and token-creation rates (order of magnitude)? | severity / recommendation | Sizes PERF-005 and PERF-003 |
| 4 | medium | Is `/api/v1/healthcheck` polled, how often, with what timeout and mode? | severity / confidence | Frequent polling keeps PERF-006 at P2; manual-only drops it to Low |
| 5 | medium | Where does stderr go in production, and is log volume a cost or disk concern? | severity / recommendation | A blocking sink makes PERF-005 a latency-coupling risk |
| 6 | medium | Is production Redis dedicated, and what are its persistence, `maxmemory` and eviction policy? | severity / recommendation | Widens or narrows the blast radius of PERF-003, PERF-004 and PERF-008 |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `POST /api/v1/token` | yes | 1..N `SETNX` (retry loop) | time-bounded (500 ms default); body unbounded | no | PERF-002, PERF-003, PERF-004 |
| 2 | `GET /<token>` redirect | yes | 1 `GET` | yes, O(1) | no | Hot path; fixed logging cost (PERF-005) |
| 3 | `GET /api/v1/healthcheck` | yes (prober) | 1 `SETNX` + 1 `GET` + 1 `EXPIRE`, plus 3 loopback HTTP | no timeout | no | PERF-006 |
| 4 | `GET /ui/generate` | yes | same loop as #1 | time-bounded | no | Shares PERF-003/PERF-004 (default `exp` only) |
| 5 | `POST /api/v1/expire` | yes | 1 `EXPIRE` | body unbounded | no | PERF-002; SEC-001 |
| 6 | `GET /` | yes | none | yes | no | Static HTML |
| 7 | `GET /favicon.ico` | yes | none | yes | no | Embedded bytes |

**Amplification points:**
- The token-creation loop issues 1/(1 − p) `SETNX` calls per success (derived), on a single-threaded shared Redis.
- Each healthcheck spawns 3 loopback requests.
- Each request writes 2 synchronous log lines.

**Paths deliberately not analyzed in depth:** Startup (a fixed 300 ms sleep and one self-test, `urlshortener.go:77-78`) runs once per process and does not affect requests.

---

## 6. Layer analysis

### 6.1 Application

The concurrency pattern is Go's goroutine-per-connection with no application-level bound. The only extra goroutine is a short statistics goroutine per token creation (`service.go:481`). It is bounded and terminates, so it was discarded. `time.After` per creation is not a leak on Go ≥ 1.23 (`go.mod` targets 1.24.0). The real application costs are per-request header logging (PERF-005) and the retry loop (PERF-003).

### 6.2 API

The server has no timeouts (PERF-001). Request bodies and stored URL length are unbounded (PERF-002). Write endpoints are unauthenticated (SEC-001). The healthcheck does write-path work with no timeout (PERF-006). Redirects are `302` with no caching headers, which is deliberate because tokens can be expired (see Considered and not reported).

### 6.3 Data access and datastore

All access is by key: single-key `SETNX`, `GET`, `EXPIRE` and `DEL`. There are no scans and no multi-key work. Key design is fine. The risks are keys written without TTL (PERF-004), unbounded value size (PERF-002), the retry loop's round trips (PERF-003), and `BGSAVE` on shutdown (PERF-008). Client configuration sets only `Addrs`/`Password`, and no request context reaches Redis calls. The pool and timeout implications depend on library defaults and instance count, so they are recorded as a question, not a finding.

### 6.4 Infrastructure

There is one container with no CPU or memory limits, so the GOMAXPROCS-vs-quota risk does not apply to this deployment. The binary is UPX-packed (PERF-009).

### 6.5 Observability

The service is uninstrumented (PERF-007). The only performance signal is `s.attempts`, an extrapolated attempts-per-timeout value shown on the HTML home page.

---

## 7. Findings

### PERF-001 — HTTP server has no read, write or idle timeouts

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `service.go:611` (`NewHandler`) |
| **Tags** | quick-win |

**Problem**
The `http.Server` is built with only `Addr` and `Handler`. Slow or idle clients can therefore hold connections, goroutines, file descriptors and partially read requests for an unbounded time.

**Performance principle**
Every resource a caller can hold must have a time bound. Otherwise a slow caller turns directly into resource exhaustion for every other caller.

**Evidence**
- `service.go:611-614`: `&http.Server{Addr: config.ListenHostPort, Handler: handler}`. There is no `ReadTimeout`, `ReadHeaderTimeout`, `WriteTimeout`, `IdleTimeout` or `MaxHeaderBytes`.
- `service.go:585`: plain `ListenAndServe`. No other server configuration exists anywhere.
- No runtime evidence exists.

**Impact**
- Position: critical path, because every request goes through it.
- Frequency: per connection.
- Growth: O(n) in the number of slow connections.
- Blast radius: service, because file descriptors and memory are process-wide.

**Conditions**
This matters if clients can reach the Go server directly, or through a proxy that does not enforce its own client timeouts. The assumption is that no such guarantee exists, because no proxy is configured in the repository.

**Counter-evidence**
- The default listen address `localhost:8080` (`tools.go:49`) and the public demo domain in the README suggest a fronting proxy may exist. **Lowers confidence** to Medium.
- Go has no fixed worker pool, so exhaustion needs many held connections, not a few. **Bounds impact**, which keeps severity at High rather than Critical.

**Why this might not matter**
A well-configured reverse proxy that terminates client connections would make this defence in depth only.

**Recommendation**
Set all four timeouts in `NewHandler`, configurable like `URLSHORTENER_TIMEOUT`. Derive the values from measured latency once PERF-007 exists. Until then, treat any value as a provisional bound.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Server-side timeouts on `http.Server` | **preferred** | Bounds the resource where it is owned, works with or without a proxy, and takes a few lines |
| Rely on proxy timeouts | | Valid only if the proxy is guaranteed; the compose file shows none |
| Raise fd limits or add replicas | | Adds capacity without bounding hold time |

**Trade-offs**
Short timeouts cut off legitimate slow clients. `WriteTimeout` must exceed the healthcheck handler's worst case, because that handler makes three loopback calls (PERF-006).

**Validation**
See §9.

---

### PERF-002 — Request bodies and stored URLs have no size limit

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | memory |
| **Location** | `service.go:144` (`readBody`) |
| **Tags** | quick-win |

**Problem**
`POST /api/v1/token` and `POST /api/v1/expire` read the whole body with `io.ReadAll` and no cap. The URL inside is checked only for emptiness. It is then stored in Redis as the value, written to logs, and returned in the `Location` header.

**Performance principle**
The server, not the caller, must bound the work and memory each request uses.

**Evidence**
- `service.go:144`: `io.ReadAll(r.Body)`. There is no `MaxBytesReader` or `LimitReader`.
- `service.go:108, 117`: `readBody` is called before any validation.
- `service.go:416`: `params.URL` is only checked for `""`. `generateToken` then stores it with `SETNX` (`service.go:517`).
- `service.go:417, 428`: the whole body is logged on failure.
- `service.go:55`: the UI's `maxLength=1024` is only a browser hint.
- The endpoints are unauthenticated (`service.go:396`).

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in body size.
- Blast radius: system-wide. Process memory is affected per request, and Redis memory, the shared system of record, is affected per stored URL.

**Conditions**
This matters when any caller sends large bodies. Nothing in the repository caps them. The assumption is that no proxy body limit is guaranteed.

**Counter-evidence**
- A proxy body limit may exist but is not in the repository. **Lowers confidence.**
- All `.go` files were searched for `MaxBytesReader`, `LimitReader`, `ContentLength` checks and URL length checks. None found. **No effect.**

**Why this might not matter**
Real URLs are small. The risk comes from abusive or buggy callers, and a proxy limit would remove most of it.

**Recommendation**
Use `http.MaxBytesReader` in `readBody`, and reject URLs above a documented maximum before storing them.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| `MaxBytesReader` plus a URL length check | **preferred** | Bounds both process memory and Redis value size at entry, and returns 413 |
| Stream-decode with `json.Decoder` | | Avoids one copy but still accepts an unbounded string |
| Rely on proxy limits | | Not guaranteed by anything in the repository |

**Trade-offs**
Some legitimately long URLs will be rejected. The limit must be documented in the README.

**Validation**
See §9.

---

### PERF-003 — Token creation retries SETNX in a tight loop against shared Redis

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `service.go:506` (`serviceHandler.generateToken`) |
| **Tags** | scalability-risk |

**Problem**
On collision, `generateToken` retries `SETNX` with no backoff until it succeeds or the timeout (default 500 ms) fires. Redis round trips per created token therefore rise as the keyspace fills. The timeout is checked only between iterations, so a single slow `SETNX` already in flight is not bounded by it.

**Performance principle**
Retries against a shared single-threaded resource need a rate bound, not only a time bound. Otherwise one caller's contention becomes load on every caller.

**Evidence**
- `service.go:506-522`: the `select` with a `default:` branch calls `tokenDB.Set` each iteration, with no sleep.
- `tools.go:48`: the timeout default is 500.
- `dataBaseRedis.go:55`: `SetNX` takes no context or deadline.
- Derived: expected attempts = 1/(1 − p), with keyspace 64^6 = 68,719,476,736 at the default length.
- `README.md:53` describes filling the token space to 70-80% as an expected operating regime.
- No runtime evidence exists.

**Impact**
- Position: critical path (token POST and UI generate).
- Frequency: per request.
- Growth: unknown. Round trips scale as 1/(1 − p) in the fill fraction, which is not a standard O-class.
- Blast radius: system-wide, because Redis executes commands serially.
- Amplification: N×.

**Conditions**
This matters when active tokens are a material fraction of the keyspace: a short configured `TokenLength`, long expiries, or TTL-less keys accumulating (PERF-004). At low fill, which is assumed because there is no evidence, the cost is about one round trip.

**Counter-evidence**
- The loop is time-bounded (`service.go:500`). **Bounds impact.**
- The keyspace is large at length 6. **Bounds impact.**
- No token counts are available. **Lowers confidence.**

**Why this might not matter**
At default settings the loop almost never iterates more than once, and the design is deliberate and documented.

**Recommendation**
Keep the time bound. Add an attempt cap, and pass a context with the remaining deadline into `Set`. Export the existing `attempts` value as a metric. If fill rises, increase `TokenLength` rather than retrying harder.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Attempt cap, context deadline, attempts metric | **preferred** | Bounds both the count and duration of Redis calls per request, and makes fill pressure visible |
| Increase `TokenLength` | | Correct once high fill is shown; premature before that |
| Sequential tokens via `INCR` | | No collisions, but tokens become enumerable |

**Trade-offs**
The service returns 408 sooner under high fill. Adding a context changes the `TokenDB` interface and its mocks.

**Validation**
See §9.

---

### PERF-004 — Negative `exp` writes keys with no TTL; no maximum expiry

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `dataBaseRedis.go:51` (`tokenDBR.Set`) |
| **Tags** | scalability-risk |

**Problem**
A negative `exp` is coerced to 0 and passed as a zero duration to `SETNX`, which stores the key with no TTL. There is no upper bound on `exp`. Through an unauthenticated endpoint, Redis keys can therefore accumulate without bound.

**Performance principle**
Every collection written on a user action needs a size bound.

**Evidence**
- `dataBaseRedis.go:51-55`: `if expiration < 0 { expiration = 0 }`, then `SetNX(..., 24h * 0)`.
- `service.go:473`: only `exp == 0` is replaced with the default, so negative values pass through.
- `service.go:411`: `exp` is an unbounded `int`.
- `dataBaseRedis_test.go:185`: the repository's own benchmark writes permanent keys.

**Impact**
- Position: critical path (write).
- Frequency: per request.
- Growth: O(n) over time.
- Blast radius: system-wide, because Redis memory is shared and fill feeds PERF-003.

**Conditions**
This matters only if callers send `exp <= -1` or very large values. The default of 1 day bounds normal use. Nothing rejects these values, and there is no evidence either way about whether callers send them.

**Counter-evidence**
- Default usage always sets a TTL (`tools.go:50`). **Bounds impact.**
- Searched for `exp` validation: none found. **No effect.**

**Why this might not matter**
If every real client omits `exp` or sends a positive value, no key ever lacks a TTL.

**Recommendation**
Reject or clamp negative `exp` on creation, and enforce a configured maximum.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Validate and clamp at the handler | **preferred** | Removes the growth at its source |
| Redis `maxmemory` with eviction | | Redis is the system of record, so eviction would silently delete live links |
| Periodic cleanup scan | | Adds scans on a single-threaded store to repair something preventable |

**Trade-offs**
Intentional permanent links, if any exist, would need an explicit documented option.

**Validation**
See §9.

---

### PERF-005 — Per-request synchronous logging of the full header map on the hot path

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `service.go:98` (`serviceHandler.ServeHTTP`) |
| **Tags** | needs-measurement, quick-win |

**Problem**
Every request, including redirects, formats the full `r.Header` map and writes it synchronously through the global logger. The handler then writes a second line. All goroutines serialize on the logger's single writer.

**Performance principle**
Fixed per-request work should be proportional to its value. Synchronous writes through one shared sink couple every request's latency to that sink.

**Evidence**
- `service.go:98`: `log.Println("access from:", ..., r.Header)`.
- `service.go:347, 374`: the redirect builds `rMess` with `Sprintf` before any check, then logs it with the long URL.
- `urlshortener.go:30`: the flag `Lshortfile` makes each log line resolve the caller's file and line (`runtime.Caller`).
- `service.go:365`: the redirect otherwise does one Redis `GET`.
- No profile exists.

**Impact**
- Position: critical path.
- Frequency: per request, 2 lines each (2×).
- Growth: O(1).
- Blast radius: service, because of the shared log writer.

**Conditions**
This matters at redirect rates high enough for formatting plus two writes to be a measurable CPU share, or when the log sink is slow. The assumption is that redirects dominate traffic. Actual rates are unknown.

**Counter-evidence**
- The cost is a bounded constant. **Bounds impact.**
- There is no profile or sink configuration. **Lowers confidence.**

**Why this might not matter**
At moderate traffic this costs microseconds next to a Redis round trip, and it is the service's only operational record.

**Recommendation**
Write one structured access line per request after the handler returns: method, route kind, status, duration and token. Drop the header dump and `Lshortfile` on this path.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| One structured access line with duration | **preferred** | Less fixed work and better observability (PERF-007) |
| Async/buffered writer | | Keeps the formatting cost and adds a buffer that can drop entries or grow |
| Sampling | | Loses the per-request audit trail |

**Trade-offs**
Some forensic detail is lost, and anyone parsing the current log format is affected.

**Validation**
See §9.

---

### PERF-006 — Healthcheck runs a full write-path self-test over HTTP with no timeout

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `service.go:227` (`serviceHandler.healthCheck`) |
| **Tags** | quick-win |

**Problem**
Each probe makes three nested HTTP calls (create, follow redirect, expire) with the default `http.Client`, which has no timeout. Each probe writes to Redis. A hang in any hop blocks the probe and its nested requests indefinitely.

**Performance principle**
Health checks should be cheap and bounded. A probe doing real write work with no timeout becomes load and a leak exactly when the system is degraded.

**Evidence**
- `service.go:257` `http.Post` to `/api/v1/token`.
- `service.go:297` `http.Get` of `ShortDomain/<token>`, which follows the redirect to `ShortDomain/favicon.ico`. If `ShortDomain` is a public domain, this call leaves the host.
- `service.go:324` `http.Post` to `/api/v1/expire`.
- Derived: 3 Redis commands per probe in mode 0.
- `service.go:319`: in `disableExpire` mode each probe leaves a key alive for 1 day.

**Impact**
- Position: async (prober).
- Frequency: per probe.
- Growth: O(1) per probe. Blocked work grows with stall duration × poll rate.
- Blast radius: service.
- Amplification: 3×.

**Conditions**
This matters if an orchestrator or monitor polls the endpoint. The interval is not in the repository.

**Counter-evidence**
- No probe configuration exists. **Lowers confidence.**
- go-redis has default client timeouts (not read here), which would release Redis-originated stalls. **Bounds impact.**

**Why this might not matter**
Infrequent or manual probing makes this negligible, and the end-to-end check gives a stronger health signal than a ping.

**Recommendation**
Use a dedicated client with a total timeout below the prober's own timeout. Do not follow the redirect over the public domain; assert the 302 `Location` instead. Optionally split cheap liveness from the deep self-test.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Dedicated client with timeout, no redirect following | **preferred** | Bounded, no external hop, keeps the end-to-end check |
| Split liveness and deep check | | Better long-term, but changes the documented contract |
| Call `tokenDB` directly | | Cheaper, but no longer exercises the HTTP layer |

**Trade-offs**
A momentarily slow instance may fail its probe. The public path is no longer checked end to end.

**Validation**
See §9.

---

### PERF-007 — No latency, throughput or Redis instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | observability |
| **Location** | `service.go:97` (`serviceHandler.ServeHTTP`) |
| **Tags** | needs-measurement |

**Problem**
There are no request-duration, status or rate metrics, no Redis latency measurement and no `pprof`. None of this review's findings can be confirmed, sized or validated.

**Performance principle**
A system that cannot measure its critical path cannot tell a real bottleneck from a guessed one.

**Evidence**
- `service.go:97` records no duration or status.
- `service.go:485`: `s.attempts` is only rendered on HTML.
- `go.mod:5-9` has no metrics, tracing or profiling dependency, and no `.go` file imports `net/http/pprof` or `expvar`.
- `dataBaseRedis_test.go:176` has benchmarks without committed results.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1).
- Blast radius: service.

**Conditions**
This applies at any traffic level, and becomes urgent before any other fix, since validation needs a baseline.

**Counter-evidence**
Searched all files for metrics, tracing, `pprof` and `expvar`. None found. **No effect.**

**Why this might not matter**
For a small or demo deployment, access logs plus an external uptime check may suffice.

**Recommendation**
Add per-route duration and status metrics, labelled by route kind and not by raw token path. Add Redis call latency. Add `net/http/pprof` on a non-public port. Export `attempts` as a metric.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Route metrics plus internal `pprof` | **preferred** | Directly enables validating every other finding |
| Duration in the access log only | | Cheapest, but percentiles need log processing |
| Distributed tracing | | Disproportionate for one service and one datastore |

**Trade-offs**
Small per-request cost. A `pprof` endpoint must be kept off the public listener. Labels must stay bounded in cardinality.

**Validation**
See §9.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-008 | Low | Medium | P3 | `dataBaseRedis.go:92` (`tokenDBR.Close`) | Every app shutdown issues `BGSAVE`, forcing a whole-dataset fork on the shared Redis. Rare (per shutdown), but cost grows with dataset size. Remove it and leave persistence to Redis's own config. Validate with `redis-cli INFO persistence` / `latest_fork_usec` around a restart (safe-on-production). |
| PERF-009 | Low | High | P3 | `build.sh:4` | Binary is `upx --best` packed, so each start decompresses into private memory. The image is already `FROM scratch`. Drop UPX unless image size is a measured constraint. Validate by comparing start-to-listening time and RSS for packed and unpacked binaries. |

### Considered and not reported

| Observation | Path / resource | Evidence checked | Why not reported | Revisit when |
|:--|:--|:--|:--|:--|
| Redis client leaves pool size and timeouts at go-redis v7 defaults; no request context reaches Redis | Redis client pool | `dataBaseRedis.go:33-47`, `go.mod:6`, `docker-compose.yml` | Defaults exist but were not read (out of scope); whether pool size fits Redis `maxclients` depends on an instance count no file states | Instance count and Redis `maxclients` known, or pool wait measured |
| Redirects are `302` with no `Cache-Control`, so every click reaches Redis | `GET /<token>` | `service.go:344-378`, `README.md:60-83` | Intentional: tokens can be expired, so caching would serve stale targets | Redirect rate is high and staleness after expiry becomes acceptable |
| `GOMAXPROCS` vs container CPU quota | Go scheduler | `docker-compose.yml`, `dockerfile`, `go.mod:3` | No CPU limit declared, so there is no quota to mismatch | Deployed with a CPU limit |

### Adjacent findings — outside performance scope

### SEC-001 — Unauthenticated, unrate-limited token creation and expiry

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `service.go:533` (also `service.go:396`) |

**Problem**
Anyone can create unlimited links, including non-expiring ones (PERF-004). Anyone can also expire or delete any short URL whose token they know: `exp <= 0` maps to `EXPIRE 0`, which deletes the key.

**Evidence**
- `service.go:396` and `service.go:533` both carry `// TODO: check some authorization ???`.
- `dataBaseRedis.go:66-71` maps negative `exp` to `EXPIRE key 0`.
- Tokens are visible in every short URL.

**Impact**
Medium risk: any visitor who has seen a short URL can revoke it, and creation can be abused for storage exhaustion.

**Recommendation**
Require authentication or an ownership secret for `/api/v1/expire`. Add authentication or rate limiting for creation.

**Trade-offs**
Adds credential management for API clients and the UI.

**Validation**
Attempt to expire a token created by another client and confirm it is refused.

**Would need**
A security review of the API threat model, and an abuse and rate-limit design review.

### SEC-002 — Full request headers and long URLs logged in plain text

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Low |
| **Location** | `service.go:98` |

**Problem**
Every request's full header map, including any `Cookie` or `Authorization` header, is logged. Redirect targets, which may carry tokens in query strings, are logged too.

**Evidence**
`service.go:98` (`r.Header`) and `service.go:374` (`longURL`).

**Impact**
Low risk: the service sets no cookies itself, but browsers and proxies may send sensitive headers, and logs are often retained and shipped widely.

**Recommendation**
Log an allow-list of headers, and consider redacting query strings.

**Trade-offs**
Less forensic detail.

**Validation**
Inspect a log line for a request carrying a `Cookie` header.

**Would need**
A privacy and logging review covering retention.

---

## 8. Prioritized action plan

There are no P0 findings. P1: PERF-001, PERF-002. P2: PERF-003 through PERF-007. P3: PERF-008, PERF-009.

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-007 | P2 | Small–medium | **Sequenced first despite P2.** It provides the baseline needed to validate everything else, and its access line also resolves PERF-005 |
| 2 | PERF-001 | P1 | Small | Highest impact; a few lines |
| 3 | PERF-002 | P1 | Small | Highest impact; a few lines |
| 4 | PERF-004 | P2 | Small | Removes unbounded growth at the source and reduces pressure on PERF-003 |
| 5 | PERF-006 | P2 | Small | Bounds the probe |
| 6 | PERF-005 | P2 | Small | Mostly done by step 1 |
| 7 | PERF-003 | P2 | Medium (interface change) | Act once the attempts and fill metrics show pressure |
| 8 | PERF-008 | P3 | Trivial | Cheap cleanup |
| 9 | PERF-009 | P3 | Trivial | Cheap cleanup |

**If only one thing is done:** Add server timeouts and a body-size limit at the HTTP edge (PERF-001 and PERF-002). Together they are about ten lines of code, and they bound every resource an unauthenticated caller can currently hold indefinitely.

---

## 9. Validation plan

### PERF-001
- **Baseline:** Open N slow or idle connections against a staging instance and count open connections and goroutines. — not-safe-on-production
- **Change:** Add server timeouts.
- **Measurement:** Open connections over time while the slow clients persist.
- **Expectation:** Before the change, connections persist indefinitely. After it, they close once the configured timeout elapses.
- **Falsifier:** A production proxy already closes slow clients, and connection counts at the Go process never grow.
- **Guard:** Unit test asserting non-zero timeouts on the server returned by `NewHandler`.
- **Commands:**
  - `safe-on-production` `grep -nE 'ReadHeaderTimeout|ReadTimeout|WriteTimeout|IdleTimeout' *.go` — confirm presence of server timeouts (currently none)

### PERF-002
- **Baseline:** RSS and status code for an oversized POST on a local instance. — not-safe-on-production
- **Change:** `MaxBytesReader` plus a URL length check.
- **Measurement:** Status and RSS for the same request.
- **Expectation:** 413 at the limit, with flat RSS.
- **Falsifier:** No body larger than a proxy limit ever reaches the process in production.
- **Guard:** Handler test posting an oversized body and asserting 413 and that nothing was stored.
- **Commands:**
  - `not-safe-on-production` `head -c 104857600 /dev/zero | curl -s -o /dev/null -w '%{http_code}\n' -X POST --data-binary @- http://localhost:8080/api/v1/token` — send a 100 MiB body to a local instance

### PERF-003
- **Baseline:** `SETNX` calls divided by tokens created, and keyspace fill. — safe-on-production
- **Change:** Attempt cap, context deadline, attempts metric.
- **Measurement:** The same ratio, and the per-request maximum.
- **Expectation:** Ratio near 1 at low fill; never above the cap after the change.
- **Falsifier:** Ratio stays near 1 and token count is a negligible fraction of 64^TokenLength.
- **Guard:** Alert on the attempts metric.
- **Commands:**
  - `safe-on-production` `redis-cli INFO commandstats | grep -E 'cmdstat_(setnx|get|expire)'` — retry amplification
  - `safe-on-production` `redis-cli INFO keyspace` — active key count for the fill fraction

### PERF-004
- **Baseline:** `keys - expires` from `INFO keyspace`. — safe-on-production
- **Change:** Validate `exp` and enforce a maximum.
- **Expectation:** The gap stops growing.
- **Falsifier:** `keys == expires` today and over time, meaning the risk is latent only.
- **Guard:** Handler test for negative and over-maximum `exp`; alert on the gap.
- **Commands:**
  - `safe-on-production` `redis-cli INFO keyspace` — keys without TTL = keys − expires

### PERF-005
- **Baseline:** CPU profile share of logging on redirects under a fixed-rate load test in staging. Requires PERF-007's `pprof`. — not-safe-on-production
- **Change:** One structured access line.
- **Expectation:** Logging share and per-redirect allocations drop.
- **Falsifier:** Logging is a negligible share in the profile.
- **Guard:** Handler benchmark with `-benchmem`.

### PERF-006
- **Baseline:** Time a probe; count goroutines during a simulated Redis stall while polling at the production interval. — the stall test is not-safe-on-production
- **Change:** Dedicated client with a timeout and no redirect following.
- **Expectation:** Goroutines stay flat and probes fail fast.
- **Falsifier:** Goroutines never accumulate even without the change.
- **Guard:** Test with a blocking mock `TokenDB`, asserting the probe returns within its timeout.
- **Commands:**
  - `safe-on-production` `curl -s -o /dev/null -w '%{http_code} %{time_total}\n' http://localhost:8080/api/v1/healthcheck` — time one probe

### PERF-007
- **Baseline:** None exists; that is the finding.
- **Expectation:** p50/p95/p99 and error rate per route, plus Redis call latency, become visible.
- **Guard:** Test asserting the metrics endpoint exposes per-route duration.

### Instrumentation gaps to close first

Do PERF-007 before any optimization. Every expectation above that mentions latency is unmeasured today and cannot be checked without it.

---

## 10. Machine-readable output

The machine-readable review is in `url-guided-1.json`, which sits next to this report. It conforms to `schemas/review.schema.json`, and `scripts/validate_review.py` reports it as a valid review with 9 findings. `stable_id` values were computed with `scripts/compute_stable_id.py`. If the JSON and this Markdown ever disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are graded by evidence; no finding is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated. The keyspace size, the 1/(1 − p) attempt formula and the per-probe command count are derivations, and their inputs are shown where they are used.
- Workload questions could not be put to anyone. They are recorded in §4 as asked and unanswered. The review proceeded under stated assumptions, and workload-dependent findings are capped at `Medium` confidence.
