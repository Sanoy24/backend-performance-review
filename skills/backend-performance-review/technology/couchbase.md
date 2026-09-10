# Couchbase

Load after `databases/universal.md` and `databases/document.md`.

This file contains **only what those files do not give you**. Document design, embedding
versus referencing, projection, and the index fundamentals of a document store are covered
there and are not repeated here.

---

## 1. Detection signals

Dependencies: `couchbase` (the SDK package name in both Python and Node.js), `gocb` (Go),
`com.couchbase.client` (Java/Kotlin/Scala Maven coordinate), `CouchbaseNetClient` (.NET),
`couchbase-lite` (mobile/edge — a different product, note it rather than assuming the
server).

Other signals: `couchbase://` and `couchbases://` connection strings, `couchbase/server`
container images, N1QL/SQL++ query text in application code (`SELECT ... FROM \`bucket\``
with backtick-quoted keyspaces), `cbq`/`cbimport`/`cbbackupmgr` invocations in scripts,
Sync Gateway configuration.

**Record which services are deployed on which nodes** during discovery — Couchbase runs
Data, Query, Index, Search, Analytics, and Eventing as separately deployable services, and
a cluster running Query and Data on the same nodes has a resource-contention profile a
service-separated cluster does not.

---

## 2. What differs from the document baseline

### Two access paths with radically different costs, and choosing the wrong one is the defining finding

Couchbase exposes the same documents through **key-value operations** (direct fetch by
document ID, memory-resident, sub-millisecond) and through **N1QL/SQL++ queries** (parsed,
planned, index-dependent, orders of magnitude more expensive). A query whose predicate is
effectively "the document ID I already have" — `SELECT * FROM bucket USE KEYS ...`, or
worse a `WHERE META().id = ...` — is doing query-service work for something the KV path
answers directly.

This is the single most checkable Couchbase-specific finding, and it has no equivalent in
the document category file, which assumes one query path. In application code it looks like
a repository layer that reaches for the query API uniformly because that is the familiar
shape from a relational ORM.

### The working set is expected to live in memory, and the resident ratio is the health metric

Couchbase is memory-first: each bucket has a RAM quota, and documents are ejected to disk
when it is exceeded. A **cache miss** — a read for a document no longer resident — costs a
disk fetch and is visible as `cache_miss_ratio` / resident-item-ratio in the stats. A
cluster whose working set has outgrown its RAM quota does not degrade gently in the way a
disk-based engine does; its whole latency profile changes.

The **ejection policy** determines what a miss costs: *value-only* ejection keeps every
document's metadata in RAM (so a miss fetches the value), while *full* ejection removes
metadata too (so even determining existence may touch disk, and metadata no longer bounds
memory by document count). Which policy a bucket uses is a checkable configuration fact
with a direct bearing on how a high-document-count workload behaves.

### Every N1QL predicate needs a Global Secondary Index, and the fallback is a full scan

Unlike engines that scan when no index fits, Couchbase's query service requires an index to
service a query at all. The escape hatch — a **primary index** (`CREATE PRIMARY INDEX`) —
makes any query runnable by scanning every document in the keyspace. It is frequently
created during development to make queries "work" and then left in place, converting
missing-index problems into silent full scans rather than errors.

**A primary index existing on a production keyspace is a checkable, high-value finding**,
and it is visible in DDL, migration scripts, or `system:indexes`. Covering indexes (where
the index contains every field the query needs, avoiding the fetch back to the data
service) are the corresponding win, and `EXPLAIN` shows whether one is being used.

### Documents are distributed across a fixed 1024 vBuckets, hashed by key

The key's hash determines its vBucket, and vBuckets are distributed across data nodes and
moved wholesale during rebalance. The count is fixed at cluster creation, so unlike engines
where partition count is a tuning decision, the checkable question here is key
distribution: a keyspace whose IDs share a structure that hashes unevenly, or a workload
concentrated on a small set of keys, produces node-level imbalance that no rebalance fixes.

### Durability level is a per-operation latency decision, not a cluster-wide setting

A write can be acknowledged when it reaches the active node's memory (the default, fastest,
and least durable), or only after replication and/or persistence to a configurable number
of nodes. Since Couchbase 6.5 this is expressed as durability levels (`majority`,
`majorityAndPersistActive`, `persistToMajority`); older code uses `PersistTo`/`ReplicateTo`
parameters. Each step up adds a round trip or a disk wait to *every write that requests
it*. Code that applies a strong durability level uniformly — including to writes whose loss
would be tolerable — is paying for a guarantee it did not need, and this is visible per call
site in application code.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `EXPLAIN <n1ql query>` | Plan, index selection, whether a primary-index scan is used | **safe-on-production** |
| `ADVISE <n1ql query>` | Index recommendations for a statement | **safe-on-production** |
| `SELECT * FROM system:indexes` | Every index, its keyspace, keys, and state — the basis for a primary-index or unused-index finding | **safe-on-production** |
| `SELECT * FROM system:active_requests` | Currently executing queries | **safe-on-production** |
| `SELECT * FROM system:completed_requests` | Recently completed queries above the logging threshold, with elapsed time and result counts | **safe-on-production** |
| `cbstats <host> all` (and `cbstats ... timings`) | Per-node KV engine statistics including resident ratio, cache miss ratio, disk write queue | **safe-on-production** — read-only, but run against one node at a time |
| Web console → Buckets / Statistics | Resident-item ratio, cache-miss ratio, disk queue depth, ops/sec per bucket | **safe-on-production** |
| `cbq` with `profile = 'timings'` | Per-operator timing within one query's execution | **safe-on-production for the one query** |
| `SELECT * FROM system:vitals` | Per-node resource vitals for the query service | **safe-on-production** |

Cache-miss ratio and resident-item ratio together answer the first question worth asking
about any Couchbase latency complaint — whether the working set still fits the RAM quota —
before any query-level investigation is worth starting.

---

## 4. Common failure modes and their symptoms

| Symptom | Couchbase-specific cause to check first |
|:--|:--|
| Read latency degrades broadly and disk I/O rises, with no query change | Working set outgrew the bucket RAM quota; resident ratio falling, cache misses rising |
| A query is slow and `EXPLAIN` shows `PrimaryScan` | No usable GSI for the predicate; a primary index is masking the missing index |
| Queries are fast in isolation but slow under load, while KV ops stay fast | Query-service contention — check whether Query and Data services share nodes |
| Writes are slower than expected for simple documents | A strong durability level applied per-operation, adding replication or persistence waits |
| One data node is consistently hotter than its peers | Uneven key hashing across vBuckets, or a concentrated hot-key workload |
| Latency spikes during a rebalance | vBucket migration moving data between nodes; expected, but its duration and impact scale with dataset size |
| `Fetch` dominates query timing in a profile | Index is not covering — the query returns to the data service per document |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Bucket RAM quota | Resident working set (and therefore latency profile) versus memory shared across buckets and services |
| Ejection policy (value-only versus full) | Metadata always resident, bounding memory by document count, versus supporting far more documents per node at a higher miss cost |
| Number of replicas | Failover safety versus memory and network cost on every write |
| Durability level per operation | Write safety versus an added round trip or disk wait on each write that requests it |
| Covering versus non-covering GSI | Avoiding the fetch back to the data service versus a larger index and more write-time index maintenance |
| Index replicas / `num_replica` on GSI | Query availability and read throughput versus index build and storage cost |
| Service co-location (Data/Query/Index on the same nodes) | Fewer nodes versus contention between query execution and KV serving |

---

## 6. Version differences worth knowing

Record the server version during discovery; Couchbase's data model gained a level of
hierarchy in 7.0 that changes what queries and index definitions look like.

- **Scopes and collections arrived in 7.0**, adding two levels between bucket and document.
  Pre-7.0 code addresses `` `bucket` `` directly; 7.0+ code addresses
  `` `bucket`.`scope`.`collection` ``. A codebase mixing both forms, or one that migrated
  buckets to collections without revisiting its indexes, is worth checking — indexes are
  defined per collection.
- **Multi-document ACID transactions** arrived in 6.5 (via the SDKs) and were extended in
  7.0 with SQL++ transaction statements. Their cost profile is not that of a single KV
  write, and code using them for operations that do not need atomicity is paying for it.
- **Durability levels replaced `PersistTo`/`ReplicateTo` in 6.5**; both forms still appear
  in code, and they are not identical in semantics.
- **N1QL is called SQL++ in current documentation** — the same language; older material and
  code comments use the older name.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version.**

---

## 7. What this file does NOT cover

- Document design, embedding versus referencing, projection, and index fundamentals in a
  document context — see `databases/document.md`.
- The Search (FTS), Analytics, and Eventing services beyond noting that they are separately
  deployable and contend for node resources when co-located.
- Couchbase Mobile / Sync Gateway / Couchbase Lite replication topology and conflict
  resolution.
- Cross Datacenter Replication (XDCR) configuration and its bandwidth/lag characteristics.
- Cluster operations: rebalance orchestration, failover policy, and node sizing formulas.
- Capella (the managed offering) and its instance sizing and billing model.
- Security: authentication, RBAC configuration, and encryption settings.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
