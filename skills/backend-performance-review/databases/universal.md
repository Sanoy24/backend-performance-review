# Datastores — universal principles

Applies to every datastore regardless of category or engine. Load this before any category
file, and both before any engine-specific file.

The reasoning here is deliberately product-independent. If a claim in this file only holds
for one engine, it is in the wrong file.

---

## 1. Classify before analyzing

Identify the **category** first. Category determines which access patterns are cheap, which
are expensive, and what the engine can be expected to do well.

| Category | Optimized for | Typically expensive |
|:--|:--|:--|
| Relational | Flexible querying, joins, transactional consistency | Very high write rates on a single node; unbounded scans |
| Document | Retrieval of whole aggregates by key or indexed field | Cross-document joins; queries the document shape did not anticipate |
| Key-value | Point access by key | Anything that is not access by key |
| Wide-column | High-volume writes, queries along the partition/clustering key | Ad-hoc queries; anything requiring a scan across partitions |
| Graph | Traversing relationships from a starting point | Global scans; unbounded-depth traversal |
| Search | Ranked text and faceted retrieval | Use as a system of record; frequent per-document updates |
| Time-series | Append-heavy writes; range queries over time | High-cardinality dimensions; arbitrary non-time queries |
| Vector | Approximate nearest-neighbour similarity | Exact search at scale; heavy filtering combined with search |
| Object store | Large immutable blobs | Small-object access patterns; anything transactional |

**A datastore used against its category's grain is a design finding, not a tuning finding**,
and it should be reported as such. Tuning cannot rescue a fundamentally mismatched access
pattern, and recommending tuning in that case wastes the reader's time.

If the engine is unrecognized, infer the category from its query interface and usage, apply
this file plus the category file, and mark engine-specific conclusions as unknowns. Do not
invent engine behavior.

---

## 2. The universal questions

Ask these of every datastore in the system, in this order.

**How much data does this operation touch?** Not how many rows it returns — how many it
must examine. The ratio between examined and returned is the single most useful signal
available from a query plan, and its absence from a review is a gap worth naming.

**Is the amount touched bounded?** By a key, by an index, by a limit, by a partition, by a
time range. If nothing bounds it, cost grows with the dataset and the operation has no
worst case you can state.

**How many round trips?** Datastore latency is dominated by round trips for small
operations. One query returning 100 rows is not comparable to 100 queries returning one row
each, even though the data volume is identical.

**What does the access pattern do to the write path?** Every index, every replica, every
durability guarantee costs something on write. Read optimization is a trade against write
cost, not a free improvement.

**What is shared?** Connections, buffer/cache memory, locks, I/O bandwidth, a single writer.
Operations that saturate a shared resource affect every other operation.

---

## 3. Indexes are a trade, always

An index makes a read cheaper by making writes and storage more expensive. State both sides
whenever recommending one.

Costs of an index, all of which belong in `Trade-offs`:

- Every insert, update touching the indexed field, and delete must maintain it.
- Storage, and cache/buffer memory that could have held data.
- Longer index-build time and, on some engines, locking during the build.
- More options for the planner, which occasionally makes a worse choice.

Signals that an index may be justified: a predicate on a field with no supporting index and
high selectivity; a sort matching no index order; a repeated join or lookup key. Signals
that it is not: the table is tiny; the predicate is unselective; an existing index already
covers the prefix; the query runs rarely.

**Index proliferation is a real failure mode.** A collection with many overlapping indexes
pays maintenance on all of them for every write, and often only a few are used. "Which
indexes are unused" is a better question than "which index should we add", and most engines
can answer it from their own statistics.

---

## 4. Selectivity and cardinality

Selectivity is the fraction of data a predicate eliminates. An index on a low-cardinality
field (a boolean, a status with three values) frequently does not help, because reading the
index and then the matching data costs more than scanning.

Compound index ordering follows from this: the leading field determines which queries can
use the index at all. An index on `(a, b)` supports queries filtering on `a`, and on `a`
and `b`; generally not on `b` alone.

Cardinality is also what makes some categories fail: high-cardinality dimensions in
time-series stores, high-cardinality metric labels, and hot low-cardinality partition keys
are all instances of the same underlying issue — the distribution of values determines
whether the structure works.

---

## 5. Working set and memory

Every datastore is dramatically faster when the data it repeatedly touches fits in memory.
The consequences are often more important than query tuning:

- A working set that fits in cache produces fast reads regardless of storage speed.
- A working set that has just outgrown available memory produces a sudden, sharp degradation
  that looks like a mystery, because no code changed.
- Selecting fewer fields, and indexes that let the engine avoid touching the data itself,
  both reduce working-set size.
- One large scan can evict the working set and slow every other query until it repopulates.
  This is why an unthrottled analytics query on a primary is a shared-resource finding.

Growth in data therefore does not degrade performance smoothly. It degrades in steps, at the
points where the working set crosses a memory boundary. Say so when reporting a
`scalability-risk`.

---

## 6. Writes, durability, and replication

- Durability guarantees cost latency. A commit that waits for a durable write, or for
  acknowledgement from replicas, is bounded below by that wait.
- Write amplification is real: one logical write becomes several physical ones through
  journals, indexes, and background reorganization.
- Background maintenance (compaction, vacuuming, merging, rebalancing) consumes I/O and CPU
  and can cause periodic latency spikes — a tail-latency cause invisible in application code.
- Replicas trade consistency for read capacity. Replication lag is a correctness concern
  with performance origins: reading your own write from a lagging replica returns stale data.
- Failover and leader election are latency events. A system with no timeout handling
  experiences them as outages.

---

## 7. Transactions and contention

- Longer transactions hold locks longer and increase conflict probability.
- Stricter isolation trades throughput for consistency, either by blocking or by retrying.
- Retry-based concurrency control converts contention into wasted work, which grows
  non-linearly as contention rises.
- A single hot row, key, or partition serializes everything touching it regardless of total
  capacity — the hot-key problem, and one of the few issues that cannot be solved by adding
  capacity.

---

## 8. Evidence available from any datastore

Before speculating, check what the engine can tell you. Most expose some form of:

| Evidence | Question it answers |
|:--|:--|
| Query plan | How the engine intends to execute this, and what it will examine |
| Executed plan with actual counts | What it really examined versus what it estimated |
| Slow-operation log | Which operations are actually slow in production |
| Statistics on index usage | Which indexes are earning their write cost |
| Connection and lock views | Whether contention or saturation is present now |
| Cache/buffer hit statistics | Whether the working set fits |

**Plan-only inspection is generally safe on production; executing a plan is not** when the
statement has side effects. Label every command accordingly. If the review has none of this
evidence, that is a finding about observability, and it caps confidence for the whole
datastore section.

---

## 9. What to look for in a review

- Category-to-usage fit before anything else.
- Operations with no bound on the data they examine.
- Round-trip counts and how they scale with result size.
- Index coverage for actual predicates and sorts — and unused indexes paying write cost.
- Whether the working set plausibly fits in memory, and what happens as it grows.
- Transaction scope and hot keys.
- Whether any of the engine's own evidence is available.

## 10. What not to conclude

- Do not state a query is slow without a plan or a measurement; state that it is unbounded
  or repeated, which is provable from code.
- Do not recommend an index without naming the write cost.
- Do not recommend replicas without addressing replication lag and read-after-write.
- Do not invent engine behavior for an engine you do not have a reference for — apply the
  category file and mark the rest unknown.
