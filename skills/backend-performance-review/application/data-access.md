# Data access

The layer between application code and the datastore. This is where the highest density of
real backend performance findings lives, because an abstraction that hides remote calls
makes them easy to write accidentally.

Engine-specific behavior belongs in `databases/*` and `technology/*`. This file is about
the application-side patterns that produce the load.

---

## 1. The core question

**How many round trips does this code path issue, and how does that number scale with the
data?**

Read every data-access path with that question. An abstraction that makes a remote call
look like a local attribute access is convenient and dangerous in equal measure.

---

## 2. N+1, and why ORMs make it invisible

The pattern: fetch a collection, then issue one query per element.

The explicit form is easy to spot — a query inside a loop. The dangerous form is implicit:

```
orders = repo.list_orders(...)          # 1 query
for order in orders:
    total += order.customer.discount    # 1 query per order, triggered by attribute access
```

Nothing in the loop body looks like I/O. Detection strategies:

- Read the model/entity definitions for relationship declarations and their loading
  strategy. A relationship without explicit eager loading is lazy in most ORMs.
- Look for attribute or property access on related entities inside loops and inside
  serializers.
- Look for the codebase's eager-loading idiom and note where it is *absent* on paths that
  traverse relationships.
- Check serializers, response schemas, and template rendering — queries issued during
  rendering are outside the handler and easy to miss entirely.
- If a query-count test or query log exists, use it. That converts confidence from `High`
  to `Confirmed`.

Related failure modes worth separating in the report:

- **Over-eager loading.** The opposite error: eagerly joining relations the response never
  uses, producing wide result sets and expensive joins.
- **The cartesian explosion.** Eagerly joining two or more one-to-many relations in one
  query multiplies rows. The query count drops to one and the row count explodes, which
  frequently looks like a fix and is not.

---

## 3. Query shape

- **Selecting all columns** when a few are needed. Costs more read I/O, more transfer, more
  deserialization — and can prevent an index-only path.
- **Filtering in application code** after fetching more than needed. Move the predicate to
  the query.
- **Sorting in application code** when the datastore could sort using an index.
- **Counting by materializing.** Fetching rows to count them is O(n) transfer for a number.
- **Existence checks by full fetch.** A limited or dedicated existence query suffices.
- **Aggregation row by row** in application code.
- **Joining collections in a loop** rather than fetching both and joining by key in memory
  (O(n+m)) or in the datastore.

Each is a case of `work-and-algorithms.md` §4 — work in the wrong place.

---

## 4. Batching and bulk operations

Where a loop of single-item operations exists, check whether a batched form is available:

- Multi-key fetch instead of per-key fetch.
- Bulk insert/update instead of per-row statements.
- A single query with a set-membership predicate instead of one query per id.
- The dataloader pattern for resolver-level batching.

Two caveats to state whenever recommending batching:

- Very large set-membership lists can degrade planning or exceed parameter limits; batch in
  chunks.
- A bulk write is one failure unit unless partial failure is explicitly handled.

---

## 5. Transactions

Transaction scope is a performance concern, not only a correctness one, because it
determines how long locks are held.

Findings to look for:

- **A transaction spanning an external call.** Lock duration becomes dependent on a third
  party's latency. Severe and common; treat as a shared-resource finding.
- **A transaction spanning user interaction or a long computation.**
- **Transaction-per-item in a loop**, where one transaction would do — or the reverse, one
  giant transaction that holds locks across a huge batch.
- **Read-only work inside a write transaction**, extending its duration for no benefit.
- **Missing transactions**, where a multi-statement operation retries partially, causing
  duplicate work.

Isolation level affects contention: stricter isolation means more blocking or more retry.
If the code sets a non-default isolation level, check whether it needs to.

---

## 6. Connection and session handling

- Is a connection acquired for the whole request, or only for the duration of the query?
  Holding across non-database work wastes pool capacity — see
  `application/connection-pools.md`.
- Are read-only queries directed to a replica, where one exists? And does the code handle
  replication lag, or does it read-after-write from a replica and get stale data?
- Are prepared statements reused, or re-prepared per call?
- Does the code open a connection per operation instead of using the pool?

---

## 7. Caching at the data-access layer

Only after removing and bounding work. When it is genuinely warranted, the review must
address:

- **Hit rate.** Without an estimate, the benefit is unknown. What makes you think this key
  will be requested again before it expires?
- **Invalidation.** How does the cache learn the underlying data changed? "TTL only" is a
  valid answer if staleness is acceptable — say how stale is acceptable, and to whom.
- **Key granularity.** Too coarse and every write invalidates everything; too fine and the
  hit rate collapses.
- **Stampede.** What happens when a popular key expires and many requests miss at once?
- **Consistency.** Which readers can tolerate stale data, and which cannot?

An ORM's built-in first- or second-level cache deserves particular scrutiny: it can silently
mask an N+1 in development and expose it in production, where the process is short-lived or
the cache is per-request.

See `distributed/caching.md`.

---

## 8. Migrations and schema operations

- Does a migration lock a table in a way that blocks writes on a live system?
- Is a large backfill throttled, or does it run at full speed against production?
- Are index builds using an online or concurrent path?
- Do migrations run at application startup, serializing deployments and delaying readiness?

---

## 9. What to look for in a review

- Query count per path and its scaling with result size.
- Lazy-loaded relationships accessed in loops or in serializers.
- Over-eager loading and cartesian row explosions.
- Column selection, predicate placement, sort placement.
- Loops of single-item operations where a bulk form exists.
- Transaction scope, especially transactions containing external calls.
- Connection hold duration relative to actual query time.
- Read/write splitting and replication-lag handling.
- Migration and backfill safety.

## 10. What not to conclude

- Do not report a query as slow without a plan or a measurement — you can report that it is
  *unbounded* or *repeated* from code alone, which is a different and defensible claim.
- Do not recommend an index without stating the write-side cost.
- Do not recommend caching to paper over an N+1.
- Do not assume an ORM is generating what you would write; find the generated query or say
  you could not.
