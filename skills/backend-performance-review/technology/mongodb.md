# MongoDB

Load after `databases/universal.md` and `databases/document.md`.

This file contains **only what those files do not give you**. Embedding versus referencing,
projection, unbounded arrays, and document design are covered there and are not repeated
here.

---

## 1. Detection signals

Dependencies: `pymongo`, `motor`, `mongoengine`, `beanie`, `mongoose`, `mongodb` (Node
driver), `mongo-go-driver`, `org.mongodb`, `MongoDB.Driver`, `Prisma` with
`provider = "mongodb"`.

Other signals: `mongodb://` or `mongodb+srv://` schemes, `mongo`/`mongod` container images,
`mongosh` or `mongo` in scripts, Atlas configuration in infrastructure code, `replicaSet=` or
`readPreference=` in a connection string, collection creation with validators.

The connection string is worth reading carefully (without recording credentials): it commonly
carries `readPreference`, `w`, `retryWrites`, `maxPoolSize`, and `readConcernLevel`, each of
which changes the performance analysis.

---

## 2. What differs from the document baseline

### The aggregation pipeline has an optimizer, and stage order matters

Unlike a simple find, a pipeline is a sequence of stages, and its cost is dominated by how
much data reaches each one. The optimizer moves some stages earlier, but not all, and not
across every boundary.

Practical review rules:

- `$match` as early as possible — ideally as the first stage, so an index can serve it.
- `$project` or `$unset` early to reduce document size flowing through later stages.
- `$sort` before `$limit` can use an index and stop early; `$sort` after a `$group` cannot.
- `$lookup` is the expensive stage. It runs per input document, so filtering before it is
  what determines cost. A `$lookup` early in a pipeline over an unfiltered collection is the
  characteristic MongoDB performance bug.
- `$unwind` multiplies document count; anything after it pays the multiplied count.
- Once a stage prevents index use, no later stage recovers it.

### Sort has a hard memory limit

An in-memory sort that exceeds the server's limit fails outright unless disk use is
explicitly allowed. This makes an unindexed sort a **correctness cliff**, not just a slow
path: it works during development at small data sizes and starts erroring in production.
Allowing disk use converts the error into a slow query rather than fixing it.

An index supporting the sort avoids both outcomes. This is a stronger argument for
sort-supporting indexes than in engines that simply spill.

### The working set and the storage engine cache

Reads served from cache are fast; reads requiring disk are not. The engine's cache is
typically sized as a fraction of available memory, and it must also hold index data. Two
review consequences:

- **Total index size competing with document data for cache** makes index proliferation more
  costly here than the generic rule suggests.
- **When the working set outgrows cache, degradation is sharp rather than gradual** — the
  same step-change described in `databases/universal.md` §5, but often more pronounced.

### Index intersection is limited

MongoDB can intersect indexes but usually prefers a single compound index, and the
single-index plan is generally the better one. Practical consequence: designing one compound
index for a query shape is more reliable than expecting several single-field indexes to
combine. The equality-then-sort-then-range field ordering guideline follows from this and is
worth checking against actual query shapes.

### Write acknowledgement and read concern are explicit dials

Both appear in connection strings and per-operation options, and both directly trade latency
against guarantees:

- Write concern ranges from unacknowledged through acknowledgement by a majority of replica
  set members. Majority acknowledgement costs a round trip to replicas on every write.
- Read preference determines whether reads go to the primary or to secondaries; secondary
  reads add capacity and introduce staleness.
- Read concern determines what visibility guarantee a read has; stronger levels cost more.

Findings arise when these are set by accident rather than by requirement — for example
secondary reads on a path that reads its own writes, or majority write concern on a
high-volume non-critical write path.

### Transactions are relatively expensive

Multi-document transactions exist but carry more overhead than single-document atomic
operations, and long-running ones can be aborted by the server. A data model that requires
frequent multi-document transactions is usually a model worth revisiting — which loops back
to document design as the real lever.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `db.coll.find(...).explain("queryPlanner")` | Chosen plan, index used, without executing | **safe-on-production** |
| `db.coll.find(...).explain("executionStats")` | Documents examined vs returned, keys examined, execution time | **safe-on-production for reads** |
| `db.coll.aggregate([...], {explain: true})` | Pipeline plan and stage-by-stage behavior | **safe-on-production for read pipelines** |
| `db.currentOp()` | Operations in flight, with duration and waits | **safe-on-production** |
| `db.coll.getIndexes()` | Index definitions | **safe-on-production** |
| `db.coll.aggregate([{$indexStats:{}}])` | Per-index usage counts — finds indexes paying write cost for nothing | **safe-on-production** |
| `db.serverStatus()` | Connections, cache statistics, operation counters | **safe-on-production** |
| `db.coll.stats()` | Document count, average size, index sizes | **safe-on-production** |
| Profiler at level 1 with a slow-operation threshold | Actual slow operations in production | **safe-on-production with care** — adds write load; use a high threshold and a bounded profile collection |
| Profiler at level 2 (all operations) | Everything | **not-safe-on-production** |
| `explain("allPlansExecution")` on a write pipeline | Plan comparison | **not-safe-on-production** — executes |

The key number in `executionStats` is **`totalDocsExamined` versus `nReturned`**. A large
ratio means the query is reading far more than it returns — the same examined-versus-returned
signal as elsewhere, and the fastest route to a real finding. `totalKeysExamined` alongside it
distinguishes an index that narrows well from one that does not.

---

## 4. Common failure modes and their symptoms

| Symptom | MongoDB-specific cause to check first |
|:--|:--|
| A sort operation fails with an error at scale but works in development | Unindexed sort exceeding the memory limit |
| Aggregation slow and getting worse | `$lookup` or `$unwind` before `$match`; unfiltered input to an expensive stage |
| `COLLSCAN` in a plan | No usable index, or a query shape the compound index ordering does not support |
| Sharp performance drop with no code change | Working set outgrowing the storage engine cache |
| High write latency on an otherwise simple insert | Many indexes, especially multikey indexes on large arrays |
| Reads returning stale data | Secondary read preference plus replication lag |
| Connection count high at the cluster | Per-instance pool multiplied by instances; drivers maintain their own pools per host |
| Documents growing and updates slowing | Repeated growth causing relocation, updating every index entry |
| Queries fast on one shard and slow overall | Uneven shard key distribution, or scatter-gather queries lacking the shard key |
| A query uses a different index than expected | Plan cache holding a plan chosen under different data conditions |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Storage engine cache size | More cache versus memory left for the OS and connections; must fit inside container limits |
| `maxPoolSize` (per driver, per host) | Concurrency versus total server connections — multiply by instances |
| Write concern | Durability versus write latency on every write |
| Read preference | Read capacity versus staleness; the read-after-write hazard |
| Read concern | Visibility guarantee versus cost |
| `retryWrites` | Resilience versus duplicate-effect risk on non-idempotent operations |
| Profiler level and threshold | Evidence versus write overhead |
| `allowDiskUse` on aggregations | Prevents failure; converts it into a slow, I/O-heavy operation — usually a signal that an index is missing |
| Compression settings | Storage and I/O versus CPU |
| Shard key | The most consequential and least reversible decision; determines distribution, scatter-gather frequency, and hot-shard risk |

The shard key deserves separate treatment in any sharded deployment: a monotonically
increasing shard key concentrates writes on one shard, and a key absent from common queries
forces scatter-gather across all of them. Changing it later is expensive.

---

## 6. Version differences worth knowing

Record the server version and the driver version during discovery; both matter.

- Aggregation pipeline capabilities and optimizer behavior have expanded substantially across
  recent major versions; advice that says a stage cannot use an index may be outdated.
- Multi-document transaction support arrived for replica sets in 4.0 and for sharded clusters
  in 4.2 — reasoning that assumes no transactions at all applies only to much older versions.
- Time-series collections (5.0) change the analysis entirely for time-series workloads and
  should be treated as a different category.
- Clustered collections (5.3) alter physical document ordering and the cost profile of range
  scans on the clustering key.
- Default write concern changed to majority in 5.0, which affects write latency for
  deployments upgraded from earlier versions without revisiting the setting.
- Driver versions differ in pooling defaults and in whether operations are retried by default.

**Confirm version-specific claims against the release notes for the deployed version.** If
the version is undeterminable, keep recommendations version-independent and say why.

---

## 7. What this file does NOT cover

- Document design, embedding versus referencing, unbounded arrays, projection — see
  `databases/document.md`.
- General index reasoning and selectivity — see `databases/universal.md`.
- Connection pool arithmetic — see `application/connection-pools.md`.
- Replica set election behavior, oplog sizing, and initial sync cost.
- Sharding topology design beyond the shard-key cautions above: chunk balancing, zone
  sharding, and resharding are deep topics not treated here.
- Atlas-specific features: Search indexes, Data Federation, Online Archive, and autoscaling
  behavior. These have distinct performance models — treat conclusions about them as unknowns.
- Change streams and their resource cost on the cluster.
- Specific numeric recommendations for any setting; recommend the measurement that determines
  it.
