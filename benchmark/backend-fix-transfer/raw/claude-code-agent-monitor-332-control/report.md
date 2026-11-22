# Performance review: analytics tool-usage panel

The tool-usage panel is served by `GET /api/analytics` (`server/routes/analytics.js`, handler at line 16). The cause of the slow load and the server freeze is the `tool_usage` query, which has no usable index and runs synchronously. I did not run anything; this is from reading the code.

## 1. (Critical) Tool-usage query scans and sorts the whole `events` table, with no index on `tool_name`

- Unscoped path: `server/db.js:1961-1968` (`stmts.toolUsageCounts`), called from `server/routes/analytics.js:32`.

  ```sql
  SELECT tool_name, COUNT(*) FROM events WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY count DESC LIMIT 20
  ```

- Scoped path: `server/lib/scoped-stats.js:97-106`. It adds `AND session_id IN (SELECT id FROM sessions WHERE source/provider ...)`. It is chosen when the `sources` or `providers` filter is active, at `analytics.js:30-31`.
- The `events` schema is at `db.js:195-206`, and the indexes are at `db.js:346-406`. There is no index that has `tool_name` as a leading or covering column.
  - The existing indexes are on `session_id`, `event_type`, `created_at`, and combinations of `session_id`, `agent_id`, `event_type` and `created_at`.
  - SQLite therefore reads every row of `events` and builds a temp B-tree to group. That temp B-tree also sorts `ORDER BY count`.
  - The rows are wide, because `events` carries `summary` and a JSON `data` blob (`db.js:201-202`). The full scan reads that wide row data from disk, so cost grows linearly with table size.
  - In the scoped variant, the planner may also probe the `session_id IN (...)` subquery per row.
- Fix:
  - Add a partial covering index: `CREATE INDEX IF NOT EXISTS idx_events_tool_name ON events(tool_name) WHERE tool_name IS NOT NULL;`.
    - The grouped count then becomes an index-only scan, with no table-row reads. It will be orders of magnitude faster.
    - Build it in `db.js` next to the other index definitions at lines 346-406.
    - Beware that the one-time build on a very large table takes a while at startup. Do it once, in a migration, and log it.
  - For the scoped query, add `events(session_id, tool_name)`. Keep `session_id` first so the scope subquery can drive the lookup, with `tool_name` as the second column so counting stays index-only.
  - Better, if even the index scan is too slow at tens of millions of rows, maintain a counter table `tool_usage_counts(tool_name, count)` (plus a per-session or per-source variant). Update it incrementally in the event insert path (`db.js:1675-1678`) instead of aggregating on read.

## 2. (Critical) Blocking, synchronous execution freezes the whole server

- `server/db.js:10` loads `better-sqlite3`, with `node:sqlite` as the fallback. Both are synchronous.
- The handler at `routes/analytics.js:16-93` calls `.all()` and `.get()` directly on the main thread. Node cannot serve any other request while the query runs, including hook ingestion, WebSocket traffic and other API calls. A multi-second scan therefore freezes everything, which is exactly the reported symptom.
- Fix:
  - Make the query cheap (item 1) so the blocking time is milliseconds.
  - Cache the whole analytics response (see item 3).
  - If heavy aggregation must stay, move it to a worker thread or a read-only second DB connection in a `worker_threads` pool. Another option is a periodic background refresh into a summary table.

## 3. (High) No caching, and the whole analytics bundle is recomputed on every request

- `routes/analytics.js:27-72` runs about 12 aggregate queries per request: tool usage, daily events, event types, average events per session, stats overview, and so on. Several are full scans of `events`:
  - `eventTypeCounts` (`db.js:1989-1995`) is `GROUP BY event_type` over all rows. It is scannable through `idx_events_type`, but it still touches every entry.
  - `avgEventsPerSession` (`db.js:1996-1999`) is `COUNT(*)` over all events.
  - `dailyEventCounts` (`db.js:1970-1976`) runs `DATE(created_at, ?)` per row for the last 365 days. The function on the column prevents using the index for grouping.
  - The scoped overview and counts (`scoped-stats.js:33-50`, `152-172`) are `COUNT(*) FROM events WHERE session_id IN (subquery)`, one full pass each.
- The panel only needs the top-20 tool list, but it pays for all of these, so total latency is the sum of every scan. The page (`client/src/pages/Analytics.tsx:695`) fetches the full payload with `api.analytics.get()`.
- Fix:
  - Add a short TTL cache keyed by (`sources`, `providers`, `tz_offset`), for example 15-60 s, with in-flight request coalescing.
  - Consider splitting out a dedicated `/api/analytics/tool-usage` endpoint so this panel does not wait on the other aggregates.
  - Replace `COUNT(*)` over `events` with maintained counters or `MAX(id)` approximations where exact values are not needed.

## 4. (Medium) Unbounded `token_usage` load used for cost computation

- `routes/analytics.js:64-72` runs `SELECT tu.*, s.provider, DATE(s.started_at) FROM token_usage tu JOIN sessions s ...` and then `.all()`. The scoped variant is `scoped-stats.js:174-182`. Both load every `token_usage` row into JS memory, and the cost is then computed in JS (`calculateProviderCost`).
- It is also synchronous, so it adds to the blocking time. It is not specific to tool usage, but it runs on the same request path, and its memory and CPU cost grows with the number of sessions and models.
- Fix: aggregate in SQL (`GROUP BY model, provider, date`) before pricing, or cache the cost total. Also stop using `tu.*`, which pulls unused columns.

## 5. (Medium) `DATE(created_at, ?)` and `LIKE`/`LOWER()` patterns defeat indexes

- `db.js:1970-1976` and `scoped-stats.js:108-117` wrap the column in `DATE(...)` for grouping. The `WHERE created_at >= DATE('now','-365 days')` part can use `idx_events_created`, but every matching row is still read and transformed. If most history is within the last year, that is a near-full scan.
- Fix: pre-aggregate into a `daily_event_counts` table, or cache.
- The per-session error count (`db.js:2019-2030`) uses `LOWER(summary) LIKE '...'` and similar. It is bounded by `session_id` today, but would be slow on very large sessions.

## 6. (Low) Startup full-table UPDATE on every boot

- `db.js:539-542` runs `UPDATE events SET tool_name = NULL WHERE event_type IN (...)` on every start. The comment says "once", but nothing guards it with a flag. `idx_events_type` lets it find the matching rows, but it still has to evaluate the predicate each boot. It rewrites rows if any `tool_name` is non-null, and holds the write lock while doing so.
- Fix: guard it with a one-time migration marker, for example `PRAGMA user_version` or a settings row.

## Priority

1. Add the partial index on `events(tool_name)`, plus `(session_id, tool_name)` for the scoped variant. This is the main fix and is low risk.
2. Cache the analytics response and split out a tool-usage endpoint, so the request stops blocking the event loop repeatedly.
3. Aggregate `token_usage` and the daily counts in SQL or a summary table.
4. Guard the startup UPDATE.
5. Long term, keep counters incrementally or move heavy reads off the main thread.
