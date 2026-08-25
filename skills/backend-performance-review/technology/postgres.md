# PostgreSQL

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, transaction scope, and plan reading are covered there and are not repeated
here.

---

## 1. Detection signals

Dependencies: `psycopg` / `psycopg2`, `asyncpg`, `pg8000`, `SQLAlchemy` with a `postgresql`
dialect, `pg`, `postgres.js`, `Prisma` with `provider = "postgresql"`, `pgx`, `lib/pq`,
`Npgsql`, `org.postgresql`, `Diesel`/`sqlx` with a Postgres feature.

Other signals: `postgres://` or `postgresql://` schemes, `postgres`/`timescale`/`pgvector`
container images, `pg_hba.conf`, `postgresql.conf`, `psql` in scripts, Alembic or
Flyway migrations containing Postgres-specific DDL, `pgbouncer`/`pgpool` in compose or
manifests, RDS/Aurora/Cloud SQL Postgres instances in infrastructure code.

Extensions worth noting because they change the analysis: `pgvector` (vector workload),
`TimescaleDB` (time-series), `PostGIS` (spatial index behavior), `pg_stat_statements`
(evidence is available).

---

## 2. What differs from the relational baseline

### MVCC produces dead rows, and dead rows are a whole-database concern

An `UPDATE` writes a new row version and leaves the old one; a `DELETE` marks rather than
removes. Reclaiming that space is `autovacuum`'s job. Three consequences that do not exist
in engines using in-place updates or undo logs:

- **Bloat.** Heavily updated tables and their indexes grow beyond their live data. Scans read
  more pages, the working set inflates, and cache effectiveness falls.
- **A long-running transaction blocks cleanup globally.** Vacuum cannot remove row versions
  that any open transaction might still need — including transactions on *other* tables. One
  forgotten idle-in-transaction session can degrade the whole database. This is the single
  most Postgres-specific failure mode worth checking, and it is invisible in application code.
- **Update-heavy tables need more aggressive autovacuum than the defaults provide.** Defaults
  are scaled proportionally to table size, so large tables are vacuumed relatively less often
  as they grow.

Related: **HOT updates** avoid rewriting index entries when no indexed column changes and the
new row version fits on the same page. Practical implication: updating an indexed column is
substantially more expensive than updating an unindexed one, and every additional index
raises update cost more than it raises insert cost.

### Index-only scans depend on vacuum

Postgres can answer from the index alone only when the visibility map says the page is
all-visible — which vacuum maintains. A covering index on a table that is not being vacuumed
effectively will not deliver index-only scans. This links index performance to maintenance
in a way the generic relational model does not capture.

### Index types beyond B-tree

B-tree is the default and the subject of the category file. Postgres also offers:

| Type | Suits | Cost |
|:--|:--|:--|
| GIN | Containment queries — array elements, JSONB keys, full-text | Slow to update; larger |
| GiST | Ranges, geometric, nearest-neighbour | Lossy; may need recheck |
| BRIN | Very large tables with strong physical/value correlation, typically append-only time series | Tiny; useless without correlation |
| Hash | Equality only | Rarely worth it over B-tree |

**Partial indexes** (`WHERE` clause) and **expression indexes** are frequently the right
answer where the category file would suggest a plain index: a partial index on the small
active subset of a large table is far cheaper on both storage and write path.

### TOAST

Large values are compressed and moved out of line automatically. Consequences: `SELECT *` on
a table with large text or JSONB columns pays de-TOASTing that a narrower projection would
avoid; and a wide column can make an otherwise cheap scan expensive.

### Connections are relatively expensive

Each connection is backed by a process. Connection count therefore has a real memory and
scheduling cost, and Postgres tolerates high connection counts less gracefully than engines
using a thread-per-connection or multiplexed model. This is why an external pooler is a
common part of a Postgres deployment rather than an exotic addition.

### `work_mem` is per operation, not per connection

This is the most misread setting in Postgres. It applies to *each* sort, hash, or similar
operation — a single complex query can use several multiples of it, and many concurrent
queries multiply that again. Raising it globally to fix one slow sort is a common route to
memory exhaustion. Where a plan shows a spill to disk, the safer recommendation is to raise
it for that session or role, not the cluster.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `EXPLAIN <query>` | Planned execution, estimates only | **safe-on-production** |
| `EXPLAIN (ANALYZE, BUFFERS) <select>` | Actual rows, timing, and pages read | **safe-on-production for reads**; measure on a replica when possible |
| `EXPLAIN (ANALYZE) <insert/update/delete>` | Same | **not-safe-on-production** — it executes the statement. Wrap in a transaction and roll back, or use a copy |
| `SELECT * FROM pg_stat_statements ORDER BY total_exec_time DESC` | Aggregate cost by normalized query — usually the fastest route to the real top consumers | **safe-on-production** (extension must be enabled) |
| `SELECT * FROM pg_stat_activity WHERE state <> 'idle'` | Running queries, wait events, transaction start times | **safe-on-production** |
| `pg_stat_activity` filtered on `state = 'idle in transaction'` | The long-transaction problem above, directly | **safe-on-production** |
| `SELECT * FROM pg_stat_user_tables` | Sequential vs index scans, dead tuple counts, last autovacuum | **safe-on-production** |
| `SELECT * FROM pg_stat_user_indexes` | Index usage — finds indexes paying write cost for nothing | **safe-on-production** |
| `SELECT * FROM pg_locks` joined to `pg_stat_activity` | Blocking chains | **safe-on-production** |
| `SELECT * FROM pg_stat_database` | Cache hit ratio, deadlocks, conflicts | **safe-on-production** |
| `\d+ table` in `psql` | Schema, indexes, storage parameters | **safe-on-production** |

`pg_stat_statements` deserves emphasis: where it is enabled, it turns speculation into
evidence in one query, and it is the first thing to ask for. Note that its totals are
cumulative since the last reset — a large total may be a cheap query run constantly, which is
itself useful information.

`BUFFERS` is worth including in every `ANALYZE` — the shared hit versus read split tells you
whether the working set is in cache, which is usually the actual question.

---

## 4. Common failure modes and their symptoms

| Symptom | Postgres-specific cause to check first |
|:--|:--|
| Whole database gradually slowing, no code change | Bloat from ineffective autovacuum, often caused by a long-running or idle-in-transaction session |
| A query that was fast becomes slow overnight | Plan flip after statistics changed, or a table crossing the size where a scan is no longer preferred |
| High CPU on the database with low query volume | Bloat causing large scans; or JIT compilation on frequently-executed short queries |
| Sudden out-of-memory on the database host | `work_mem` multiplied by concurrent operations; or a hash aggregate the planner under-estimated |
| Connection errors under load | `max_connections` exceeded — check pool × workers × max replicas, plus migrations and monitoring agents |
| `idle in transaction` sessions accumulating | Application acquiring a connection and beginning a transaction before doing non-database work |
| An index exists but is not used | Type mismatch, a function applied to the column, `LIKE '%...'`, low selectivity, or stale statistics |
| Index-only scan expected but not happening | Visibility map not current — a vacuum question, not an index question |
| Autovacuum "never finishes" on a big table | Default cost-delay settings throttling it below the rate of change |
| Writes stalling periodically | Checkpoint activity; correlate with checkpoint settings |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `shared_buffers` | Postgres's own cache versus leaving memory for the OS page cache, which Postgres also relies on |
| `effective_cache_size` | Not an allocation — a planner hint about total available cache. Too low pushes the planner away from index scans |
| `work_mem` | Per-operation memory versus total memory risk. Prefer per-role or per-session overrides |
| `maintenance_work_mem` | Faster vacuum and index builds versus memory |
| `random_page_cost` | Lowering it (typical on SSD) makes index scans more attractive; the default is historically tuned for spinning disks |
| `default_statistics_target` | Better estimates versus planning time and analyze cost; can be raised per column |
| `max_connections` | Must exceed the application's arithmetic with headroom; raising it is not free — consider a pooler instead |
| `statement_timeout` | Bounds runaway queries. Its absence means one query can hold a connection indefinitely |
| `idle_in_transaction_session_timeout` | Directly mitigates the long-transaction/vacuum problem. Frequently unset |
| `lock_timeout` | Prevents unbounded waiting behind a lock, especially during migrations |
| Autovacuum scale factors and cost limits | More aggressive cleanup versus background I/O. Per-table overrides are usually better than global changes |
| `jit` | Helps long analytical queries; adds compilation overhead to short ones executed frequently |
| `synchronous_commit` | Durability versus commit latency. Relaxing it is a data-loss trade that must be stated explicitly |

The three timeout settings are worth calling out together: `statement_timeout`,
`idle_in_transaction_session_timeout`, and `lock_timeout` are cheap, high-value defenses that
are unset in a large share of deployments.

### Poolers

An external pooler (PgBouncer and similar) reduces connection cost, and its **pooling mode
matters**:

- Session pooling — a client holds a server connection for its whole session; safest, least
  multiplexing benefit.
- Transaction pooling — the common choice; a server connection is held only for a
  transaction. It restricts session-scoped features: session-level prepared statements,
  `SET` for the session, advisory locks held across statements, `LISTEN`/`NOTIFY`.

If the application uses any of those and the pooler is in transaction mode, that is a
correctness finding. Check the driver's prepared-statement behavior specifically — several
drivers use them by default and need explicit configuration to work in transaction pooling.

---

## 6. Version differences worth knowing

Record the major version during discovery; several behaviors depend on it.

- Partitioning support and pruning behavior have improved substantially across the version 10
  to 13 range; advice based on older inheritance-based partitioning does not apply to modern
  declarative partitioning.
- B-tree index deduplication (introduced in version 13) reduced index size significantly for
  indexes with many duplicate values; index-bloat advice predating it may overstate the
  problem.
- JIT compilation is enabled by default from version 12 onward and can add overhead to short
  queries executed at high frequency — a known cause of unexplained CPU on OLTP workloads.
- Connection-handling and snapshot-scaling improvements in version 14 reduced the cost of
  high connection counts, but did not eliminate the underlying process-per-connection model.
- `CREATE INDEX CONCURRENTLY` and `REINDEX CONCURRENTLY` availability and reliability differ
  by version; check before recommending an online index build.

**Confirm version-specific claims against the release notes for the version actually
deployed** rather than asserting them. If the version cannot be determined, say so and keep
the recommendation version-independent.

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, compound index ordering, plan reading — see
  `databases/relational.md`.
- Connection pool arithmetic — see `application/connection-pools.md`.
- Replication topology and failover behavior beyond noting replication lag exists.
- Logical replication, and its interaction with long transactions and slot retention.
- Extension-specific tuning: `pgvector` index parameters and recall trade-offs, TimescaleDB
  hypertables and compression, PostGIS spatial indexing. These are separate workloads with
  their own performance models; treat conclusions about them as unknowns.
- Aurora, AlloyDB, and other Postgres-compatible engines. Their storage layers differ enough
  that I/O and checkpoint reasoning here may not transfer — note the divergence rather than
  applying this file wholesale.
- Specific numeric recommendations for any setting. Every value depends on hardware, workload,
  and data; recommend the measurement that determines it.
