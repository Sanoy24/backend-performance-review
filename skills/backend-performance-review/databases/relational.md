# Relational datastores

Load after `databases/universal.md`, before any engine-specific file.

Relational engines are optimized for flexible querying over normalized data with
transactional guarantees. Their performance characteristics follow from three things: a
cost-based planner, B-tree-family indexes, and multi-statement transactions.

Nothing in this file is engine-specific. Where engines differ materially — and they do —
that belongs in `technology/<engine>.md`.

---

## 1. The planner decides, and it can be wrong

Application code expresses intent; the planner chooses execution. Two consequences:

- **The same query can perform differently on different data.** A plan chosen for a small
  table is not the plan chosen after it grows. Performance changing with no code change is
  usually a plan change.
- **The planner relies on statistics.** Stale or absent statistics produce bad estimates,
  and bad estimates produce bad plans. After a bulk load or a large deletion, statistics
  frequently do not reflect reality until refreshed.

This is why plan inspection is the primary evidence for relational work, and why a review
without plans must be explicit that its query-cost claims are inferences.

### Reading a plan

Regardless of engine, look for:

| Signal | Meaning |
|:--|:--|
| Scan of a large table where a predicate should have narrowed it | Missing or unusable index |
| Rows examined greatly exceeding rows returned | Poor selectivity; wasted I/O |
| Estimated rows far from actual rows | Stale statistics or a predicate the planner cannot estimate |
| A nested loop over a large outer input | Often an index-choice problem; can be quadratic |
| A sort where an index could supply the order | Avoidable memory and CPU |
| An intermediate result spilling to disk | Insufficient working memory for the operation |
| Repeated identical subplans | A candidate for restructuring |

The examined-versus-returned ratio is the most useful single number a plan gives you.

---

## 2. Indexes

**Leading-column rule.** A composite index on `(a, b, c)` supports predicates on `a`, on
`a, b`, and on `a, b, c`. A predicate on `b` alone generally cannot use it. Index order is
therefore a design decision driven by actual query predicates, not by field importance.

**Predicates that defeat an index.** Applying a function to the indexed column, an implicit
type conversion, a leading wildcard in a pattern match, or an `OR` across differently
indexed columns. These are visible in code, which makes them findable without a plan — a
rare case where a query-cost finding is defensible from source alone.

**Covering the query.** When an index contains every column a query needs, the engine can
answer from the index without touching the table. This is often the largest available
improvement for a hot read path, and its cost is a wider index.

**Sort support.** An index matching the `ORDER BY` avoids a sort. Combined with a limit,
this turns "sort everything, take ten" into "read ten".

**Foreign keys.** A referencing column is frequently unindexed by default. Its absence
affects both joins and the cost of checking referential integrity on delete.

**Unused indexes.** Every index costs on every write. Engines track index usage; "which
indexes are never used" is a question worth putting in the validation plan.

---

## 3. Query patterns that recur in review

- **`SELECT *`** — extra I/O and transfer, and it prevents index-only paths.
- **Offset pagination at depth** — the engine must produce and discard the skipped rows, so
  cost grows with offset. Keyset pagination avoids it, at the cost of random page access.
- **Counting everything for a total** — an exact count of a large table is a full traversal.
  Approximate counts, cached counts, or "has more" instead of "total pages" are the usual
  answers, and each is a product decision as much as a technical one.
- **`OR` across columns** — often prevents index use where a union of two indexed queries
  would not.
- **Predicates on computed expressions** — unusable unless an expression index exists.
- **Wide joins for a narrow need** — joining many tables to return a few fields.
- **Cartesian growth from joining multiple one-to-many relations** — row count multiplies;
  frequently introduced while "fixing" an N+1.
- **`IN` with a very large list** — planning cost and parameter limits; batch it.
- **`DISTINCT` compensating for a join that multiplies rows** — the join is the problem.

---

## 4. Transactions and locking

Lock duration is the dominant contention factor, and it is determined by transaction scope.

Findings, in rough order of severity:

- **A transaction spanning a network call.** Lock duration becomes a third party's latency.
- **A long-running transaction** anywhere: it blocks conflicting writers and, on
  multi-version engines, prevents cleanup of old row versions — which degrades the whole
  database, not just the conflicting queries.
- **Inconsistent lock ordering** across code paths: deadlocks, which appear as errors and
  retries under load and are frequently reported as intermittent slowness.
- **Unnecessarily strict isolation**, chosen by default rather than by requirement.
- **Row-level hot spots** — a counter row every request updates serializes the whole system
  regardless of capacity.
- **Idle-in-transaction connections** — opened, then left while the application does other
  work.

Bulk operations deserve separate attention: a single statement affecting a very large number
of rows can hold locks for a long time and generate a large amount of journal/log activity.
Chunked batches with pauses are usually the safer form, and this belongs in the
recommendation whenever a backfill is involved.

---

## 5. Schema and data model

- **Normalization** reduces write anomalies and update cost; it increases join count.
  Denormalization is the reverse trade and is on the cargo-cult list — it costs write
  complexity and consistency risk, and should follow evidence that joins are the constraint.
- **Column types matter more than expected**: narrower rows mean more rows per page, which
  means fewer pages read and a smaller working set.
- **Large values stored inline** can bloat rows and push out-of-line storage; a large text
  or binary column on a frequently scanned table is worth noticing.
- **Nullable versus default** affects index size and, on some engines, plan choices.
- **A table with no primary key** is a problem for replication, for update efficiency, and
  usually for the data model.
- **Unbounded tables with no retention** are the most common scalability risk in relational
  systems: performance is fine until the working set stops fitting in memory, then degrades
  sharply. Partitioning or archival is the structural answer.

---

## 6. Connections and shared capacity

Relational engines typically allocate significant per-connection resources, so connection
count is a real constraint rather than an abstract limit. The arithmetic in
`application/connection-pools.md` applies directly, and exceeding the configured maximum
produces refused connections rather than degraded service.

Where an external connection pooler sits in front, note that its pooling mode may restrict
session-scoped features. Check compatibility with how the application actually uses the
connection.

---

## 7. Replication and read scaling

- Read replicas add read capacity, not write capacity. If the constraint is writes, they do
  not help.
- Replication lag makes read-after-write unsafe on a replica. Code that writes and then
  immediately reads must either target the primary or tolerate staleness — this is a
  correctness bug with a performance origin, and it is worth checking explicitly whenever
  read/write splitting exists.
- Long-running queries on a replica can, on some configurations, delay replication or be
  cancelled by it. Note the interaction rather than assuming replicas are free.

---

## 8. What to look for in a review

- Predicates, sorts, and joins without supporting indexes — and index-defeating expressions.
- The examined-versus-returned ratio wherever a plan is available.
- Offset pagination on large or growing tables.
- Exact counts of large tables on hot paths.
- Transaction scope, especially transactions containing network calls.
- Bulk operations and backfills without chunking.
- Tables with unbounded growth and no retention or partitioning.
- Connection arithmetic against the configured maximum.
- Read/write splitting without replication-lag handling.

## 9. What not to conclude

- Do not assert a plan you have not seen. Say "this predicate has no supporting index in the
  migrations" — that is provable — rather than "this query does a sequential scan".
- Do not recommend an index without the write-cost trade-off and the query it serves.
- Do not recommend denormalization before establishing that joins are the constraint.
- Do not assume an engine's defaults; if a setting matters to a finding, find it or say you
  could not.
