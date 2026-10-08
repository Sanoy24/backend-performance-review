# Performance Review: URLshortener (Go + Redis)

## Scope and context

This review covers the whole repository: `urlshortener.go`, `service.go`, `dataBaseRedis.go`, `shortToken.go`, `tools.go`, plus the build and deploy files. The service is a single Go binary. It uses a hand-written mux (`serviceHandler.ServeHTTP`) over `net/http` and keeps its data in Redis through `go-redis/v7` `UniversalClient`. There are four request paths:

| Path | Work per request | Expected volume |
|---|---|---|
| `GET /<token>` (redirect) | validate token, 1 Redis `GET`, 302 | **Hot path**, by far the most traffic |
| `POST /api/v1/token` | JSON parse, N x Redis `SETNX` in a retry loop | Moderate |
| `POST /api/v1/expire` | JSON parse, 1 Redis `EXPIRE` | Low |
| `GET /api/v1/healthcheck` | full self-test: 3 HTTP calls and 3+ Redis ops | Every load balancer or orchestrator probe |

The core data path is efficient: one Redis round trip per redirect, and the token is checked for length and alphabet before Redis is touched. The serious problems are not about CPU efficiency. They are **unbounded resource usage, tail latency that ignores the configured timeouts, and load amplification**. These are the problems that take a service like this down under real traffic or abuse.

## Summary of findings

| # | Finding | Severity | Effort |
|---|---|---|---|
| 1 | HTTP server has no timeouts and request bodies are read without a size limit | **High** | Small |
| 2 | Health-check endpoint runs an expensive, amplifying self-test on every probe | **High** | Small to medium |
| 3 | Token-creation loop ignores its own timeout during Redis calls, does not back off, and multiplies load near keyspace saturation | **Medium-High** | Small |
| 4 | Redis client is untuned: default timeouts and pool far exceed the latency budget, and no read scaling or caching on the redirect path | **Medium** | Small (tuning) / Medium (cache) |
| 5 | Synchronous, verbose logging on every request, including a full header dump | **Medium** | Small |
| 6 | `BGSAVE` is forced on Redis every time an instance shuts down | **Low-Medium** | Trivial |
| 7 | `GET /ui/generate` writes to Redis, URLs are not deduplicated, and stored URL size is unbounded | **Low-Medium** | Small |
| 8 | Minor items: a goroutine per token creation, favicon has no cache headers, fixed 300 ms startup sleep, UPX-compressed binary | **Low** | Trivial |

---

## 1. HTTP server has no timeouts and request bodies are read without a size limit — HIGH

**Where**
- `service.go`, `NewHandler`:
  ```go
  handler.server = &http.Server{
      Addr:    config.ListenHostPort,
      Handler: handler,
  }
  ```
- `service.go`, `readBody`: `io.ReadAll(r.Body)`. Used by `POST /api/v1/token` and `POST /api/v1/expire`.

**Problem**
- `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout` and `IdleTimeout` are all left at zero, which means no limit. Each connection costs a goroutine (about 4-8 KB of stack and growing), a file descriptor and buffers. A slow client, or a deliberate Slowloris-style attack that trickles headers, can hold connections open forever. The sample config listens on `0.0.0.0:80` and `docker-compose.yml` uses `network_mode: host`, so the service is meant to face the internet directly. Exhausting file descriptors (the `ulimit -n` limit) or memory is a realistic outage scenario, and it also blocks the legitimate redirect traffic.
- `io.ReadAll` with no limit buffers the whole request body in memory. One `POST /api/v1/token` with a body of several hundred MB allocates that much heap. A few concurrent requests like that can get the process OOM-killed. `io.ReadAll` also grows its buffer by repeated doubling, so peak memory is a multiple of the body size and there is a lot of GC churn.
- The body is then also logged in full on parse errors (`log.Printf("%s: bad request parameters:%s", rMess, body)`), so a huge body is formatted and written to the log a second time.

**Fix**
```go
handler.server = &http.Server{
    Addr:              config.ListenHostPort,
    Handler:           handler,
    ReadHeaderTimeout: 2 * time.Second,
    ReadTimeout:       5 * time.Second,
    WriteTimeout:      10 * time.Second, // > token Timeout + Redis timeouts
    IdleTimeout:       60 * time.Second,
    MaxHeaderBytes:    16 << 10,
}
```
and
```go
const maxBody = 8 << 10 // a URL + JSON envelope; 8 KB is generous
func readBody(w http.ResponseWriter, r *http.Request) ([]byte, error) {
    r.Body = http.MaxBytesReader(w, r.Body, maxBody)
    ...
}
```
Return `413` when the limit is exceeded. Also truncate the body in log lines (for example, log only the first 256 bytes). Consider making the limits configurable alongside the existing env vars.

---

## 2. Health-check endpoint runs an expensive, amplifying self-test on every probe — HIGH

**Where**: `service.go`, `healthcheck` handler and `healthCheck()`.

**Problem**
Each `GET /api/v1/healthcheck` runs the full end-to-end self-test:
1. `http.Post` to `http://<ListenHostPort>/api/v1/token`. This is a loopback HTTP request through the full handler: JSON parsing, Redis `SETNX`, logging.
2. `http.Get("http://" + repl.URL)`, where `repl.URL` is `ShortDomain + "/" + token`. **This goes to the public short domain**, through DNS, the edge or load balancer, and possibly a *different* instance, not to loopback. The client then follows the 302 to `http://ShortDomain/favicon.ico`, which is another request through the public path. So one health check makes 2 external HTTP requests.
3. `http.Post` to `/api/v1/expire`, which issues Redis `EXPIRE`.

So one probe costs **4 HTTP requests (2 of them through the public edge), at least 3 Redis commands including a write, and about 5 access-log lines plus handler logs**. Specific consequences:

- **Amplification and DoS surface:** the endpoint is unauthenticated and public. An attacker gets about a 4x request multiplier and forces Redis writes. Kubernetes or load balancer probes every 1-5 s per instance add a constant background write load. With N instances behind the same domain, the probes also fan out across instances.
- **No client timeout:** these calls use `http.DefaultClient`, which has no timeout. If the server is saturated, which is exactly when the probe matters, the self-call queues behind the same overload and can hang forever. Probe goroutines then pile up, and each holds a server goroutine and a connection. The probe tells you nothing useful under load and makes the load worse.
- **Connections are not reused:** `resp2` (the favicon body, after the redirect) and `resp3` are closed without being drained. Go only returns a connection to the keep-alive pool if the body was read to EOF, so every probe opens fresh TCP connections, including to the public endpoint.
- **External dependency in liveness:** if a health check that goes through the public domain is used as a liveness probe, a DNS or LB problem can make the orchestrator restart every healthy instance at the same time.
- If the process dies between step 1 and step 3, the self-test token stays in Redis for a day. The leak is small but it adds up.

**Fix**
- Split the probes:
  - `GET /api/v1/healthz` (liveness): returns 200 with no I/O.
  - `GET /api/v1/readyz` (readiness): one Redis `PING` with a short context timeout, for example 200 ms, and cache the result for about 1 s so that probe bursts cost at most one `PING` per second.
- Keep the full end-to-end self-test only for startup (`startService`), or behind an authenticated or internal-only route. If it must stay, give it a dedicated `http.Client{Timeout: 2*time.Second}`. Do not follow redirects (`CheckRedirect` returning `http.ErrUseLastResponse`, then check the `Location` header). Target loopback (`ListenHostPort`), not `ShortDomain`. Drain bodies (`io.Copy(io.Discard, resp.Body)`). Use `singleflight` so that concurrent probes share one run.

---

## 3. Token-creation loop ignores its own timeout during Redis calls, does not back off, and multiplies load near keyspace saturation — MEDIUM-HIGH

**Where**: `service.go`, `generateToken`. `dataBaseRedis.go`, `Set` (`SETNX`).

```go
stop := time.After(time.Millisecond * time.Duration(s.config.Timeout))
for ok := false; !ok; {
    select {
    case <-stop:
        return "", fmt.Errorf("token creation error: ...")
    default:
        sToken = s.shortToken.Get()
        attempt++
        ok, err = s.tokenDB.Set(sToken, url, exp)   // blocking Redis round trip
        ...
```

**Problems**
1. **The timeout is checked only between attempts.** `tokenDB.Set` has no context or deadline. It inherits go-redis v7 defaults: `ReadTimeout` 3 s, `WriteTimeout` 3 s, `PoolTimeout` = ReadTimeout + 1 s = 4 s, `DialTimeout` 5 s. If Redis is slow or the pool is exhausted, one "500 ms" create request can block for **4-8 s or more** per attempt. The configured `URLSHORTENER_TIMEOUT` (500 ms by default, 777 ms in the sample) is not a real latency bound. Under a Redis hiccup, handler goroutines pile up holding pool slots, and redirects then wait on `PoolTimeout` for a connection. A slowdown in the create path becomes an outage of the redirect path.
2. **Tight retry loop with no backoff.** Each collision immediately issues another `SETNX`. As the keyspace fills, the collision probability p = active_tokens / 64^len grows. The expected number of attempts per create is 1/(1-p), and at p≈0.9 a create takes about 10 round trips. Up to the timeout, a single request can issue as many `SETNX` calls as fit in 500 ms (hundreds at sub-millisecond RTT). The README itself says operators should expect to "fill the space of tokens up to 70-80%". With `TOKENLENGTH=5` (sample config) the space is 64^5 ≈ 1.07 B, which is large, but the default TTL-based churn plus the unthrottled UI path (finding 7) can push it up. When saturation happens, Redis load grows non-linearly at exactly the moment the system is struggling.
3. **A 408 invites client retries.** On timeout the handler returns `408 Request Timeout`, which the README describes as "the request can be repeated". This compounds point 2: clients retry into a saturated keyspace and multiply the `SETNX` storm again.
4. A small allocation cost per call: `time.After` allocates a timer and channel, and the deferred closure spawns a goroutine (see finding 8).

**Fix**
- Carry a deadline into Redis. Upgrade to go-redis v8/v9 (context-aware API) or use `WithContext` in v7, and derive the context from `r.Context()` with `context.WithTimeout(ctx, Timeout)`. Then `select` on `ctx.Done()` and pass the same `ctx` to `SetNX`. This makes the timeout a real bound and cancels work when the client disconnects.
- Cap attempts as well as time, for example at most 10 attempts. With a reasonably sized keyspace, more than 3-4 collisions in a row is an operational signal (grow the token length), not something to brute-force. Return `503` with `Retry-After` instead of `408`.
- Optionally pipeline candidates: generate k tokens and send k `SETNX` in one pipeline, or use a small Lua script that tries candidates in order. This cuts round trips near saturation.
- Export real metrics (a collision counter and an attempts-per-create histogram) instead of the single "last measured" `attempts` value. Alert on the collision rate so the token length is raised before saturation.

---

## 4. Redis client is untuned: default timeouts and pool far exceed the latency budget, and no read scaling or caching on the redirect path — MEDIUM

**Where**: `dataBaseRedis.go`, `NewTokenDB`:
```go
db := redis.NewUniversalClient(&redis.UniversalOptions{
    Addrs:    addrs,
    Password: password,
})
```

**Problems**
- Only addresses and password are set. All timeouts are the defaults described above (3 s read/write, 4 s pool wait, 5 s dial), against a service whose create budget is 500 ms and whose redirects should take milliseconds. Tail latency during any Redis disturbance is set by these defaults, not by the service's intent.
- `PoolSize` defaults to 10 x GOMAXPROCS. Because the retry loop (finding 3) and health checks (finding 2) can hold connections for seconds, the pool can drain, and every redirect then waits up to `PoolTimeout` (4 s). Nothing is configured for `MinIdleConns`, so the first burst after idle pays dial latency.
- **All redirect reads go to the Redis primary.** Redirects dominate traffic and are pure reads of rarely-changing keys, yet every one costs a network round trip to a single node. `ReadOnly` / `RouteByLatency` / `RouteRandomly` are not set, so in cluster mode replicas are never used. There is no in-process cache, so a viral link (one token receiving most of the traffic) becomes a single hot key on one Redis shard.
- Configuration risk with performance impact: `UniversalClient` builds a **ClusterClient whenever more than one address is given** and no `MasterName` is set. `.env_sample` lists `<RedisHost>:6379,<BackupRedisHost>:6379` as a primary and backup pair. That is not a cluster, so it will not behave as a failover pair. Either it errors, or it adds cluster-topology discovery overhead (and `MaxRedirects` retries) to every command. Sentinel needs `MasterName`.

**Fix**
- Set explicit options sized to the latency budget, for example `DialTimeout: 500ms`, `ReadTimeout: 200ms`, `WriteTimeout: 200ms`, `PoolTimeout: 300ms`, `MinIdleConns: a few`, `MaxRetries: 1`. Expose `PoolSize` and the timeouts as env vars.
- For cluster or replica setups, set `ReadOnly: true` plus `RouteByLatency: true` (or `RouteRandomly`) so `GET` can be served by replicas. Note that replica reads can briefly miss a just-created token, so fall back to the primary on a miss.
- Add a small in-process cache for redirects: a bounded LRU such as `hashicorp/golang-lru/v2/expirable`, sized by entry count, with a short TTL of 10-60 s. Also cache negative results (not-found) for a shorter time, such as 5 s, so scans of random tokens do not each hit Redis. The trade-off is that a token expired via `/api/v1/expire` may keep redirecting for up to the TTL on other instances. If that is unacceptable, invalidate locally on expire and use Redis pub/sub (or Redis 6 client-side caching with `CLIENT TRACKING`) for cross-instance invalidation.
- Document and validate the meaning of multiple addresses. Add a `URLSHORTENER_REDISMASTERNAME` option for Sentinel.

---

## 5. Synchronous, verbose logging on every request, including a full header dump — MEDIUM

**Where**
- `service.go`, `ServeHTTP` line 1: `log.Println("access from:", r.RemoteAddr, r.Method, r.RequestURI, r.Header)`
- Every handler builds an `rMess` with `fmt.Sprintf` and logs again on completion. For example, `redirect` emits 2 log lines per request and creates make 3+.
- `urlshortener.go`: `log.SetFlags(... | log.Lmicroseconds | log.Lshortfile)`

**Problem**
- On the hot redirect path, each request formats the whole `http.Header` map (reflection-based `%v` of `map[string][]string`, which allocates heavily; browsers send around 1 KB of headers including cookies). It does this twice with `fmt`, then serializes the result through the `log` package's global mutex and makes a synchronous `write(2)` to stderr. Under a container runtime, stderr is a pipe to the logging driver. When the driver or disk is slow, the writes block, and **every request goroutine stalls on the log mutex**. That puts an external system (the log pipeline) on the critical path of every redirect.
- `Lshortfile` makes the logger call `runtime.Caller` for every line, a stack walk that is noticeable at high RPS. It does this outside the mutex in recent Go versions, but it is still per-line work.
- Log volume is roughly 2-3 lines and 1-2 KB per redirect. At 5k RPS that is 5-10 MB/s of logs, mostly redundant.
- (Side effect worth knowing: logging all headers writes cookies and `Authorization` headers to the logs.)

**Fix**
- Emit **one** structured access-log line per request, at the end, with method, path, status, latency and remote address, using `log/slog` with a JSON handler. Drop the header dump entirely, or log only selected headers.
- Remove the per-handler duplicate lines on success. Keep the error and warning lines.
- Drop `Lshortfile` for access logs.
- If logs must stay this verbose, wrap stderr in a buffered, asynchronous writer that drops lines under backpressure, so logging never blocks request handling. Consider sampling success-path redirect logs.

---

## 6. `BGSAVE` is forced on Redis every time an instance shuts down — LOW-MEDIUM

**Where**: `dataBaseRedis.go`, `Close()`:
```go
_, err := t.db.BgSave().Result()
```

**Problem**
Every service instance shutdown (each pod in a rolling deploy, each autoscaler scale-in, each restart) makes Redis `fork()` and write a full RDB snapshot. On a Redis with a large dataset, the fork itself can stall the Redis main thread for tens to hundreds of milliseconds (page-table copy), and copy-on-write increases memory use during the save. A rolling deploy of N instances triggers N back-to-back BGSAVEs, which hurts redirect latency on every other instance. Persistence is Redis's responsibility (its `save` / `appendonly` config), not a stateless client's. Managed Redis offerings often reject `BGSAVE`. The call is also wrong for a `ClusterClient`, which sends it to a single node.

**Fix**: remove the `BgSave` call and let `Close()` just close the client. Configure persistence on the Redis server.

---

## 7. `GET /ui/generate` writes to Redis, URLs are not deduplicated, and stored URL size is unbounded — LOW-MEDIUM

**Where**: `service.go`, `generate` (`r.FormValue("s")` then `generateToken`), `new`.

**Problem**
- A `GET` with `?s=...` creates a new Redis key that lives for `DefaultExp` days. Crawlers, link-preview bots, browser prefetch and reloads all issue GETs, so each one writes a key and uses keyspace. That speeds up the saturation that drives the collision storm in finding 3. The response also has no cache headers, so intermediaries will not absorb repeat requests.
- Shortening the same long URL repeatedly always creates a new token. Memory and keyspace grow linearly with request count instead of with the number of distinct URLs.
- The URL length is not validated server-side (the `maxLength=1024` on the HTML form is client-only). Together with finding 1, a caller can store multi-MB values per key, which inflates Redis memory, replication traffic and RDB size.

**Fix**
- Make token creation in the UI a `POST` (the form already exists, so just change `method`), or at least send `Cache-Control: no-store` and `X-Robots-Tag: noindex`.
- Enforce a maximum URL length server-side (for example 2048 bytes) in both `new` and `generate`.
- Optionally deduplicate: keep a reverse index `url-hash -> token` (with `SET ... NX` on both keys in a Lua script or `MULTI`) and return the existing token when one exists. Whether this fits depends on product semantics, since each create has its own expiry.
- Add per-IP rate limiting on the create endpoints.

---

## 8. Minor items — LOW

- **A goroutine per token creation** (`generateToken` defer): spawns a goroutine just to compute a statistic and store it atomically. The cost is small (about 2-4 KB of stack and scheduler work) but it adds nothing. Compute inline. It is a few integer operations plus an atomic store, and the `log.Printf` warnings only fire in abnormal cases.
- **`time.After` per create**: fine on Go 1.23+ (go.mod says 1.24, so unfired timers are collected). It is moot anyway once finding 3's `context.WithTimeout` replaces it.
- **Favicon** (`GET /favicon.ico`): served with no `Content-Type`, so `net/http` sniffs the bytes on every response, and no `Cache-Control`. Browsers request it alongside every visit to the UI and home pages. Set `Content-Type: image/png` and `Cache-Control: public, max-age=86400`.
- **Home and health pages** are re-rendered with `fmt.Appendf` per request. This is negligible, but the static parts could be pre-rendered once at startup.
- **Startup**: `time.Sleep(300 * time.Millisecond)` before the initial health check is a fixed delay that may also be too short on a loaded node. Instead, listen first with `net.Listen`, then `server.Serve(ln)`, and run the check once the listener exists. That removes both the race and the delay.
- **`upx --best` on the binary** (`build.sh`): compressed executables must be decompressed into anonymous memory at every start. This adds startup latency, loses page-sharing between processes, and some environments flag it. For a container image built `FROM scratch`, the size savings rarely matter. Consider dropping UPX.

---

## Recommended order of work

1. **Now (small, high impact):** server timeouts and `MaxBytesReader` (#1). Replace the public health check with cheap liveness and readiness probes (#2). Explicit Redis timeouts and pool settings (#4, tuning part). Remove `BgSave` (#6).
2. **Next:** context-bounded token creation with an attempt cap and 503 (#3). One structured access-log line per request with no header dump (#5). Server-side URL length limit and a POST-only UI creation path (#7).
3. **When redirect traffic grows:** in-process LRU cache with negative caching and replica reads (#4, scaling part). Collision-rate metrics and alerting so the token length is raised before the keyspace saturates (#3).

## Suggested verification

- Load-test `GET /<token>` with a tool such as `vegeta` or `k6` before and after #5 and the cache from #4. Compare p50/p99 latency and allocations per request (`go test -bench` with `-benchmem` on `ServeHTTP` using `httptest`, plus `pprof` in a live run).
- Fault injection: add 1-2 s of latency to Redis (`toxiproxy` or `tc netem`) and confirm that, after #3 and #4, create requests fail within the configured timeout and redirects keep their own latency budget.
- Slowloris and large-body tests against the server after #1: the goroutine count (`/debug/pprof/goroutine`) and RSS should stay bounded.
- Fill a test Redis to 80-90% of `64^TokenLength` with a small token length (for example 3, which gives about 262 k keys) and measure `SETNX` calls per create before and after the attempt cap.
