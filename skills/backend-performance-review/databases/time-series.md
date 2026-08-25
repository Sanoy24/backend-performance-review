# Time-series datastores

Load after `databases/universal.md`, before any engine-specific file.

Time-series engines are optimized for append-heavy writes of timestamped measurements and for
queries bounded by a time range. Almost everything distinctive about this category follows from
two facts: **time is the primary organizing dimension**, and **the combination of identifying
labels attached to a measurement determines a cost structure that has no direct analogue
elsewhere in this skill** — get that part of the model wrong and both writes and queries degrade
together, which is unusual; most other categories let you trade one against the other.

Nothing here is engine-specific.

---

## 1. Time is the partition key

Data is typically stored in time-bounded chunks (by hour, day, or week), and a query naturally
maps to "which chunks overlap the requested time range" before anything else happens. This plays
the same structural role as a partition key in `databases/wide-column.md` §2, and the same
consequence follows:

- **A query with a bounded, reasonably narrow time range** touches only the chunks that overlap
  it — proportional cost, the good case.
- **A query with no time bound, or a bound wide enough to cover the whole retention period**,
  touches every chunk that exists — the time-series equivalent of a missing partition key or a
  full table scan, and just as checkable from the query construction in code: is there always an
  enforced time-range predicate, or can a caller omit one entirely?

An API that accepts an optional time-range filter and silently defaults to "all time" when it's
omitted is a specific, common, and structurally severe version of this — the caller may not even
realize they triggered a full-history scan.

---

## 2. Series cardinality — the defining failure mode of this category

A **series** is one unique combination of the identifying labels attached to measurements (for
example: host, region, and metric name together). Most engines maintain an in-memory index
mapping every distinct series to its storage location, so that a query naming a specific series
can jump straight to it — a mechanism similar in spirit to index-free adjacency in
`databases/graph.md` §1, except here the index is over *label combinations*, not individual
values.

**The number of distinct series is the single most consequential number in a time-series
deployment, and it is easy to make unboundedly large by accident.** Attaching a naturally
high-cardinality value — a raw user ID, a request ID, a container instance ID that churns
constantly, any identifier with effectively unlimited distinct values — as an identifying label
multiplies the series count by every distinct value that identifier can take. Because the series
index is typically held in memory and grows with cardinality regardless of how much data exists
per series, this is unlike most other cardinality problems in this skill: **it is not primarily
a query-time cost, it degrades ingestion as well.** Every new distinct combination is a new
series the index must accommodate, so a churning high-cardinality label can turn a steady write
rate into unbounded index growth.

This is the reason a time-series-specific category file exists at all rather than treating this
as "a database with a clock" — nothing else in this skill has a single modeling decision that
simultaneously threatens write throughput, query latency, and memory footprint the way an
unbounded-cardinality label does here.

---

## 3. The schema-time decision that creates or prevents the risk

Most engines distinguish, at write time, between values that identify a series (commonly called
**tags** or **labels** — indexed, used for filtering and grouping, and part of what makes a
combination "a series" at all) and values that are simply **measured data** (commonly called
**fields** or **values** — stored, but not part of series identity and not part of the
cardinality-driving index).

**Whether a given value belongs as a tag or as a field is the concrete, code-visible decision
that determines whether the cardinality risk in §2 exists at all.** A value that will only ever
be filtered on with a small, bounded set of distinct options (an environment name, a region, a
small fixed set of host groups) is a reasonable tag. A value with effectively unbounded
distinct options that will rarely or never be filtered on (a raw identifier, a free-text detail,
a value whose only purpose is to be retrieved alongside the measurement, not to select which
measurements to retrieve) belongs as a field instead. Finding a naturally high-cardinality value
modeled as a tag is one of the highest-value, most mechanically checkable findings available in
this category, because it is visible directly in the write-path code that constructs each point.

---

## 4. Retention and downsampling: growth is the default, not the exception

Time-series data is written continuously by nature, so **unbounded growth is what happens
without a deliberate decision to prevent it**, unlike most other categories where growth is at
least partly a function of application usage. Two structural answers exist, and their absence is
itself worth checking for:

- **Retention** — expiring raw data older than some age. No retention policy at all means the
  dataset, and the cost of any query spanning "all time," grows without bound indefinitely.
- **Downsampling / continuous rollups** — precomputing lower-resolution aggregates (for example,
  five-minute averages) for older data, so a long-range query touches a much smaller rolled-up
  dataset instead of every raw point in the range. This is the time-series-idiom form of the
  materialized-view trade-off in `databases/universal.md` §5: write-time or background
  computation cost, paid once, in exchange for dramatically cheaper long-range reads paid
  repeatedly.

**A dashboard or report that aggregates over a long time range directly against raw,
full-resolution data** — with no downsampled tier to fall back to — pays a cost proportional to
the total number of raw points in that range, which for a long window over a high-frequency
metric can be very large. This is checkable directly: does a rollup/continuous-aggregate exist
for the resolution this query actually needs, or is every query, regardless of range, hitting
the same raw table?

---

## 5. Write patterns: order matters more here than elsewhere

Storage engines in this category are usually built on the same append-heavy, chunked, background-
merge storage lineage described in `databases/wide-column.md` §1, and share its general
consequences (fast sequential writes, periodic background compaction, read cost that depends on
how many unmerged pieces exist). One additional, more severe distinction applies specifically to
time-series workloads:

**Writes arriving out of time order — a point whose timestamp is well in the past relative to
data already written — are typically far more expensive than in-order appends**, because they
may need to reopen, re-sort, or re-merge chunks that were already closed and compacted on the
assumption that time moves forward. A workload that legitimately needs to accept significantly
delayed or backfilled data (from a device that buffers offline, or a batch import job) is not
wrong to exist, but it is a materially different cost profile from steady, near-real-time
ingestion, and conflating the two when reasoning about write performance will produce the wrong
conclusion in either direction.

---

## 6. Evidence available from a time-series engine

Follow `databases/universal.md` §8; time-series-specific evidence to look for:

| Evidence | Question it answers |
|:--|:--|
| Series/cardinality count, or a cardinality-estimation query | Whether cardinality is bounded or growing without limit |
| Query plan / profile output | Whether a query resolved to specific series and chunks, or scanned broadly |
| Retention policy and continuous-aggregate/rollup definitions | Whether growth is bounded and whether long-range queries have a cheaper tier to use |
| Write-path code constructing each point | Whether a given identifying value is attached as a tag or a field |
| Chunk/shard count and size over time | Whether background compaction is keeping pace with the write rate |

Reading cardinality statistics, plans, and retention/rollup definitions is generally safe on
production; a query deliberately constructed to force a full-history, all-series scan for
profiling purposes is not-safe-on-production without confirming its cost first.

---

## 7. What to look for in a review

- Any query path where a time-range filter is optional rather than enforced.
- A naturally high-cardinality value (raw IDs, churning instance identifiers, free-text values)
  attached as a tag/label rather than a field/value.
- Absence of a retention policy, meaning raw data and full-history query cost grow without
  bound by default.
- Long-range aggregation queries with no downsampled/rolled-up tier to use instead of scanning
  raw data.
- A workload accepting significantly out-of-order or backfilled writes, and whether that path is
  reasoned about separately from steady near-real-time ingestion.
- Chunk/compaction backlog growing relative to write rate.

## 8. What not to conclude

- Do not treat every tag as a cardinality risk — a small, bounded set of tag values is exactly
  what tags are for; the risk is specifically an unbounded or naturally high-cardinality value.
- Do not recommend downsampling before checking whether a rollup already exists for the
  resolution the query actually needs — recommending it twice over adds nothing.
- Do not assume a specific engine's series-index memory behavior or chunk duration defaults;
  state that a technology file would be needed for that and mark specifics unknown.
- Do not conflate out-of-order-write cost with steady-ingestion cost — they are different
  workloads with different performance profiles, not the same thing at different volumes.
