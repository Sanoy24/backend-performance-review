# Neo4j

Load after `databases/universal.md` and `databases/graph.md`.

This file contains **only what those files do not give you**. Index-free adjacency,
traversal depth as the dominant cost factor, supernodes, graph data modeling, and the
general shape of a graph query are covered there and are not repeated here.

---

## 1. Detection signals

Dependencies: `neo4j` (the official driver package name in Python, JavaScript, and Go),
`neo4j-driver` (older JavaScript package name), `org.neo4j.driver:neo4j-java-driver` (JVM),
`Neo4j.Driver` (.NET), `py2neo` (a third-party Python library, now unmaintained — its
presence dates the codebase), `spring-data-neo4j` (JVM, an OGM layer whose query generation
is worth reading directly).

Other signals: `bolt://`, `bolt+s://`, `neo4j://`, and `neo4j+s://` connection strings
(the `neo4j://` scheme means routing/cluster-aware, `bolt://` means a direct single-instance
connection — a checkable distinction, see §2), Cypher query text in application code,
`neo4j.conf`, `cypher-shell` invocations, `neo4j/neo4j` container images, `*.databases.neo4j.io`
hosts (AuraDB).

---

## 2. What differs from the graph baseline

### The page cache is the setting that determines whether index-free adjacency is actually fast

The category file explains that traversal follows stored pointers rather than performing
joins. That is only cheap while the relevant store files are in memory. Neo4j's **page
cache** (`server.memory.pagecache.size`, `dbms.memory.pagecache.size` on 4.x) is configured
separately from the JVM heap, and if the working portion of the graph does not fit in it,
every hop becomes a disk read and the category file's central mechanism stops delivering
what it promises.

This produces a specific, checkable review question that has no analogue in the category
file: **page cache size versus store file size on disk**, and the page-cache hit ratio in
practice. It also produces a specific misconfiguration — heap sized large at the page
cache's expense, on the reasoning that "the JVM needs memory" — which degrades exactly the
mechanism the engine is chosen for.

### An index finds the starting node; it does not accelerate the traversal

Neo4j indexes (`RANGE`, `TEXT`, `POINT`, `FULL-TEXT`, and composite forms) exist to resolve
the *anchor* of a query — the node the traversal begins from. Once anchored, the traversal
itself uses relationship pointers and no index participates. The practical consequences are
Neo4j-specific and checkable in a `PROFILE`:

- A query with no usable index for its anchor begins with `AllNodesScan` or
  `NodeByLabelScan` — the graph equivalent of a full table scan, and often the entire
  explanation for a slow query that "should" be a cheap traversal.
- Adding indexes to *relationship* properties or intermediate nodes to "speed up a
  traversal" does not do that. It is a real and common misconception, and worth declining
  explicitly rather than leaving unexamined.

### `PROFILE` reports db hits, and the eager operator is the specific plan hazard

`EXPLAIN` gives the plan; `PROFILE` executes and reports **db hits** per operator — the
concrete unit of storage access to compare between query formulations. Two specific
operators are worth recognizing:

- **`Eager`** forces the query to materialize all intermediate rows before continuing,
  inserted by the planner to protect correctness when a read and a write in the same query
  could interfere. It converts a streaming query into one bounded by total result size, and
  it appears most often in `LOAD CSV` and mixed read/write statements — a documented and
  frequently-surprising cause of an import that runs out of memory.
- **`CartesianProduct`** between disconnected `MATCH` patterns multiplies rows, exactly as
  the relational category file describes for joins, and is usually a modeling or query-shape
  error rather than an intended operation.

### Dense nodes change how relationships are stored, and the threshold is configurable

Neo4j stores relationships as linked chains per node; past a configurable **dense-node
threshold** (default 50 relationships), it switches that node to a grouped structure indexed
by relationship type and direction. This is the engine's built-in mitigation for the
supernode problem the category file describes — and its existence means the practical
question in review is not only "is there a supernode" but "are traversals from it filtered
by type and direction," since that is what the dense-node representation is able to
accelerate. An unfiltered `MATCH (n)--(m)` from a dense node cannot use it.

### Large writes need explicit batching, and the mechanism is version-specific

A single transaction holds locks and accumulates state in heap until commit, so a bulk
update expressed as one statement is a memory and lock-duration risk rather than merely a
slow query. Neo4j provides in-query batching — `CALL { ... } IN TRANSACTIONS OF n ROWS`
(4.4+ / 5.x), and `apoc.periodic.iterate` in older codebases. Bulk *initial* load is a
different tool again (`neo4j-admin import`, offline). Which of these a codebase uses is
directly readable and dates the code as much as it describes it.

### The driver's routing scheme and session lifecycle are application-side performance decisions

`neo4j://` creates a routing driver that discovers cluster members and directs reads to
followers/read replicas and writes to the leader; `bolt://` pins to one instance and
forfeits that. Additionally, the driver's sessions are cheap but **not** thread-safe, results
are streamed by default, and code that materializes every record before processing (or
opens a session per query inside a loop) gives up the streaming behavior. These are
application-code findings, visible without touching the database.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `EXPLAIN <cypher>` | Plan and estimated rows, without executing | **safe-on-production** |
| `PROFILE <cypher>` | Executed plan with actual rows and **db hits** per operator | **not safe-on-production for an expensive query** — it executes the statement |
| `SHOW TRANSACTIONS` (5.x) / `CALL dbms.listTransactions()` (4.x) | Currently running transactions, elapsed time, and the query text | **safe-on-production** |
| `SHOW INDEXES` / `SHOW CONSTRAINTS` | Every index and constraint, its type and state — the basis for a missing-anchor-index finding | **safe-on-production** |
| `CALL db.stats.retrieve('GRAPH COUNTS')` | Node/relationship counts by label and type, for sizing and cardinality reasoning | **safe-on-production** |
| Query log (`db.logs.query.*` settings) | Queries above a configured threshold, with elapsed time, allocated bytes, and page-cache hits/faults | **safe-on-production** — configured verbosity has its own cost; thresholded logging is modest |
| `:sysinfo` in Neo4j Browser, or the metrics endpoint | Page-cache hit ratio, store sizes, transaction rates, heap usage | **safe-on-production** |
| `CALL db.schema.visualization()` | The actual label/relationship-type schema as stored, versus what the code assumes | **safe-on-production** |

Page-cache hit ratio and store size are the first pair worth reading for any Neo4j
performance complaint, for the reason given in §2 — they determine whether the engine's
central mechanism is operating at all.

---

## 4. Common failure modes and their symptoms

| Symptom | Neo4j-specific cause to check first |
|:--|:--|
| Traversals are slow despite a well-modeled graph | Page cache too small for the store files; check hit ratio and page faults, not query shape |
| A query is slow and `PROFILE` starts with `NodeByLabelScan`/`AllNodesScan` | No usable index for the query's anchor node |
| A `LOAD CSV` or mixed read/write query exhausts heap | An `Eager` operator materializing all intermediate rows before proceeding |
| Row counts explode mid-plan | `CartesianProduct` between disconnected `MATCH` patterns |
| Traversal from one specific node is far slower than from similar nodes | A dense node whose traversal is not filtered by relationship type and direction |
| A bulk update holds locks for minutes and blocks other writes | A single unbatched write transaction; no `CALL { } IN TRANSACTIONS` or equivalent |
| Reads do not scale after adding cluster members | `bolt://` used instead of `neo4j://`, so the driver never routes reads to followers |
| Memory pressure on the JVM with a healthy-looking page cache | Heap sized at the page cache's expense, or query results fully materialized client-side |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `server.memory.pagecache.size` versus heap size | Graph resident in page cache (the engine's core mechanism) versus heap for query execution and results — this is a zero-sum split of the same host memory |
| Dense-node threshold | Grouped relationship storage for high-degree nodes versus the overhead of that structure for nodes near the boundary |
| Index set on anchor properties | Fast query anchoring versus write-time index maintenance |
| Query timeout (`db.transaction.timeout`) | Bounding runaway traversals versus killing legitimate long analytical queries |
| Query-log threshold | Diagnosability of slow queries versus log volume and its I/O cost |
| `neo4j://` (routing) versus `bolt://` (direct) | Read scaling across cluster members versus a simpler single-endpoint connection |
| Batch size in `CALL { } IN TRANSACTIONS OF n ROWS` | Shorter lock duration and bounded memory versus more transaction overhead and non-atomicity across the whole operation |

---

## 6. Version differences worth knowing

Record the major version during discovery — Neo4j 5 renamed enough configuration and schema
surface that guidance written for 4.x can be syntactically wrong rather than merely dated.

- **Configuration namespace changed in 5.x**: `dbms.memory.pagecache.size` became
  `server.memory.pagecache.size`, and many other settings moved similarly. A `neo4j.conf`
  using the older names indicates a 4.x-era deployment (or a migration that has not been
  revisited).
- **Index types were reworked in 5.x**: the 4.x `BTREE` index was replaced by `RANGE`,
  `POINT`, and `TEXT` indexes. Migration guidance existed precisely because a straight
  upgrade could leave queries without the index they had been planning against.
- **`CALL { ... } IN TRANSACTIONS` (4.4+)** is the supported in-query batching mechanism;
  codebases relying on `apoc.periodic.iterate` predate it and depend on APOC being
  installed, which is a deployment fact worth confirming rather than assuming.
- **AuraDB** (the managed offering) fixes memory configuration by tier — the page-cache
  finding above becomes a tier-sizing question rather than a configuration one, and
  `neo4j.conf` will not be present to read.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version.**

---

## 7. What this file does NOT cover

- Index-free adjacency, traversal-depth reasoning, supernodes, and graph data modeling —
  see `databases/graph.md`.
- Cluster topology, leader election, and the causal-consistency bookmark protocol beyond
  naming that routing exists.
- Graph Data Science (GDS) library algorithms and their own, quite different memory model.
- APOC procedures individually, beyond noting that some batching patterns depend on it.
- Neo4j's full-text and vector index capabilities in depth.
- AuraDB tier sizing, billing, and its operational limits.
- Security: authentication, RBAC, and fine-grained access control.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
