# Performance Review: gatus-1541-treatment

**Date:** 2026-11-22
**Mode:** Full review, scoped to the status dashboard read path
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The slow dashboard under many stored results is explained by the SQL store's read path. Every uncached load runs a key-listing query that scans all stored results (PERF-001). On Postgres, the schema also lacks indexes on the columns that the per-endpoint queries filter on (PERF-002). Both are found from source only; no plans or measurements were supplied, so finding confidence is High and Medium, and review confidence is Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-002 (P1)** | Check `pg_indexes`, then add indexes on `endpoint_results(endpoint_id)`, `endpoint_result_conditions(endpoint_result_id)`, `endpoint_events(endpoint_id)` | Cheapest change, mirrors the SQLite schema, and PERF-001's fix depends on it |
| 2 | **PERF-001 (P1)** | Rewrite `getAllEndpointKeys` so it does not scan every result row | Cost follows retained results, the exact condition the operators report |
| 3 | **PERF-003 (P2)** | Batch per-endpoint queries | Only after 1 and 2 are measured |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Deployed storage type and whether write-through caching is on | Whether PERF-002 applies at all (SQLite already has the indexes) | Read the deployed storage config |
| Real plans for the three queries | Confidence of PERF-001 and PERF-002 | `EXPLAIN (ANALYZE, BUFFERS)` on a restored copy |
| Number of endpoints | Weight of PERF-003 | Count rows in `endpoints` |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-002 | safe-on-production | `SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename IN ('endpoint_results','endpoint_result_conditions','endpoint_events');` | If the indexes exist, drop PERF-002 |
| PERF-001 | not-safe-on-production | `EXPLAIN (ANALYZE, BUFFERS)` of the key-listing query on a restored copy | Shows rows read versus endpoint count |

---

## 2. Scope and method

**Reviewed:** the status API handler (`api/endpoint_status.go`, `api/cache.go`, `api/util.go`), the SQL store (`storage/store/sql/sql.go`, `specific_postgres.go`, `specific_sqlite.go`), and storage defaults.
**Not reviewed:** write path beyond the retention delete, alerting, watchdog, remote-instance fetch, the web frontend, the in-memory store.

**Evidence available:** Uninstrumented for this review (no profiles, plans, traces or load tests supplied; none read).
**Ranking method:** Structural signals only. Ranking is inference from source.
**Reference depth:** Postgres, SQLite and Go have deep references. The repository also contains a Node frontend (`web/app`); it was not analyzed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Dashboard read path and the SQL schema; the rest was not read |
| **Critical paths** | 1 / 1 identified (status list API) |
| **Shared resources** | 3 / 3 (database, API response cache, write-through cache) |
| **Technology support** | Postgres deep, SQLite deep, Go deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Actual plans and row counts | No evidence exists | `EXPLAIN (ANALYZE, BUFFERS)` on a copy with many results per endpoint |
| Deployed storage type, caching setting, endpoint count | No evidence exists | Deployed config |
| Cost of write path, alerting, frontend | Not examined | Separate review |

---

## 3. Architecture overview

A Go service using Fiber. `GET` on the endpoint statuses API checks a 10-second in-process cache (`api/cache.go`, max 100 entries); on a miss it calls `GetAllEndpointStatuses` on the SQL store. The store optionally keeps a write-through cache (10000 entries). Storage is Postgres or SQLite; SQLite is limited to one connection.

| Component | Technology | Support tier | Role |
|:--|:--|:--|:--|
| API | Go, Fiber | deep | Serves statuses |
| Store | Postgres / SQLite | deep | Results, events, uptimes |

**Shared resources:** the database (all dashboard loads and the writers of every check result), the 10s API cache, the write-through cache.

---

## 4. Critical paths

Status list request: API cache lookup, then on a miss one transaction that lists endpoint keys, and for each key loads the endpoint row, events, results and result conditions, then JSON-marshals everything.

---

## 5. Findings

### PERF-001: Key listing scans every stored result (P1)

- **Severity / Confidence / Priority:** High / High / P1. **Root cause:** ROOT-001. **Category:** data-access
- **Location:** `storage/store/sql/sql.go:689` (`getAllEndpointKeys`)
- **Problem:** `SELECT DISTINCT e.endpoint_key FROM endpoints e INNER JOIN endpoint_results er ... WHERE er.suite_result_id IS NULL` must read every non-suite result row to produce one key per endpoint. Cost follows total stored results, not endpoints or page size.
- **Performance principle:** Work per request should be proportional to what is returned, not to retained history.
- **Evidence:** static, `sql.go:689`; called first by `GetAllEndpointStatuses` (`sql.go:117`); reached on each miss of the 10s cache (`api/endpoint_status.go:24`). No runtime evidence.
- **Impact:** critical path, per request on cache miss, O(n) in stored results, service-wide (shared database).
- **Conditions:** many results retained per endpoint and cache misses. Retention default is 100 (`storage/config.go:8`); the operators' report implies it is set higher.
- **Counter-evidence:** no existence check, LIMIT or pre-aggregation exists. Retention is capped per endpoint (`sql.go:1026`), which bounds but does not remove the growth.
- **Why this might not matter:** with the default of 100 results and few endpoints the scan is cheap, and both caches absorb repeats. No plan proves it dominates CPU.
- **Recommendation:** list keys from `endpoints` with an `EXISTS` check on `endpoint_results` (stops at first match per endpoint), preserving the suite-only exclusion.
- **Alternatives:** preferred is the `EXISTS` form (needs the PERF-002 index). Raising the cache TTL masks the work and adds staleness.
- **Trade-offs:** must keep suite semantics; a flag column would need a write-path update and migration.
- **Validation:** measure rows read and time of the query on a restored copy with many results per endpoint; expect rows to fall from results-table order to endpoints-table order. Falsified if the current plan already touches about one row per endpoint. Command: `EXPLAIN (ANALYZE, BUFFERS) SELECT DISTINCT e.endpoint_key FROM endpoints e INNER JOIN endpoint_results er ON e.endpoint_id = er.endpoint_id WHERE er.suite_result_id IS NULL ORDER BY e.endpoint_key;` (not-safe-on-production; use a copy). Guard: a benchmark with many results per endpoint.

### PERF-002: Postgres schema lacks indexes on lookup columns (P1)

- **Severity / Confidence / Priority:** High / Medium / P1. **Root cause:** ROOT-002. **Category:** data-access. **Tags:** quick-win, needs-measurement
- **Location:** `storage/store/sql/specific_postgres.go:108` (`createPostgresSchema`)
- **Problem:** No index on `endpoint_results(endpoint_id)`, `endpoint_result_conditions(endpoint_result_id)` or `endpoint_events(endpoint_id)`. Postgres does not index foreign keys automatically. The SQLite schema indexes the first two (`specific_sqlite.go:115`, `:127`).
- **Performance principle:** A selective lookup should cost in proportion to matches, not table size.
- **Evidence:** static. Only two index statements exist for Postgres (`specific_postgres.go:113`, `:122`). The results query filters on `endpoint_id` per endpoint (`sql.go:791`); the conditions lookup filters on `endpoint_result_id IN (...)` per endpoint (`sql.go:826`); the retention delete filters on `endpoint_id` on every insert (`sql.go:1026`).
- **Impact:** critical path, per endpoint, O(n) in table size, service-wide.
- **Conditions:** Postgres on a separate server, many retained results. The results query might walk the primary key backwards and stop early, which is why confidence is Medium. The conditions lookup has no such shortcut.
- **Counter-evidence:** no Postgres index or migration for these columns anywhere in the store package.
- **Why this might not matter:** an index added out of band, early termination of the primary-key scan, or small tables would shrink the effect.
- **Recommendation:** add the three indexes with `CREATE INDEX IF NOT EXISTS`, mirroring SQLite.
- **Alternatives:** preferred is three simple btree indexes; a composite `(endpoint_id, endpoint_result_id DESC)` costs more write space, use only if plans demand it.
- **Trade-offs:** extra write cost on insert-heavy tables; index builds on large tables need `CONCURRENTLY` to avoid blocking writes.
- **Validation:** run `SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename IN ('endpoint_results','endpoint_result_conditions','endpoint_events');` (safe-on-production), then `EXPLAIN (ANALYZE, BUFFERS)` on the conditions lookup on a copy (not-safe-on-production). Expect scans to become index scans. Falsified if the indexes already exist or the plans already use indexes.

### PERF-003: Per-endpoint query loop (P2)

- **Severity / Confidence / Priority:** Medium / High / P2. **Root cause:** ROOT-003. **Category:** data-access
- **Location:** `storage/store/sql/sql.go:117` (`GetAllEndpointStatuses`)
- **Problem:** For each key, up to four queries (id lookup, events, results, conditions) run serially, so round trips grow with endpoint count. Errors per key are silently skipped (`sql.go:129`).
- **Performance principle:** Round trips should not scale with the number of returned entities.
- **Evidence:** static, loop at `sql.go:117-139` calling `getEndpointStatusByKey` (`sql.go:711`). Derived count: up to 4 per endpoint plus 1 for the key list.
- **Impact:** critical path, per item, O(n) in endpoints, endpoint-level blast radius.
- **Conditions:** many endpoints and cache misses; latency impact depends on DB network distance, unknown.
- **Counter-evidence:** the write-through cache can serve lookups when enabled (`sql.go:713`); one transaction on one connection, so no pool exhaustion from the loop.
- **Why this might not matter:** low-latency DB and few endpoints make it cheap; it matters mainly when PERF-002 makes each query slow.
- **Recommendation:** set-based queries (window function over `endpoint_id`, one `IN` list), or pre-warm the cache.
- **Alternatives:** preferred is set-based queries; relying on the write-through cache fails on cold start and after the 10s API cache expires.
- **Trade-offs:** more complex SQL and pagination logic.
- **Validation:** count queries per cache-miss request (safe-on-production via query logging); expect the count to stop growing with endpoints. Falsified if the write-through cache serves nearly all lookups.

---

## 6. Other candidates

| ID | Severity | Confidence | Priority | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| (considered, not reported) | n/a | n/a | n/a | `sql.go:820` | Prepending results with `append` is quadratic, but bounded by the clamped page size; revisit if that limit is raised |

---

## 7. Adjacent findings

None.

---

## 8. Prioritized actions

1. PERF-002, then 2. PERF-001, then 3. PERF-003.

## 9. Validation plan

See each finding. The index check on `pg_indexes` is safe on production; all `EXPLAIN (ANALYZE)` commands should run on a restored copy.
