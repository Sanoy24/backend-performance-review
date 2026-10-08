# Backend Performance Review: fastapi-template

**Scope:** `backend/` (FastAPI + SQLModel/SQLAlchemy 2 + psycopg3 + PostgreSQL 18), plus the Dockerfile and compose files that decide how it runs.
**Method:** I read the code by hand: routes, deps, CRUD, models, Alembic migrations, engine setup and deployment config. I didn't profile or load-test anything, so the severity ratings are my estimates of cost at realistic scale (thousands of users, 10^5 to 10^7 items) and under concurrent load.

## Summary

The codebase is small and mostly clean: no N+1 loops, no async/sync mixing bugs, and every endpoint is a plain `def`, so blocking calls go to the threadpool rather than stalling the event loop. The real risks fall into three groups:

1. **The schema doesn't fit the main query.** The paginated item listing filters by `owner_id` and sorts by `created_at`, and neither column has an index.
2. **Unbounded or expensive work on the request path.** Clients can pass any `limit`, SMTP sends run inline, and Argon2 hashing (64 MiB per call) has no concurrency limit.
3. **Connection and resource management.** The pool is left at its defaults, connections stay checked out across slow non-DB work, and ORM-side cascade deletes load every child row into memory.

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | No index on `item.owner_id` / `item.created_at`, so listing, counting and cascade-deleting items are full scans | **High** | Low |
| 2 | `limit` on list endpoints has no upper bound | **High** | Trivial |
| 3 | SMTP email sent synchronously inside request handlers | **High** | Low–Med |
| 4 | Argon2 hashing (64 MiB, t=3) has no limit on concurrency, so memory and CPU can blow up | **Medium–High** | Low–Med |
| 5 | Default connection pool, held across slow non-DB work, no `pool_pre_ping` | **Medium** | Low |
| 6 | Deleting a user loads and deletes every item row-by-row through the ORM | **Medium** | Low |
| 7 | Exact `COUNT(*)` plus `OFFSET` pagination on every list request | **Medium** (at scale) | Med |
| 8 | Sentry tracing at a 100% sample rate in production | **Low–Medium** | Trivial |
| 9 | Pydantic validation runs twice on list responses | **Low** | Trivial |
| 10 | Email templates read from disk and compiled by Jinja on every send | **Low** | Trivial |
| 11 | Fixed `--workers 4`, no response compression | **Low** | Trivial |

---

## 1. Missing indexes on `item.owner_id` and `item.created_at` (High)

**Where**
- `backend/app/models.py:91-100`: `Item.owner_id` and `Item.created_at` have no `index=True`.
- Migrations (`backend/app/alembic/versions/*`): the only secondary index created is `ix_user_email`. PostgreSQL **does not** index foreign key columns on its own, so `item_owner_id_fkey` has no supporting index.
- Hot queries in `backend/app/api/routes/items.py:29-42` (the normal-user branch of `read_items`):
  ```sql
  SELECT count(*) FROM item WHERE owner_id = :uid;
  SELECT ... FROM item WHERE owner_id = :uid ORDER BY created_at DESC OFFSET :skip LIMIT :limit;
  ```
- Superuser branch (`items.py:21-27`): `ORDER BY created_at DESC OFFSET .. LIMIT ..` over the whole table.
- `backend/app/api/routes/users.py:180-182` (`users.read_users`): `ORDER BY user.created_at DESC`, also unindexed.
- `users.py:228` (`DELETE FROM item WHERE owner_id = :uid`) and the DB-level `ON DELETE CASCADE` from migration `1a31ce608336`.

**Impact**
- Every time a normal user loads the items page (the app's main screen), Postgres runs **two sequential scans of the whole `item` table**, plus a sort for the second query. Cost grows with the total number of items across all users, not with that user's own item count. With 10^6 items that means tens to hundreds of ms per request and heavy buffer-cache churn, and it gets worse as the table grows.
- The superuser listing and `read_users` need a full scan plus a top-N sort.
- Deleting a user, whether through the explicit `DELETE ... WHERE owner_id` or the FK cascade, is also a full scan of `item`, and it holds row locks while it runs.

**Fix**
Add a composite index that serves both the filter and the sort, plus one for the global ordering:

```python
# models.py
class Item(ItemBase, table=True):
    __table_args__ = (
        sa.Index("ix_item_owner_id_created_at", "owner_id", sa.text("created_at DESC")),
        sa.Index("ix_item_created_at", sa.text("created_at DESC")),
    )
```

```python
# new alembic migration
op.create_index("ix_item_owner_id_created_at", "item",
                ["owner_id", sa.text("created_at DESC")],
                postgresql_concurrently=True)
op.create_index("ix_item_created_at", "item", [sa.text("created_at DESC")],
                postgresql_concurrently=True)
op.create_index("ix_user_created_at", "user", [sa.text("created_at DESC")],
                postgresql_concurrently=True)
```
(Run the concurrent index builds outside a transaction, inside `with op.get_context().autocommit_block():`.)

The `(owner_id, created_at)` index gives the per-user list an index-only range read with the order already correct. It also makes the per-user `COUNT(*)` an index-only scan and covers the FK cascade lookup. Afterwards, check the plans with `EXPLAIN (ANALYZE, BUFFERS)`. `created_at` is nullable, so think about making it `NOT NULL` with a server default so the sort order is well defined; otherwise NULLs come first in `DESC` order.

---

## 2. No upper bound on `limit` (High)

**Where**
- `backend/app/api/routes/items.py:14-16`: `skip: int = 0, limit: int = 100`
- `backend/app/api/routes/users.py:37`: same pattern

**Impact**
Any authenticated user can call `GET /api/v1/items/?limit=10000000`, and a superuser can do the same on `/users/`. The handler then:
1. loads every matching row into ORM objects (`.all()`),
2. builds a Pydantic model for each one, twice (see #9),
3. serializes one big JSON body in memory.

On a large table, a few of these requests in parallel can take a worker's memory into the GBs and keep a threadpool thread and a DB connection busy for seconds. It's a cheap way to degrade the service, whether by accident or on purpose. Negative values aren't rejected either, so `skip=-1` causes a database error and a 500.

**Fix**
```python
from fastapi import Query
skip: int = Query(0, ge=0),
limit: int = Query(100, ge=1, le=100),
```
Apply this to both list endpoints. For large datasets, also see #7 about deep `OFFSET`.

---

## 3. Synchronous SMTP delivery on the request path (High)

**Where**
- `backend/app/utils.py:33-56` (`send_email`): opens an SMTP connection, runs the TLS handshake, authenticates and sends, all blocking.
- Called inline from:
  - `login.py:53-74` (`POST /password-recovery/{email}`, **unauthenticated**)
  - `users.py:69-77` (`POST /users/`, admin user creation)
  - `utils.py:16-26` (`POST /utils/test-email/`)

**Impact**
- Each call blocks one threadpool thread for the full SMTP round trip, usually 200 ms to several seconds and much longer if the SMTP server is slow or unreachable. The `emails` library's default socket timeout is generous, so a dead mail server can pin threads for a long time.
- The anyio threadpool that runs sync endpoints is capped at 40 threads per worker by default. With the 4 workers set in the Dockerfile, about 160 slow SMTP calls in flight are enough to starve **every** sync endpoint, including login and item reads.
- `/password-recovery/{email}` is unauthenticated, so anyone can trigger this cost.
- In `recover_password`, the DB connection checked out for `get_user_by_email` stays held through the whole SMTP send (see #5), so this also drains the connection pool.
- Side effect: the endpoint responds much more slowly when the email exists than when it doesn't. The code tries to prevent enumeration (`login.py:60-61`), and this timing difference undoes that.

**Fix**
- Quick fix: use FastAPI `BackgroundTasks` so the response returns before the send:
  ```python
  def recover_password(email: str, session: SessionDep, background: BackgroundTasks) -> Message:
      ...
      background.add_task(send_email, email_to=user.email, subject=..., html_content=...)
  ```
  This also gives every request the same response time. Background tasks still run on the same worker's threadpool, so they cut latency but don't reduce resource use.
- Better fix: put emails on a job queue (Celery, RQ, arq, Dramatiq, or a Postgres outbox table) handled by a separate worker, with retries.
- Either way, set an explicit SMTP timeout (`smtp_options["timeout"] = 10`) and add rate limiting to `/password-recovery`, per IP and per email.

---

## 4. Argon2 password hashing with no concurrency limit (Medium–High)

**Where**
- `backend/app/core/security.py:11-16`: `PasswordHash((Argon2Hasher(), BcryptHasher()))` with pwdlib defaults. The stored `DUMMY_HASH` in `crud.py:42` shows the parameters: `m=65536, t=3, p=4`, i.e. **64 MiB of memory and 3 passes per hash**.
- Hash or verify runs on: `POST /login/access-token` (always, including the dummy verify for unknown emails, `crud.py:45-60`), `/users/signup`, `POST /users/`, `PATCH /users/me/password` (verify **and** hash), `/reset-password/`, `/private/users/`.

**Impact**
- Each call costs about 30-100 ms of CPU across 4 lanes and **allocates 64 MiB**. argon2-cffi releases the GIL, so threads really do run in parallel, but nothing limits how many. Up to 40 threads per worker × 4 workers = 160 concurrent hashes ≈ **10 GiB of transient RSS**. A burst of login attempts, a credential-stuffing run, or a signup spam script can OOM-kill the container or push it into swap, which takes everything down.
- All of these endpoints except password change are unauthenticated, and login deliberately spends the same cost on unknown emails, so attackers can trigger this cheaply.
- `login_access_token` also holds a DB connection while it hashes (see #5).

**Fix**
- Put a process-wide semaphore around hashing so memory is bounded and the remaining threads stay free for normal requests:
  ```python
  _hash_slots = threading.BoundedSemaphore(int(os.getenv("HASH_CONCURRENCY", os.cpu_count() or 2)))
  def verify_password(p, h):
      with _hash_slots:
          return password_hash.verify_and_update(p, h)
  ```
- Add rate limiting on `/login/access-token`, `/users/signup` and `/password-recovery` (slowapi, or at Traefik with the `ratelimit` middleware), keyed by IP and by username.
- Make the Argon2 parameters a deliberate, documented choice. RFC 9106's second recommended option (m=64 MiB, t=3) is fine for security, but size the container memory for it (`HASH_CONCURRENCY × 64 MiB × workers`). If the budget is tight, a profile like m=19 MiB, t=2, p=1 (OWASP's minimum) cuts memory by more than 3×. `verify_and_update` will rehash existing users transparently.
- Release the DB connection before hashing (see #5).

---

## 5. Connection pool left at defaults and held across slow non-DB work (Medium)

**Where**
- `backend/app/core/db.py:7`: `engine = create_engine(str(settings.DATABASE_URL))` uses `pool_size=5, max_overflow=10, pool_timeout=30`, has no `pool_pre_ping`, and has no `pool_recycle`.
- `backend/app/api/deps.py:21-23`: one `Session` per request, closed only when the response is done.
- `deps.py:41`: `get_current_user` runs `session.get(User, ...)`, which autobegins a transaction and checks out a connection at the **start** of every authenticated request. The connection is held until the request finishes.

**Impact**
- Each worker can have up to 40 threads running sync endpoints but only 15 DB connections. Under load, threads 16 to 40 block in `pool.connect()` for up to 30 s and then fail with `QueuePool limit ... reached`. The threadpool is effectively capped at 15 by the DB pool, and extra concurrency turns into queueing rather than throughput.
- Connections stay checked out (idle in transaction) while the request does non-DB work: Argon2 verify in login (#4), SMTP in password recovery (#3), and Pydantic serialization of list responses. Holding scarce connections during slow CPU and network work is the main thing that makes pool exhaustion likely.
- Without `pool_pre_ping`, a Postgres restart or failover, or an idle connection dropped by a proxy or NAT, shows up as 500s on the first request to each stale connection.
- Rough numbers: 4 workers × 15 = 60 connections, which fits under Postgres's default `max_connections=100`. Scaling to 2+ containers goes over that, so the pool needs sizing together with replica count, or PgBouncer.

**Fix**
```python
engine = create_engine(
    str(settings.DATABASE_URL),
    pool_size=settings.DB_POOL_SIZE,        # e.g. 10
    max_overflow=settings.DB_MAX_OVERFLOW,  # e.g. 10
    pool_timeout=5,                         # fail fast rather than hang 30s
    pool_pre_ping=True,
    pool_recycle=1800,
)
```
- Release the connection before slow work: in `authenticate` and `recover_password`, do the lookup, then `session.commit()` (or `session.rollback()`) to end the read transaction and return the connection to the pool, and only then hash or send. `expire_on_commit` would reload attributes later, so read the fields you need first.
- Size `pool_size + max_overflow` close to the threadpool limit you actually expect, or deliberately lower the anyio threadpool limit to match the pool. Track `pool.checkedout()` and connection wait time in metrics.

---

## 6. Deleting a user loads and deletes every item through the ORM (Medium)

**Where**
- `backend/app/models.py:59`: `items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)`. This becomes SQLAlchemy `cascade="all, delete-orphan"` with `passive_deletes=False`.
- `backend/app/api/routes/users.py:133-143` (`DELETE /users/me`): `session.delete(current_user)`.
- `users.py:228-231` (`DELETE /users/{id}`): already does a bulk `DELETE FROM item WHERE owner_id=...` first, then `session.delete(user)`.

**Impact**
With `passive_deletes=False`, when SQLAlchemy flushes `session.delete(user)` it **lazy-loads the whole `user.items` collection** (one `SELECT` that also scans the whole table, see #1), builds an ORM object for every row, then sends a `DELETE ... WHERE id = ?` for each item (as executemany). For a user with 100k items, that's a big memory spike and a long transaction on a request thread, even though the database already has `ON DELETE CASCADE` on the FK and could do it all in one statement. In the admin path, the explicit bulk delete happens first, so the ORM load is a wasted extra scan that comes back empty.

**Fix**
- Let the database do the cascade:
  ```python
  items: list[Item] = Relationship(back_populates="owner", cascade_delete=True, passive_deletes=True)
  ```
  With `passive_deletes=True`, SQLAlchemy skips loading unloaded children and relies on `ON DELETE CASCADE`, which the migration already defines.
- After that, the explicit `delete(Item)` in `users.py:228` isn't needed (keeping it does no harm).
- Fix #1 first so the cascade uses the index.
- For very large accounts, consider an asynchronous or batched purge so a single request doesn't hold locks for long.

---

## 7. Exact `COUNT(*)` and `OFFSET` pagination on every list call (Medium, grows with data)

**Where**
- `items.py:22-23, 29-34` and `users.py:42-43`: `SELECT count(*)` runs on every page request.
- `.offset(skip)` on all list queries.

**Impact**
- Superuser item listing: `SELECT count(*) FROM item` with no `WHERE` is a full scan every time, because Postgres has no cached row count under MVCC. At 10^7 rows that's hundreds of ms to seconds per page view.
- `OFFSET n` reads and throws away `n` rows, so later pages get linearly slower even with the index from #1, and concurrent inserts make pages shift.

**Fix**
- Per-user count: fine once the `(owner_id, created_at)` index exists (index-only scan). Keep it.
- Global counts: drop the exact count for admin views, or return an estimate (`SELECT reltuples FROM pg_class WHERE relname='item'`), or cap it (`SELECT count(*) FROM (SELECT 1 FROM item LIMIT 10001) t` and show "10,000+").
- Move to keyset (cursor) pagination: `WHERE (created_at, id) < (:last_created_at, :last_id) ORDER BY created_at DESC, id DESC LIMIT :n`, with the index extended to `(owner_id, created_at DESC, id DESC)`. This is an API change for the generated frontend client, so plan it rather than doing it as a quick fix.

---

## 8. Sentry tracing samples every request (Low–Medium)

**Where:** `backend/app/main.py:18-19`: `sentry_sdk.init(dsn=..., enable_tracing=True)`

**Impact**
`enable_tracing=True` (deprecated in recent SDKs) means a `traces_sample_rate` of 1.0, so **every** request in production gets a transaction with spans for each SQL query and HTTP call, all serialized and sent to Sentry. That adds per-request CPU and allocation (usually a few percent, more on cheap endpoints such as the health check that compose polls every 10 s), plus network traffic and Sentry quota or cost.

**Fix**
```python
sentry_sdk.init(
    dsn=str(settings.SENTRY_DSN),
    traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,  # e.g. 0.05–0.1
    # or a traces_sampler that drops /health-check
)
```

---

## 9. Pydantic validation runs twice on list responses (Low)

**Where:** `items.py:44-45`, `users.py:50-51` together with `response_model=ItemsPublic/UsersPublic` on the decorator.

**Impact**
The handler validates each ORM row into `ItemPublic`, wraps them in `ItemsPublic`, and returns that. Because the declared return type is `Any` and `response_model` is set, FastAPI then **validates the whole object again** against `response_model` before serializing. For a 100-row page this costs roughly 2× the necessary serialization CPU, a few ms per request on the hottest endpoint. It's small, but it's also the easiest one to fix.

**Fix**
Annotate the return type and drop the duplicate model, or return a dict and let FastAPI validate once:
```python
@router.get("/")
def read_items(...) -> ItemsPublic:
    ...
    return ItemsPublic(data=items, count=count)  # items validated once via from_attributes
```
Alternatively, keep the explicit construction and return `ORJSONResponse(content=result.model_dump(mode="json"))` to skip the response-model pass.

---

## 10. Email templates read from disk and compiled on every send (Low)

**Where:** `backend/app/utils.py:25-30`: `Path(...).read_text()` followed by `jinja2.Template(template_str)` on every call.

**Impact**
A file read plus a Jinja parse and compile (roughly a millisecond or two) on every email. It's small next to the SMTP round trip, but it's pure waste and runs on the request thread.

**Fix**
Use a module-level `jinja2.Environment(loader=FileSystemLoader(...), auto_reload=False)` and `env.get_template(name)`, which caches compiled templates, or wrap the loader in `functools.lru_cache`. Turning on `autoescape` is also advisable, though that's a security point rather than a performance one.

---

## 11. Deployment-level tuning (Low)

**Where:** `backend/Dockerfile:65` (`fastapi run --workers 4`), `backend/app/main.py` (no compression middleware).

- **Fixed worker count:** 4 workers whatever the container's CPU quota. On a 1-vCPU container that's oversubscribed (4× memory, including 4 connection pools and 4 threadpools of Argon2 risk). On a large host it leaves cores idle. Make it configurable (`WEB_CONCURRENCY`) and size it to the CPU limit.
- **No response compression:** list responses (up to 100 items × ~300 B) and the SPA assets served through `app.frontend(...)` are sent uncompressed. Turn on compression at Traefik (`traefik.http.middlewares.gzip.compress=true`, which is cheaper than doing it in Python) or add `GZipMiddleware(minimum_size=1024)`.

---

## Things checked and found fine

- **No N+1 queries:** response models (`ItemPublic`, `UserPublic`) don't include relationships, so serialization never triggers a lazy load.
- **No blocking calls in `async def`:** the only async endpoint is `health_check`, which does no I/O. Everything else is sync and correctly runs on the threadpool.
- **`get_current_user` runs one PK lookup per request:** cheap with the PK index. Caching it isn't worth the staleness and invalidation risk at this scale. Just note that it's what checks out the DB connection early (#5).
- **`ix_user_email` exists and is unique**, so `get_user_by_email` (login, signup, recovery) is an index lookup.
- **Opportunistic rehash on login** (`crud.authenticate`) only writes when the parameters change, so it's not a steady-state cost.

## Recommended order of work

1. Add `ge`/`le` bounds to `skip`/`limit` (#2): minutes of work, closes a DoS vector.
2. Add the `item(owner_id, created_at)` and `created_at` indexes with a concurrent migration (#1), and set `passive_deletes=True` (#6).
3. Move `send_email` off the request path and add an SMTP timeout (#3).
4. Configure the engine pool (`pool_pre_ping`, sizes, short timeout) and release connections before hashing or SMTP (#5).
5. Bound Argon2 concurrency and rate-limit the auth endpoints (#4).
6. Set a Sentry sample rate (#8); remove double validation (#9); cache templates (#10); worker count and compression (#11).
7. Plan keyset pagination and drop the global exact counts (#7) before the item table gets into the millions.
