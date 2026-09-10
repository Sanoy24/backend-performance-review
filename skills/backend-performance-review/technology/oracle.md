# Oracle Database

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, transaction scope as the dominant contention factor, and plan reading are
covered there and are not repeated here.

Everything below applies to on-premises, VM-hosted, and Oracle Cloud (Autonomous Database
and DBCS) deployments unless noted otherwise — the SQL/PL execution engine, shared pool,
and undo-based read consistency are the same underlying database; Autonomous Database's
own auto-scaling and auto-tuning layers are out of scope (§7).

---

## 1. Detection signals

Dependencies: `cx_Oracle` (legacy) and `oracledb` (its successor, python-oracledb — the
same package name is also Oracle's official Node.js driver, so a bare `oracledb` hit needs
the surrounding manifest to disambiguate the runtime), `ojdbc` (the JDBC driver jar/artifact
name — `ojdbc8`, `ojdbc11`, etc. — present whether declared via Maven/Gradle coordinate or a
vendored jar), `oracle.jdbc` (the Java import package), `godror` (the dominant modern Go
driver; the older `go-oci8` still appears in legacy code), `Oracle.ManagedDataAccess` (the
official ODP.NET managed driver's NuGet package prefix, covering both the .NET Framework and
.NET Core/`.Core`-suffixed packages).

Other signals: `jdbc:oracle:thin:@` (the JDBC connection-string prefix — catches a
properties file or `application.yml` even when no driver dependency is declared explicitly,
the same pattern this registry already uses for SQLite's `jdbc:sqlite:`), a TNS-style Easy
Connect descriptor (`//host:port/service_name` inside an Oracle-labeled connection
property), a `tnsnames.ora` or `sqlnet.ora` file present anywhere in the repository,
`sqlplus` invocations in scripts, `oracle/database` container images.

**Record whether a service name (a pluggable database, §6) or a legacy SID is used to
connect** during discovery — this determines which V$ views (§3) are scoped to the whole
container instance versus one pluggable database.

---

## 2. What differs from the relational baseline

### Literal values in SQL text, not bind variables, is Oracle's most consequential anti-pattern

Oracle's shared pool caches a parsed-and-optimized execution plan keyed by the *exact text*
of a SQL statement. A statement built by concatenating a literal value into the SQL string
(`WHERE id = 47` versus `WHERE id = :id`) is, to the shared pool, a completely different
statement from the next call with a different literal — each one requires a full parse and
optimization ("hard parse") rather than reusing a cached plan, and every hard parse takes a
latch on the shared pool, a resource every session parsing SQL contends for. A high-throughput
code path built with string-concatenated literals rather than bind variables produces a hard-
parse storm that degrades the whole instance, not just that one query — the single most
cited Oracle-specific performance defect in the database's own literature, and directly
checkable from application code: grep for string concatenation building SQL text, or the
`CURSOR_SHARING` setting (§5) if the driver layer cannot be changed.

### Read consistency is undo-based, and running out of undo produces a distinct, named failure

The category file (§4) already names the general point that a multi-version engine's old
row versions must eventually be cleaned up. Oracle's specific mechanism: every write records
its prior version in the **undo tablespace**, and a long-running query's read-consistent
view is reconstructed from that undo as of the query's start SCN (system change number).
If a concurrent write's undo needed to satisfy that reconstruction has already been
overwritten (the undo tablespace is finite and reused in a ring), the query fails outright
with **`ORA-01555: snapshot too old`** — a distinct, named error most other MVCC engines do
not have an equivalent for by this exact mechanism. This is a checkable interaction between
two independent things: a long-running query's duration, and the undo tablespace's
retention (`UNDO_RETENTION`) versus the write volume competing for that same undo space —
increasing retention or sizing undo larger buys headroom, it does not remove the coupling.

### PL/SQL exists specifically to avoid a per-statement network round trip, and row-by-row procedural code forfeits that

Oracle's PL/SQL engine runs inside the database process itself; a stored procedure or
package can execute many SQL statements without a network round trip between each one — the
entire reason PL/SQL exists as a performance mechanism, not only a language choice.
Procedural code that still loops over rows one at a time, issuing one SQL statement per
iteration (a cursor `FOR` loop with an `INSERT`/`UPDATE` inside it), pays a context switch
between the PL/SQL and SQL engines on every iteration — cheaper than a full network round
trip, but still a per-row cost that **`BULK COLLECT`** (bulk fetch) and **`FORALL`** (bulk
DML) exist specifically to eliminate by batching the whole set through one context switch.
This is the PL/SQL-specific form of the "per-row operation in a loop" finding the category
file already names generically — the checkable evidence here is a cursor loop containing a
single-row DML statement inside a package or procedure body.

### A bitmap index is a real, chosen trade-off — and a dangerous default for a write-heavy table

Oracle offers bitmap indexes as an alternative to the default B-tree, built for low-
cardinality columns in read-heavy analytical workloads (a `status` or `region` column
scanned across millions of rows). Their structure is the risk on a concurrent-write table:
one bitmap-index entry can cover a wide range of rowids, so updating a single indexed
column on one row can lock every row sharing that bitmap segment, not just the row being
changed — a correctness-adjacent concurrency hazard, not merely a slower write. A bitmap
index found on a column that is also updated by concurrent OLTP transactions is a checkable,
high-severity finding regardless of how well it performs the read queries it was added for.

### A sequence's `CACHE` size is a real, checkable contention knob

An Oracle `SEQUENCE` pre-allocates and caches a block of numbers in memory (default `CACHE
20`) so concurrent `NEXTVAL` calls usually do not need to persist a change for every single
value. A sequence created or altered to `NOCACHE` — or a small cache size under genuinely
high concurrent insert volume — forces every `NEXTVAL` to update the sequence's own data
dictionary entry, serializing on that entry the same way a hot counter row serializes
writers (the category file's general hot-row finding, here with a specific, checkable
setting as both cause and fix). A cache size increase is close to free: gaps in the sequence
on instance restart are already an accepted property of Oracle sequences, not a new
trade-off this introduces.

### Real Application Clusters (RAC) adds a cross-instance cost with its own wait-event vocabulary

Where deployed, RAC lets multiple Oracle instances share one database via **Cache Fusion** —
a data block held in one instance's buffer cache is shipped to another instance over the
private interconnect on demand, rather than each instance reading independently from disk.
A workload with high write concurrency on the same rows/blocks from sessions connected to
*different* instances pays interconnect latency moving that block back and forth ("block
ping-ponging"), visible as `gc buffer busy` and related `gc` (global cache) wait events —
RAC-specific, and not present at all on a single-instance deployment. Confirm during
discovery whether RAC is actually in play; the finding does not apply otherwise.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `V$SQL` / `V$SQLAREA` | Cached SQL text, parse/execution counts, and `VERSION_COUNT` — a high version count for one statement is a symptom of the bind-variable problem above | **safe-on-production** |
| `V$SQL_SHARED_CURSOR` | Why a statement failed to share an existing cursor (bind mismatch, optimizer-environment difference) | **safe-on-production** |
| `V$SESSION` joined to `V$SESSION_WAIT` | What each session is doing right now, and what it is waiting on | **safe-on-production** |
| `V$LOCK` / `V$SESSION_BLOCKERS` | Current blocking-lock chains | **safe-on-production** |
| `V$UNDOSTAT` | Recent undo consumption and retention headroom — the basis for investigating `ORA-01555` risk before it occurs | **safe-on-production** |
| `EXPLAIN PLAN FOR ...` then `DBMS_XPLAN.DISPLAY` | Estimated plan for one statement | **safe-on-production** |
| `DBMS_XPLAN.DISPLAY_CURSOR` | The *actual* executed plan with real row counts, for a statement already in the shared pool | **safe-on-production** |
| SQL Trace (`10046` event) + `TKPROF` | Per-statement parse/execute/fetch timing and wait events for one session | **not safe-on-production at broad scope** — enabling for one targeted session is low-impact; instance-wide tracing adds meaningful overhead |
| AWR (`DBMS_WORKLOAD_REPOSITORY`) and ASH (`V$ACTIVE_SESSION_HISTORY`) reports | Historical instance-wide performance snapshots and sampled active-session activity | **safe-on-production to read** — **requires the separately licensed Diagnostics Pack on most Oracle Database editions**; confirm licensing before recommending it, not merely technical access |
| `V$SEGMENT_STATISTICS` | Per-segment (table/index) logical reads, buffer busy waits, ITL waits | **safe-on-production** |

`V$SESSION_WAIT` (or `V$ACTIVE_SESSION_HISTORY` where the Diagnostics Pack is licensed),
read as "what wait event dominates right now," is usually the fastest route from "something
feels slow" to a specific mechanism worth chasing next — the same triage role
`sys.dm_os_wait_stats` plays for SQL Server.

---

## 4. Common failure modes and their symptoms

| Symptom | Oracle-specific cause to check first |
|:--|:--|
| Instance-wide slowness under load, high `library cache: mutex X` or latch waits | Hard-parse storm from literal values in SQL text instead of bind variables |
| A long-running report or batch job fails with `ORA-01555: snapshot too old` | Undo tablespace retention too small (or undersized) for the query's duration and concurrent write volume |
| A stored procedure processing many rows is slower than expected despite simple logic | Row-by-row cursor loop issuing per-row DML instead of `BULK COLLECT`/`FORALL` |
| Unexpected blocking or deadlocks on a table with a bitmap index under concurrent writes | Bitmap index locking granularity — one update can lock rows far beyond the one changed |
| High contention specifically around `NEXTVAL` under heavy concurrent inserts | Sequence `CACHE` size too small, or `NOCACHE` |
| High `gc buffer busy`/`gc cr block` wait time (RAC only) | Cross-instance block contention (Cache Fusion) from concurrent writes to the same data from different instances |
| A query's plan changed for no code or data reason | Stale or newly-gathered optimizer statistics (`DBMS_STATS`), or a bind-variable "peeking" mismatch on a skewed column |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `CURSOR_SHARING` (`EXACT` / `FORCE`/`SIMILAR`) | `FORCE` lets the optimizer substitute bind variables for literals automatically, reducing hard parses — at the cost of the optimizer sometimes choosing a less-specific plan than it would for the literal value; fixing the application's SQL to use bind variables directly is the more durable answer |
| `UNDO_RETENTION` and undo tablespace size | Headroom against `ORA-01555` for long-running queries versus undo tablespace storage cost |
| Sequence `CACHE` size | Reduced `NEXTVAL` contention versus a larger gap in sequence values on an instance restart (already an accepted property, not a new risk) |
| Bitmap versus B-tree index choice | Analytical scan/compression benefit versus OLTP write-locking granularity on the same column |
| `OPTIMIZER_MODE` and statistics-gathering job schedule | Plan stability and quality versus the overhead and risk of a stats-gathering job coinciding with peak load |
| PGA/SGA memory allocation (`PGA_AGGREGATE_TARGET`, `SGA_TARGET`) | Buffer-cache hit rate and per-session sort/hash-join memory versus host memory shared with everything else on the instance |

---

## 6. Version differences worth knowing

Record the Oracle Database version and, since 12c, whether the deployment is a container
database (CDB) with one or more pluggable databases (PDBs) or the older non-multitenant
architecture — this changes which V$ views are container-scoped versus PDB-scoped and is a
real, checkable architectural fact, not a cosmetic naming difference.

- **Multitenant architecture (CDB/PDB)**, introduced in 12c and the default topology from
  19c onward, lets multiple pluggable databases share one instance's SGA and background
  processes — a resource contention finding (shared pool pressure, buffer cache contention)
  can originate from a *different* PDB on the same container, not necessarily the one being
  reviewed. Confirm which PDBs share the container before attributing a resource finding to
  one application alone.
- **19c is Oracle's long-term-support release**; many enterprise deployments pin to it
  specifically for its extended support window. A production system still on 11g or 12c
  (both long past their original support lifecycles) should have version-specific behavior
  treated as an unknown until confirmed against that exact version's documentation, not
  assumed to match current-version behavior.
- **Native JSON support** (a dedicated `JSON` data type and functions) arrived in 21c,
  after an earlier constraint-based JSON validation approach on `VARCHAR2`/`BLOB` columns
  in 12c — a schema found modeling JSON via the older constraint approach is not a bug, but
  is worth confirming the deployed version actually supports the newer type before
  recommending a migration to it.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version.**

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, plan-reading signals, and transaction scope as the
  general contention factor — see `databases/relational.md`.
- Real Application Clusters topology, interconnect sizing, and services-based workload
  routing beyond naming Cache Fusion's contention mechanism (§2, §4).
- Data Guard (physical/logical standby) replication and failover behavior.
- Autonomous Database's and Oracle Cloud's own auto-scaling, auto-indexing, and
  auto-tuning layers, which can change or mask some of the mechanisms above.
- Licensing and edition (Standard Edition 2 versus Enterprise Edition, and Enterprise
  Edition's separately-licensed options and packs) beyond the one Diagnostics Pack caveat
  in §3 — confirm licensing before recommending any feature gated behind it.
- Security: Transparent Data Encryption, Virtual Private Database, Data Redaction, and
  authentication/authorization configuration.
- Backup, recovery, and RMAN mechanics.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
