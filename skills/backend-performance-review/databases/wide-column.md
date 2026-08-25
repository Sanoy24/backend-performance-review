# Wide-column datastores

Load after `databases/universal.md`, before any engine-specific file.

"Wide-column" covers two related but distinct access patterns built on a shared storage
foundation, and a review needs to know which one it's looking at before applying either half of
this file:

- **Partition-oriented** engines — data is distributed by a partition key, and the dominant
  access pattern is reading or writing a known partition, optionally scanning a clustering-key
  range within it. Optimized for high-throughput, low-latency access to specific rows at scale.
- **Column-oriented analytical** engines — data is stored and scanned by column across very
  large row counts, and the dominant access pattern is aggregation over many rows and few
  columns. Optimized for scan throughput, not point access.

Both are usually built on a **sorted, immutable, merge-based storage engine** (commonly called
an LSM-tree or a merge-tree family), which is why they share §1–§3 below. Where the two access
patterns diverge, this file says so explicitly rather than picking one and calling it "wide-
column performance."

Nothing here is engine-specific.

---

## 1. The shared storage mechanism: sorted, immutable, merged

Writes are appended to an in-memory structure and periodically flushed to disk as new, sorted,
immutable files; a background process merges these files together over time. This mechanism
has performance consequences that apply regardless of which access pattern the engine is built
for:

- **Writes are fast and sequential.** There is no in-place update to seek to; a write is
  appended, which is why these engines tolerate very high write throughput well.
- **A read may need to check several files before returning.** Depending on how many
  unmerged files exist for the data in question, a read can cost more than "look it up once" —
  it may check the in-memory structure and multiple on-disk files, discarding older, superseded
  versions as it goes.
- **Background compaction/merging is a real, periodic cost**, not a one-time housekeeping task.
  It consumes I/O and CPU continuously, and an under-compacted dataset degrades read latency the
  same way an unvacuumed table bloats in a relational engine — the mechanism differs, the
  symptom (growing read cost with no application code change) does not.
- **Deletes and updates are also appends.** A delete typically writes a marker (a "tombstone")
  rather than removing data immediately; an update writes a new version rather than modifying
  the old one in place. Both are cleaned up by the same background merge process, on its own
  schedule, not on the read path.

**A workload with heavy deletes or repeated updates to the same keys accumulates markers and
superseded versions faster than they can be merged away**, and every read in the affected range
pays the cost of skipping past them. This is the wide-column-family equivalent of dead-row
bloat, and it is often more severe: past a configured threshold some engines refuse to serve a
read at all rather than degrade further, converting a performance problem into an outage with no
code change.

---

## 2. Partition-oriented access: the partition key is the whole data model

For partition-oriented engines, **the partition key decides two things at once: where the data
lives, and which queries can be answered efficiently at all.** This is a stronger and less
forgiving version of the same idea in `databases/key-value.md` — the key is the data model
there too, but a partition-oriented wide-column engine adds a second dimension, the clustering
key, that determines physical sort order within a partition.

- **Any query that does not supply the full partition key** is either rejected outright by the
  engine, or serviced by scanning every partition in the cluster — the wide-column-family
  equivalent of a full table scan, except distributed across the whole cluster rather than
  contained to one node. Look for this specifically: it is a checkable, code-visible pattern
  (a query builder call with no partition-key predicate, or an explicit "allow full scan" flag)
  rather than something requiring a plan to detect.
- **The clustering key sets on-disk order within a partition.** A range query or an `ORDER BY`
  matching the clustering key order is close to free; one that doesn't requires an in-memory
  sort of the partition's contents, or is rejected outright depending on the engine.
- **A partition that grows without bound** — one keyed on something with no natural cap, like a
  single tenant's entire event history with no time-bucketing — is the partition-oriented form
  of a supernode or a hot key: it concentrates read and compaction cost onto one node
  indefinitely, and some engines impose hard partition-size limits that convert this from a
  performance problem into a write failure once crossed. Time-bucketing or otherwise splitting
  an unboundedly-growing key into multiple partitions is the usual structural fix, decided at
  modeling time, not at query time.
- **Query-first modeling, not normalization.** The idiomatic pattern is one table (or query
  table) per access pattern, with data duplicated across tables as needed, rather than a
  normalized schema joined at read time — because these engines generally do not support
  efficient cross-partition joins at all. **A reviewer applying relational instinct — normalize,
  then join in application code across partitions — is very often the actual finding**: it forces
  either an unbounded multi-partition fan-out or a full-cluster scan to reconstruct what a
  single well-modeled partition would have answered directly.
- **Secondary indexes exist on most of these engines but are usually the wrong tool** compared to
  modeling the query as its own partition/clustering key from the start; a secondary index
  commonly requires a cluster-wide fan-out to satisfy, defeating the partition model's whole
  performance advantage. Treat a secondary index used on a hot path as worth checking, not
  worth assuming is fine.

---

## 3. Consistency level is a direct, checkable latency trade

Most partition-oriented engines let each read or write specify how many replicas must
acknowledge it. This is not merely a correctness knob:

- A level requiring more replicas to respond bounds latency by the **slower** of those
  replicas, not the fastest — the same "parallel calls take the max, not the sum" reasoning as
  anywhere else, but here it is a per-query setting rather than an architectural fact.
- A level requiring only the nearest or a single replica is fast but can return stale data, and
  shifts risk onto how promptly writes actually propagate.
- **Consistency level combined with replication factor is arithmetic worth doing explicitly**,
  the same way connection-pool sizing is: a read level and write level whose required replica
  counts overlap (their sum exceeds the replication factor) guarantee the read sees the latest
  write; if they don't overlap, staleness is possible by design, not by bug. Whether that is
  intended or accidental is worth establishing rather than assuming.

---

## 4. Column-oriented analytical access: scan cost, not lookup cost

Where the engine's dominant pattern is scanning many rows to aggregate over few columns, the
partition-key reasoning above mostly does not apply — there may be no partition key in that
sense at all. Instead:

- **Cost is governed by how many columns and how much data must be read**, not by an index
  finding a row. Selecting unneeded columns costs real I/O in a column store specifically
  because columns are what's stored and scanned separately — the projection discipline in
  `application/api.md` and `databases/universal.md` applies here with unusual force.
- **The table's physical sort/order key is the primary lever**, playing a role closer to a
  relational clustered index than to a partition key: a filter matching a prefix of that order
  allows the engine to skip large ranges of data entirely (often tracked via block-level
  min/max statistics); a filter on an unrelated column requires scanning everything.
- **Joins are commonly the weakest part of these engines.** A large distributed join across
  big tables is frequently far more expensive than the equivalent relational join, because the
  engine's strength is independent column scanning, not row-oriented matching. Pre-aggregating
  or denormalizing to avoid a large join at query time is a legitimate, common trade here —
  the reverse of the usual normalization advice, and worth stating explicitly as such rather
  than silently recommending denormalization out of habit.
- **Insert pattern matters more than insert volume.** These engines are built for large, batched
  writes; many small, frequent single-row inserts each become their own small storage part,
  multiplying the background-merge burden described in §1 far beyond what the same total data
  volume would cost as batched writes. A high-frequency single-row insert pattern into an
  analytical engine is a real, checkable finding, and batching is the direct fix.

---

## 5. Evidence available from either kind of engine

Follow `databases/universal.md` §8; wide-column-specific evidence to look for:

| Evidence | Question it answers |
|:--|:--|
| Query trace / plan output | Whether a query hit a single partition or fanned out across the cluster; whether a scan used the sort/order key or read everything |
| Partition or row-count statistics on the largest keys | Whether an unbounded partition is plausible for this domain |
| Compaction/merge metrics and pending-compaction backlog | Whether background merging is keeping up with write volume |
| Tombstone or superseded-version counts | Whether a delete/update-heavy workload is accumulating read-amplifying markers |
| Consistency-level configuration in client code | Whether the read/write levels are a deliberate choice or an unexamined default |

Reading statistics and trace/plan output is generally safe on production; anything that forces a
full-cluster scan for profiling purposes on a large dataset is not-safe-on-production without
confirming its cost first.

---

## 6. What to look for in a review

- Any query with no partition key (or, for an analytical engine, no filter on the sort/order
  key) — the scan/fan-out equivalent of a missing index.
- A partition (or a single row's history) that can grow without a natural bound.
- A normalized, join-across-partitions data model applied where the engine expects one
  table per access pattern.
- Secondary index usage on a hot path where a dedicated query table would avoid the fan-out.
- Consistency-level and replication-factor arithmetic, done explicitly rather than assumed.
- Delete- or update-heavy workloads and their tombstone/superseded-version accumulation.
- High-frequency single-row inserts into an engine built for batched analytical writes.
- Large joins on an analytical engine that could be pre-aggregated or denormalized instead.

## 7. What not to conclude

- Do not apply partition-key reasoning to a column-oriented analytical engine, or scan-cost
  reasoning to a partition-oriented one — confirm which access pattern the engine and the
  workload actually use before reasoning about either.
- Do not recommend normalization for a partition-oriented engine; the idiomatic model here is
  the opposite, and recommending it without qualification would itself be a false positive.
- Do not recommend a stronger consistency level without stating the latency cost, or a weaker
  one without stating the staleness risk — both are real trade-offs, not free improvements.
- Do not invent engine-specific tombstone/compaction thresholds or timing; state that a
  technology file would be needed for that and mark the specifics as unknown.
