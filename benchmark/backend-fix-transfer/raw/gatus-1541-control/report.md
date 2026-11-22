# Performance review: slow status dashboard / high DB CPU with many stored results

Static review only. Relevant code: `storage/store/sql/` (Postgres path) and `api/endpoint_status.go`.

## Summary

The most likely cause is a schema gap. The Postgres schema creates no index on `endpoint_results(endpoint_id)`, `endpoint_result_conditions(endpoint_result_id)` or `endpoint_events(endpoint_id)`. The SQLite schema creates the first two. Every dashboard read and every write goes through queries on these columns. As stored results per endpoint grow, each query becomes a scan of an ever-larger table, so CPU on the DB server rises with row count.

## Findings, in priority order

### 1. [Critical] Postgres has no index on `endpoint_results(endpoint_id)`
- Where: `storage/store/sql/specific_postgres.go`, `createPostgresSchema`. The `endpoint_results` table is created around line 60. The only indexes created are at lines ~113-122: `suite_results_suite_id_idx` and `endpoint_results_suite_result_id_idx`.
- Compare with `storage/store/sql/specific_sqlite.go` lines 113-128. SQLite creates `endpoint_results_endpoint_id_idx`, `endpoint_uptimes_endpoint_id_idx` and `endpoint_result_conditions_endpoint_result_id_idx`. Postgres has no equivalent.
- Postgres does not index foreign-key columns automatically. Only the primary key and UNIQUE constraints get indexes.
- Queries that suffer:
  - `getEndpointResultsByEndpointID` (`sql.go:791`): `WHERE endpoint_id=$1 ORDER BY endpoint_result_id DESC LIMIT/OFFSET`. Without an index on `endpoint_id`, the planner either scans the primary key backwards and filters, or scans the whole table and sorts. Cost grows with total rows across all endpoints, not just this endpoint's rows.
  - `getAllEndpointKeys` (`sql.go:689`): a `DISTINCT` join of `endpoints` to `endpoint_results` with `suite_result_id IS NULL`. This reads every result row of every endpoint on each uncached dashboard load.
  - On the write path, each check run executes these against `endpoint_results`:
    - `getNumberOfResultsByEndpointID` (`sql.go:955`): `COUNT(1) ... WHERE endpoint_id=$1`.
    - `getLastEndpointResultSuccessValue` (`sql.go:993`).
    - `deleteOldEndpointResults` (`sql.go:1026`): a `NOT IN (subselect ORDER BY ... LIMIT)` delete.
  - The `ON DELETE CASCADE` from `endpoints` also scans the table.
- Severity: highest. It matches the symptom exactly: cost scales with stored results, and it hits the DB CPU.
- Fix:
  - Add `CREATE INDEX IF NOT EXISTS endpoint_results_endpoint_id_idx ON endpoint_results (endpoint_id, endpoint_result_id DESC);`.
  - A composite index lets the "latest N results" query read N index entries and stop. It also makes the count and the delete's subselect index-only or index-range scans.
  - Build it with `CREATE INDEX CONCURRENTLY` on a large live table.
  - Optionally add a partial index for `getAllEndpointKeys` (see #4).

### 2. [Critical] No index on `endpoint_result_conditions(endpoint_result_id)` in Postgres
- Where: `specific_postgres.go`, table created around line 85, no index. Queried at `sql.go:~826` (`getEndpointResultsByEndpointID`) and at `sql.go:~1550` (`getSuiteResults`).
- Both queries are `WHERE endpoint_result_id IN (...)`. This is the biggest table, with roughly (results x conditions) rows. Each dashboard load triggers one such query per endpoint, so it is a sequential scan of the biggest table once per endpoint. `ON DELETE CASCADE` from `endpoint_results` also scans it for each deleted result.
- Cleanup is hit hardest. `deleteOldEndpointResults` removes rows and cascades into this table without an index, so each delete is a full scan.
- Fix: `CREATE INDEX CONCURRENTLY endpoint_result_conditions_endpoint_result_id_idx ON endpoint_result_conditions (endpoint_result_id);`

### 3. [High] No index on `endpoint_events(endpoint_id)` in Postgres
- Where: `specific_postgres.go` (table around line 45, no index). Queries at `sql.go:761` (`getEndpointEventsByEndpointID`), `sql.go:949` (`COUNT(1)` on every insert) and `sql.go:1006` (`deleteOldEndpointEvents`).
- The count runs on every check result, so a full scan of the events table happens on every write. The events table is smaller than results, so it matters less than #1 and #2, but it is a constant tax.
- Fix: `CREATE INDEX endpoint_events_endpoint_id_idx ON endpoint_events (endpoint_id, endpoint_event_id DESC);`

### 4. [High] The dashboard list does N+1 queries and runs one `DISTINCT` join on every cache miss
- `GetAllEndpointStatuses`, `sql.go:117-140`:
  - It first runs `getAllEndpointKeys` (`sql.go:689-707`). This is `SELECT DISTINCT ... JOIN endpoint_results ... WHERE suite_result_id IS NULL`. It de-duplicates a join across every result row when it only needs to know whether one qualifying row exists.
  - Then per key it calls `getEndpointStatusByKey` (`sql.go:711`). Each call issues: a lookup of the endpoint id (`getEndpointIDGroupAndNameByKey`), an events query, a results query, and a conditions query. That is about 4 queries per endpoint, all in one transaction. With E endpoints, a page load costs about 4E+1 round trips, so it is sequential and latency-bound as well as CPU-bound.
- Fix:
  - Replace the `DISTINCT` join with `SELECT e.endpoint_key FROM endpoints e WHERE EXISTS (SELECT 1 FROM endpoint_results er WHERE er.endpoint_id = e.endpoint_id AND er.suite_result_id IS NULL)`. Back it with a partial index, for example `ON endpoint_results (endpoint_id) WHERE suite_result_id IS NULL`. The code comment at `sql.go:~691` claims the JOIN is faster than EXISTS, which is wrong once the index exists.
  - Batch the per-endpoint results with a window function (`ROW_NUMBER() OVER (PARTITION BY endpoint_id ORDER BY endpoint_result_id DESC)`, or a `LATERAL` join) and one conditions query for all of the result ids. This drops the query count from about 4E to about 4.

### 5. [Medium] The conditions query is built from a very large `IN (...)` list
- `sql.go:~815-835` and `~1545-1560`. Each query has one placeholder per result, so it has up to pageSize placeholders, and a new SQL text per call. Postgres cannot reuse a plan for it, and parse/plan cost grows with page size.
- Fix: use `WHERE endpoint_result_id = ANY($1)` with an array parameter, or a join onto the page's subquery. With the index from #2, this becomes a cheap index lookup.

### 6. [Medium] The suite path has N+1 queries per suite result
- `getSuiteResults`, `sql.go:1453-1603`. For each suite result in the page it runs one endpoint-results query and one conditions query, so a page of 20 results costs about 40 queries per suite. `GetAllSuiteStatuses` (`sql.go:1163`) multiplies that by the number of suites.
- Fix: fetch endpoint results for all suite result ids in the page with a single `WHERE er.suite_result_id = ANY($1)`, then a single conditions query. `endpoint_results_suite_result_id_idx` already exists.

### 7. [Medium] Write path cost on every check: cleanup and the cache refresh
- `InsertEndpointResult`, `sql.go:239-382`. Each result insert does the following in one transaction:
  - Several `COUNT(1)` queries: events (`:949`), results (`:955`), uptime entries (`:961`). Without the indexes, each is a scan.
  - A `NOT IN (SELECT ... ORDER BY ... LIMIT N)` delete (`sql.go:1026`). It is only triggered once the count exceeds max plus a threshold, but it then does a large anti-join. A `DELETE ... WHERE endpoint_id=$1 AND endpoint_result_id < (SELECT endpoint_result_id FROM ... ORDER BY endpoint_result_id DESC OFFSET N LIMIT 1)` is a simple range delete.
  - A write-through cache refresh (`sql.go:~366-377`). For each cached page of that endpoint it re-reads the results via `getEndpointStatusByKey`, inside the write transaction, which holds the transaction open longer. Consider invalidating only, or refreshing after commit.
- Fix: use the indexes above. Replace the count-then-delete with a range delete, or run cleanup less often (every K inserts). Move the cache refresh after the commit.

### 8. [Low] Smaller items
- `sql.go:~805`: `results = append([]*endpoint.Result{result}, results...)` prepends to a slice for every row, which is O(n^2) in the page size. Append and then reverse once. The comment calls it faster than a subselect, but a reversal at the end is cheaper.
- `getEndpointResultsByEndpointID` (`sql.go:791`) and `getAllEndpointKeys` (`sql.go:689`) do not `defer rows.Close()` on the first result set. An early error path can keep the connection and cursor open inside the transaction.
- Silent error handling: `GetAllEndpointStatuses` ignores errors per endpoint (`continue` at `sql.go:~130`), which hides failures behind a fast but incomplete response.
- `api/endpoint_status.go:20-45`: the handler caches the response, but on a miss all concurrent requests run the full query set (no single-flight). After a cache expiry, a dashboard with many viewers causes a burst. Add `singleflight`, or refresh the cache in the background.
- Confirm that the default `maximumNumberOfResults` and the dashboard's `pageSize` are bounded. A large page size multiplies every cost above.

## Suggested order of work
1. Add the three Postgres indexes (#1, #2, #3), built concurrently. This should remove most of the CPU load.
2. Rewrite `getAllEndpointKeys` and add the partial index (#4).
3. Batch the results and conditions reads (#4, #5, #6).
4. Tidy the write-path cleanup and the cache refresh (#7), then the low-severity items (#8).

## Verification
- Run `EXPLAIN (ANALYZE, BUFFERS)` on the queries in `sql.go:791`, `sql.go:689` and the conditions query before and after. Look for `Seq Scan` on `endpoint_results` or `endpoint_result_conditions`.
- Check `pg_stat_user_tables` for `seq_scan` and `seq_tup_read` on those tables, and `pg_stat_statements` for the most expensive statements.
- Test with a realistic dataset (for example 100 endpoints x the configured maximum results x several conditions each). I did not run anything, so the ranking rests on reading the schema and queries.
