# REST frameworks: FastAPI, Flask, Django, Express, NestJS, Koa, Gin, Echo, Fiber, Spring Boot, Actix, Axum, Laravel, Rails, and ASP.NET Core compared

Load after `application/api.md`. This file contains only what that file does not give you:
the concrete divergences between fifteen web frameworks in what they bound by default,
what they run synchronously versus asynchronously by default, and what their middleware
pipeline actually costs — the same gap `docs/supported-technologies.md` named for this
signal before it was promoted.

**This is a deliberately comparative file, not a single-framework one** (see
`docs/roadmap.md`) — these frameworks span five languages and differ in exactly the
dimensions that change what a review should check. **Confirm which framework is actually
deployed before applying a row from any table below**, and treat every entry as "confirm
against the deployed version's documentation," not as a fact frozen at the time of writing.

---

## 1. Detection signals

| Framework | Ecosystem | Concurrency model |
|:--|:--|:--|
| **FastAPI** | Python | `asyncio`, ASGI |
| **Flask** | Python | Synchronous WSGI by default; ASGI/async views require an adapter |
| **Django** | Python | Synchronous WSGI by default; async views and ASGI support are opt-in |
| **Express** | Node.js | Single-threaded event loop |
| **NestJS** | Node.js | Single-threaded event loop (built on Express or Fastify) |
| **Koa** | Node.js | Single-threaded event loop |
| **Gin** | Go | Goroutine-per-request |
| **Echo** | Go | Goroutine-per-request |
| **Fiber** | Go | Goroutine-per-request, built on `fasthttp` — **not** `net/http`, see §2 |
| **Spring Boot** | Java/Kotlin (JVM) | Thread-per-request (Servlet stack) by default; WebFlux is a separate, reactive stack |
| **Actix (Web)** | Rust | Actor-model workers over async Tokio |
| **Axum** | Rust | Async Tokio, Tower middleware stack |
| **Laravel** | PHP | Process-per-request (PHP-FPM) in the common deployment shape |
| **Rails** | Ruby | Thread- or process-per-request depending on the app server (Puma, Unicorn) |
| **ASP.NET Core** | .NET | Async by default (`Task`-based), thread-pool-backed |

Other signals: framework-specific manifest entries (`fastapi`/`flask`/`django` in
`requirements.txt`/`pyproject.toml`; `express`/`koa`/`@nestjs/core` in `package.json`;
`gin-gonic/gin`/`labstack/echo`/`gofiber/fiber` in `go.mod`, matched by full module path —
see the collision note below; `spring-boot-starter-web` in `pom.xml`/`build.gradle`;
`actix-web`/`axum` in `Cargo.toml`; `laravel/framework` in `composer.json`;
`rails` in `Gemfile`; an ASP.NET Core `.csproj` referencing `Microsoft.AspNetCore.App`).

**Go framework tokens use their full module path, not the bare framework name.** Behavioral
evaluation (`docs/evaluation.md`) found the bare token `gin` matching inside the ordinary
English word "logging," and the bare token `echo` matching inside ordinary shell `echo`
commands in CI YAML — both are common substrings with no relationship to the frameworks
when unqualified. `gin-gonic/gin`, `labstack/echo`, and `gofiber/fiber` are the tokens
actually used.

---

## 2. What differs from the API baseline, framework by framework

### The concurrency model determines what "one slow handler" costs the rest of the process

`application/async-and-blocking.md` covers the general blocking-call-in-async-handler
pattern; what differs here is which of these frameworks are exposed to it at all, and how
severely:

- **Express, Koa, and NestJS-on-Express run one JavaScript event loop per process.** A
  synchronous CPU-bound operation, or a blocking I/O call that bypasses Node's async I/O
  APIs, stalls every other request the process is currently handling — the most severe
  version of this failure mode. NestJS's default adapter is Express; check whether the
  Fastify adapter is in use instead, since Fastify's routing and serialization overhead
  differs materially from Express's despite both sitting on the same Node event loop.
- **FastAPI is exposed to the same event-loop-stall risk as the Node frameworks, but only in
  `async def` route handlers.** A synchronous, blocking call inside `async def` blocks the
  single event loop; the same blocking call inside a plain `def` handler is automatically
  run in FastAPI's thread pool instead, which avoids stalling the loop but introduces
  ordinary thread-pool-sizing reasoning as the new constraint. Which handler style is in use
  changes which failure mode applies.
- **Flask and Django are synchronous-by-default (WSGI), so a "blocking call" is simply
  normal control flow, not a defect** — the relevant question for these two is worker/thread
  pool sizing (how many concurrent requests the WSGI server, e.g. Gunicorn or uWSGI, actually
  runs), not event-loop blocking. Django's async views and ASGI support exist but must be
  deliberately opted into per-view; do not assume a Django deployment is async-capable
  without confirming the server is actually running under ASGI.
- **Gin, Echo, and Spring Boot's default Servlet stack are thread- or goroutine-per-request**,
  so a slow handler occupies its own goroutine/thread rather than blocking siblings — the
  failure mode under load is thread-pool or goroutine-scheduler saturation, not a stalled
  single loop. Spring's WebFlux (Reactor-based) is architecturally distinct from the default
  Servlet stack; confirm which is actually deployed, since WebFlux reintroduces the
  Node-style event-loop-blocking risk that the default Servlet stack does not have.
- **Actix and Axum are async Tokio applications**; the blocking-call-in-async-handler
  question applies to them in the same shape `technology/rust.md` describes for Tokio
  generally — confirm CPU-bound or blocking work is offloaded via `spawn_blocking` rather
  than run inline on an async task.
- **ASP.NET Core is `Task`-based and thread-pool-backed**; the sync-over-async and
  synchronous-provider-call patterns `technology/dotnet.md` describes apply directly, and
  are a more common real-world failure mode here than event-loop blocking, precisely because
  the framework does not fail loudly the way stalling a single Node event loop does.

### Request body size, upload limits, and static-file serving are bounded very differently by default

`application/api.md` §1 asks about response boundedness; the request-side, framework-level
equivalent varies sharply:

- Express and Koa historically impose **no default body-size limit** on raw request bodies
  unless a body-parsing middleware (`express.json({ limit: ... })`, `koa-bodyparser`) is
  explicitly configured with one — an unconfigured limit is an unbounded-request-size
  finding, not a hardening nice-to-have.
- FastAPI/Starlette, Django, and ASP.NET Core each have a request-size behavior governed by
  their underlying server (Uvicorn/Hypercorn, the WSGI/ASGI server, Kestrel respectively)
  rather than the framework itself — confirm the actual deployed server's configuration, not
  only the framework's own documentation.
- Frameworks that also serve static files directly in production (rather than delegating to
  a reverse proxy or CDN) add a distinct cost profile — file I/O and, depending on
  configuration, in-process caching — that a pure API framework does not have. Whether
  static assets are served by the application process at all is worth confirming before
  reasoning about its request-handling capacity from request-count alone.

### Fiber's non-`net/http` foundation is a real divergence from the other three Go frameworks

Gin and Echo are both built on Go's standard `net/http`. **Fiber is built on `fasthttp`, a
deliberately non-`net/http`-compatible HTTP implementation** chosen for lower per-request
allocation overhead. The practical consequence: `net/http`-based middleware, instrumentation,
and diagnostic tooling that Gin/Echo can use directly generally cannot be used with Fiber
without an adapter, and `fasthttp`'s request/response objects are reused across requests by
the framework for performance — code that retains a reference to a `fasthttp` request object
past the handler's return (a common mistake ported over from `net/http` habits, where this
is safe) can observe another request's data. This is a correctness footgun with a
performance-adjacent cause worth naming here rather than only under a correctness review.

### PHP-FPM's process-per-request model changes what "connection pooling" and "warm state" mean

Laravel's common deployment shape (PHP-FPM) starts each request in a fresh PHP process
(or a process drawn from a pool of already-booted-but-stateless workers, depending on
configuration), which means in-process caching, connection reuse, and any other
module-scope state that a long-lived process (Express, Spring Boot, ASP.NET Core) gets for
free do not carry over between requests the same way. A "why isn't this being cached
in-process" question that would be a legitimate finding for a long-lived-process framework
may be a misapplied question for a PHP-FPM deployment — confirm the actual process model
(PHP-FPM versus a long-running Swoole/RoadRunner-based deployment, which behaves more like
the long-lived-process frameworks) before applying that reasoning.

### Rails' thread- or process-per-request behavior is governed by the app server, not the framework

Rails itself does not dictate concurrency; the app server does. Puma runs a configurable
number of threads per worker process (and can run multiple worker processes); Unicorn runs
one request per worker process with no in-process threading. Whether Active Record's
connection pool is sized correctly is a function of *threads-per-process × process count*
against the database's connection ceiling — the same arithmetic
`application/connection-pools.md` describes generically, but the inputs to that arithmetic
live in the app-server configuration, not in Rails' own configuration files.

---

## 3. Diagnostics

- **Per-route latency and throughput from the framework's own metrics integration** (Django
  and Flask via WSGI middleware timing; FastAPI/Starlette via ASGI middleware; Express/Koa
  via `response-time`-style middleware or APM agent hooks; Spring Boot Actuator's metrics
  endpoint; ASP.NET Core's built-in `Microsoft.AspNetCore.Metrics`). `safe-on-production`
  when already deployed; adding new middleware to measure this for the first time should be
  validated on a non-production environment given it wraps every request.
- **Worker/thread/process pool utilization** — Gunicorn/uWSGI worker stats for Flask/Django,
  PHP-FPM's status page (`pm.status_path`) for Laravel, Puma's stats endpoint for Rails,
  the JVM thread dump for Spring Boot's Servlet stack. Confirms whether the failure mode is
  actually pool saturation before assuming it is. `safe-on-production` (read-only status
  endpoints); a full JVM thread dump under heavy load has a brief pause cost and should be
  scheduled deliberately rather than run reflexively — treat as
  `not-safe-on-production` without first confirming the deployment's tolerance for a
  momentary pause.
- **Event-loop lag** (Node frameworks specifically) — `perf_hooks`'s event-loop-delay
  monitoring or an APM agent's equivalent. The direct way to confirm whether Express/Koa/
  NestJS-on-Express is actually being stalled by a blocking operation, rather than inferring
  it from latency alone. `safe-on-production`.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| A Node (Express/Koa/NestJS) service's P99 spikes for *all* endpoints together, not just one | A blocking operation stalling the single event loop; check for synchronous file/crypto/compression calls or CPU-bound work with no `worker_threads` offload |
| A FastAPI service's async endpoints are slow but sync (`def`) endpoints on the same server are fine | A blocking call inside an `async def` handler stalling the event loop; the sync handler is automatically thread-pool-offloaded and does not have this exposure |
| A Flask/Django/Rails/Laravel service degrades under concurrency with no single slow request visible | Worker/thread/process pool exhaustion — check the app server's configured concurrency against actual load, not the framework's own settings |
| A Fiber service exhibits data that looks like it belongs to a different request | A retained reference to a `fasthttp` request/response object past the handler's return, given the framework reuses these objects across requests |
| A Spring Boot service's thread pool saturates well before CPU or the database appears saturated | The default Servlet thread-per-request model combined with a blocking downstream call (a synchronous JDBC query, a blocking HTTP client) holding a thread for the call's full duration; confirm whether WebFlux is genuinely in use before assuming reactive, non-blocking I/O |

---

## 5. Configuration worth checking, and what it trades

- **Request body size limits.** Trades caller flexibility for a bound on per-request memory
  and I/O cost — Express and Koa in particular do not have a safe default without explicit
  configuration.
- **Worker/thread/process count** (Gunicorn/uWSGI workers, PHP-FPM `pm.max_children`, Puma
  threads/workers, JVM thread-pool size). Trades memory and context-switch overhead for
  concurrency headroom; sized against downstream connection-pool ceilings, not chosen in
  isolation — see `application/connection-pools.md`.
- **Keep-alive and idle-connection timeouts** at the framework/server level. Trades holding
  idle connections open (a resource cost under high connection churn) for avoiding repeated
  handshake cost on reused connections.
- **Static-file serving being handled by the application process at all**, versus delegated
  to a reverse proxy or CDN. Trades operational simplicity for competing directly with
  request-handling capacity on the same process/thread pool.

---

## 6. Version differences worth knowing

Default behavior has changed across major versions for several of these frameworks in ways
that are directly performance-relevant: Django's async view and ASGI support was added
incrementally across versions rather than present from the start; Spring Boot's WebFlux
stack and its relationship to the default Servlet stack has evolved across major Spring
Framework versions; Node's own `worker_threads` API (relevant to offloading CPU-bound work
out of Express/Koa's event loop) postdates earlier Node versions still in production use.
Confirm the deployed framework and runtime version's release notes before assuming a
capability (async support, a specific offloading API) is available, rather than assuming
current documentation describes every deployed version.

---

## 7. What this file does NOT cover

- Templating-engine rendering cost (Django templates, Rails' ERB/Blade) — real, but a
  content-generation cost distinct from the request-handling reasoning this file covers.
- ORM-specific behavior for any of these frameworks' default ORMs (Django ORM, Active
  Record, Eloquent, Entity Framework) — covered by `application/data-access.md` and, where a
  dedicated technology file exists for the underlying datastore, there.
- Frontend build tooling bundled with some of these frameworks (Laravel Mix/Vite, Rails'
  asset pipeline) — a build-time, not request-time, concern.
- Framework-specific dependency-injection container performance (Spring's, NestJS's) beyond
  what is named above — a real but narrow cost, usually dwarfed by I/O on the same path.
- Serverless deployments of these frameworks (e.g. Laravel Vapor, Express via Lambda) — see
  `infrastructure/resources.md` and the serverless technology reference for the per-invocation
  model that changes the reasoning above.
