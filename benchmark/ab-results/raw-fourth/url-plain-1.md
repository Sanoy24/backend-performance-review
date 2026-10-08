# URLshortener: Backend Performance Review

**Scope:** `urlshortener/` (Go 1.24, `net/http`, `go-redis/redis/v7`). Files reviewed: `urlshortener.go`, `service.go`, `dataBaseRedis.go`, `shortToken.go`, `tools.go`, `dockerfile`, `build.sh`, `.env_sample`, `README.md`.

## How the service works

This is a single-binary Go HTTP service. All state is in Redis, one string key per token: `SETNX token longURL EX days`.

- The **hot path** is `GET /<token>`. The handler validates the token in memory, runs one Redis `GET`, and returns a `302`.
- The **write path** is `POST /api/v1/token` and `GET /ui/generate?s=`. It generates a random token and loops `SETNX` until one succeeds or the time limit passes.
- The **health check** is `GET /api/v1/healthcheck`. It runs a full end-to-end self-test.

The core design is sound: an O(1) key-value lookup, cheap token checks before any Redis call, and `SETNX` to avoid collisions. The problems are around this core: the health check, the lack of server limits, logging, Redis client and lifecycle settings, and how much data is retained.

## Summary (ordered by priority)

| # | Finding | Where | Severity |
|---|---------|-------|----------|
| 1 | Health-check endpoint multiplies load: 3 internal HTTP calls and Redis writes per probe, no timeouts, connection leaks, public and unauthenticated | `service.go:206-337` | **High** |
| 2 | No HTTP server timeouts and unbounded request bodies (slowloris, memory blow-up, unbounded Redis values) | `service.go:143-149, 611-614` | **High** |
| 3 | Synchronous, verbose logging on every request, including the full header map, under a global mutex | `service.go:98, 153, 207, 347, 374, 398, 448` | **Medium-High** |
| 4 | Redis calls have no per-request deadline; the token-creation time limit is not enforced against a slow Redis | `service.go:455-525`, `dataBaseRedis.go:33-47` | **Medium** |
| 5 | Redis client uses only defaults (pool, timeouts, cluster read routing) | `dataBaseRedis.go:36-39` | **Medium** |
| 6 | Negative `exp` creates keys that never expire; `exp` has no upper bound, so Redis memory grows without limit | `dataBaseRedis.go:50-56`, `service.go:409-425` | **Medium** |
| 7 | `BGSAVE` on every process shutdown (fork-induced latency spikes on Redis during deploys) | `dataBaseRedis.go:91-97` | **Medium** |
| 8 | Write endpoints have no rate limiting; a GET (`/ui/generate`) writes to Redis | `service.go:124-126, 152-183` | **Medium** |
| 9 | No cache for hot redirects; every click is a Redis round trip | `service.go:344-378` | **Low-Medium** |
| 10 | Small per-request inefficiencies (stats goroutine per write, eager `Sprintf`, mux string concat, favicon headers, 300 ms startup sleep, UPX) | various | **Low** |

---

## 1. Health-check endpoint multiplies load and can hang (High)

**Where:** `service.go:206-224` (`healthcheck`), `service.go:227-337` (`healthCheck`), routed publicly at `service.go:103-105`.

**What happens:** each `GET /api/v1/healthcheck` runs these steps synchronously inside the request goroutine:

1. `http.Post("http://"+ListenHostPort+"/api/v1/token", ...)`. This is a loopback HTTP request into the same server, which causes a Redis `SETNX` (a write that is replicated and persisted to AOF/RDB).
2. `http.Get("http://" + repl.URL)`. `repl.URL` is built from **`ShortDomain`**, not `ListenHostPort`. In production that is the public domain, so the request goes out through DNS, the load balancer and TLS-terminating proxy, possibly reaching *another* instance. It then **follows the redirect** to `ShortDomain/favicon.ico`, which is a second external request. One Redis `GET`.
3. `http.Post(.../api/v1/expire)`, a loopback request that runs Redis `EXPIRE key 0` (a delete, also a write).

So one probe costs **4-5 HTTP requests (2 of them through the external edge), 1 Redis read and 2 Redis writes, and about 10 log lines**. Problems that make this worse:

- **No timeouts.** `http.Post`/`http.Get` use `http.DefaultClient`, which has `Timeout: 0`. If the LB, DNS or Redis stalls, the probe goroutine blocks forever. Orchestrators keep probing (every 5-10 s per instance, often from several sources), so goroutines and sockets pile up exactly when the system is already degraded. This is a positive-feedback failure mode.
- **Connection reuse is broken.** `resp2.Body` (the favicon bytes) and `resp3.Body` are closed **without being drained**, so the `DefaultTransport` cannot return those keep-alive connections to the idle pool. Each probe opens new TCP connections, and on Linux they sit in `TIME_WAIT`. The external hop also pays a fresh TLS/TCP handshake if a proxy is in front.
- **Public and unauthenticated.** Anyone can run `GET /api/v1/healthcheck` in a loop and get about 5x request amplification plus Redis write load from a single cheap GET. It is a ready-made DoS lever.
- **Leaks on partial failure.** If step 2 fails, the step-1 token stays in Redis for 1 day. Under a flapping edge, every failed probe leaves garbage behind.
- **False negatives.** Health depends on the public edge (DNS/LB) and on whatever instance the LB picks. A healthy instance can be marked unhealthy and restarted because of an unrelated edge or peer problem, which causes restart storms.

**Recommendations:**

1. Split the probes:
   - `GET /healthz` (liveness): returns `200` with no I/O.
   - `GET /readyz` (readiness): runs `PING` against Redis with a short context deadline (e.g. 200-500 ms). Optionally it can do a `SET`/`GET` of a single fixed key such as `__health__:<instance>` with a short TTL, which avoids creating new keys.
2. Keep the full end-to-end self-test only for the **startup** check in `startService`, or put it behind an auth or internal-only listener. Call the handler methods in-process (e.g. via `httptest.NewRecorder()`) instead of going through the network.
3. If any outbound HTTP stays, use a dedicated `http.Client{Timeout: 2*time.Second, CheckRedirect: func(...) error { return http.ErrUseLastResponse }}`. Check the `302` and `Location` header instead of following the redirect. Use `ListenHostPort` rather than `ShortDomain`. Drain bodies before closing with `io.Copy(io.Discard, resp.Body)`.
4. Add a singleflight or a short result cache (e.g. 1-2 s) so concurrent probes share one check.

---

## 2. No HTTP server timeouts and unbounded request bodies (High)

**Where:** `service.go:611-614` (server construction), `service.go:143-149` (`readBody`), `service.go:161` (`r.FormValue`).

```go
handler.server = &http.Server{
    Addr:    config.ListenHostPort,
    Handler: handler,
}
```

- **No `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout` or `IdleTimeout`.** The service binds `0.0.0.0:80` directly (`.env_sample`, `network_mode: host`), possibly with no proxy in front. Slow or idle clients can hold goroutines and file descriptors indefinitely (slowloris). With enough connections the process hits `ulimit -n` and stops accepting new ones, so redirects fail.
- **`io.ReadAll(r.Body)` has no limit.** A single `POST /api/v1/token` with a multi-GB body is buffered fully in memory, then `json.Unmarshal`'d, which makes a second copy. A few concurrent requests can OOM the container. On error paths (`service.go:417, 428, 554`) the **entire body is also written to the log** with `%s`, which multiplies the cost.
- **URL length is unbounded.** After parsing, `params.URL` can be megabytes and is stored as-is in Redis. That inflates Redis memory and network transfer for every later redirect of that token. The UI's `maxLength=1024` is client-side only.

**Recommendations:**

```go
handler.server = &http.Server{
    Addr:              config.ListenHostPort,
    Handler:           handler,
    ReadHeaderTimeout: 2 * time.Second,
    ReadTimeout:       5 * time.Second,
    WriteTimeout:      5 * time.Second,
    IdleTimeout:       60 * time.Second,
    MaxHeaderBytes:    16 << 10,
}
```

- In `readBody`, wrap the body with `http.MaxBytesReader(w, r.Body, 8<<10)` and return `413` when the limit is exceeded. Better still, decode directly with `json.NewDecoder(http.MaxBytesReader(...)).Decode(&params)`.
- Reject `params.URL` longer than about 2-4 KB, and the same for `s` in `/ui/generate`. Validate it with `url.Parse` and allow only the http/https schemes.
- Never log raw request bodies. Log a truncated, escaped prefix if needed.

---

## 3. Synchronous, verbose logging on the hot path (Medium-High)

**Where:** `service.go:98` and each handler (`347/374` redirect, `398/423/448` new token, `153/181` UI, `207/215` health).

```go
log.Println("access from:", r.RemoteAddr, r.Method, r.RequestURI, r.Header)
```

- **Every request writes at least 2 log lines.** One is the access line, and the redirect path adds `"...: redirected to <longURL>"`. The std `log.Logger` holds a **global mutex** and does a **synchronous `write(2)` to stderr** per line. Under Docker's json-file driver or a slow log collector, stderr back-pressure blocks request goroutines, and all handlers serialize on that one mutex. For a redirect service, whose real work is a sub-millisecond Redis GET, logging is likely the dominant CPU and latency cost.
- **Dumping `r.Header`** (a `map[string][]string`) through `fmt` uses reflection and allocates heavily. It produces lines of several hundred bytes to a few KB, which inflates log volume and storage cost. It also logs `Cookie`/`Authorization` headers, which is a security concern.
- `rMess` strings are built with `fmt.Sprintf` **before** it is known whether they will be used (e.g. `redirect` builds `rMess` even on the success path, `new` concatenates `rMess += fmt.Sprintf(...)`).

**Recommendations:**

- Switch to `log/slog` with a JSON handler and **one structured access-log line per request** (method, path, status, duration, remote addr, token). Emit it from a small middleware that wraps `ResponseWriter` to capture the status. Drop the per-handler duplicate lines, or move them to Debug level.
- Do not log the header map. Log only selected headers (e.g. `User-Agent`, `Referer`), truncated.
- Consider sampling success-path redirect logs at high QPS, or buffering the writer (e.g. a `bufio.Writer` flushed periodically, or an async handler). Make sure errors are still flushed promptly.
- Expose Prometheus counters and histograms (request latency, Redis latency, SETNX attempts) instead of using logs as metrics.

---

## 4. Redis calls have no deadline, and the token time limit is not enforced (Medium)

**Where:** `service.go:455-525` (`generateToken`), all `tokenDBR` methods in `dataBaseRedis.go`.

- Requests are not tied to `r.Context()`, so Redis work continues after the client disconnects.
- In `generateToken`, the `select { case <-stop: ... default: SETNX }` checks the deadline **only between attempts**. If a single `SETNX` blocks (e.g. Redis failover or a stall), it waits for go-redis's default `ReadTimeout` (3 s), plus retries (`MaxRetries` defaults to 0 in v7 for single node, but cluster clients retry and follow redirects). The configured `URLSHORTENER_TIMEOUT` (500 ms) is therefore not a real upper bound on latency.
- When Redis is slow, every in-flight request holds a pool connection for up to 3 s. The pool (default `10 * GOMAXPROCS`) is quickly exhausted, and later requests then wait `PoolTimeout` (default `ReadTimeout + 1s` = 4 s) **before** even trying. The redirect hot path stalls behind the write path because they share one pool.
- The retry loop has no backoff, so retries hit Redis back-to-back.

**Recommendations:**

- Thread a `context.Context` through `TokenDB` methods. Use `t.db.WithContext(ctx)` in go-redis v7, or better, upgrade to `github.com/redis/go-redis/v9`, where every call takes a `ctx`.
- In `generateToken`, create `ctx, cancel := context.WithTimeout(r.Context(), timeout)` and pass it to each `SETNX`, so the time limit is a hard bound.
- In `redirect`, use a short per-call deadline (e.g. 100-200 ms) and return `503` quickly rather than queueing.
- Collisions are expected to be rare. At token length 6 there are 2^36 tokens and at length 5 there are 2^30. Cap attempts at a count (e.g. 10) *as well as* the time limit, and add a small jittered backoff after a few attempts.

---

## 5. Redis client uses only defaults (Medium)

**Where:** `dataBaseRedis.go:36-39`.

```go
db := redis.NewUniversalClient(&redis.UniversalOptions{Addrs: addrs, Password: password})
```

- **Cluster mode is selected implicitly.** With `len(Addrs) > 1` (as in `.env_sample`: `<RedisHost>:6379,<BackupRedisHost>:6379`), `NewUniversalClient` returns a **ClusterClient**. If the second host is really a standalone backup or replica, not a cluster node, the client either fails or does extra `CLUSTER SLOTS` discovery work. This should be explicit.
- **No read scaling.** In cluster mode every redirect `GET` goes to the master. Redirects are about 100% reads, so `ReadOnly: true` with `RouteByLatency: true` or `RouteRandomly: true` lets replicas serve the hot path and spreads load. This is acceptable because a shortly stale read only affects newly created or expired tokens.
- **Timeouts and pool are not tuned.** Set `DialTimeout`, `ReadTimeout` and `WriteTimeout` to values that fit an in-datacenter KV store (e.g. 200-500 ms). Set `PoolSize` from expected concurrency, `MinIdleConns` so traffic bursts do not pay dial cost, and a `PoolTimeout` shorter than the HTTP `WriteTimeout`.
- The client is go-redis **v7**, which is unmaintained. v9 has better pool behaviour, context support throughout, and RESP3.

**Recommendation:** expose these as config, set sensible defaults, decide explicitly between single, sentinel (`MasterName`) and cluster mode, and enable replica reads for `Get`.

---

## 6. Keys that never expire and unbounded TTLs (Medium, capacity)

**Where:** `dataBaseRedis.go:50-56`, `service.go:409-425`.

```go
if expiration < 0 { expiration = 0 }
return t.db.SetNX(sToken, longURL, time.Hour*24*time.Duration(expiration)).Result()
```

- In go-redis, a TTL of `0` means **no expiry**. Any client sending `{"url": "...", "exp": -1}` creates a **permanent** key. Large `exp` values (e.g. `999999`) have the same practical effect, and with very large ints the `time.Duration` multiplication overflows.
- The README notes that the token space is finite. With length 5 (as in `.env_sample`) there are about 1.07 B tokens. As the fill ratio *p* rises, the expected number of `SETNX` attempts grows as 1/(1-*p*), so write latency and Redis load rise until creation times out (`408`). Permanent keys move the system toward that cliff, and toward Redis `maxmemory` evictions or OOM. Combined with finding 2 (no URL size limit), memory per key is also unbounded.

**Recommendations:**

- Validate `exp` on input: reject `< 0` for creation (or treat it as the default), and clamp to a configured `MaxExp` (e.g. 365 days).
- Set Redis `maxmemory` and choose an eviction policy deliberately. `volatile-ttl` is reasonable here, and `noeviction` is acceptable if you would rather fail writes than lose links.
- Track key count (`DBSIZE`, or the `INFO keyspace` stats) against the token-space size, and alert at about 50% fill.

---

## 7. `BGSAVE` on every shutdown (Medium)

**Where:** `dataBaseRedis.go:91-97`, called from the `defer` in `urlshortener.go:55-61`.

Every time any instance stops (rolling deploy, autoscale-in, crash loop from finding 1's false negatives), it sends `BGSAVE`. Redis `fork()`s to write an RDB snapshot. On a large dataset the fork itself can pause Redis for tens to hundreds of milliseconds, copy-on-write can briefly double memory, and the disk I/O competes with AOF. With N instances deploying, that is N back-to-back fork requests. Most fail with "Background save already in progress", but the first one still costs. A client application should not control persistence.

**Recommendation:** remove `BgSave` from `Close()`. Configure persistence on the Redis side (AOF `everysec` and/or RDB `save` rules) and leave snapshots to the database operator.

---

## 8. Write endpoints have no rate limiting, and a GET writes to Redis (Medium)

**Where:** `service.go:106-114, 124-126, 152-183`.

- `POST /api/v1/token` and `GET /ui/generate?s=` have no authentication (the TODO at `service.go:396` acknowledges this) and no rate limit. Each call is a Redis write plus a key held for at least 1 day. A single client can fill the token space or Redis memory (findings 2 and 6), which slows writes for everyone.
- `/ui/generate` creates state on **GET**. Link previewers, crawlers and browser prefetch can trigger writes, and it is cache-unsafe.

**Recommendations:** add per-IP or per-API-key token-bucket limiting on the write endpoints, e.g. `golang.org/x/time/rate` keyed by client IP, or at the edge proxy. Change the UI form to `method="POST"`. Return `429` with `Retry-After` when the limit is hit.

---

## 9. No caching of hot redirects (Low-Medium)

**Where:** `service.go:344-378`.

Every redirect is a network round trip to Redis. Short-link traffic is usually heavily skewed, with a small number of links taking most clicks. Redis can handle this, but the round trip dominates p50 latency, and it sends all load from a viral link to one Redis shard.

**Recommendations (choose according to correctness needs):**

- **In-process LRU with a short TTL** (e.g. `hashicorp/golang-lru/v2/expirable`, 10-100k entries, 10-30 s TTL). The trade-off is that an `expire` call may take up to the TTL to take effect on other instances. That is usually acceptable, and can be fixed with a Redis pub/sub invalidation on `expire`. Also cache negative results (not found) briefly to absorb token scanning.
- **HTTP caching:** today the service sends `302` with no `Cache-Control`. Adding `Cache-Control: private, max-age=60` (or more) lets browsers and CDNs absorb repeat clicks, at the same cost to expire immediacy. Do not use `301`, because browsers cache it indefinitely and that breaks `expire`.

Measure first. This matters only if redirect QPS is high or Redis latency is noticeable.

---

## 10. Small inefficiencies (Low)

- **Stats goroutine per token creation** (`service.go:478-495`): every successful or failed `generateToken` spawns a goroutine just to compute a ratio and do an atomic store. Do it inline, since it is a handful of integer ops. The metric is also last-writer-wins across concurrent requests, so a histogram (finding 3) would be more useful.
- **Eager string building:** `fmt.Sprintf` for `rMess` in every handler, and `r.Method + r.URL.Path` in the mux (`service.go:99`), allocate on every request. Use `http.ServeMux` with Go 1.22 method patterns (`mux.HandleFunc("GET /{token}", ...)`), which is allocation-light and clearer.
- **Static pages are rendered per request:** `home` formats `homePage` on every hit. That is cheap, but the static part could be pre-rendered once at startup.
- **favicon** (`service.go:127-130`) is written with no `Content-Type` (so `net/http` sniffs it) and no `Cache-Control`. Set `Content-Type: image/png` and `Cache-Control: public, max-age=86400` so browsers stop re-requesting it on every short-link click.
- **Startup `time.Sleep(300ms)`** (`urlshortener.go:77`): replace it with `net.Listen` first and then `server.Serve(ln)`. The socket is then ready before the health check runs, startup is deterministic, and there is no fixed delay.
- **UPX-compressed binary** (`build.sh`): the binary must be decompressed into anonymous memory at every start. That adds startup latency and RSS, and the code pages cannot be shared across containers. For a ~10 MB Go binary in a `scratch` image, the size saving is rarely worth it.

---

## Suggested order of work

1. **Now:** split health checks (1), add server timeouts and body/URL limits (2), validate `exp` (6), remove `BgSave` (7). These are small, low-risk changes that close the main availability and DoS risks.
2. **Next:** structured, single-line access logging (3); context deadlines on Redis calls plus Redis client tuning or a v9 upgrade (4, 5); rate limiting and POST for the UI (8).
3. **Then, based on metrics:** redirect caching (9) and the minor cleanups (10).

Before and after these changes, run a simple load test, e.g. `wrk`/`vegeta` against `GET /<token>` and `POST /api/v1/token` with a realistic token mix. Track p50/p99 latency, Redis ops/s and CPU, so the gains from items 3, 5 and 9 can be measured rather than assumed.
