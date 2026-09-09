# ClickHouse

Load after `databases/universal.md` and `databases/wide-column.md`.

This file contains **only what those files do not give you**. The column-oriented
scan-cost model, the sort/order key as the primary lever for skipping data, projection
discipline, join weakness, and "insert pattern matters more than volume" are covered in
`databases/wide-column.md` §4 and are not repeated here — this file is written for
ClickHouse's column-oriented analytical access pattern specifically, since that is the
access pattern the engine is built for.

---

## 1. Detection signals

Dependencies: `clickhouse-driver` and `clickhouse-connect` (Python), `@clickhouse/client`
and `@clickhouse/client-web` (Node.js), `clickhouse-go` (Go), `ClickHouse.Client` (.NET),
the ClickHouse JDBC driver (Java/Kotlin).

Other signals: `clickhouse` container images, `config.xml`/`users.xml` (server
configuration), `.sql` migrations containing `ENGINE = MergeTree` or a related engine name
(`ReplacingMergeTree`, `SummingMergeTree`, `AggregatingMergeTree`,
`CollapsingMergeTree`/`VersionedCollapsingMergeTree`, `ReplicatedMergeTree`,
`Distributed`), the `clickhouse-client` CLI in scripts or runbooks, references to
ClickHouse Keeper or ZooKeeper alongside a ClickHouse connection.

The `ENGINE = ...` clause on `CREATE TABLE` is worth reading directly: it is the single
most consequential line in a ClickHouse schema, deciding not just storage but read/write
semantics (§2).

---

## 2. What differs from the wide-column baseline

### The MergeTree engine choice is a correctness-and-timing decision, not just a performance one

`ENGINE = MergeTree` is the base case: rows are simply stored and merged. The named
variants change what a query sees, not only how fast it runs:

- **`ReplacingMergeTree`** deduplicates rows sharing the same sort key, but **only when
  parts are merged** — a query issued before that merge completes can still return
  duplicate rows. Relying on it for immediate deduplication (rather than issuing `FINAL` or
  accepting eventual convergence) is a checkable correctness gap that looks like a
  performance inconsistency.
- **`SummingMergeTree`/`AggregatingMergeTree`** pre-aggregate rows sharing a sort key
  during background merges, trading write-time simplicity for the same "not yet merged"
  staleness window as above, plus a schema constraint: columns outside the sort key must
  be summable (or explicitly aggregated) for the semantics to be correct.
- **`CollapsingMergeTree`/`VersionedCollapsingMergeTree`** implement update/delete by
  inserting a cancelling row (a `sign` column) rather than mutating in place — correct only
  if the application reliably emits matching cancel rows; a missing cancel row leaves a
  stale row present indefinitely, not merely delayed.

Using a plain `MergeTree` and reimplementing deduplication or aggregation in application
code, when a purpose-built engine variant already does it in the merge path, is a common
and checkable finding in the other direction.

### `ORDER BY` and `PARTITION BY` are two different keys with two different jobs

The category file's "sort/order key" (§4) is `ORDER BY` here specifically: it defines the
table's physical sort order and backs the sparse primary index (below). `PARTITION BY` is
a **separate** key governing data lifecycle — which physical part a row lands in for the
purpose of TTL expiry, efficient whole-partition drops, and merge scope — and does not by
itself accelerate a query the way a matching `ORDER BY` prefix does. Assuming a
`PARTITION BY` column behaves like an index for point or range queries — or, in the other
direction, choosing a high-cardinality `PARTITION BY` key (e.g. a customer ID) expecting
query speedup — is a common, checkable misunderstanding: it multiplies part count (see
"too many parts" below) for no query benefit while the actual query-skipping lever is
`ORDER BY`.

### The primary index is sparse, so "indexed" does not mean "one row read"

Unlike a B-tree index, ClickHouse's primary index stores one entry per **granule**
(`index_granularity`, documented default 8192 rows), not per row. A query filtering on a
prefix of the `ORDER BY` key skips whole granules efficiently, but a granule containing
even one matching row is read in full — so a point lookup by an indexed column still has
a real I/O floor set by `index_granularity`, not the single-row cost a B-tree-backed engine
would give the same query. Expecting sub-granule point-lookup latency from a table sized
for scan workloads is a checkable, mechanism-specific finding distinct from the category
file's general scan-cost framing.

### Mutations are asynchronous, whole-part rewrites, not row-level updates

`ALTER TABLE ... UPDATE`/`DELETE` (a "mutation") does not modify rows in place: it
rewrites every affected **part** in the background, asynchronously, and a large table
under a mutation can take a long time to fully apply while old and new part versions
coexist. Issuing mutations at OLTP-like frequency — one per user action, say — against a
large table is a heavyweight, checkable failure mode with no equivalent in a typical
row-store `UPDATE`. Lightweight deletes (see §6) mark rows for exclusion immediately but
still require the same background rewrite before space is physically reclaimed.

### "Too many parts" turns the category's insert-pattern warning into a specific, named failure

The category file (§4) notes that a high-frequency single-row insert pattern multiplies
background-merge burden. In ClickHouse this is not just slower — each unbatched insert (or
each insert issued without `async_insert`, below) creates its own part, and once
outstanding part count crosses a configured limit, the server **rejects further inserts**
with a "too many parts" error rather than continuing to degrade. This is directly
checkable from `system.parts` (§3) and is the single most common ClickHouse-specific
production incident this file exists to help catch.

### The default join builds the right-hand side fully in memory

ClickHouse's default join algorithm loads the right-hand table into an in-memory hash
table before matching. A large right-hand table risks `Memory limit exceeded`, not a slow
query — the category file's general "joins are the weakest part of these engines" (§4)
has this specific mechanism and this specific failure signature here. Ensuring the smaller
table is on the right, or switching to a disk-spilling join algorithm via
`join_algorithm` at the cost of latency, are the two checkable levers.

### Skip indexes are a distinct, opt-in mechanism from the primary index

Beyond the sparse primary index above, ClickHouse supports secondary "data-skipping"
indexes (`minmax`, `set`, `bloom_filter`, `ngrambf_v1`, and others) declared per column.
These let a query skip granules based on a column outside the `ORDER BY` prefix — worth
checking for on a high-cardinality filter column that is queried often but not part of the
sort key, since without one every such filter falls back to reading every granule the
`ORDER BY` prefix couldn't already exclude.

### Materialized views are insert-triggered, not query-time

A ClickHouse materialized view runs its transform once, on every insert into its source
table, and stores the result — it is not a saved query re-run at read time. A materialized
view attached to a high-write source table adds cost to every insert proportional to the
view's transform, not to read traffic; a materialized view added "to speed up" a rarely-run
report while sitting on a high-throughput ingestion table is a checkable, easily-missed
write-path cost.

### Replicated tables depend on a coordination service outside the data path

`ReplicatedMergeTree` (and any table engine built on it) requires ZooKeeper or ClickHouse
Keeper for replica coordination. That service becoming slow or unavailable degrades or
blocks writes to every replicated table on the cluster, independent of storage or CPU
capacity — worth identifying during discovery as a distinct dependency, and worth checking
directly rather than assuming write latency issues are storage-layer.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `SELECT * FROM system.parts WHERE table = '<table>' AND active` | Current part count and size per table/partition — the direct signal for "too many parts" risk | **safe-on-production** |
| `SELECT * FROM system.merges` | Merges currently in progress, and their progress | **safe-on-production** |
| `SELECT * FROM system.mutations WHERE NOT is_done` | Pending or stuck mutations | **safe-on-production** |
| `SELECT * FROM system.query_log` (requires `log_queries=1`) | Historical per-query timing, rows/bytes read, memory used | **safe-on-production** — the log itself adds modest overhead if enabled newly |
| `SHOW PROCESSLIST` / `SELECT * FROM system.processes` | Currently running queries and their live memory usage | **safe-on-production** |
| `EXPLAIN PLAN <query>` | Logical query plan: which steps and in what order | **safe-on-production** |
| `EXPLAIN PIPELINE <query>` | Physical execution pipeline, including parallelism | **safe-on-production** |
| `clickhouse-client --send_logs_level=trace` for one query | Full per-query execution trace | **safe-on-production for the traced query — adds overhead, avoid at high volume on a hot path** |
| `OPTIMIZE TABLE <table> FINAL` | Forces a full merge, used to test a part-count or duplicate-row hypothesis | **not-safe-on-production** — heavy I/O, can take a long time and temporarily increase disk usage |

`system.parts`' row count per table, cross-checked against insert frequency, is usually
the fastest route from "writes feel slow or are erroring" to a specific, confirmed
insert-batching problem — faster than reasoning about query latency first.

---

## 4. Common failure modes and their symptoms

| Symptom | ClickHouse-specific cause to check first |
|:--|:--|
| Insert fails with a "too many parts" error | Unbatched or non-`async_insert` single-row inserts outpacing background merge — check `system.parts` |
| Query returns duplicate rows from a `ReplacingMergeTree` table | Rows not yet merged; query issued without `FINAL` or without accounting for eventual convergence |
| Query fails with `Memory limit exceeded` on a join | Right-hand table too large for the default in-memory hash join |
| `ALTER ... UPDATE`/`DELETE` runs far longer than expected, or table feels "stuck" | Large asynchronous mutation still rewriting parts — check `system.mutations` |
| A supposedly-indexed point lookup is still slow | `index_granularity` floor — the matching granule(s) are read in full regardless of index use |
| A `PARTITION BY` column was expected to speed up a query and didn't | `PARTITION BY` governs lifecycle/lookup scope, not row-level skipping the way `ORDER BY` does |
| Write latency or failures cluster-wide with no storage-capacity change | ZooKeeper/ClickHouse Keeper unavailable or slow, affecting all `ReplicatedMergeTree` tables |
| High-write table has unexplained extra write-path cost | A materialized view attached to it, running its transform on every insert |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `index_granularity` | Primary-index memory/size versus the minimum I/O read per matching granule |
| `PARTITION BY` key choice | Lifecycle/drop/TTL efficiency and merge scope versus part-count multiplication if too high-cardinality |
| `async_insert` / `wait_for_async_insert` | Insert latency versus server-side batching and reduced part-count pressure |
| MergeTree engine variant (plain/`Replacing`/`Summing`/`Aggregating`/`Collapsing`) | Write-path simplicity versus which correctness guarantee is delegated to background merges, and when |
| `join_algorithm` | In-memory join speed versus disk-spilling resilience against a large right-hand table |
| Data-skipping index type (`minmax`/`set`/`bloom_filter`/`ngrambf_v1`) | Extra storage and insert-time cost versus which filter columns can skip granules |
| Merge/background-pool settings (`background_pool_size` and related) | Compaction keeping up with insert rate versus I/O contention with foreground queries |
| `max_memory_usage` / `max_bytes_before_external_group_by`/`_sort` | Query memory ceiling and OOM risk versus slower disk-spilling execution |

---

## 6. Version differences worth knowing

Record the ClickHouse version, and whether it is self-managed or ClickHouse Cloud, during
discovery.

- **Lightweight deletes** (`DELETE FROM ... WHERE`, marking rows rather than issuing a full
  mutation immediately) became generally available in 23.3 — a codebase still routing all
  deletes through `ALTER TABLE ... DELETE` on a version that supports lightweight deletes
  may be paying mutation cost it no longer needs to.
- **`async_insert`** (server-side batching of small inserts) matured across the 21.x–22.x
  line; confirm it is actually enabled before attributing a "too many parts" incident to an
  unfixable insert pattern rather than a missing setting.
- **Parallel replicas** and newer join algorithms have shifted join and large-scan
  performance characteristics materially across recent major versions — do not assume an
  older performance caveat (e.g. join memory limits) still applies at its old severity
  without checking the deployed version's release notes.
- **ClickHouse Keeper**, a from-scratch replacement for the ZooKeeper coordination
  dependency, is newer than ZooKeeper-based coordination and not universally adopted yet —
  confirm which one a given cluster actually uses before citing either's specific
  operational characteristics.
- **ClickHouse Cloud** separates storage and compute and manages scaling and replication
  differently from a self-managed cluster; do not assume self-managed operational guidance
  (mutation timing, part-count limits, `background_pool_size` tuning) applies unchanged to
  Cloud without confirming.

**Confirm version- and deployment-specific claims against the current release notes for
the deployed version.**

---

## 7. What this file does NOT cover

- The column-oriented scan-cost model, projection discipline, the sort/order key as the
  general skip-vs-scan lever, and joins as an analytical-engine weak point as a concept —
  see `databases/wide-column.md` §4.
- The general sorted-immutable-merge storage mechanism as a concept — see
  `databases/wide-column.md` §1.
- Multi-shard cluster topology: the `Distributed` table engine's routing and sharding-key
  design in depth are a different, broader surface than this file details.
- ClickHouse Cloud-specific autoscaling, pricing, and managed-only features.
- Authentication, encryption in transit/at rest, and role-based access control.
- Backup and restore mechanics.
- User-defined functions and embedded scripting.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
