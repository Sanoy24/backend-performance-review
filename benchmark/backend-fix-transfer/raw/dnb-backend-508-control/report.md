# Performance review: badge awarding and notification list

Scope: static read of src/services/badge.service.js, its callers, the Badge/UserBadge/CourseProgress/Notification models, and src/controllers/notificationController.js (plus the bulk job). Nothing was run. The service has a TypeScript twin (src/services/badge.service.ts, src/controllers/badge.controller.ts, models *.ts); I did not diff it, so check that the same fixes land in whichever copy is actually built and deployed.

## Priority summary

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | High | Badge evaluation (with writes) runs on every badge read and every user profile read | src/services/badge.service.js:74-79, src/controllers/userController.js:142 |
| 2 | High | Per-badge sequential queries in award loop (N+1), 3 round trips per awarded badge | badge.service.js:39-69 |
| 3 | Medium | Seeding upserts executed sequentially on every call | badge.service.js:7-15, 18, 82 |
| 4 | Medium | Full CourseProgress documents + populate just to count | badge.service.js:20-23 |
| 5 | Medium | Notification list: 3 queries plus 2 populates, deep skip, count on every page | notificationController.js:298-312 |
| 6 | Low/Medium | Notification index set does not match the query shapes; redundant indexes | src/models/Notification.js:87-90 |
| 7 | Low | Notification fan-out and bulk delivery details | notificationController.js:226-230, 260-264; src/jobs/handlers.js:126-134 |

## 1. Badge evaluation on the read path (High)

`getUserBadges` (badge.service.js:74-75) calls `checkAndAwardBadges` before it does a plain read. It is called from:
- GET /badges/my-badges and GET /badges/user/:userId (src/routes/badge.routes.js:13,16),
- `getUser` in src/controllers/userController.js:142, i.e. every profile view, including views of other people's profiles by anonymous or other users.

So each badge list or profile load pays for: 5 seed upserts, a CourseProgress query with populate, a Badge query, one UserBadge lookup per eligible badge, then the real list query. In the steady state, where nothing new is awarded, all of that work is wasted, and it scales with traffic to profiles rather than with course completions. It also means GET requests perform writes (inserts) and a public route can trigger them.

Fix:
- Make `getUserBadges` a pure read: `UserBadge.find({user}).populate("badge").sort({awardedAt:-1}).lean()` (the single `{user:1, badge:1}` index covers the filter; consider `{user:1, awardedAt:-1}` for the sort).
- Move evaluation to the event that changes the inputs: call `checkAndAwardBadges` when a course is completed (where CourseProgress `completedAt`/`percentComplete` hits 100), or enqueue it via the existing job queue (src/jobs). Keep POST /evaluate as the manual trigger.
- If a read-time fallback is wanted, gate it with a short per-user Redis TTL key (src/config/redis.js already exists).
- In userController, skip badge evaluation entirely and use the read-only call, fetched in parallel with the user query.

## 2. N+1 in the award loop (High)

badge.service.js:39-69 iterates badges and, for each eligible badge, does `UserBadge.findOne` (line 49), `UserBadge.create` (52) and `UserBadge.findById(...).populate("badge")` (60), all awaited serially. A user with many eligible badges costs about 3 round trips per badge plus a populate round trip, in series. The re-fetch at line 60 is also unnecessary because the `badge` document is already in hand.

Fix:
- Do one `UserBadge.find({user}, {badge:1}).lean()` before the loop (or fold into the read) and compute the set of already-owned badge ids in memory.
- Compute the list of new awards in memory, then `UserBadge.insertMany(docs, {ordered:false})` once, tolerating E11000 duplicates (the unique `{user,badge}` index at user-badge.model.js:28 already guards races, so the `findOne` pre-check can be dropped).
- Build the response by attaching the in-memory `badge` doc rather than re-querying/populating.
Result: constant number of queries (roughly 3) regardless of badge count.

## 3. Seeding on every call (Medium)

`seedDefaultBadges` (lines 7-15) awaits one `findOneAndUpdate` upsert per default badge, sequentially, and is called at the top of `checkAndAwardBadges` (18) and `getAllBadges` (82). That is 5 write-capable round trips on every badge request, for static data. `new: true` additionally returns documents nobody uses.

Fix: seed once at startup/migration (or lazily once per process with a module-level promise/flag). If kept, use a single `Badge.bulkWrite` with upserts. Cache `Badge.find()` (a tiny, rarely changing collection) in memory or Redis with a TTL, which also removes the query at line 36 and 83 from the hot path. `getAllBadges` could be served with `.lean()` and HTTP caching.

## 4. Over-fetching progress data (Medium)

Lines 20-23 load whole CourseProgress documents, including the `lessonsCompleted` ObjectId array which can be large, and then `populate("course","category")` issues a second query, only to compute a count and per-category counts. The `$or` of `percentComplete: 100` and `completedAt: {$ne: null}` cannot use the `{user,course}` index efficiently beyond the `user` prefix, and the work is done in JS.

Fix: use an aggregation: `$match` user + completion condition, `$lookup` course category (or `$project` out `lessonsCompleted`), `$group` by category, returning counts directly. At minimum add `.select("course").lean()` and a `.lean()` on the populate. Consider denormalising `category` onto CourseProgress or maintaining completed counters on the user so the evaluation is O(1). Normalise "completed" to a single field so the `$or` goes away.

## 5. Notification list endpoint (Medium)

getUserNotifications, notificationController.js:298-312:
- Three sequential queries per request (find, `countDocuments(query)`, a second `countDocuments` for unread). The unread count is identical to `total` when `unreadOnly` is true. The two counts and the find are independent and could run with `Promise.all`.
- Two populates (`data.courseId`, `data.bookId`) plus `sender` is three additional queries per page; `sender` is `required` on every notification, but the course/book populates run even for notification types that have no course or book. With `limit` up to 100 (line 283) pages are heavy.
- Offset pagination (`skip`) at line 303 degrades linearly for users with thousands of notifications; counting all matching documents (line 307) grows too. Users with many notifications are exactly the reported case.
- No `.select()` on the notification fields, so full documents are returned.

Fix: run find and counts in parallel; reuse `total` as the unread count when `unreadOnly`; replace `total` with `hasNextPage` via `limit+1`, or use cursor pagination keyed on `createdAt/_id` (mongo/utils/cursorPagination.js already exists in this repo, as does `NotificationRepository`, which the controller bypasses); cap default limit lower; maintain or cache the unread count (Redis counter incremented on create, decremented on read) because the bell badge typically polls it. Populate only what the UI needs, or denormalise the title/thumbnail into `data`.

## 6. Notification indexes (Low/Medium)

src/models/Notification.js:87-90 declares four indexes that overlap: `{recipient,createdAt}` is a prefix-subset of nothing the list query uses (the list filters `recipient, isDeleted:false` and sorts by `createdAt`, which `{recipient:1,isDeleted:1,createdAt:-1}` at line 90 serves), `{recipient,isDeleted}` (89) is a prefix of line 90 and redundant, and `{recipient,isRead}` (88) does not cover the unread query `recipient+isRead+isDeleted`. The unread filter with the sort cannot use an index for ordering. Each redundant index also taxes every notification insert, which matters for fan-out writes.

Fix: keep `{recipient:1,isDeleted:1,createdAt:-1}`, add `{recipient:1,isDeleted:1,isRead:1,createdAt:-1}` for the unread list and count, and drop lines 87, 89 (and 88 once the new one exists). Verify with `explain()` before dropping. Also add a TTL or scheduled cleanup (NotificationRepository.deleteOlderThan exists but notes that nothing calls it), since the collection otherwise grows without bound and soft-deleted rows stay in the index.

## 7. Fan-out details (Low)

- createNewCourseNotification / createNewBookNotification (notificationController.js:222-230, 256-264) do a single `insertMany` (good) but then fire one Redis `publish` per follower with unawaited promises and no error handling, so a creator with many followers produces a burst of publishes and potential unhandled rejections; and `insertMany` of the whole follower list is unbounded. Chunk (for example 500-1000) and publish one batched message, or enqueue the work via the job queue as sendBulkNotification already does.
- The `notifications.bulk` job (src/jobs/handlers.js:106-134) loads all CourseProgress rows for a course into memory and does one unbounded `insertMany`; chunk it and use `.lean()`/a distinct query for the user ids (`CourseProgress.distinct("user", {course})`). It also calls `deliverLocalSSENotification` directly, so other instances never receive these events (a correctness gap, not performance; `dispatchSSENotification` would fix it).
- `sendNotificationToUser` (line 157-168) does create plus a separate `populate` round trip; populate sender from data already at hand as the fan-out paths do.

## Suggested order of work

1. Remove evaluation from `getUserBadges` and from the user profile path (issue 1). This is the single largest reduction in DB load.
2. Batch the award loop and cache/seed badges once (issues 2, 3).
3. Parallelise and slim the notification list query, fix indexes (5, 6).
4. Aggregation for progress counts, and fan-out batching (4, 7).
