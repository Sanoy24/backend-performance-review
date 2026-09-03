# Elasticsearch / OpenSearch

Load after `databases/universal.md` and `databases/search.md`.

This file contains **only what those files do not give you**. Query-versus-filter context,
deep pagination, mapping trade-offs, aggregation memory cost, the segment/refresh mechanism,
retrieval over-fetch, and sharding as a capacity decision are covered there and are not
repeated here.

OpenSearch forked from Elasticsearch 7.10.2 in 2021 and remains close to API-compatible for
everything in this file; where they've since diverged, or where a claim is specific to one
distribution's licensed-only features, this file says so. Solr shares the same Lucene
foundation and much of `databases/search.md`'s reasoning, but its cluster coordination
(ZooKeeper-based SolrCloud) and administrative surface differ enough that this file's
diagnostics and config sections are written for Elasticsearch/OpenSearch specifically — treat
Solr specifics as unknowns beyond what §2 notes explicitly.

---

## 1. Detection signals

Dependencies: `@elastic/elasticsearch` (Node), `elasticsearch` (Python `elasticsearch-py`, or
the Java/`.NET` client), `elastic/go-elasticsearch` (Go), `opensearch-py`, `opensearch-js`,
`opensearch-java`, `pysolr`, SolrJ.

Other signals: `elasticsearch.yml` / `opensearch.yml`, `elasticsearch`/`opensearch`/`solr`
container images, index mapping or template definitions (JSON with a `"mappings"` or
`"settings"` key), ILM (Index Lifecycle Management) or ISM (Index State Management) policy
configuration, `solrconfig.xml`.

The client construction code is worth reading for the same reason connection-pool setup
matters elsewhere: sniffing/discovery settings, request timeout, and retry-on-timeout
configuration all change what a slow cluster does to the calling application.

---

## 2. What differs from the search baseline

### Circuit breakers turn the category's memory-exhaustion risk into a named, checkable mechanism

`databases/search.md` §5 describes aggregation memory cost as a general risk. Elasticsearch
and OpenSearch make this concrete: named circuit breakers (the request breaker, the fielddata
breaker, and a parent breaker bounding their combined total) track estimated memory use and
**reject** the operation with a `CircuitBreakingException` naming which breaker tripped, rather
than letting the node degrade or run out of memory silently. A rejected aggregation or query is
therefore direct, attributable evidence of a specific memory pressure source — read the
exception's breaker name before guessing at a cause.

### Heap size trades directly against the OS page cache Lucene depends on

Both engines run on the JVM, and Lucene (the underlying search library) relies heavily on the
operating system's file-system cache to keep segment files fast to read — a documented,
structural reason **not** to give the JVM heap all of a node's memory, unlike a typical
JVM service where more heap is usually better. Both projects' own documentation additionally
caps recommended heap around the point where compressed ordinary object pointers (compressed
oops) stop applying — roughly 32GB uncompressed-pointer boundary — since going past it can
mean *more* heap produces *worse* per-object memory efficiency. Heap sizing here is a real,
checkable trade-off between JVM headroom and OS cache for segment reads, not a single
"more is better" dial.

### `_source`, stored fields, and doc values are three different storage layers

A mapped field can be backed by up to three separate storage mechanisms, each serving a
different access pattern and each with its own cost:

- **`_source`** — the original document JSON, stored by default. Needed for reindexing,
  highlighting, and returning full documents; disabling it saves storage and indexing cost but
  forecloses all three.
- **Doc values** — a columnar, on-disk structure built by default for most field types,
  purpose-built for sorting, aggregating, and scripting. Disabling doc values on a field that
  is ever sorted or aggregated on forces a slower, fielddata-based path built in memory instead
  (and consuming the fielddata circuit breaker's budget) — a checkable, mapping-level cause for
  aggregation cost that looks otherwise unexplained.
- **Stored fields** — an opt-in, per-field alternative to reading a value out of `_source`,
  worth it only when retrieving that one field without the rest of the document matters enough
  to justify the extra storage.

### Deep pagination has three named alternatives, each with a real trade-off

Beyond the general cursor-based fix `databases/search.md` §3 recommends, the specific
mechanisms are worth naming because their trade-offs differ:

- **`scroll`** holds a point-in-time snapshot context alive on the cluster for its
  `keep_alive` duration, consuming resources proportional to how many scroll contexts are
  open at once — an application that opens scrolls and does not explicitly clear them (or sets
  an excessively long `keep_alive`) accumulates cluster-side resource cost that has nothing to
  do with query volume. It is documented as discouraged for ordinary deep pagination in current
  versions, reserved for genuine full-corpus export.
- **`search_after`** is stateless and cheap on the cluster, but requires a stable, unique sort
  tiebreaker (commonly `_id` appended to the sort) — an unstable sort produces duplicate or
  skipped results across pages, a correctness bug that looks like a pagination bug.
- **Point-in-time (PIT)**, combined with `search_after`, is the current recommended approach:
  consistent results across pages without scroll's per-context resource accumulation. A
  codebase still using `scroll` for ordinary paginated UI traffic (as opposed to bulk export)
  is a checkable, version-appropriate finding.

### The bulk API has its own queue, and saturating it is a specific, named failure

Bulk indexing requests are typically queued at a dedicated write/bulk thread pool, separate
from search. A `TOO_MANY_REQUESTS` (HTTP 429) or `EsRejectedExecutionException` response is
that queue's capacity being exceeded — evidence of indexing throughput outrunning the cluster's
configured concurrency, checkable directly from thread-pool statistics (§3), not something to
diagnose by guessing at network or client-side causes first.

### Node roles determine where cluster-wide coordination work actually happens

A cluster's nodes can be assigned roles — data, master-eligible, ingest, and coordinating-only
— and query/aggregation fan-out and result-merging happen on the coordinating node handling the
request, which may not be a data node at all. An undersized or overloaded coordinating/master
tier can bottleneck the whole cluster even when every data node has spare capacity — worth
checking node roles and their resource allocation separately from data-node capacity.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `GET _cluster/health` | Cluster status, node/shard counts, unassigned shards | **safe-on-production** |
| `GET _cat/thread_pool?v` | Per-pool active/queued/rejected counts (search, write, get) — the clearest saturation signal | **safe-on-production** |
| `GET _nodes/stats` | Per-node JVM heap, circuit-breaker usage, indexing/search latency | **safe-on-production** |
| `GET _cat/indices?v` | Per-index document count, size, and health | **safe-on-production** |
| `GET _cat/shards?v` | Per-shard size and node placement — surfaces uneven shard distribution | **safe-on-production** |
| `GET _cat/segments?v` | Segment count per shard | **safe-on-production** |
| `GET <index>/_search` with `"profile": true` | Per-clause timing, including whether a clause ran in query or filter context | **safe-on-production for the profiled request — adds overhead, avoid at high volume on a hot path** |
| `GET <index>/_mapping` | Field mappings: analyzed vs exact-match, doc-values status | **safe-on-production** |
| Slow log (`index.search.slowlog.threshold.query.warn`, etc.) | Actual slow queries/indexing operations in production, at a chosen threshold | **safe-on-production with a sane threshold** — a very low threshold adds significant log volume |
| `POST <index>/_forcemerge` | Merges segments down, used to test a segment-count hypothesis | **not-safe-on-production** — heavy I/O, can temporarily double disk usage for the index |
| `POST <index>/_cache/clear` | Clears request/query caches | **not-safe-on-production** — causes a temporary latency spike as caches rebuild |

`_cat/thread_pool?v`'s `rejected` column climbing is usually the fastest route from "something
feels slow" to "this specific pool is saturated" — faster than reasoning about individual query
latency first.

---

## 4. Common failure modes and their symptoms

| Symptom | Elasticsearch/OpenSearch-specific cause to check first |
|:--|:--|
| Aggregation or query fails with `CircuitBreakingException` | Named breaker (request/fielddata/parent) tripped — read which one from the exception |
| Bulk indexing returns `429`/`EsRejectedExecutionException` | Write/bulk thread pool queue saturated — check `_cat/thread_pool?v` |
| Search latency degrades steadily with no code change | Segment count growing faster than merge keeps up (over-aggressive refresh), or shard count mismatched to node count |
| Deep pagination request errors past a certain depth | `index.max_result_window` exceeded (default caps `from + size`) |
| Cluster memory pressure rises with no obvious query change | Open scroll contexts accumulating, not cleared or with an excessive `keep_alive` |
| Whole cluster feels sluggish even though data nodes have spare capacity | Master/coordinating node overloaded, or cluster state size inflated by too many indices/shards |
| Node instability or GC-related slowdowns | Heap sized too large relative to the compressed-oops boundary, or too little RAM left for the OS page cache |
| Aggregation on a field is slower than expected | Doc values disabled on that field, falling back to in-memory fielddata |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| JVM heap size vs available node RAM | JVM headroom versus OS page cache available for Lucene segment reads |
| `refresh_interval` | Search freshness versus segment count and merge overhead |
| `number_of_shards` / `number_of_replicas` | Query parallelism and resilience versus per-shard overhead and per-query fan-out cost |
| `index.max_result_window` | How deep offset pagination is allowed to go versus the per-shard resource cost that depth implies |
| Circuit breaker limits | Safety margin against memory exhaustion versus legitimate headroom for large aggregations |
| Node roles (dedicated master/data/ingest/coordinating) | Isolation of cluster-management, indexing, and query-coordination work versus added operational complexity |
| Bulk request size and concurrent bulk request count | Indexing throughput versus risk of queue saturation and rejected requests |
| `scroll` `keep_alive` | How long a scroll context survives client-side pauses versus the cluster resource it holds open |

---

## 6. Version differences worth knowing

Record whether the cluster is Elasticsearch or OpenSearch, and its version, during discovery.

- OpenSearch forked from Elasticsearch 7.10.2 (2021); confirm which distribution and version
  before citing anything from either project's licensed-only or post-fork-exclusive features
  (notably some machine-learning and security capabilities), since they have diverged.
- Point-in-time (PIT) search, combined with `search_after`, is the currently recommended
  mechanism for consistent deep pagination — `scroll` still exists but is documented as
  discouraged for that use case in current versions, reserved for true full-corpus export.
- Pre-7.x indices supported multiple mapping "types" per index; that concept was removed —
  documentation or code referencing types predates this and should be read accordingly.
- Security features (TLS, role-based access control) were gated behind a commercial license
  (X-Pack) for part of Elasticsearch's history before becoming free at the basic tier; do not
  assume a cluster has authentication or encryption configured without checking directly.

**Confirm version- and distribution-specific claims against the current release notes for the
deployed engine.**

---

## 7. What this file does NOT cover

- Query-context-versus-filter-context, deep pagination as a general pattern, mapping/schema
  trade-offs, aggregation memory cost as a concept, the segment/refresh storage mechanism, and
  sharding as a capacity decision — see `databases/search.md`.
- Solr-specific diagnostics, administration, and configuration in depth: SolrCloud's
  ZooKeeper-based cluster coordination, `solrconfig.xml` request-handler configuration, and
  Solr's own circuit-breaker/backpressure mechanisms are a different surface not detailed here.
- Managed-service specifics: Elastic Cloud and Amazon OpenSearch Service autoscaling, pricing,
  and managed-only features.
- Security configuration: TLS, authentication, field- and document-level access control.
- Ingest pipeline design and its resource cost beyond the bulk-queue behavior noted above.
- Cross-cluster search and cross-cluster replication.
- Specific numeric threshold recommendations beyond the documented engine defaults and
  boundaries cited above; recommend the measurement that determines the right value for a
  given cluster and workload.
