# Performance review: feed refresh path (CommaFeed server)

Static review only; nothing was run. Paths are from the repo root. Prefix `commafeed-server/src/main/java/com/commafeed/backend/` is abbreviated `B/`; resources are under `commafeed-server/src/main/resources/`.

Flow: `B/feed/FeedRefreshEngine.java` refill -> `FeedRefreshWorker.update` (HTTP, 3 threads) -> `FeedRefreshUpdater.update` (DB, **1 thread by default**) -> notifier. The DB stage is the bottleneck, and it is written to do many small round trips per entry.

## Priority list

| # | Severity | Issue | Location |
|---|---|---|---|
| 1 | High | One transaction and one existence query per entry, even for entries already stored | `B/feed/FeedRefreshUpdater.java` 65-105, 130-142; `B/service/FeedEntryService.java` 36-39; `B/dao/FeedEntryDAO.java` 25-27 |
| 2 | High | Single DB thread by default, with per-entry locking and hashing inside the hot loop | `CommaFeedConfiguration.java` 233 (`databaseThreads` default 1); `FeedRefreshUpdater.java` 72-87 |
| 3 | High | Content dedup does a lookup query per new entry plus a full-content comparison; no batching | `B/service/FeedEntryContentService.java` 147-159; `B/dao/FeedEntryContentDAO.java` 104-106 |
| 4 | Medium | Per-entry x per-subscription filter evaluation: Jsoup re-parse, CEL recompile, thread hand-off each time | `B/service/FeedEntryFilteringService.java` 51-99; `FeedEntryService.java` 55-70 |
| 5 | Medium | Subscriber list loaded in its own transaction; feed row merged every refresh; refill query uses DISTINCT + join | `FeedRefreshUpdater.java` 132, 160; `B/dao/FeedDAO.java` 31-44 |
| 6 | Medium | Cleanup tasks delete row-by-row via entity loads | `B/dao/FeedEntryDAO.java` 46-78; `B/dao/FeedEntryContentDAO.java` 108-117 |
| 7 | Low | Refill queue scans, notifier per-subscription work | `B/feed/FeedRefreshEngine.java` 262-290; `B/feed/FeedUpdateNotifier.java` 37-48 |

## 1. Per-entry transaction and existence query (High)

`FeedRefreshUpdater.update` loops over every entry the parser returned (up to `maxFeedCapacity`, default 500, per `FeedRefreshWorker.java` 54-57) and calls `addEntry` (line 134). Each call opens its own transaction via `unitOfWork.call(...)` (line 90). Inside it `feedEntryService.find` (line 91) runs `FeedEntryDAO.findExisting` (`FeedEntryDAO.java` 26): a `select ENTRY ... where guidHash=? and feed=? limit 1`, loading the whole entity.

Consequences:
- A feed with N entries costs N transactions and N selects on every refresh, even when nothing is new, which is the common case for a changed-but-mostly-same feed. A 500-entry feed means 500 begin/commit cycles plus 500 queries per refresh; with many feeds this is the "heavy DB load".
- Each new entry then does separate persists (content, entry, status rows) flushed inside its own transaction, so there is no JDBC batching and one commit (fsync) per entry. The default prod DB is H2 file (`application.properties` line 73), where commit cost is significant.

Fix:
- Fetch all existing guid hashes for the feed once: `select guidHash from FEEDENTRIES where feed_id = ? and guidHash in (:hashes)` (the `guidHash_feed_index` from `db.changelog-1.2.xml` 117-121 covers it), or load the hashes for the feed's latest N entries. Select only the hash column, not the entity.
- Filter the incoming list to new entries in memory, then insert the new ones in a single transaction (or chunks of ~50), with Hibernate JDBC batching enabled (`quarkus.hibernate-orm.jdbc.statement-batch-size`, `order_inserts`). Sequence/id generation should also be checked to be batch-friendly.
- Also skip the whole loop when the worker already knows nothing changed (the content hash is already compared in the fetcher, which is fine).

## 2. Single DB thread, lock/hash overhead in the hot loop (High)

- `databaseThreads` defaults to 1 (`CommaFeedConfiguration.java` 231-233) while `httpThreads` is 3. `FeedRefreshEngine.createExecutors` (line 202) uses a blocking executor, so when the DB stage is slow the HTTP workers block (line 364-372 rejection handler `put`s), throttling the entire refresh. With issue 1 making each feed slow, the throughput ceiling is one feed's DB time at a time. Fixing issue 1 helps first; then raise the default or document tuning.
- Inside `addEntry` for every entry, including ones that already exist: computes a SHA-1 over `content + title` (line 77; content can be large), takes two striped locks via `bulkGet` (79-81), and `tryLock` with up to 1 minute wait each (86-87). The feed-level key (`key1`) is locked per entry instead of once per feed, so it does not even prevent two refreshes of the same feed interleaving between entries. Move the feed lock to wrap the whole `update`, lock the content key only for entries that are actually new, and compute the digest only then.
- Lock timeout path (106-107, 151-154) sets `disabledUntil` to the minimum so the feed is requeued immediately; under contention this can cause repeated full re-processing of the same feed (amplifying issue 1).

## 3. Content dedup per new entry (High for first fetch / big feeds)

`FeedEntryContentService.findOrCreate` (147-159) first builds the full cleaned content (`buildContent`, line 161-187: Jsoup-based `cleaningService.clean` on title, body, author, media description), then runs `findExisting(contentHash, titleHash)` (a select that returns full `FeedEntryContent` rows including the large `content` columns, `FeedEntryContentDAO.java` 104-106) and compares fields with `equivalentTo`. Cleaning and the select happen before knowing whether a duplicate exists; this is the case for each new entry (and is where a newly subscribed 500-entry feed spends its time).

Fix: hash the raw content/title (already cheap) and look up by hash first, selecting only `id` (and verify equality only on hash match); do the Jsoup cleaning only when inserting a new content row. Batch the hash lookups for all new entries of a feed in one `IN` query. The (contentHash,titleHash) index exists (`db.changelog-1.2.xml` 99-100), so the query shape is fine, the volume is the problem. The method comment says it is "NOT thread-safe", which is why the content lock exists; batching must keep that guarantee (or add a unique constraint and handle conflicts).

## 4. Filter evaluation cost (Medium; severe for users with filters)

`FeedEntryService.applyFilter` (55-70) is called for every new entry for every subscription (`FeedRefreshUpdater.java` 98-103). For subscriptions with a filter, `FeedEntryFilteringService.filterMatchesEntry`:
- Re-parses the title and body with Jsoup (lines 56, 58) per subscription, although they depend only on the entry. Compute the text/lowercase variants once per entry and share across subscriptions (also skip entirely when all filters are blank, which the code already does cheaply).
- Compiles the CEL expression and creates a program on each evaluation (96-97). Cache compiled `Program`s keyed by the expression string (e.g. a bounded Caffeine/Guava cache).
- Submits each evaluation to an unbounded `Executors.newCachedThreadPool()` (line 48, 78) to implement a timeout; many concurrent evaluations can spawn many threads, and a timed-out future is not cancelled (no `future.cancel(true)`, line 87), so runaway expressions keep burning CPU. Use a bounded pool and cancel on timeout.
Also, when a filter does not match, a `FeedEntryStatus` row is persisted individually (66); batch these.

## 5. Other per-refresh DB work (Medium)

- `FeedRefreshUpdater.java` 130-133: subscriptions are loaded lazily inside the loop with a separate transaction on the first entry (a feed with zero entries skips it correctly). Load once, outside the per-entry loop, in the same transaction as the inserts. `FeedSubscriptionDAO.findByFeed` (77-79) returns full subscription rows; `feed_id` should be indexed (FK) - verify the index exists on the target DB, since `FEEDSUBSCRIPTIONS.feed_id` is not explicitly indexed in the changelogs I checked.
- Line 160: `feedService.update(feed)` does `merge` of the full Feed on every refresh, also on not-modified/error paths (`FeedService.java` 51-58), re-normalizing URL and re-hashing each time. Acceptable cost per feed, but combine with issue 1 into one transaction per feed and skip recomputing the normalized URL unless the URL changed.
- `FeedDAO.findNextUpdatable` (`FeedDAO.java` 31-44): `select distinct FEED ... join SUBSCRIPTION ... order by disabledUntil limit N`. The `distinct` + join over all subscriptions forces a sort/dedupe over all due feeds, and with `userInactivityPeriod` set it also joins users. On installations with many subscriptions per feed this is slower than `where exists (select 1 from subscription where feed = f)`. Use `EXISTS` (as `findWithoutSubscriptions` already does in the other direction) and keep the `disabled_lastupdated_index`/`disableduntil_index` usable. It runs each refill (every 15s when the queue is empty, `FeedRefreshEngine.java` 240-260).
- `FeedRefreshEngine.getNextUpdatableFeeds` (311-323) is fine (bulk `setDisabledUntil`), batch cap 100 (325-327).

## 6. Cleanup tasks (Medium, indirect load)

These compete with refresh for the single H2 database:
- `FeedEntryDAO.delete(feedId, max)` (46-49), `deleteEntriesOlderThan` (54-65), `deleteOldEntries` (70-78) load entity lists then `entityManager.remove` each, cascading `REMOVE` to `statuses` and `tags` (`FeedEntry.java` 54-58), which issues per-row selects and deletes (N+1). Use bulk `delete ... where` queries (delete statuses/tags first via subquery), or at least batch by id lists.
- `FeedEntryContentDAO.deleteWithoutEntries` (108-117): left join anti-join over all contents each run (`OrphanedContentsCleanupTask`, every 60 minutes); use `NOT EXISTS` and delete in bounded batches. Note `FeedEntry.content` has no explicit index on `content_id` in the changelogs I saw (FK only); check, since this join and content-deletion FK checks depend on it.
- `findFeedsExceedingCapacity` (29-44) groups the entire entries table; consider counting only for feeds that just received inserts.

## 7. Minor

- `FeedRefreshEngine.refillQueueAsync` (279-283): `queue.stream().noneMatch` per feed is O(n^2), but batch is capped at 100 so negligible. `refreshImmediately` `removeIf` (265) similar.
- `FeedUpdateNotifier.sendPushNotifications` (37-48): one `unitOfWork.call` per subscription to load user settings; cache per user for the duration of a refresh. Only when push notifications are enabled.
- The default HTTP/DB thread ratio (3:1) means the HTTP stage will usually be idle waiting on the DB stage; tune together.

## Recommended order of work

1. Replace per-entry find+transaction with a single hash lookup per feed and one insert transaction (issue 1), with JDBC batching on.
2. Restructure locking (per feed once, content lock only for new entries) and avoid hashing existing entries (issue 2).
3. Move Jsoup cleaning after the dedup check and select ids only (issue 3).
4. Cache CEL programs, share parsed entry text across subscriptions, bound the filter executor (issue 4).
5. Raise `databaseThreads` default after the above; rewrite refill query and cleanup deletes (issues 5-6).

Caveat: no profiling or SQL logs were available, so severity is based on query counts and transaction structure in the code. Index existence was checked only in the Liquibase changelogs (`guidHash_feed_index`, content hash index, `disableduntil_index` exist).
