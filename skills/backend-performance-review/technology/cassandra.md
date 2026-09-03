# Cassandra / ScyllaDB

Load after `databases/universal.md` and `databases/wide-column.md`.

This file contains **only what those files do not give you**. Partition and clustering key
modeling, consistency-level-versus-replication-factor arithmetic, and the general
sorted-immutable-merge storage mechanism are covered there and are not repeated here.

Cassandra and ScyllaDB share the same wire protocol (CQL) and data model, so most of this
file applies to both. Where they diverge — mainly around the JVM — this file says so
explicitly rather than treating ScyllaDB as "Cassandra with a faster compactor."

---

## 1. Detection signals

Dependencies: `cassandra-driver` (Python), `gocql`/`gocqlx` (Go), `datastax` (any language —
the DataStax drivers cover Java, Node.js, C#, and Astra), `cassandra` (various), Spring Data
Cassandra.

Other signals: `.cql` files, `cassandra.yaml` or `scylla.yaml` in configuration, `cassandra`
or `scylladb` container images, `nodetool` invocations in scripts or runbooks, a
`CREATE KEYSPACE ... WITH replication` statement, DataStax Astra connection bundles
(`secure-connect-*.zip`), consistency-level constants in client code (`LOCAL_QUORUM`, `ONE`,
`ALL`, etc.).

The client-side load-balancing and retry policy configuration is worth reading alongside the
connection setup: a token-aware policy versus round-robin changes whether requests reach the
correct replica directly or add a coordinator hop, and the driver's default retry policy
determines what happens on a timeout — both change the performance analysis.

---

## 2. What differs from the wide-column baseline

### Compaction strategy is a real, workload-specific choice

The category file describes compaction as a background cost; here it is also a checkable
**configuration decision** with three common strategies, each trading write and read
amplification differently:

- **Size-Tiered (STCS)**, the default: merges similarly-sized files together. Good general
  throughput, but read amplification grows with the number of unmerged files, and space
  amplification can spike during large merges.
- **Leveled (LCS)**: keeps data in size-bounded levels, bounding how many files a read must
  check. Lower, more predictable read amplification; substantially more compaction I/O,
  since data is rewritten more often. Suited to read-heavy tables where STCS's read cost is
  the actual problem.
- **Time-Window (TWCS)**: groups writes by time window, avoiding the compaction of the whole
  dataset together. Built for time-series or TTL-heavy tables where old windows expire and
  are simply dropped rather than compacted at all.

A table using STCS with heavy point-read traffic, or a time-series table not using TWCS, is a
checkable, engine-specific finding — the fix is a strategy that matches the actual access
pattern, not a universal "compact more often."

### Tombstones have engine-specific thresholds, not just the general accumulation cost

The category file (§1) covers tombstone accumulation generally. Cassandra additionally
**rejects a read outright** past a configured number of tombstones scanned in a single
partition read (`tombstone_failure_threshold`, documented default 100,000), and warns well
before that (`tombstone_warn_threshold`, documented default 1,000) — turning unbounded
tombstone accumulation into a hard query failure, not just a slow one. `gc_grace_seconds`
(documented default 10 days) controls how long a tombstone is kept before it can be purged by
compaction; repair must complete within this window on every replica, or a tombstone can be
purged on one node while a deleted value still lives on another, letting deleted data
reappear ("zombie" resurrection).

### Read repair and hinted handoff both trade consistency for background work

Two Cassandra-specific mechanisms manage replica convergence outside the request path:

- **Read repair** triggers when replicas disagree on a read; depending on configuration, this
  can add latency to the request that discovered the mismatch (foreground) or happen after
  the response is returned (background). Frequent read repair on a hot read path is a signal
  of replicas that are out of sync more than expected, not a free consistency mechanism.
- **Hinted handoff** lets the coordinator store a write intended for a down replica and
  replay it on recovery, bounded by a hint window (documented default 3 hours). A replica
  down longer than the window needs a full repair, not just hint replay, to catch up — a
  distinction worth checking when diagnosing stale-replica symptoms.

### Lightweight transactions are Paxos, and Paxos is expensive

A CQL statement with `IF NOT EXISTS` or `IF <condition>` is not a cheap conditional write — it
runs a full Paxos round (multiple round trips across replicas) rather than a single write.
Using LWTs as the default write path, rather than reserving them for the specific operations
that actually need compare-and-set semantics, is a common and checkable source of elevated
write latency and reduced throughput under contention on the same partition.

### `ALLOW FILTERING` and secondary indexes are checkable red flags in CQL text

`ALLOW FILTERING` is CQL's explicit escape hatch permitting a query with no efficient plan —
the query text itself names the problem, making it one of the easiest wide-column findings to
locate: `grep` for it. Built-in secondary indexes and materialized views both carry known
consistency and performance caveats beyond the category file's general "usually the wrong
tool" guidance: a secondary index query still requires a cluster-wide fan-out to every node
unless restricted to a single partition, and materialized views add write-path overhead to
keep the view consistent with the base table and have documented consistency edge cases under
node failure.

### `BATCH` is not a cross-partition performance optimization

A CQL batch spanning multiple partitions adds coordinator overhead (the coordinator must
still contact every partition's replicas, plus batch-log bookkeeping for atomicity) — it is
slower than the equivalent individual statements, not faster. A batch confined to a single
partition is atomic and reasonably efficient. Batching writes across partitions specifically
to "reduce round trips" is a common misapplied optimization worth checking for directly in
client code.

### ScyllaDB changes the resource story, not the data model

ScyllaDB is wire-compatible with CQL and shares the modeling guidance above, but it is a
from-scratch C++ implementation with a shard-per-core architecture: each CPU core owns a
fixed shard of data and its own connections, with no JVM and no garbage-collection pauses.
Findings that trace back to JVM GC behavior (below) do not apply to ScyllaDB at all; findings
about connection distribution should account for per-shard connection awareness in
Scylla-aware drivers rather than only per-node pooling.

### JVM GC pauses are a Cassandra-specific (not Scylla) failure mode

Cassandra runs on the JVM, and a long garbage-collection pause can exceed the failure
detector's threshold for marking a node down — intermittent node flapping under load with no
network or hardware issue is a classic GC symptom, not a distributed-systems bug. This is
irrelevant to ScyllaDB.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `nodetool tablehistograms <keyspace>.<table>` | Read/write latency and partition-size percentiles for one table | **safe-on-production** |
| `nodetool tablestats <keyspace>.<table>` | SSTable count, tombstone-per-read ratio, compaction backlog for one table | **safe-on-production** |
| `nodetool tpstats` | Thread pool pending/blocked/dropped task counts — the clearest single view of node-level saturation | **safe-on-production** |
| `nodetool compactionstats` | Compactions in progress and pending compaction count | **safe-on-production** |
| `nodetool proxyhistograms` | Coordinator-level latency, separate from local read/write latency | **safe-on-production** |
| `nodetool netstats` | Streaming and hint-replay activity between nodes | **safe-on-production** |
| `TRACING ON` in `cqlsh`, or per-query tracing in a driver | Full per-query execution trace: which replicas were contacted, digest mismatches, time per stage | **safe-on-production for the traced query, adds overhead — avoid on a busy hot path** |
| `nodetool gcstats` / GC logs | Garbage-collection pause frequency and duration (Cassandra only) | **safe-on-production** |
| `nodetool cfstats` (older versions) | Predecessor to `tablestats`; same information | **safe-on-production** |
| Full repair (`nodetool repair`) | Confirms replica convergence | **not-safe-on-production without scheduling care** — I/O- and CPU-intensive; run incrementally and off-peak |

`tpstats`' pending/blocked counts are usually the fastest route to "is this node actually
saturated" — a growing pending count on `MutationStage` or `ReadStage` under load is a direct
capacity signal, distinct from any single query being slow.

---

## 4. Common failure modes and their symptoms

| Symptom | Cassandra/Scylla-specific cause to check first |
|:--|:--|
| Read rejected with a tombstone-related error | Partition read exceeded `tombstone_failure_threshold`; check delete/update pattern on that partition |
| Intermittent read latency spikes | Read repair triggered by replica digest mismatch, or (Cassandra only) a JVM GC pause |
| Write timeouts under load with no capacity change | Compaction falling behind (check `compactionstats` pending count) or hinted-handoff backlog |
| Node repeatedly marked down and back up | JVM GC pause exceeding the failure detector threshold (Cassandra; not applicable to Scylla) |
| `BATCH` statement slower than the equivalent individual writes | Multi-partition batch — coordinator fan-out plus batch-log overhead, not an optimization |
| LWT operations slow or timing out under concurrency | Paxos contention from repeated compare-and-set on the same partition |
| A table's read latency worsens steadily with no schema change | Compaction strategy mismatched to access pattern (commonly STCS under heavy point reads), or falling-behind compaction |
| Deleted data reappears | `gc_grace_seconds` elapsed without repair completing on every replica |
| A query plan requires `ALLOW FILTERING` | No partition key or index supports the query as written |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Compaction strategy (STCS/LCS/TWCS) | Read amplification versus compaction I/O versus fit for the access pattern (point read, scan, time-series) |
| `gc_grace_seconds` | Repair-window safety against tombstone resurrection versus how long tombstones (and their scan cost) persist |
| Read repair configuration | Replica convergence versus added read-path latency or background work |
| Consistency level per operation | Latency and availability versus staleness — see `databases/wide-column.md` §3 for the arithmetic against replication factor |
| Driver retry policy | Resilience to transient failures versus risk of duplicate effects on non-idempotent writes |
| Driver load-balancing policy (token-aware vs round-robin) | Direct-to-replica requests versus an extra coordinator hop |
| JVM heap size and GC settings (Cassandra only) | Throughput and GC pause frequency/length versus memory available to the OS page cache |
| `concurrent_compactors` / compaction throttling | Compaction keeping up with writes versus I/O contention with foreground reads |

---

## 6. Version differences worth knowing

Record the server version (and whether it is Cassandra or ScyllaDB) during discovery; the gap
between them matters more here than between most engine versions.

- Materialized views have carried known consistency caveats since their introduction and
  remain flagged as experimental in Cassandra's own documentation across the 3.x and 4.x
  line — treat any materialized view in a reviewed schema as worth scrutiny regardless of
  version.
- Cassandra 4.0 added zero-copy streaming (faster node addition/repair) and virtual tables
  (queryable internal metrics via CQL) — check whether diagnostics could use virtual tables
  instead of `nodetool` where both are available.
- Storage-Attached Indexes (SAI), introduced after the 4.x line, are a newer secondary-index
  implementation with different performance characteristics than the legacy secondary index
  or SASI — do not assume older secondary-index caveats apply to a schema using SAI without
  confirming which implementation is actually in use.
- ScyllaDB versions track their own release cadence independent of Cassandra's, despite
  wire-protocol compatibility; confirm Scylla-specific behavior (shard-per-core tuning,
  Scylla's own monitoring stack) against Scylla's own documentation, not Cassandra's.

**Confirm version-specific claims against the release notes for the deployed version and
engine.**

---

## 7. What this file does NOT cover

- Partition and clustering key modeling, query-first table design — see
  `databases/wide-column.md` §2.
- Consistency-level-versus-replication-factor arithmetic — see `databases/wide-column.md` §3.
- The general sorted-immutable-merge storage mechanism and tombstone accumulation as a
  concept — see `databases/wide-column.md` §1.
- Multi-datacenter replication topology, `NetworkTopologyStrategy` placement, and
  cross-datacenter consistency levels.
- Authentication, encryption in transit/at rest, and role-based access control.
- DataStax Astra's managed-service specifics: autoscaling behavior, serverless pricing, and
  Astra-only features.
- Backup and snapshot mechanics.
- Specific numeric threshold recommendations beyond the engine's own documented defaults
  cited above; recommend the measurement that determines the right value for a given
  workload.
