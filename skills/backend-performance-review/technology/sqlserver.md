# SQL Server

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, transaction scope as the dominant contention factor, and plan reading are
covered there and are not repeated here.

Everything below applies to Azure SQL Database (the PaaS offering) as well as on-premises
or VM-hosted SQL Server unless noted otherwise — the query engine, locking model, and
Query Store are shared; Azure SQL Database's own resource-governance and elastic-pool
specifics are out of scope (§7).

---

## 1. Detection signals

Dependencies: `pyodbc`, `pymssql` (Python), `System.Data.SqlClient`/`Microsoft.Data.SqlClient`
(the ADO.NET client — the older namespace is deprecated but still appears in older
codebases), `Microsoft.EntityFrameworkCore.SqlServer` (the EF Core provider — the dominant
way a .NET project declares this dependency; a repo can reference only this package
without a lockfile naming the underlying client library), `mssql` (Node.js), `tedious`
(the driver `mssql` wraps), the SQL Server JDBC driver (Java/Kotlin).

Other signals: `sqlserver://` connection strings, `mssql`/`azure-sql-edge` container
images, `.sql` migrations with T-SQL-specific syntax (`WITH (NOLOCK)`, `MERGE`,
`OUTPUT INTO`), Azure SQL Database or Managed Instance references in infrastructure code,
`sqlcmd` invocations in scripts.

**Record which product surface is deployed** — on-premises/VM SQL Server, Azure SQL
Database, Azure SQL Managed Instance, or Azure SQL Edge — during discovery; the query
engine and locking model below apply to all of them, but resource governance and some DMV
availability differ (§6).

---

## 2. What differs from the relational baseline

### Lock escalation converts many small locks into one big one, automatically

SQL Server automatically escalates row- or page-level locks to a table-level (or
partition-level) lock once a single statement holds roughly 5,000 locks on one object —
not a setting most codebases tune deliberately, but a threshold ordinary bulk operations
cross without warning. The category file's transaction-scope guidance (§4) explains why
long-held locks matter; escalation is the specific, checkable mechanism by which a large
UPDATE or DELETE that "should" only affect its own rows can suddenly block every other
statement touching the table. Chunking the same operation into batches small enough to
stay under the escalation threshold is the direct fix, distinct from the general "chunk
your bulk operations" advice in that both prevent long lock duration *and* avoid the
escalation cliff specifically.

### The default isolation level blocks readers against writers — SQL Server does not default to snapshot semantics

Unlike an MVCC-by-default engine, SQL Server's default `READ COMMITTED` uses locking: a
reader can block behind an uncommitted writer, and vice versa. Snapshot-style behavior
(readers never blocked by writers) is available but **opt-in**, via one of two
database-level settings with different costs:

- **`READ_COMMITTED_SNAPSHOT` (RCSI)** — makes ordinary `READ COMMITTED` reads use row
  versioning instead of locks, with no application code change required.
- **`ALLOW_SNAPSHOT_ISOLATION`** — enables an explicit `SNAPSHOT` isolation level that
  application code must request.

Both route versioning through **tempdb** (below), so enabling either trades reader/writer
blocking for tempdb version-store load — a real trade-off, not a free upgrade. A codebase
built assuming MVCC semantics (common among teams whose prior experience is Postgres or
MySQL) running against a SQL Server database with neither setting enabled is a checkable,
high-value finding: the blocking they're debugging may be the engine's documented default
behavior, not a bug.

### tempdb is a single shared resource behind several unrelated features

Row versioning (RCSI/snapshot isolation, above), user-created and internal temp tables,
sort and hash-join spills that exceed memory grant, and index rebuilds with `SORT_IN_TEMPDB`
all share one system database, instance-wide. A workload that stresses any one of these
degrades tempdb-dependent performance for every other workload on the same instance — a
distinct, checkable shared-resource risk from ordinary buffer-pool contention, and worth
identifying directly (`tempdb` file count and placement, and wait statistics naming
tempdb pages) rather than assuming a slowdown is query- or table-specific.

### Parameter sniffing turns one query's shape into every execution's plan

SQL Server caches a compiled plan keyed by query text and reuses it for later executions
with different parameter values. A plan compiled for an atypical first execution (a
parameter matching almost no rows, say) can be reused for a typical execution matching
millions — producing the classic "this query was fast yesterday, slow today, with no code
or data change" symptom. This is a distinct, checkable mechanism from ordinary stale
statistics (which the category file already covers, §1): the statistics can be perfectly
current and the plan still wrong for the parameter actually supplied. `OPTION (RECOMPILE)`,
plan guides, or the "optimize for ad hoc workloads" server setting are the checkable
mitigations, each with its own compilation-overhead trade-off.

### Heaps exist as a real, chosen alternative to a clustered index — and they fragment differently

A SQL Server table with no clustered index is a **heap**: rows have no enforced physical
order, located via a row identifier rather than a clustering key. This differs from an
engine where every table is implicitly clustered (as InnoDB is; see `technology/mysql.md`):
choosing a heap is a real, checkable schema decision, most often made for a pure
write-once staging table. Heaps have their own fragmentation mode not present in a
clustered table: an `UPDATE` that grows a row past its page's free space leaves a
**forwarding pointer** at the original location and moves the row, so a subsequent read
via that RID pays an extra page lookup — a heap under a growing-row `UPDATE` pattern
accumulates this cost in a way a clustered table does not.

### `WITH (NOLOCK)` is a common reflex that risks more than dirty reads

`WITH (NOLOCK)` (equivalent to `READ UNCOMMITTED`) is frequently added to a query
specifically to "avoid blocking" — but beyond permitting dirty (uncommitted) reads, it can
also **skip rows or read the same row twice** during a concurrent page split, because it
reads without the locking that would otherwise serialize against a structural page
change. This makes it a correctness risk wearing a performance-optimization disguise, not
merely a staleness trade-off — worth flagging directly wherever it appears in query text
(`grep`-able, like the category file's other index-defeating patterns) rather than
accepting it as a routine blocking fix.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `sys.dm_exec_query_stats` | Aggregated per-plan execution statistics: CPU, logical reads, execution count | **safe-on-production** |
| `sys.dm_os_wait_stats` | Cumulative wait-type totals since last restart — the primary instance-wide bottleneck signal | **safe-on-production** |
| `sys.dm_exec_requests` joined to `sys.dm_os_waiting_tasks` | Currently executing requests and what each is waiting on, right now | **safe-on-production** |
| `sys.dm_tran_locks` | Currently held locks, including escalated table-level locks | **safe-on-production** |
| `sys.dm_db_index_usage_stats` | Seeks/scans/lookups/updates per index — the basis for an "unused index" finding | **safe-on-production** |
| `SET STATISTICS IO, TIME ON` for one query | Logical/physical reads and CPU/elapsed time for that execution | **safe-on-production for the one query — avoid enabling broadly on a busy hot path** |
| Actual execution plan (`SET SHOWPLAN_XML ON` or SSMS "Include Actual Execution Plan") | Real row counts and operator choices, versus the estimated plan | **safe-on-production for the captured query — capturing adds overhead, avoid at high volume** |
| Query Store (SQL Server 2016+, and Azure SQL Database) | Historical plan-and-performance data per query, including automatic plan-regression detection | **safe-on-production** — capture overhead is modest and configurable; a very verbose capture policy adds meaningful overhead |
| `DBCC SHOW_STATISTICS` | Statistics histogram for one index/column, to test a stale-statistics hypothesis | **safe-on-production** |
| `sys.dm_db_index_physical_stats` | Index/heap fragmentation, including forwarded-record count for heaps | **safe-on-production for the default (`LIMITED`) mode — the `DETAILED` mode scans data and is not-safe-on-production on a large table** |

`sys.dm_os_wait_stats`, read as a delta across a time window rather than the
since-restart cumulative total, is usually the fastest route from "something feels slow"
to a specific wait category (locking, tempdb contention, I/O) worth chasing next.

---

## 4. Common failure modes and their symptoms

| Symptom | SQL Server-specific cause to check first |
|:--|:--|
| A bulk UPDATE/DELETE suddenly blocks unrelated queries on the same table | Lock escalation crossed the ~5,000-lock threshold for that statement |
| Readers and writers block each other with no obvious long transaction | `READ COMMITTED` locking default — RCSI/snapshot isolation not enabled |
| A query was fast, then became slow, with no data or code change | Parameter sniffing — a cached plan compiled for atypical parameter values |
| Widespread, seemingly unrelated slowness across many workloads | tempdb contention — check wait stats for tempdb-page waits and version-store size |
| Missing or duplicated rows from a query using `WITH (NOLOCK)` | Concurrent page split read without the lock that would otherwise prevent it — a correctness bug, not just staleness |
| A heap table's read performance degrades after updates with no row-count change | Forwarding pointers from rows that outgrew their page after an `UPDATE` |
| Query Store shows a plan regression at a specific point in time | A cached plan changed — check what execution triggered the recompile |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `READ_COMMITTED_SNAPSHOT` / `ALLOW_SNAPSHOT_ISOLATION` | Eliminating reader/writer blocking versus tempdb version-store overhead |
| `MAXDOP` / cost threshold for parallelism | Parallel query throughput versus thread contention (`CXPACKET`-family waits) |
| Query Store capture mode and retention | Plan-history diagnosability versus capture and storage overhead |
| tempdb file count and placement | Reduced tempdb contention versus additional storage/configuration complexity |
| Clustered index versus heap | Physical row ordering and range-scan support versus write-path simplicity for a staging/append-only table |
| Columnstore index on an otherwise row-store table | Analytical scan speed versus OLTP update cost on the same table |
| "Optimize for ad hoc workloads" server setting | Plan-cache memory pressure from single-use ad hoc plans versus first-execution compile cost |

---

## 6. Version differences worth knowing

Record the SQL Server version (or Azure SQL Database, which tracks the latest engine
continuously) and compatibility level during discovery — compatibility level can hold a
database's optimizer behavior at an older version even after a binary upgrade.

- **Query Store**, introduced in SQL Server 2016, is the primary tool for diagnosing
  parameter-sniffing and plan-regression symptoms after the fact; a database on an older
  version (or an on-prem instance not yet upgraded) has no equivalent history to inspect.
- **Adaptive query processing** (interleaved execution for multi-statement table-valued
  functions, memory grant feedback, table variable deferred compilation) arrived across
  SQL Server 2017–2019 and materially changed some of the plan-quality issues named above
  — do not assume an older written-up caveat about table variables or memory grants still
  applies at full severity without checking the deployed version.
- **Azure SQL Database** tracks the engine's latest version continuously (no discrete
  version number to record) but applies its own resource governance (DTU or vCore-based
  limits) on top of the same query engine — a wait-stat or DMV-based finding can still
  apply, but a resource-ceiling explanation should be checked against the tier's actual
  limits, not assumed away.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version or service tier.**

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, plan-reading signals, and transaction scope as the
  general contention factor — see `databases/relational.md`.
- Always On Availability Groups topology, failover behavior, and readable-secondary
  routing in depth.
- Azure SQL Database's and Managed Instance's own elastic-pool sizing, DTU-versus-vCore
  purchasing model, and serverless auto-pause/resume behavior.
- In-Memory OLTP (memory-optimized tables and natively compiled procedures) beyond noting
  it exists as a different storage engine within the same product.
- Security: Transparent Data Encryption, Row-Level Security, Always Encrypted, and
  authentication/authorization configuration.
- Backup, restore, and log-shipping mechanics.
- Specific numeric threshold recommendations beyond the documented defaults and
  mechanisms cited above (e.g. the exact lock-escalation threshold); recommend the
  measurement that determines the right value for a given workload.
