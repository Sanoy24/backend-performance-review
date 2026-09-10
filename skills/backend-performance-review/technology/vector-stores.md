# Vector datastores: Pinecone, Weaviate, Qdrant, Milvus, Chroma, pgvector, FAISS, and LanceDB compared

Load after `databases/universal.md` and `databases/vector.md`.

This file contains **only what those files do not give you**: the ANN mechanism, the
recall/latency/memory trade, filter-then-search versus search-then-filter, dimensionality,
memory residency, index build cost, and distance-metric correctness are covered there and
are not repeated here.

**This is a deliberately comparative file, not a single-engine one** (see
`docs/roadmap.md`). These eight are not forks of one shared engine — they differ in
deployment model, index algorithm, and above all in **what the recall/latency knob the
category file calls "search breadth" is actually named**, which is exactly the gap
`docs/supported-technologies.md` named as missing. **Confirm which engine is actually
deployed before applying a row from any table below.**

---

## 1. Detection signals

| Engine | Package / dependency signal | Notes |
|:--|:--|:--|
| **Pinecone** | `pinecone-client` (Python), `@pinecone-database/pinecone` (Node.js) | Fully managed only — no self-hosted form |
| **Weaviate** | `weaviate-client` (Python), `weaviate-go-client` (Go), `weaviate-ts-client` (Node.js) | Self-hosted or managed (WCS) |
| **Qdrant** | `qdrant-client` (Python), `@qdrant/js-client-rest` (Node.js) | Self-hosted, Docker, or managed |
| **Milvus** | `pymilvus` (Python), `@zilliz/milvus2-sdk-node` (Node.js) | Self-hosted or managed (Zilliz Cloud) |
| **Chroma** | `chromadb` (Python and Node.js — same package name) | Embedded/local by default; a server mode also exists |
| **pgvector** | `pgvector` (Python client helper), `CREATE EXTENSION vector` / `CREATE EXTENSION IF NOT EXISTS vector` in a migration | A **Postgres extension**, not a standalone service — the `postgres` signal fires alongside this one and loads `databases/relational.md` and `technology/postgres.md` too |
| **FAISS** | `faiss-cpu` / `faiss-gpu` (Python), `faiss-java` | A **library linked into the application process**, not a service — see §2 |
| **LanceDB** | `lancedb` (Python), `@lancedb/lancedb` (current Node.js package; the older, now-renamed package was `vectordb`) | Embedded, disk-first design — see §2 |

---

## 2. What differs from the vector baseline, engine by engine

### The search-breadth parameter has a different real name in every engine, and this is the single highest-value comparison in this file

The category file (§2) calls out the search-breadth parameter as the most consequential,
most overlooked setting in this category. Here is what it is actually called:

| Engine | Index | Build-time parameter | Search-time (recall/latency) parameter |
|:--|:--|:--|:--|
| **Weaviate** | HNSW | `efConstruction`, `maxConnections` | `ef` |
| **Qdrant** | HNSW | `ef_construct`, `m` | `ef_search` (also settable per-query as `hnsw_ef`) |
| **Milvus** | Selectable: `HNSW`, `IVF_FLAT`, `IVF_PQ`, `DiskANN`, others | Index-dependent (`M`/`efConstruction` for HNSW; `nlist` for IVF family) | `ef` (HNSW) or `nprobe` (IVF family) — **the parameter itself changes with the chosen index type** |
| **pgvector** | `HNSW` (0.5.0+) or `IVFFlat` | `m`, `ef_construction` (HNSW); `lists` (IVFFlat) | `hnsw.ef_search` (a session-level `SET`) or `ivfflat.probes` |
| **FAISS** | Chosen explicitly by the application (`IndexFlatL2`, `IndexIVFFlat`, `IndexHNSWFlat`, `IndexIVFPQ`, ...) | Constructor arguments, e.g. `nlist` for IVF | `nprobe` (IVF family) or `efSearch` (HNSW family) — set directly on the index object in code |
| **Pinecone** | Abstracted — not user-selected | Not exposed | Not exposed as a tunable per-query parameter in the same form; effective recall is a function of the managed service's internal configuration |
| **Chroma** | HNSW (via `hnswlib`) by default | `construction_ef`, `M` | `search_ef` |
| **LanceDB** | `IVF_PQ` | `num_partitions`, `num_sub_quantizers` | `nprobe`, `refine_factor` |

**A finding of "the search-breadth parameter is left at a default" is only actionable once
the reviewer knows which of these names to look for in the code.** This table exists
specifically so that check does not require re-deriving each engine's documentation from
scratch during a review.

### Filtering-and-search integration is a real, checkable engine-by-engine difference

The category file (§3) describes the general failure modes of combining a filter with
similarity search. Engines differ concretely in what mitigation they offer:

- **Qdrant** integrates payload filtering directly into HNSW graph traversal and picks a
  strategy based on the filter's estimated selectivity — a genuine, built-in mitigation for
  the filter-then-search degradation the category file warns about, though it does not
  eliminate the underlying trade-off.
- **Weaviate** supports pre-filtering (restricting the candidate set before the vector
  search) using a bitmap-based approach, with similar intent.
- **Milvus** supports pre-filtering with index-type-dependent efficiency; how well it avoids
  degrading toward a full scan depends on which index type is in use.
- **FAISS has no built-in filtering at all.** It is a similarity-search library, not a
  database — filtering by metadata must be implemented by the application, most often as
  post-hoc filtering of returned candidate IDs against metadata stored elsewhere. This makes
  the category file's "search, then filter" failure mode essentially the *only* option
  available unless the application hand-rolls something more sophisticated, and it is worth
  stating plainly rather than assuming FAISS offers a mitigation it does not.
- **pgvector** filtering combines with ordinary Postgres `WHERE` clauses and is therefore
  subject to the relational planner's own join/filter-ordering decisions
  (`databases/relational.md` §1) — a genuinely different mechanism from every other engine
  in this table, worth checking with `EXPLAIN` rather than assumed.

### Deployment model changes what "memory residency" and "network round trip" even mean

The category file (§5) treats memory residency as a near-universal concern. Whether it is
paid as *network* latency, *process* memory, or *disk* I/O depends entirely on deployment
shape:

- **Pinecone, Weaviate (managed), Milvus (Zilliz Cloud)** — a genuine network service; every
  query pays a round trip, and the category file's memory-residency concern applies to a
  process the application never sees directly.
- **Weaviate, Qdrant, Milvus (self-hosted)** — a separately deployed server process; still a
  network round trip from the application, but the operator controls (and must size) the
  memory the index actually lives in.
- **FAISS** is a **library, not a service**: the index lives in the application process's
  own memory (or is memory-mapped from disk, where the application chooses that mode), with
  **no network round trip at all**. The category file's memory-residency warning becomes a
  direct statement about the application process's own memory budget, and persistence
  (saving/loading the index to/from disk) is something the application must implement, not
  something the engine provides automatically.
- **Chroma**, in its default local/embedded mode, behaves similarly to FAISS
  (in-process, no round trip) but adds a thin persistence and metadata layer FAISS does not
  have; a separate Chroma server mode exists and reintroduces a network round trip.
- **LanceDB** is deliberately disk-first: it stores vectors in the columnar **Lance format**
  on local disk or object storage and is designed for **larger-than-memory collections with
  fast random access**, rather than assuming the whole index is memory-resident. This is a
  direct, positive counter-example to the category file's general memory-residency warning
  — confirm which mode (memory-resident versus disk-resident-by-design) an engine is using
  before treating "does the index fit in memory" as the right question at all.

### Index-type selectability is itself a Milvus-specific decision surface

Where Weaviate, Qdrant, Chroma, and pgvector default to (or exclusively offer) HNSW, **Milvus
exposes index-type choice explicitly** — HNSW, the IVF family, DiskANN (purpose-built for
billion-scale, larger-than-memory collections), and others, selectable per-collection. This
makes "which index type, and why" a legitimate, checkable Milvus-specific review question
that mostly does not arise for the HNSW-only engines, where the question is only ever "what
are the HNSW parameters set to."

### pgvector inherits the Postgres query planner — for better and for worse

Because pgvector is a Postgres extension, an ANN index built with it participates in
Postgres's own query planner alongside ordinary indexes and joins. This means
`databases/relational.md`'s "the planner decides, and it can be wrong" reasoning applies
directly to vector queries here in a way it does not for any standalone vector engine — the
planner can choose a sequential scan over the vector index for reasons unrelated to vector
search at all (stale statistics, a `LIMIT` combined with other filters), and `EXPLAIN` is
the correct tool to confirm the index is actually being used, exactly as for any other
Postgres index.

---

## 3. Diagnostics

| Engine | Command / source | What it shows | Production safety |
|:--|:--|:--|:--|
| Weaviate | GraphQL `_additional { explainScore }` / REST metrics endpoint | Per-query scoring detail; Prometheus-format metrics for the server | **safe-on-production** |
| Qdrant | Collection info API (`GET /collections/{name}`), telemetry endpoint | Index configuration, segment counts, and cluster telemetry | **safe-on-production** |
| Milvus | `describe_collection` / `describe_index`, Milvus's Prometheus/Grafana metrics | Index type and parameters in use; query latency and resource metrics | **safe-on-production** |
| pgvector | Postgres `EXPLAIN (ANALYZE, BUFFERS)` on a vector query | Whether the vector index is used at all, versus a sequential scan | `EXPLAIN` **safe-on-production**; `ANALYZE` executes the query |
| Pinecone | `describe_index_stats` | Vector count, index fullness, namespace breakdown | **safe-on-production** |
| FAISS | Application-level instrumentation only | Whatever the application chooses to measure — the engine itself exposes no separate diagnostic surface, since it is not a service | **safe-on-production** by construction (nothing external to query) |
| Chroma | `collection.count()`, server logs in server mode | Collection size; request logs where server mode is used | **safe-on-production** |
| LanceDB | `Table.stats()` / dataset version/fragment inspection | Fragment count and layout — relevant to on-disk read performance | **safe-on-production** |

For any engine without a query-explain facility (Pinecone, FAISS, Chroma), the recall
question the category file (§2) raises can only be answered by an actual recall evaluation
against ground truth — there is no plan to read instead.

---

## 4. Common failure modes and their symptoms

| Symptom | Engine-specific cause to check first |
|:--|:--|
| Recall is lower than expected, and no one can say what the search-breadth parameter is set to | Wrong name looked for — check the §2 table for what this engine actually calls it |
| Filtered queries return far fewer results than requested | FAISS or another engine with no built-in filter mitigation, applying naive search-then-filter |
| A pgvector query is slow and the vector index appears unused | Postgres planner chose a different plan — confirm with `EXPLAIN (ANALYZE, BUFFERS)` |
| An in-process FAISS or Chroma index consumes unexpectedly large application memory | The index is memory-resident by construction; there is no separate server process to blame |
| A LanceDB-backed workload is treated as "must fit in memory" and over-provisioned | LanceDB is disk-first by design; the memory-residency question does not apply the same way |
| Milvus query latency changed after a migration with no code change | Index type changed (e.g. IVF to HNSW, or vice versa) — the search-time parameter itself is different now |
| A managed engine (Pinecone) shows no tunable recall/latency knob in code | Expected — some managed offerings do not expose one the way self-hosted engines do; note this as a real constraint, not a missed setting |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| HNSW `ef`/`ef_search`/`search_ef` (naming per §2 table) | Recall versus query latency — the single most consequential per-query setting across most of these engines |
| HNSW `M`/`maxConnections`/build-time connectivity | Recall ceiling and memory footprint versus index build time |
| IVF `nlist` (build) / `nprobe` (search) | Partition count and search breadth versus memory and query latency, for the IVF family specifically |
| Milvus index-type choice | Query latency/recall shape versus memory footprint and whether the collection needs to exceed memory (DiskANN) |
| FAISS in-memory versus memory-mapped index | Fastest possible query latency versus a larger-than-RAM index at some latency cost |
| LanceDB fragment/dataset layout | Read performance versus write/compaction overhead — a disk-first, not memory-first, tuning question |
| pgvector `lists`/`probes` (IVFFlat) or HNSW parameters | Same recall/latency trade as elsewhere, but tunable per-session via ordinary Postgres `SET` |

---

## 6. Version differences worth knowing

- **pgvector's HNSW support arrived in 0.5.0** — earlier deployments have IVFFlat only,
  which has different build and query-time parameters and generally different
  recall/latency characteristics for the same data. Confirm the extension version before
  assuming HNSW is available.
- **LanceDB's Node.js package was renamed** from `vectordb` to the scoped `@lancedb/lancedb`
  — both may appear in the wild; they are the same product under different package names at
  different points in time.
- **Milvus 2.x** restructured around independently scalable components (proxy, query nodes,
  data nodes) versus 1.x's simpler architecture; performance characteristics and the
  available index types both changed across that boundary.
- **Chroma's default backend has changed across releases**; confirm which persistence layer
  a given deployment actually uses rather than assuming current documentation describes an
  older installation.

**Confirm version- and deployment-specific claims against the current documentation for the
deployed version.**

---

## 7. What this file does NOT cover

- The ANN mechanism, the recall/latency/memory trade, filter-then-search versus
  search-then-filter, dimensionality as a cost multiplier, memory residency in general, index
  build cost, and distance-metric correctness — see `databases/vector.md`.
- Embedding model selection and embedding quality — a modeling question, not a performance
  one.
- pgvector's own storage/indexing mechanics beyond how they interact with the Postgres
  planner; deeper Postgres-specific findings belong to `technology/postgres.md`.
- Each engine's clustering, replication, and high-availability configuration.
- Managed-service pricing and capacity-tier selection (Pinecone, Zilliz Cloud, Weaviate
  Cloud).
- Hybrid (sparse + dense) search scoring mechanics, beyond noting that several of these
  engines support it.
- Specific numeric threshold recommendations beyond the documented parameter names and
  mechanisms cited above; recommend the measurement (a recall evaluation, a load test) that
  determines the right value for a given workload.
