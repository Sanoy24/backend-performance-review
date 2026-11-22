# Performance Review: commafeed-2089-treatment (feed refresh path)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the feed refresh path named in the request
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The refresh pipeline is sensibly structured (HTTP fetch, DB update and notification on separate bounded pools, and unchanged feeds are skipped early by content-hash and published-date checks). The material problem is in the database stage: each parsed entry of a changed feed gets its own transaction and its own existence query, run serially on a single updater thread by default. That matches the reported symptom for feeds with many entries and for many feeds refreshing together. Review confidence is Medium: static reading only, no measurements.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Replace the per-entry existence query with one set-based lookup per feed, then insert only missing entries | Matches the reported symptom; cost grows with entries times feeds on the shared database |
| 2 | **PERF-002 (P2)** | Hoist HTML parsing and cache compiled filter programs out of the per-subscription loop | Only matters if filters are used; confirm first |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Entries per feed and how many are already stored at each refresh | Severity of PERF-001 | Count SQL statements during one refresh cycle |
| Number of filtered subscriptions per feed | Whether PERF-002 is worth doing | Count subscriptions with a non-blank filter |
| DB engine and pool size in deployment | How costly each round trip is | Deployment config |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | `safe-on-production` | `grep -n "updater" commafeed-server/src/main/java/com/commafeed/backend/feed/FeedRefreshEngine.java` | Finds the existing updater gauges to chart during a refresh cycle as a baseline |
| PERF-001 | `safe-on-production` | Procedure: in staging, enable SQL statement logging for one refresh cycle and count SELECTs on the feed entries table per refreshed feed | Confirms or lowers PERF-001 |

---

## 2. Scope and method

**Reviewed:** `commafeed-server` feed refresh path: `FeedRefreshEngine`, `FeedRefreshWorker`, `FeedFetcher`, `FeedRefreshUpdater`, `FeedEntryService`, `FeedEntryContentService`, `FeedEntryContentCleaningService`, `FeedEntryFilteringService`, `FeedUpdateNotifier`, `FeedDAO`, `FeedEntryDAO`, `FeedEntryContentDAO`, `FeedSubscriptionDAO`, `UnitOfWork`, refresh-related configuration defaults, and Liquibase index changelogs.
**Not reviewed:** REST resources, the web client, cleanup tasks, push notification delivery internals, full `application.properties`. Nothing was run.

**Evidence available:** Partially instrumented. Dropwizard meters and gauges exist for refresh (queue size, active worker/updater/notifier threads, feedFetched, feedUpdated, entryInserted). No traces, query logs, load tests or SLOs in the repo.

**Ranking method:** Structural signals only. Rankings are inference from code.

**Reference depth:** MySQL, Postgres and JVM have deep references; no engine-specific measurement was possible.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Refresh path read in full; remainder not examined |
| **Critical paths** | 2 / 2 (refill loop, per-feed fetch-and-update) |
| **Shared resources** | 4 / 4 (database, updater pool, HTTP worker pool, striped locks) |
| **Technology support** | Deep: MySQL, Postgres, JVM |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Real feed sizes, change rate and filter usage | No evidence exists | Statement counts and a count of filtered subscriptions |
| Engine, pool size and Hibernate flush behaviour in deployment | No evidence exists | Deployment config and SQL logging |

---

## 3. Architecture overview

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| commafeed-server | Quarkus, JVM, Hibernate/QueryDSL | not examined | deep | Refresh engine and API |
| Database | MySQL/MariaDB or Postgres (CI), H2 default in prod profile | not examined | deep | Feeds, entries, contents, statuses |

**Shared resources:** the primary database; the database updater pool (default 1 thread); the HTTP worker pool (default 3 threads); Guava striped locks keyed by feed id and content hash.

---

## 4. Workload model

**Known**

- Default `databaseThreads` is 1 and `httpThreads` is 3 — source: `CommaFeedConfiguration.java`.
- Default `maxFeedCapacity` is 500 entries per feed; default refresh interval 5 minutes — source: `CommaFeedConfiguration.java`.
- Refill batch is `min(100, 3 * httpThreads)` feeds — source: `FeedRefreshEngine.java:201`.

**Assumed**

- Changed feeds mostly re-contain already stored entries — affects PERF-001.
- Some subscriptions use filters — affects PERF-002.

**Unknown**

- Entries per feed, feeds per cycle, filtered subscriptions, DB engine and pool size.

**Derived**

- At most 500 existence queries per refreshed feed at default capacity — from the `maxFeedCapacity` default and `FeedRefreshWorker.java:54-57`.

**Measured:** none. This is why nothing here is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Entries per feed, feeds per cycle, share already stored? | severity, confidence | Magnitude of PERF-001 |
| 2 | high | How many subscriptions per feed have filters? | severity, recommendation | PERF-002 up to High or dropped |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | Per-feed fetch then update (`processFeedAsync`) | Background, serial on updater thread | 1 subscription load, per entry: 1 existence query plus insert path, 1 feed merge | Entries capped by `maxFeedCapacity` | Gauges only | PERF-001, PERF-002 |
| 2 | Queue refill (`getNextUpdatable​Feeds`) | Background | 1 select, 1 bulk update per refill | Batch at most 100 | Meter `refill` | Not reported |

**Amplification points:** entries times changed feeds for existence checks; entries times filtered subscriptions for filter evaluation.

**Paths deliberately not analyzed in depth:** user-facing read APIs and cleanup tasks, outside the reported symptom.

---

## 6. Layer analysis

### 6.1 Application

Background pipeline with bounded pools; the HTTP stage and the DB stage are decoupled by a blocking executor, which gives backpressure. Per-entry CPU work (HTML cleaning, filter evaluation) runs inside the DB transaction (PERF-002).

### 6.3 Data access and datastore

Indexes exist for the hot lookups: `guidHash_feed_index` on `(guidHash, feed_id)` and `content_title_index` on `(contentHash, titleHash)`. The problem is query count, not missing indexes. Do not add indexes in response to this review.

### 6.7 Observability

Meters and thread-pool gauges exist for the refresh engine; there is no per-statement or per-feed timing, so PERF-001 cannot currently be confirmed from production telemetry.

---

## 7. Findings

### PERF-001 — Per-entry transaction and existence query during feed update

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `commafeed-server/src/main/java/com/commafeed/backend/feed/FeedRefreshUpdater.java:90` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
Every refresh of a changed feed issues one existence SELECT per parsed entry, each inside its own transaction, even when almost all entries are already stored. All of it runs serially on the updater thread (default 1).

**Performance principle**
Work should scale with what changed, not with what was re-read; round trips and transaction boundaries should be amortized over a batch.

**Evidence**
- `FeedRefreshUpdater.update` loops over entries and calls `addEntry` (line 130); `addEntry` opens `unitOfWork.call` per entry (line 90).
- Inside it `feedEntryService.find` runs `FeedEntryDAO.findExisting`, a single-row query (`FeedEntryDAO.java:25`), before any insert (`FeedRefreshUpdater.java:91`).
- Entries are only capped by `maxFeedCapacity` and `entriesMaxAge` (`FeedRefreshWorker.java:54-63`), never pre-filtered against stored entries.
- `databaseThreads` defaults to 1 (`CommaFeedConfiguration.java:233`).
- No runtime evidence exists.

**Impact**
Position: async but on the shared database and the single updater thread. Frequency: per entry. Growth: O(n) in entries per feed, multiplied by changed feeds. Blast radius: system-wide for refresh latency and database load. Derived bound: at most 500 queries per feed at default capacity.

**Conditions**
Feeds carrying tens to hundreds of entries, many changing per cycle. Assumed, not measured, that changed feeds mostly contain stored entries.

**Counter-evidence**
Unchanged feeds are skipped before this loop via content-hash and published-date checks (`FeedFetcher.java:82-90`), which bounds impact. Each query is an index hit. No batched or cached existence check was found.

**Why this might not matter**
Indexed point lookups in background work with a 5 minute interval may be negligible for small feeds, mostly unchanged feeds or an embedded database.

**Recommendation**
Fetch the feed's existing guid hashes once per refresh (one query keyed by feed, bounded by capacity, or an IN list), discard entries already present, and run the transactional insert path only for the remainder, keeping the in-lock re-check for those so concurrent refreshes stay correct.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Single set-based lookup per feed | **preferred** | Removes the redundant work |
| Raise `databaseThreads` | | Parallelizes the same redundant queries; more DB load |
| One transaction per feed | | Fewer commits but longer lock hold, still N queries |

**Trade-offs**
IN-list size limits and the need to re-verify under lock; modest added code in the updater.

**Validation**
Metric: SQL statements per refreshed feed and updater active time. Expectation: statement count for an all-stored feed drops from about one per entry to a small constant (direction only). Falsifier: statement logging shows these queries are a negligible share of refresh time. Safety: `safe-on-production` (read-only observation).

---

### PERF-002 — Filter evaluation repeats parsing and compilation inside the transaction

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `commafeed-server/src/main/java/com/commafeed/backend/service/FeedEntryFilteringService.java:51` |
| **Tags** | needs-measurement |

**Problem**
For each new entry and each subscription with a filter, title and content are re-parsed with Jsoup, the expression is recompiled, and evaluation is submitted to a cached thread pool, all while the transaction and striped locks are held on the updater thread.

**Performance principle**
Loop-invariant and per-item CPU work should not be repeated or run while holding scarce resources such as a transaction and locks.

**Evidence**
- `addEntry` calls `applyFilter` for every subscription inside the transaction (`FeedRefreshUpdater.java:98`).
- `Jsoup.parse` on title and content per call (`FeedEntryFilteringService.java:56-58`); the result does not depend on the subscription.
- `CEL_COMPILER.compile(expression)` on every evaluation (line 96); blank filters return early (line 52).
- No runtime evidence.

**Impact**
Position: async. Frequency: per new entry per filtered subscription. Growth: O(n). Blast radius: service (updater thread). Only new entries pay this.

**Conditions**
Feeds with many new entries (a newly added feed, bursts) and several filtered subscriptions. Unknown whether filters are used.

**Counter-evidence**
Cost applies only to subscriptions with a filter and only to inserted entries. No caching of parsed text or compiled programs was found.

**Why this might not matter**
Few filters or small bursts make the CPU cost small compared with network fetch time.

**Recommendation**
Compute the text fields once per entry, cache compiled programs per filter string, and move evaluation outside the striped locks where possible.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Hoist text extraction and cache compiled programs | **preferred** | Local change that removes the repetition |
| Evaluate asynchronously after commit | | Changes read-state semantics |

**Trade-offs**
Cache needs a bound and invalidation on filter edits; moving evaluation out of the transaction must keep status rows consistent with the entry.

**Validation**
Metric: transaction duration in `addEntry` for a feed with many filtered subscriptions. Expectation: decreases as filtered subscriptions grow (direction only). Falsifier: a profile shows filter evaluation is a negligible share. Safety: `not-safe-on-production` (profiling).

---

### Remaining findings

None beyond the two above.

### Considered and not reported

| Path / resource | Evidence checked | Discard reason | Revisit when |
|:--|:--|:--|:--|
| Queue refill query (`FeedDAO.findNextUpdatable`, DISTINCT join) | `FeedRefreshEngine` batch size, `disableduntil_index` | Runs once per empty queue, at most 100 feeds | Plans on a very large subscription table show it dominating |
| Content lookup and persist per new entry (`FeedEntryContentService.findOrCreate`) | `content_title_index`, DAO | Indexed, only for new entries; subsumed by PERF-001 | Statement logs show content lookups far exceeding new entries |

---

## 8. Prioritized action plan

### P1 — High priority
### P2 — Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P1 | Medium | Matches the reported symptom |
| 2 | PERF-002 | P2 | Low to medium | Gate on whether filters are used |

**If only one thing is done:** measure statements per refreshed feed, then replace the per-entry existence query with one lookup per feed (PERF-001).

---

## 9. Validation plan

### PERF-001

- **Baseline:** chart the updater active gauge and count SELECTs per refreshed feed in a staging run — `safe-on-production: yes` for the gauge, staging only for statement logging.
- **Change:** one set-based existence lookup per feed.
- **Measurement:** SQL statements per feed; updater active time.
- **Expectation:** statement count stops growing with entry count for already-stored entries.
- **Falsifier:** these queries are a negligible share of refresh time.
- **Guard:** integration test asserting statement count is constant for an all-existing feed.
- **Commands:**
  - `safe-on-production` `grep -n "updater" commafeed-server/src/main/java/com/commafeed/backend/feed/FeedRefreshEngine.java` — locate the gauges used as a baseline.

### PERF-002

- **Baseline:** profile `addEntry` for a feed with many filtered subscriptions — `not-safe-on-production`.
- **Expectation:** shorter transaction per new entry (direction only).
- **Falsifier:** filter evaluation is negligible in the profile.
- **Guard:** benchmark of `filterMatchesEntry` with N subscriptions.

### Instrumentation gaps to close first

No per-feed update duration or per-statement counts exist. Add a timer around `FeedRefreshUpdater.update` and enable statement counting in staging before changing code.

---

## 10. Machine-readable output

See `review.json` in the same directory (schema version 1.0, validated).

---

## 11. Notes on this review

- Findings are classified by evidence grade; none is `Confirmed`.
- No runtime metric in this report was estimated; the only derived number (at most 500 queries per feed) is labelled and comes from the configuration default.
