# CockroachDB

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, transaction scope as the dominant contention factor, and plan reading are
covered there and are not repeated here.

---

## 1. Detection signals

Dependencies: CockroachDB speaks the **PostgreSQL wire protocol**, so applications connect
with ordinary Postgres drivers — `pgx`, `psycopg`/`psycopg2`, `pg`, `npgsql`,
`org.postgresql` — and nothing in the dependency manifest names CockroachDB at all. The
`postgres` signal will fire on such a repository; this signal fires only when something
else names the engine.

Other signals: `cockroachdb`/`cockroach` in a connection string, image name, or CLI
invocation; a `*.cockroachlabs.cloud` host in a connection URL or environment template; the
`crdb_internal` schema referenced in a query or migration; `cockroach sql`/`cockroach
start` in scripts; the `sslmode`/`options=--cluster=` parameter form used by CockroachDB
Serverless.

**This is the most important discovery note in this file: a repository can be running
CockroachDB while every dependency and connection string looks like PostgreSQL.** When the
`postgres` signal fires, confirm which engine is actually deployed before applying
`technology/postgres.md`'s engine-specific claims — vacuum, WAL, and buffer-cache
reasoning do not transfer, and the transaction-retry requirement below has no Postgres
equivalent.

---

## 2. What differs from the relational baseline

### Transactions can be aborted by the engine, and the application is required to retry

CockroachDB's default (and, before v23.2, only) isolation level is **`SERIALIZABLE`**,
enforced optimistically. When two transactions conflict in a way that cannot be serialized,
one is aborted with a **retry error** (`40001`, `restart transaction`) — not because
anything failed, but as normal operation under contention. **Application code is required
to wrap transactions in a retry loop**; code ported from a single-node engine, where a
transaction that reaches `COMMIT` generally succeeds, will surface these as user-facing
5xx errors under load.

This is the single most consequential difference from the relational baseline, and it is
directly checkable in application code: look for transaction execution without any retry
handling, or an ORM configured without its CockroachDB-specific retry adapter. The
category file's contention guidance (§4) explains why long transactions are costly here as
elsewhere; what is specific to this engine is that contention produces *errors the
application must handle*, not merely waiting.

### Data lives in ranges, and a sequential key concentrates writes on one of them

Tables are split into **ranges** (~512 MiB by default) distributed across nodes and
rebalanced automatically. A monotonically increasing primary key — a sequence, an
auto-increment column, a timestamp — directs every new insert to the single range holding
the highest keys, and therefore to the one node leading that range. This is the
distributed-SQL form of the hot-partition problem the wide-column and key-value category
files name, and it appears in a schema that would be entirely reasonable on a single-node
engine.

The documented alternatives are checkable in DDL: a `UUID` primary key (CockroachDB's
`gen_random_uuid()` default), or an explicit **hash-sharded index**
(`USING HASH WITH (bucket_count = ...)`) when the sequential ordering is actually needed
for range scans.

### Every write costs a consensus round trip, so write latency has a network floor

Each range is replicated (three replicas by default) and every write must reach a quorum
via Raft before acknowledging. Write latency is therefore bounded below by the round-trip
time between replicas — across availability zones or regions, that floor can exceed
anything disk-related by an order of magnitude, and no amount of local tuning moves it.
A finding that would be "slow disk" on a single-node engine is frequently "replicas are in
three regions" here. Replica placement is a checkable fact (`SHOW RANGES`, zone
configurations), not an assumption.

### A query touching many ranges becomes a distributed query

Execution fans out to every node holding a range the query needs. A full table scan is
therefore not merely "a scan" — it is a scan coordinated across nodes, with intermediate
results shipped over the network. The consequence for review: the cost gap between an
indexed lookup and a full scan is *wider* here than the category file's single-node framing
implies, and `EXPLAIN (DISTSQL)` shows which nodes a query actually recruits.

### Multi-region deployments make read locality an explicit, checkable schema decision

Where a cluster spans regions, CockroachDB exposes locality as schema-level configuration —
`REGIONAL BY ROW` tables, `GLOBAL` tables, and per-table/database survival goals — plus
**follower reads** (`AS OF SYSTEM TIME follower_read_timestamp()`), which serve slightly
stale reads from a local replica instead of paying a cross-region trip to the leaseholder.
An application reading recent-but-not-strictly-current data across regions without follower reads
is paying avoidable latency; whether staleness is acceptable is a product decision to
surface, not one to make in the review.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `EXPLAIN <query>` | Estimated plan, index selection | **safe-on-production** |
| `EXPLAIN ANALYZE <query>` | Actual plan with real row counts and execution time | **not safe-on-production for an expensive query** — it executes the statement |
| `EXPLAIN (DISTSQL) <query>` | Which nodes the query recruits and how work is distributed | **safe-on-production** |
| `EXPLAIN ANALYZE (DEBUG) <query>` | A downloadable statement bundle: plan, statistics, trace | **not safe-on-production for an expensive query** — it executes |
| `SHOW STATEMENTS` / `crdb_internal.cluster_queries` | Currently running statements cluster-wide | **safe-on-production** |
| `crdb_internal.transaction_contention_events` | Recorded transaction contention, including which keys and which conflicting transactions | **safe-on-production** |
| `SHOW RANGES FROM TABLE <t>` | Range boundaries, replica placement, and leaseholder per range — the basis for a hot-range finding | **safe-on-production** |
| `crdb_internal.cluster_sessions` | Open sessions and their state, including idle-in-transaction | **safe-on-production** |
| `SHOW STATISTICS FOR TABLE <t>` | Table statistics and when they were last collected | **safe-on-production** |
| DB Console → Hot Ranges / Statements pages | Per-range QPS and per-statement aggregate latency, without writing SQL | **safe-on-production** |

The Statements page and `transaction_contention_events` together are usually the fastest
route from "slow under load" to either a specific statement or a specific contended key
range — the two very different causes this engine most often produces.

---

## 4. Common failure modes and their symptoms

| Symptom | CockroachDB-specific cause to check first |
|:--|:--|
| Intermittent `40001` / `restart transaction` errors surfacing as 5xx under load | Serializable retry errors with no application retry loop — expected engine behavior, not a bug |
| Write throughput plateaus while CPU is idle across most nodes | Sequential primary key concentrating inserts on one range's leaseholder |
| Write latency is high and stable regardless of load or disk speed | Raft quorum round trip across zones/regions — a network floor, not an I/O problem |
| One node is far busier than the rest | Hot range — check `SHOW RANGES` and the DB Console Hot Ranges page |
| A query is far slower than its single-node equivalent would be | Distributed execution fanning out across ranges; check `EXPLAIN (DISTSQL)` |
| Cross-region reads are slow despite local replicas existing | Reads going to the leaseholder rather than using follower reads or a `REGIONAL BY ROW` design |
| A long-running query fails with a "batch timestamp must be after GC threshold" error | The query (or `AS OF SYSTEM TIME` value) reaches further back than the range's GC TTL retains |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Replication factor and zone/region placement of replicas | Survival guarantees versus the consensus latency floor on every write |
| Primary key design (`UUID` / hash-sharded versus sequential) | Even write distribution versus the range-scan locality a sequential key provides |
| `gc.ttlseconds` per zone configuration | How far back `AS OF SYSTEM TIME` and long queries can read, versus storage held for old versions |
| Follower reads (`AS OF SYSTEM TIME follower_read_timestamp()`) | Local read latency versus bounded staleness — a product decision, not purely technical |
| `--max-sql-memory` | Headroom for sorts/hashes before spilling to disk versus memory available to the rest of the node |
| Automatic statistics collection | Plan quality versus the background cost of collection on large tables |
| `SERIALIZABLE` versus `READ COMMITTED` (v23.2+, see §6) | Fewer application-visible retries versus giving up serializable guarantees the schema may assume |

---

## 6. Version differences worth knowing

Record the cluster version during discovery; CockroachDB's release cadence moves defaults
frequently, and advice written for an older major can be wrong rather than merely dated.

- **`READ COMMITTED` isolation became available in v23.2.** Before that, `SERIALIZABLE`
  was the only isolation level, and every deployment necessarily required application-side
  retry handling. On a cluster where `READ COMMITTED` is in use, the retry-loop finding
  above weakens considerably — but so do any serializability assumptions the application's
  invariants were relying on. Confirm which is configured rather than assuming either.
- **Multi-region abstractions** (`REGIONAL BY ROW`, `GLOBAL` tables, survival goals)
  arrived in v21.1 and have changed since; a cluster on an older version may be
  hand-managing locality through zone configurations instead, which is harder to read but
  functionally similar.
- **CockroachDB Serverless/Basic** clusters bill by request unit and scale to zero — cold
  starts and per-request costing become real dimensions that do not exist on a dedicated
  cluster, and the connection string carries an `options=--cluster=` parameter that some
  drivers require explicit configuration to pass through.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version.**

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, plan-reading signals, and transaction scope as the
  general contention factor — see `databases/relational.md`.
- PostgreSQL's own engine internals, which do **not** transfer despite the shared wire
  protocol — see `technology/postgres.md` only when Postgres is what is actually deployed.
- Changefeeds/CDC, backup and restore, and import/export mechanics.
- Cluster operations: adding/decommissioning nodes, upgrade orchestration, and rebalancing
  behavior beyond naming that it happens automatically.
- Enterprise-licensed features and the licensing model itself; confirm availability before
  recommending anything gated behind it.
- Security: authentication methods, certificate management, and row-level security.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
