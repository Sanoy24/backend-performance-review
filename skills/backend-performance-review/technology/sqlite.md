# SQLite

Load after `databases/universal.md` and `databases/relational.md`.

This file contains **only what those files do not give you**. Index fundamentals,
selectivity, plan-reading signals, and transaction scope as the dominant contention factor
are covered there and are not repeated here.

SQLite is architecturally unlike every other engine this skill covers `deep`: it is an
**embedded library, not a server process**. There is no network protocol, no separate
server to connect to, and — the single fact that drives most of what follows — **only one
writer to the database file at a time, ever, by design, not by tunable configuration.**
Confusing it with "a small Postgres" is the most common category error in a review that
encounters it, and this file exists mainly to prevent that error.

---

## 1. Detection signals

Dependencies: `sqlite3` (Python, standard library), `better-sqlite3`/`node:sqlite` (Node.js),
`mattn/go-sqlite3`/`modernc.org/sqlite` (Go), the `sqlite-jdbc` driver — **`org.xerial`**
is the Maven group ID that names it; a repo declaring only this dependency has no other
string containing the literal word "sqlite" (see the note below), `Microsoft.Data.Sqlite`
(.NET), `libsql` (a SQLite-compatible fork with its own client libraries).

Other signals: `sqlite://` or `jdbc:sqlite:` connection strings, a `.db`/`.sqlite`/`.sqlite3`
file committed to or referenced in the repository, `PRAGMA` statements in migration files,
`ATTACH DATABASE` in application code.

**A repository declaring only `sqlite-jdbc` (`org.xerial:sqlite-jdbc` in a Maven/Gradle
file) is a real, previously-fixed detection gap** (§3.13 of `docs/evaluation.md`): the
dependency's own name contains no substring matching "sqlite" at the artifact-ID level in
every build-file format, and it was invisible to this signal until `org.xerial` was added
explicitly as its own match token. Treat this as a reminder to read the actual dependency
coordinates, not just skim for the word "sqlite", when confirming this signal by hand.

**Confirm where the database file actually lives** during discovery — see §2's filesystem
point — and whether the deployment is stock SQLite, `libsql`/Turso (§6), or SQLCipher
(encrypted-at-rest; out of scope, §7).

---

## 2. What differs from the relational baseline

### There is exactly one writer, and it is a whole-file lock, not a row or page lock

Where the category file's transaction-scope guidance (§4) is about lock *duration*, SQLite's
locking is different in *kind*: writing requires locking the entire database file (in the
default rollback-journal mode) or serializing against every other writer (in WAL mode,
below) — there is no row-level or page-level write lock to contend over more finely, because
the engine was not built for concurrent writers to begin with. A second connection
attempting to write while another write is in progress receives `SQLITE_BUSY` immediately,
or waits up to `busy_timeout` (default: return the error immediately, since the default
timeout is zero) before receiving it. A retry-storm of `SQLITE_BUSY` errors under concurrent
write load is normal, expected behavior for this engine at this concurrency level, not a bug
to work around with more connections — the fix is fewer concurrent writers, batched writes,
or a different engine, not a larger pool.

### WAL mode buys concurrent readers, not concurrent writers

Write-Ahead Logging mode lets readers proceed against the last-committed state while a
single writer appends to a separate WAL file, instead of the rollback-journal default's
readers-blocked-during-commit behavior — a real, checkable availability improvement for a
read-heavy workload. It does **not** allow a second concurrent writer; the single-writer
rule above still applies in WAL mode. WAL introduces its own new resource to watch: the WAL
file must be periodically **checkpointed** (its contents folded back into the main database
file) to stay bounded, and a long-held read transaction can block a checkpoint from
completing — a WAL file growing without bound under continuous write load, with no error and
no obvious cause in application code, is this mechanism's characteristic symptom.

### A connection pool sized for concurrency does not buy write concurrency here

`application/connection-pools.md`'s general pooling arithmetic assumes more connections
means more concurrent work gets done. For SQLite, every connection to the same database
file is still bound by the single-writer rule above: a pool sized to 20 for a SQLite-backed
service provides at most 20-way concurrent *reads* under WAL mode, and zero additional
concurrent-*write* throughput — in rollback-journal mode, a larger pool can actually
increase `SQLITE_BUSY` contention by increasing how often two connections attempt to write
at once. A connection-pool size chosen using the same reasoning as for a client-server
engine is a checkable, SQLite-specific finding.

### The database file's location is a correctness precondition, not a performance tuning question

SQLite's locking relies on filesystem-level primitives that are not reliably implemented by
every filesystem — network filesystems (NFS, and many container-volume or cloud-storage
mount types) are documented as unsafe for a SQLite database that more than one process might
open, because the lock itself may not work correctly, risking silent corruption rather than
a visible error. A SQLite file path resolving to a network-mounted volume is a severity-worth
finding to raise directly during discovery, before any performance question is even asked.

### Type affinity makes an implicit-conversion index miss more likely than the category file's general warning suggests

The category file (§2) already names implicit type conversion as a predicate that defeats an
index. SQLite's column types are **affinities** — advisory hints, not enforced types, unless
a table is declared `STRICT` (§6) — so a column declared `INTEGER` can still store a text
value, and whether a comparison between a stored value and a supplied parameter can use an
index depends on whether their storage types, not just their declared types, match. This
makes the category's general "watch for implicit conversion" caution concretely more likely
to bite here than in a strictly-typed engine, and worth checking for directly wherever a
query compares a column against a literal or parameter of a different apparent type.

### Nothing runs in the background — not autovacuum, not auto-`ANALYZE`, not a checkpoint daemon

Where a client-server engine runs its own background maintenance (autovacuum, statistics
refresh, WAL checkpointing driven by a server process), SQLite has no server process to run
any of it: `VACUUM`, `ANALYZE`, and WAL checkpoints all happen either as a side effect of
ordinary operation (an automatic checkpoint after enough WAL growth, by default) or only when
the application explicitly issues them. A schema that has never had `ANALYZE` run against it
has query-planner statistics that were never populated in the first place, not merely stale
— and a database file that only grows despite heavy deletes has no autovacuum daemon coming
to reclaim the space unless `auto_vacuum` is configured or `VACUUM`/`PRAGMA incremental_vacuum`
is called explicitly, on some schedule the application itself must own.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `PRAGMA journal_mode` | Whether the database is in the default rollback-journal mode or WAL | **safe-on-production** |
| `PRAGMA wal_checkpoint(PASSIVE)` | Attempts a checkpoint and reports outstanding WAL frame count | **safe-on-production** — `PASSIVE` never blocks; other modes below can |
| `PRAGMA busy_timeout` | The currently configured busy-retry timeout | **safe-on-production** |
| `EXPLAIN QUERY PLAN <query>` | Whether a query uses an index (and which) or a full table scan | **safe-on-production** |
| `PRAGMA compile_options` | Build-time options — confirms WAL and threading support are actually compiled in | **safe-on-production** |
| `PRAGMA integrity_check` / `PRAGMA quick_check` | Structural database-file integrity | **not-safe-on-production for a large database** — holds a read lock and scans the whole file; can run for a long time |
| `PRAGMA wal_checkpoint(TRUNCATE)` or `(RESTART)` | Forces a full checkpoint and truncates or restarts the WAL file, to test a WAL-growth hypothesis | **not-safe-on-production** — blocks until it can acquire the necessary lock, which can mean waiting on or blocking other connections |
| `sqlite3` CLI's `.timer on` | Rough wall-clock timing for ad hoc queries during investigation | **safe-on-production for the query being timed** |

There is no server to query for cluster- or instance-wide state; every diagnostic above is
scoped to one database file, reachable only from a process that can open it.

---

## 4. Common failure modes and their symptoms

| Symptom | SQLite-specific cause to check first |
|:--|:--|
| `SQLITE_BUSY` errors under concurrent write attempts | Expected single-writer contention — check `busy_timeout` and whether write concurrency can be reduced |
| WAL file grows without bound | A long-held read transaction preventing checkpoint completion |
| A larger connection pool made write errors worse, not better | More connections attempting concurrent writes against the single-writer lock |
| Silent data corruption with no clear cause | Database file located on a network filesystem or unsupported volume-mount type |
| A query with a seemingly-relevant index still full-scans | Type-affinity mismatch between the stored and compared value, not just the general implicit-conversion case |
| Query planner makes visibly poor choices | `ANALYZE` was never run — no background process populates statistics automatically |
| Database file only grows despite regular deletes | No `auto_vacuum` configured and `VACUUM`/incremental vacuum never called — nothing reclaims space on its own |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `journal_mode` (`DELETE`/`WAL`/others) | Concurrent-read availability during a write versus WAL's checkpoint-management overhead |
| `synchronous` (`OFF`/`NORMAL`/`FULL`) | Write latency versus durability guarantee on power loss or crash |
| `busy_timeout` | Immediate `SQLITE_BUSY` error versus waiting (and adding latency) for a conflicting writer to finish |
| `auto_vacuum` (`NONE`/`FULL`/`INCREMENTAL`) | Automatic space reclamation versus the fragmentation and per-operation cost that reclamation adds |
| `cache_size` | Page-cache memory used versus reliance on the OS filesystem cache instead |
| `mmap_size` | Memory-mapped I/O read performance versus address-space/memory usage |
| `wal_autocheckpoint` | Checkpoint frequency (and its momentary I/O cost) versus how large the WAL file is allowed to grow between checkpoints |

---

## 6. Version differences worth knowing

SQLite maintains strict backward file-format compatibility — a version difference here is
almost always about which pragmas or features are available, not a breaking on-disk change.
Record the version, and whether the deployment is stock SQLite or a fork, during discovery.

- **WAL mode** requires SQLite 3.7.0 (2010) or later; effectively universal today, but worth
  confirming on anything unusually old or embedded in a legacy toolchain.
- **`STRICT` tables** (3.37, 2021) opt a table out of the type-affinity behavior in §2,
  enforcing declared column types — a schema using `STRICT` tables has already closed that
  particular gap and the affinity caveat does not apply to it.
- **`RETURNING`** (3.35) and generated columns (3.31) are recent enough that an older
  deployment may not have them available, if application code assumes otherwise.
- **`libsql`/Turso** is a fork adding server-mode operation and networked replication —
  materially different concurrency and availability characteristics from stock embedded
  SQLite; confirm which is actually deployed before applying this file's single-writer
  reasoning, since a networked libsql deployment may not have the same constraints.
- **SQLCipher** is an encrypted-at-rest fork; its encryption overhead and key-management
  surface are a different topic from anything in this file (§7).

**Confirm version- and fork-specific claims against the current documentation for the
deployed version.**

---

## 7. What this file does NOT cover

- Index fundamentals, selectivity, plan-reading signals, and transaction scope as the
  general contention factor — see `databases/relational.md`.
- `libsql`/Turso's own networked, replicated architecture in depth — different enough from
  embedded SQLite to need its own reference if it becomes common enough to justify one.
- SQLCipher's encryption-at-rest mechanics and key management.
- The FTS5 full-text-search extension and other loadable extensions.
- ORM-specific behavior layered on top of a SQLite connection.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
