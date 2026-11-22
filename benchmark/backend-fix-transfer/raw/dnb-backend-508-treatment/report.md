# Performance Review: dnb-backend (badge award and notification list)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the operators' report (badge awarding, notifications list)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The notification list is largely sound: its main query is indexed for filter and sort and the page size is capped at 100. The badge path is the real source of avoidable database load: every badge read, including the public unauthenticated catalog and the user profile, first re-runs the full award evaluation (PERF-001). Both findings come from static reading only; no measurements exist, so review confidence is Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P2)** | Make `getUserBadges` and `getAllBadges` pure reads; seed the catalog once at startup; run award evaluation on course completion with batched queries. | Removes the work rather than speeding it up; derived from code to be up to 15 round trips versus 2 per read. |
| 2 | **PERF-002 (P3)** | Run `explain('executionStats')` on the unread count for a heavy user before changing anything. | Cheap and safe; decides whether an index is justified. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Call rate of badge endpoints and the user profile endpoint | PERF-001 severity (Medium vs High) | Access logs or APM request counts |
| Notifications per recipient (typical and maximum) | Whether PERF-002 needs an index | Per-recipient count aggregation |
| Whether custom badges exist beyond the 5 defaults | Round-trip count in PERF-001 | `Badge.countDocuments()` |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-002 | safe-on-production | `db.notifications.find({ recipient: ObjectId('<id>'), isRead: false, isDeleted: false }).explain('executionStats')` | docsExamined far above the count justifies the index; near it means do nothing. |
| PERF-001 | not-safe-on-production | `db.setProfilingLevel(2)` on staging, call the endpoint once, then count `db.system.profile` entries for the badge collections | Confirms the derived 15 commands per read. |

---

## 2. Scope and method

**Reviewed:** `src/services/badge.service.js`, `src/controllers/badge.controller.js`, `src/routes/badge.routes.js`, `src/routes/notificationRoutes.js`, `src/controllers/notificationController.js` (list handler), models for Badge, UserBadge, CourseProgress, Notification, `src/utils/badge-criteria.js`, and callers of the badge service (`src/controllers/userController.js`).
**Not reviewed:** The rest of the repository (payments, courses, Stellar, `contracts/` Rust, `sdk/`), the SSE notification stream, notification creation and bulk send paths, and `.ts` duplicates beyond confirming they mirror the `.js` logic.

**Evidence available:** Uninstrumented for this review. A Mongoose slow-query plugin exists (`src/config/db.js`) but no output was supplied.

**Ranking method:** Structural signals only (source reading); the ranking is inference.

**Reference depth:** MongoDB, Node and REST at deep tier. Detection found a Rust service (`contracts`) and an `sdk` service; neither is on the reviewed paths.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Badge and notification-list paths read in full; remainder not examined |
| **Critical paths** | 4 / 4 (GET badges catalog, GET user badges, GET user profile, GET notifications) |
| **Shared resources** | 1 / 1 (the MongoDB primary) |
| **Technology support** | MongoDB Deep, Node Deep, REST Deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Per-user counts of badges, notifications, completed courses | No evidence exists | Count aggregation per user on the three collections |
| Query plans and slow-query output | No evidence exists | `explain` and the slow-query log |
| Request rates | No evidence exists | Access logs or APM |
| Custom badges beyond defaults | No evidence exists | `Badge.countDocuments()` |

---

## 3. Architecture overview

Express REST API on Node (ES modules, entry `server.js`) over MongoDB through Mongoose, with Redis present and a Kubernetes deployment. Routes are mounted under `/api/v1` (`/badges`, `/notifications`).

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| API | Express | not examined | Deep | HTTP surface |
| Datastore | MongoDB via Mongoose | not examined | Deep | Badges, user badges, progress, notifications |

**Shared resources:** the MongoDB primary and its connection pool, used by all endpoints.

---

## 4. Workload model

**Known**

- 5 default badges are defined in code, so the catalog is small by default — source: `src/utils/badge-criteria.js`
- Notification list page size is capped at 100 — source: `src/controllers/notificationController.js:283`
- No TTL or scheduled cleanup for notifications found — source: `mongo/repositories/NotificationRepository.js:324`

**Assumed**

- Only default badges exist — affects: PERF-001
- Notification history per user grows without bound — affects: PERF-002

**Unknown**

- Request rates for badge, profile and notification endpoints; per-user volumes.

**Derived**

- At most 15 database round trips per steady-state badge read — from: 5 serial seed upserts, 1 progress find, 1 populate, 1 badge find, up to 5 per-badge existence checks, 1 user-badge find, 1 populate (5 default badges).

**Measured**

- None. An empty list is why no finding is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | How often are the badge endpoints and profile endpoint called, and is any polled? | severity, confidence | PERF-001 toward High if polled or crawled |
| 2 | medium | How many notifications does a heavy recipient hold, and is the unread count in the slow-query log? | severity, recommendation | PERF-002 stays P3 or earns an index |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | GET `/badges/user/:userId` (public) and `/badges/my-badges` | Yes | Up to 15 incl. writes | Catalog fixed, progress by completed courses | Slow-query plugin only | Runs award evaluation on read |
| 2 | GET user profile | Yes | Badge read plus user read | As above | Same | Calls `getUserBadges` |
| 3 | GET `/badges` (public) | Yes | 5 upserts plus 1 find | Yes | Same | Seed on every call |
| 4 | GET `/notifications` | Yes | Page find, up to 3 populates, 2 counts | Page capped at 100 | Same | Indexed main query |

**Amplification points:** the per-badge `findOne` in a loop (up to 5) and the 5 serial seed upserts per request.

**Paths deliberately not analyzed in depth:** notification creation, SSE, bulk send; outside the operators' report.

---

## 6. Layer analysis

### 6.3 Data access and datastore

Badge read path: `getUserBadges` calls `checkAndAwardBadges`, which seeds, loads full completed progress documents, loads all badges, checks each eligible badge individually and may create and re-read awards. Indexes exist for every lookup (unique `user+badge`, unique `user+course`, unique `slug`), so the problem is round-trip count and write work on reads, not scans.

Notification list: filter and sort match `recipient+isDeleted+createdAt`; the `isRead` filter for the unread count lacks a covering index.

### 6.7 Observability

A slow-query logging plugin exists in `src/config/db.js`, which is the natural place to start measuring. No metrics or traces were found for these endpoints.

---

## 7. Findings

### PERF-001 — Award evaluation runs on every badge read

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/services/badge.service.js:17` (`BadgeService.checkAndAwardBadges`) |
| **Tags** | quick-win, scalability-risk, needs-measurement |

**Problem**
Every badge read (user badges, public catalog, user profile) first runs the full award evaluation: 5 upserts, a full progress load, a badge load and a per-badge existence query. Mostly it changes nothing.

**Performance principle**
Do work when its inputs change, not on every read; do not repeat idempotent setup per request; avoid per-item round trips when a set-based query answers.

**Evidence**
- `getUserBadges` awaits `checkAndAwardBadges` before reading (`badge.service.js:74`).
- `seedDefaultBadges` does one awaited `findOneAndUpdate` upsert per default badge (`badge.service.js:7`), and `getAllBadges`, a public route, does the same (`badge.service.js:81`).
- All completed `CourseProgress` documents are loaded whole, including `lessonsCompleted`, then populated; only a count and a category tally are used (`badge.service.js:20`).
- A `UserBadge.findOne` is awaited inside the badge loop (`badge.service.js:49`).
- The profile endpoint calls `getUserBadges` (`userController.js:142`); `GET /badges/user/:userId` has no auth middleware (`badge.routes.js:13`).
- Derived: 5 default badges give at most 5 + 1 + 1 + 1 + 5 + 1 + 1 = 15 round trips per steady-state read, versus 2 for a pure read.
- No runtime evidence exists.

**Impact**
Position: critical path. Frequency: per request, and per badge within it. Growth: O(n) in completed courses and badges. Blast radius: service, since all requests share the database. The derivation assumes only default badges.

**Conditions**
Badge or profile endpoints called often (page loads, polling, or crawling of the public route) by users with several completed courses and badges. Request rate is unknown.

**Counter-evidence**
- All lookups use indexes and the catalog is 5 items by default; bounded (reflected in Medium severity).
- No memoised seed flag or cache found around these calls.
- No award-on-completion hook exists: only HTTP controllers and the profile call `checkAndAwardBadges`, so the read-time check is currently the only award path.

**Why this might not matter**
Each statement is indexed and the catalog is five items; if badge reads are infrequent this will not register against the rest of the database load.

**Recommendation**
Separate awarding from reading. Seed once at startup or via migration. Make `getUserBadges` and `getAllBadges` pure reads. Run evaluation at course completion (or via `POST /badges/evaluate`), and there replace the per-badge `findOne` loop with one `UserBadge.find` for existing badge ids plus one `insertMany` of missing ones, using a lean projected progress query (select `course`, omit `lessonsCompleted`) or an aggregation for the counts.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Award on course completion, reads pure | **preferred** | Removes the work; reads drop to two queries |
| Keep on-read evaluation, seed once and batch queries | | Cuts to roughly 5 round trips but keeps write work on a public read |
| Cache evaluation result per user | | Masks the work, adds staleness and invalidation |

**Trade-offs**
Needs a completion hook and a backfill for existing completions; startup seeding adds a boot-time database dependency.

**Validation**
Baseline: count MongoDB commands per badge read in staging. Expectation: fewer round trips (direction only; the derived minimum is 2 versus up to 15). Falsifier: far fewer commands than derived, or negligible request rate. Guard: a test asserting `getUserBadges` issues no writes and a bounded query count.
- `not-safe-on-production`: `db.setProfilingLevel(2)` on staging, call the endpoint, count `db.system.profile` entries for the badge collections.

---

### PERF-002 — Notification list counts on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `src/controllers/notificationController.js:307` (`getUserNotifications`) |
| **Tags** | needs-measurement, scalability-risk |

**Problem**
After the page query, two `countDocuments` run serially. The unread count filters `recipient`, `isRead`, `isDeleted`, and no index holds all three, so documents are fetched to apply the remaining predicate.

**Performance principle**
Per-request work should not grow with a user's whole history when one page is returned; keep filters answerable from one index.

**Evidence**
- Sequential counts at `notificationController.js:307-312`.
- Indexes: `recipient+createdAt`, `recipient+isRead`, `recipient+isDeleted`, `recipient+isDeleted+createdAt` (`Notification.js:87-90`).
- No TTL or scheduled cleanup found (`NotificationRepository.js:324` describes manual cleanup).
- No runtime evidence.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in a recipient's notifications. Blast radius: endpoint.

**Conditions**
Recipients with thousands of notifications or more, with the list loaded frequently. Volumes unknown; unbounded growth is assumed because no cleanup was found.

**Counter-evidence**
- The page query matches `recipient+isDeleted+createdAt` and the limit is capped at 100.
- Counts are bounded by the recipient prefix, not the collection.
- Populate is batched per path: three extra queries regardless of page size.

**Recommendation**
Measure first. If the unread count appears in slow-query output, add an index on `recipient, isDeleted, isRead`, and if still costly run the counts concurrently or keep a per-user unread counter. Otherwise take no action.

**Trade-offs**
Index write amplification and memory; a counter risks inconsistency with read, delete and bulk paths.

**Validation**
- `safe-on-production`: `db.notifications.find({ recipient: ObjectId('<id>'), isRead: false, isDeleted: false }).explain('executionStats')` — compare `docsExamined` with the returned count. Falsifier: near-equal values, or the query absent from the slow log.

---

### Remaining findings

None beyond the two above.

### Considered and not reported

| Path | Evidence checked | Discard reason | Revisit when |
|:--|:--|:--|:--|
| Notification page query and populates (`notificationController.js:298`) | `Notification.js:90`, limit cap at line 283 | Indexed filter and sort, capped page, batched populate | Slow log shows page query examining far more documents than returned, e.g. very deep skips |
| `.ts` duplicates of badge service/controller | `badge.routes.js` imports `.js`; `package.json` main and type | Same logic, not the deployed code as far as seen | A build step compiling the `.ts` files is found |

---

## 8. Prioritized action plan

### P2 — Medium priority
### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P2 | Medium | Largest avoidable database work on read paths, including a public route |
| 2 | PERF-002 | P3 | Low to measure | Run `explain` first; act only if it shows scanning |

**If only one thing is done:** stop running `checkAndAwardBadges` and the 5 seed upserts inside read methods (PERF-001).

---

## 9. Validation plan

### PERF-001

- **Baseline:** database commands per badge read and per profile read in staging — not-safe-on-production (profiling level change).
- **Change:** reads become pure; award on completion with batched queries.
- **Measurement:** command count per request; write operations during GET requests should be zero.
- **Expectation:** fewer round trips per read; derived minimum 2 versus up to 15.
- **Falsifier:** capture shows far fewer than 15, or badge endpoints are rarely called.
- **Guard:** test asserting no writes and a bounded query count in `getUserBadges`.
- **Commands:**
  - `not-safe-on-production` `db.setProfilingLevel(2)` then count `db.system.profile` entries for `userbadges`, `badges`, `courseprogresses` — purpose: count commands per request in staging.

### PERF-002

- **Baseline:** `explain('executionStats')` on the unread count for a heavy user — safe-on-production: yes.
- **Change:** index or counter only if justified.
- **Measurement:** `docsExamined` versus returned count.
- **Expectation:** an index lowers `docsExamined` if it greatly exceeds the count; no magnitude derivable.
- **Falsifier:** near-equal values.
- **Commands:**
  - `safe-on-production` `db.notifications.find({ recipient: ObjectId('<id>'), isRead: false, isDeleted: false }).explain('executionStats')` — purpose: compare docs examined with result count.

### Instrumentation gaps to close first

The existing slow-query plugin in `src/config/db.js` logs slow operations; enable its output for these collections and add per-endpoint request counts and latency, so that the request-rate unknown can be answered.

---

## 10. Machine-readable output

See `review.json` alongside this report (schema version 1.0, validated).

---

## 11. Notes on this review

- No runtime metric was measured. The only number derived (15 round trips) is labelled a derivation with its inputs in PERF-001.
- No command or code from the repository was run.
