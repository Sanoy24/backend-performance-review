# MySQL / MariaDB

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, transaction scope, and plan reading are covered there and are not repeated
here. Assumes the **InnoDB** storage engine, the default and near-universal choice for a
transactional workload; see §6 for MyISAM and MariaDB-specific divergence.

---

## 1. Detection signals

Dependencies: `mysqlclient`, `PyMySQL`, `aiomysql`, `mysql2`, `mysql-connector`,
`go-sql-driver/mysql`, `com.mysql` (Connector/J), `mariadb` client libraries, `Sequelize`/
`Prisma`/`TypeORM` with a MySQL or MariaDB provider.

Other signals: `mysql://` or `mariadb://` schemes, `mysql`/`mariadb` container images,
`my.cnf`/`mysqld.cnf`, RDS/Aurora MySQL or Azure Database for MySQL in infrastructure code,
ProxySQL or MySQL Router in compose or manifests.

**Record which product (MySQL or MariaDB) and which major version** during discovery — they
share wire protocol and SQL surface but have diverged in optimizer internals and feature set
since MariaDB 10.x (see §6), and several behaviors below are version-dependent even within
MySQL alone. Also record the storage engine actually in use per table where determinable
(`information_schema.tables.engine`) — a MyISAM table in an otherwise InnoDB schema is a real,
checkable finding (see §6), not an assumption to skip.

---

## 2. What differs from the relational baseline

### The primary key *is* the table, and secondary indexes pay for it

InnoDB stores table data as a **clustered index** ordered by primary key — there is no separate
heap the way Postgres or most other engines have one. Every **secondary index** stores the
primary-key value where a heap engine would store a physical row pointer, so satisfying a query
through a secondary index means a lookup into that index, followed by a second lookup into the
clustered index to fetch the row — unless the secondary index already **covers** the query
(includes every column the query needs), in which case the second lookup is skipped entirely.
This is the single highest-value InnoDB-specific thing to check when a secondary-index-driven
query is slower than the index's selectivity alone would predict.

**Primary key choice is consequently a much bigger performance decision in InnoDB than in a
heap-organized engine.** Because rows are physically stored in primary-key order, inserting with
a monotonically increasing key (an auto-increment integer) appends to the end of the clustered
index cheaply; inserting with a random key (a UUID, a hash) forces a page split somewhere in the
middle of the existing data on essentially every insert, fragmenting the table and its indexes
over time. A UUID primary key on a high-insert-rate table is a specific, checkable, and common
regression — the fix is usually a surrogate auto-increment key with the UUID kept as a unique
secondary index, not abandoning UUIDs for external identifiers entirely.

### REPEATABLE READ is the default isolation level, and it locks more than the matched rows

Unlike Postgres's `READ COMMITTED` default, InnoDB defaults to `REPEATABLE READ`, which uses
**gap locking** and **next-key locking** to prevent phantom reads: a range scan or an `UPDATE`/
`DELETE` with a `WHERE` clause can lock the gaps *between* index records, not just the rows that
matched. A developer whose mental model comes from a `READ COMMITTED`-default engine
consistently under-predicts lock scope and contention here — a range update that looks like it
should only affect a handful of rows can block concurrent inserts into gaps nearby, and this is a
frequent, specific cause of deadlocks that look inexplicable from the application code alone.
Switching the isolation level to `READ COMMITTED` is a real, available trade — less lock
contention, at the cost of losing InnoDB's phantom-read protection for that transaction — worth
surfacing as an option, not assuming as the fix.

### The buffer pool is sized differently than a Postgres-style `shared_buffers`

`innodb_buffer_pool_size` plays a similar role to Postgres's `shared_buffers`, but the sizing
guidance is not interchangeable: MySQL relies much less on the OS page cache for its data pages
(InnoDB commonly uses direct I/O), so the buffer pool is conventionally sized at **70–80% of
available RAM on a dedicated database host** — far higher than the roughly-25%-of-RAM guidance
common for Postgres's `shared_buffers`. A buffer pool sized using Postgres-style reasoning is a
specific, checkable misconfiguration that under-utilizes available memory and produces avoidable
disk reads.

### EXPLAIN's row estimates were coarser before histogram support, and it doesn't execute by default

Cardinality estimation for non-indexed columns was materially cruder before **optimizer
statistics histograms** (MySQL 8.0) existed — a bad plan on a filter over an unindexed column in
an older version may simply reflect this gap rather than a data-distribution problem worth
chasing further. As with Postgres, plain `EXPLAIN` shows estimates only; getting actual executed
row counts and timing requires the executing form (`EXPLAIN ANALYZE`, available from MySQL
8.0.18) — before that version, there is no built-in equivalent, and timing must come from the
slow query log or `performance_schema` instead.

### Query cache, where still present, is a known write-scalability hazard

MySQL 5.7 and earlier shipped an optional query cache that stored full result sets keyed by exact
query text, invalidated wholesale on any write to a referenced table, behind a single global
mutex. Under any nontrivial write rate it becomes a serialization point rather than a speedup,
and it was **removed entirely in MySQL 8.0**. Finding it still enabled on an older deployment
under write load is a specific, checkable finding, not a hypothetical one; the practical answer
is disabling it, not tuning its size.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `EXPLAIN <query>` | Planned execution, estimates only | **safe-on-production** |
| `EXPLAIN ANALYZE <query>` (8.0.18+) | Actual rows and timing per step | **not-safe-on-production** for a write statement — it executes. For a `SELECT`, prefer a replica or off-peak window given real execution occurs |
| `SHOW ENGINE INNODB STATUS` | Buffer pool usage, deadlock history, active transactions, lock waits | **safe-on-production** |
| `performance_schema` (statement digests, wait events, I/O by table) | Aggregate cost by normalized query — the MySQL analogue of `pg_stat_statements` | **safe-on-production**, though enabling every instrument adds measurable overhead; the default instrument set is safe |
| `sys` schema views (e.g. `sys.statement_analysis`, `sys.schema_unused_indexes`) | Pre-aggregated, human-readable views over `performance_schema` | **safe-on-production** — same underlying cost as the `performance_schema` tables they read |
| `SHOW PROCESSLIST` / `performance_schema.threads` | Currently running queries and their state | **safe-on-production** |
| Slow query log (`long_query_time`) | Queries exceeding a duration threshold, with full text | **safe-on-production** at a reasonable threshold; a very low threshold logging most traffic adds real I/O overhead |
| `information_schema.tables` (`data_length`, `index_length`, `engine`) | Table and index size, storage engine per table | **safe-on-production** |

`performance_schema`'s statement digest tables deserve the same emphasis given to
`pg_stat_statements` in the Postgres reference — where enabled, they turn speculation about
"which query is actually expensive" into a single query's worth of evidence.

---

## 4. Common failure modes and their symptoms

| Symptom | MySQL/InnoDB-specific cause to check first |
|:--|:--|
| Insert throughput degrades and table/index size grows faster than row count | A random (UUID/hash) primary key causing clustered-index page splits on every insert |
| A secondary-index-driven query is slower than its selectivity suggests | The index doesn't cover the query, forcing a second lookup into the clustered index per matched row — check whether adding the missing columns to the index avoids it |
| Deadlocks or blocked inserts that don't map to any obviously overlapping row | Gap/next-key locking under the default `REPEATABLE READ` isolation level — check `SHOW ENGINE INNODB STATUS`'s deadlock section before assuming application-level contention |
| High disk read rate despite a large configured buffer pool | `innodb_buffer_pool_size` sized using non-InnoDB (e.g. Postgres-style) guidance rather than the conventional 70–80%-of-RAM figure for a dedicated host |
| A filter on a non-indexed column produces a poor plan on an older version | Cardinality estimation without histogram support (pre-8.0) — confirm the version before assuming a data or statistics problem |
| Write-heavy workload periodically stalls under moderate load, on a 5.7-or-earlier deployment | Query cache still enabled — a single global mutex serializing writes against any cached read |
| A read replica falls progressively further behind under write load | Single-threaded (or coarsely parallel) replication apply on an older version — check the configured replication parallelism and its granularity before assuming it's already maximized |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `innodb_buffer_pool_size` | Cache hit rate versus memory — conventionally 70–80% of RAM on a dedicated host, a different sizing philosophy from Postgres's `shared_buffers` |
| `innodb_flush_log_at_trx_commit` | `1` (default) gives full durability at a per-commit fsync cost; `0`/`2` trade durability for commit latency — a data-loss risk on crash that must be stated explicitly, not a free win |
| Transaction isolation level (`REPEATABLE READ` vs `READ COMMITTED`) | Phantom-read protection and consistent snapshots versus reduced gap-lock contention — a real, application-visible trade, not purely a performance knob |
| `innodb_file_per_table` | Per-table tablespace files that reclaim disk space on `DROP`/`ALTER` versus one shared tablespace that doesn't shrink |
| `long_query_time` / slow query log verbosity | Observability into slow queries versus logging I/O overhead at very low thresholds |
| `innodb_io_capacity` / `innodb_io_capacity_max` | Faster background flushing (dirty-page and change-buffer merges) versus more I/O contention with foreground query traffic |
| Replication parallelism (multi-threaded replica apply) | Reduced replication lag versus the coordination overhead of parallelizing apply, and dependence on the workload actually partitioning well across threads |

---

## 6. Version differences worth knowing

- **Record the storage engine per table.** MyISAM — table-level locking, no transactions, no
  crash-safe recovery — still turns up in older schemas or specific tables (often for
  full-text search, before InnoDB gained full-text support). A MyISAM table on a write-heavy or
  transactional path is a correctness-adjacent finding (see `SKILL.md` rule 8), not merely a
  performance one.
- **MySQL 8.0** added histogram-based optimizer statistics, invisible indexes (test removing an
  index without dropping it), descending indexes, window functions and CTEs, and **removed the
  query cache entirely**. Advice from 5.7-era documentation should be treated as version-specific,
  not general.
- **`ALGORITHM=INSTANT` DDL** (MySQL 8.0.12+) allows adding a column without rewriting the table
  in many cases — before this, and still for operations it doesn't cover, an `ALTER TABLE` can
  lock or rewrite the whole table. Confirm the version and the specific DDL operation before
  assuming an online, non-blocking schema change is possible.
- **`EXPLAIN ANALYZE`** (actual execution, not just estimates) is only available from **8.0.18**;
  before that, real timing requires the slow query log or `performance_schema`.
- **MariaDB is not MySQL with a different name.** It has its own optimizer, its own histogram and
  parallel-replication implementations, and version-specific features (system-versioned tables,
  the Aria storage engine) that do not exist in MySQL and vice versa. Confirm which product is
  actually deployed before applying a version-specific claim from one to the other.

**Confirm version-specific claims against the release notes for the version and product actually
deployed** rather than asserting them generically.

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, compound index ordering, plan reading — see
  `databases/relational.md`.
- Connection pool arithmetic — see `application/connection-pools.md`.
- Galera Cluster or NDB Cluster multi-master replication topologies — architecturally distinct
  from single-primary InnoDB replication and out of scope here.
- Aurora MySQL and other MySQL-compatible managed engines with a different storage layer
  underneath; their I/O and buffer-pool reasoning may not transfer directly — note the divergence
  rather than applying this file wholesale.
- MyISAM internals beyond flagging its presence as a finding in §6; it is legacy and not the
  assumed engine for this file's reasoning.
- Specific numeric recommendations for any setting. Every value depends on hardware, workload,
  and data; recommend the measurement that determines it.
