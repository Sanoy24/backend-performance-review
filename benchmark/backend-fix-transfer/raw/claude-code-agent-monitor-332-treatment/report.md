# Performance Review: claude-code-agent-monitor (analytics tool-usage panel)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the code behind the Analytics page (GET /api/analytics and its client refresh logic)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The reported symptom (tens of seconds, whole server frozen) is explained by the code. GET /api/analytics computes its tool-usage counts, and several other aggregates, by scanning the whole `events` table synchronously on the single Node event loop, and the Analytics page re-issues that request on every websocket event. No runtime measurements were supplied, so magnitude is unmeasured. Finding confidence is High for the mechanism; overall review confidence is Medium because only this one path was examined.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P0)** | Stop recomputing whole-history aggregates per request: keep a tool-count counter table (or periodic snapshot) and, as a quick interim step, add a partial index on `events(tool_name)`. | Per-request O(rows) work on the shared event loop; blocks all requests including hook ingest. |
| 2 | **PERF-002 (P1)** | Add single-flight and debounce to the Analytics page `load()` and pause polling when hidden. | Multiplies PERF-001 by hook event rate; small client change. |
| 3 | (sequencing) | Measure first on a database copy (below) to confirm the split between queries. | Chooses between index-only and counter-table fix. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Row count of `events` and share with non-null `tool_name` | Whether an index alone is enough or a counter table is needed | `SELECT COUNT(*)` on a copy of the DB |
| Hook event rate while the page is open | Whether PERF-002 is a primary or secondary cause | Request log / browser network panel during an active session |
| Cost of the pricing total-cost call made with every refresh | Whether another hot spot remains after fixing PERF-001 | Profile that endpoint (not reviewed) |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | safe-on-production (run on a COPY of the DB) | `sqlite3 <copy-of-db> "EXPLAIN QUERY PLAN SELECT tool_name, COUNT(*) FROM events WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 20;"` | A `SCAN events` line confirms the full scan; after an index, expect a covering index scan. |
| PERF-002 | safe-on-production | Procedure: open the Analytics page during an active session and count GET /api/analytics requests per minute in the browser network panel. | Rate tracking event rate confirms the amplification. |

---

## 2. Scope and method

**Reviewed:** `server/routes/analytics.js`, analytics statements in `server/db.js` (events schema and indexes, `toolUsageCounts` and sibling aggregates), `server/lib/scoped-stats.js`, `client/src/pages/Analytics.tsx` refresh logic, the hook broadcast in `server/routes/hooks.js`.
**Not reviewed:** the rest of the repository, the pricing total-cost endpoint, MCP, desktop and VS Code extension packages, deployment manifests. No code, tests or service were executed.

**Evidence available:** Uninstrumented (no runtime artifacts supplied or found for this path).
**Ranking method:** Structural signals only (static source reading).
**Reference depth:** SQLite and Node are deep-tier; reasoning follows the relational and async-and-blocking principles.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Analytics read path only (see Reviewed) |
| **Critical paths** | 1 / 1 identified |
| **Shared resources** | 2 / 2 (Node event loop; single SQLite database connection/file) |
| **Technology support** | SQLite deep, Node deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Row count of `events`, share with `tool_name` set | No evidence exists | Count query on a copy of the DB |
| Real query plans and per-query timings | No evidence exists | EXPLAIN QUERY PLAN and a timed run on a copy |
| Cost of the pricing total-cost call | Not examined | Review that endpoint |

---

## 3. Architecture overview

| Component | Technology | Support tier | Role |
|:--|:--|:--|:--|
| server | Node, Express, better-sqlite3 (synchronous) | Deep | API and hook ingest |
| datastore | SQLite (WAL mode, `server/db.js:134`) | Deep | events, sessions, agents, token_usage |
| client | React page `Analytics.tsx` | n/a | Polls and reacts to websocket frames |

**Shared resources:** the single Node event loop (all HTTP, websocket broadcast and hook ingest share it) and the SQLite database.

---

## 4. Workload model

**Known**
- `events` is an unbounded, append-only history with a `tool_name` column and no index on it — source: `server/db.js:195-206`, `:346-:406`.
- The analytics handler runs the aggregates synchronously — source: `server/routes/analytics.js:30-52`.
- Client refetches every 15 s and on new_event, session_created, session_updated, agent_created — source: `client/src/pages/Analytics.tsx:710-728`.

**Assumed**
- "Very large" means a row count where a scan takes seconds — affects PERF-001, PERF-002.
- The page is open while hooks are streaming events — affects PERF-002.

**Unknown**
- `events` row count; hook event rate; whether operators use the scoped (source/provider) path.

**Derived**
- Unscoped analytics performs at least five aggregations over `events` per call (tool usage, daily events, event types, average events per session, total events) — from `analytics.js:30-52` and `db.js:1715-1722, 1961-1985`.

**Measured**
- None.

### Questions that would change the ranking

| # | Value | Question | Dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | How many `events` rows exist, and how often do hook events arrive while the page is open? | severity, confidence | Confirms PERF-001 magnitude; could raise PERF-002 to High confidence |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | GET /api/analytics | Yes, synchronous on the event loop | 5+ full or range scans over events, plus sessions/agents/token_usage reads | No (all history, except 365-day daily buckets) | No | Called on page open, every 15 s, and per websocket event |

**Amplification points:** client reload per websocket frame (PERF-002) times per-call O(rows) scans (PERF-001).

**Paths deliberately not analyzed in depth:** pricing total-cost endpoint; per-session overview aggregates (they are session-scoped and use `idx_events_session`).

---

## 6. Layer analysis

### 6.1 Application
The handler is fully synchronous; better-sqlite3 calls block the event loop for the duration of each statement, so one slow aggregate stalls hook ingest and every other request.

### 6.3 Data access and datastore
`toolUsageCounts` (`server/db.js:1961`) filters and groups on `tool_name`, which no index covers. Other events indexes lead with `session_id`, `event_type`, `created_at` or `agent_id`. Sibling aggregates (`eventTypeCounts`, `avgEventsPerSession`, `stats.total_events`) also touch every row or index entry. A comment at `server/db.js:393` shows the project has already hit "tens of seconds and blocks the event loop" on events scans elsewhere.

### 6.7 Observability
No timing, event-loop-delay, or slow-query instrumentation was found for this route; any fix therefore cannot currently be validated from production data. Add it before or with the fix.

---

## 7. Findings

### PERF-001 — Tool-usage (and sibling) aggregates scan the whole events table on the event loop

| | |
|:--|:--|
| **Root cause** | ROOT-001 |
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | data-access |
| **Location** | `server/db.js:1961` (`toolUsageCounts`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The tool-usage query has no index on `tool_name`, no time bound and no cache, so every call reads the entire `events` table synchronously; the same handler adds several more whole-table aggregates.

**Performance principle**
Per-request work should not grow with total accumulated history, and long work must not run on a thread shared by all requests.

**Evidence**
- `server/db.js:1961`: `SELECT tool_name, COUNT(*) ... FROM events WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY count DESC LIMIT 20`. The LIMIT applies after aggregation.
- `server/db.js:346-406`: no index includes `tool_name`.
- `server/routes/analytics.js:30-52`: synchronous `.all()`/`.get()` for tool usage, daily events, event types, average events, overview.
- `server/db.js:393`: existing comment about events scans blocking the loop.
- No runtime evidence exists.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in events. Blast radius: system-wide, because the event loop is shared (this is why severity is Critical).

**Conditions**
Large `events` table (operators report "very large"; exact size unknown). Negligible on small databases.

**Counter-evidence**
Looked for an index or covering index on `tool_name`, a materialized counter, a cache and a time window: none found. The relative cost of sibling aggregates versus the tool-name scan is unknown (an event-type count may use a narrow index), which is why confidence is High rather than Confirmed.

**Why this might not matter**
On modest databases, or with a hot page cache, the scans may be fast enough. The operator report suggests that is not so for the affected deployment, but the per-query split is unmeasured.

**Recommendation**
Do not recompute history per request. Preferred: maintain tool counts incrementally (counter table updated at event insert) or refresh a snapshot periodically. Interim: add a partial index on `events(tool_name) WHERE tool_name IS NOT NULL` so the scan reads a narrow index instead of full rows, and a short-TTL single-flight cache on the analytics bundle.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Partial index on `tool_name` | | Quick, cuts I/O per scan, still O(rows) and still blocks the loop |
| Counter table or periodic snapshot | **preferred** | Removes per-request O(n) work |
| Server-side TTL cache with single-flight | | Easy, also helps PERF-002, but a miss still blocks |
| Worker thread or separate read connection | | Frees the loop but does not reduce work |

**Trade-offs**
Counter table needs backfill and consistency with deletes and the `tool_name` NULLing migration (`db.js:540`). Cache serves slightly stale charts. Any new index adds write cost on the hot ingest path.

**Validation**
Baseline: time GET /api/analytics and observe event-loop delay on a copy of the production DB (safe-on-production when run on a copy). Expectation: the tool-usage plan changes from `SCAN events` to an index scan or no scan, and loop stall falls; magnitude not derivable from code. Falsifier: the plan already uses a covering index, or profiling shows the time lies elsewhere. Command (safe-on-production, on a copy): `sqlite3 <copy-of-db> "EXPLAIN QUERY PLAN SELECT tool_name, COUNT(*) FROM events WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 20;"`.

---

### PERF-002 — Analytics page refetches the whole bundle on every websocket event

| | |
|:--|:--|
| **Root cause** | ROOT-002 |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `client/src/pages/Analytics.tsx:710` (`load`) |
| **Tags** | needs-measurement |

**Problem**
`load()` runs on mount, every 15 s, and on each new_event, session_created, session_updated and agent_created frame, with no debounce or in-flight guard.

**Performance principle**
Expensive idempotent reads should be coalesced so request rate does not track upstream event rate.

**Evidence**
- `client/src/pages/Analytics.tsx:710-728`: interval plus eventBus subscription calling `load()` directly.
- `server/routes/hooks.js:129-131`: server broadcasts per hook event.
- `client/src/pages/Analytics.tsx:692`: each load also calls the pricing total-cost API.
- `server/openapi.js:1776`: a debounce convention is documented for another panel but not applied here.

**Impact**
Position: critical path. Frequency: per request, scaling with event rate. Growth: O(n) per call (PERF-001). Blast radius: system-wide, since slow requests also delay hook ingest.

**Conditions**
Page open during active sessions with frequent hook events; event rate unknown (assumption).

**Counter-evidence**
No debounce, abort or in-flight flag found. Browser connection caps and serialized server execution bound the pile-up by tab count rather than by cost. Event rate and websocket connectivity during the freeze are unknown, hence Medium confidence.

**Why this might not matter**
On an idle system only the 15 s timer fires, and the freeze may be wholly PERF-001.

**Recommendation**
Single-flight with trailing refetch, debounce websocket-triggered reloads, pause polling when hidden, and ideally a server-side TTL cache or ETag.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Client single-flight plus debounce | **preferred** | Smallest change; bounds rate independent of event rate |
| Server TTL cache | | Also covers many tabs, stale charts, pair with preferred |
| Push incremental updates over websocket | | Largest redesign |

**Trade-offs**
Charts refresh at most once per window; server cache adds staleness questions.

**Validation**
Count GET /api/analytics requests per minute with the page open during an active session (safe-on-production). Expect the rate to become bounded by the window. Falsifier: only the 15 s poll appears even during event bursts.

---

### Remaining findings

None beyond PERF-001 and PERF-002.

### Considered and not reported

| Path | Evidence checked | Discard reason | Revisit when |
|:--|:--|:--|:--|
| Unscoped `token_usage JOIN sessions` load priced in JS (`server/routes/analytics.js:57`) | analytics.js, token_usage schema | Bounded by sessions x models x tiers, not events; unlikely to drive the symptom | token_usage is large or the pricing endpoint profiles slowly |

---

## 8. Prioritized action plan

### P0 — Immediate
### P1 — High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P0 | Medium (index is small; counter table is larger) | Root of the freeze |
| 2 | PERF-002 | P1 | Small | Quick-to-ship client change that limits repeated cost; can ship first if the index needs a migration window |

**If only one thing is done:** replace the per-request tool-usage scan with an incrementally maintained count (or at least an index plus a TTL cache).

---

## 9. Validation plan

### PERF-001
- **Baseline:** EXPLAIN QUERY PLAN and a timed GET /api/analytics on a copy of the DB — safe-on-production: yes (on a copy).
- **Change:** partial index and/or counter table; TTL cache.
- **Measurement:** request wall time; event-loop delay while the request runs.
- **Expectation:** `SCAN events` replaced by index scan or no scan; shorter loop stall. Magnitude not claimed.
- **Falsifier:** plan already covering, or profile shows time elsewhere.
- **Guard:** test asserting the plan uses an index; event-loop-delay alert on the route.
- **Commands:**
  - `safe-on-production` `sqlite3 <copy-of-db> "EXPLAIN QUERY PLAN SELECT tool_name, COUNT(*) FROM events WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 20;"` — confirm scan strategy on a copy.

### PERF-002
- **Baseline:** requests per minute to GET /api/analytics with the page open — safe-on-production: yes.
- **Change:** single-flight and debounce in `load()`.
- **Measurement:** request count and overlap.
- **Expectation:** rate bounded by the window regardless of event rate.
- **Falsifier:** only the 15 s poll appears during bursts.
- **Guard:** client test firing N frames and asserting at most one request per window.

### Instrumentation gaps to close first
No timing or event-loop-delay metric exists for this route; add one so the fix can be measured.

---

## 10. Machine-readable output

See `review.json` in the same directory (schema_version 1.0, validated).

## 11. Notes on this review

- No runtime metric was estimated; the only derived figure ("at least five aggregations") is labelled in section 4 with its inputs.
- Nothing was executed; all claims come from reading source.
