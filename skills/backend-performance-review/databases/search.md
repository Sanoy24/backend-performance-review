# Search datastores

Load after `databases/universal.md`, before any engine-specific file.

Search engines are optimized for ranked retrieval over text and facets: given a query, find the
matching documents **and** order them by relevance. That second half — ranking — is what
separates this category from every other one, and it is the source of most search-specific
performance findings that a reviewer coming from a relational or document-store background
will miss entirely.

Nothing here is engine-specific.

---

## 1. The core mechanism: an inverted index, and why ranking isn't free

A relational or document index maps a value to the rows/documents that contain it and stops
there — matching is the whole job. A search index does the same lookup (a term to the documents
containing it) but then has to **score** every candidate document against the query to decide
its rank, before any of the usual `LIMIT`-style truncation can happen.

This has a consequence that has no real analogue elsewhere in this skill's category files:
**a query that matches many documents is expensive even if only a handful are ever returned**,
because scoring — not just matching — has to happen for the full candidate set (or a
representative sample of it, depending on how the engine optimizes) before the top results can
be identified. A broad, unfiltered query with a small `size` still pays for its breadth; it did
not get cheap just because the response is short.

---

## 2. Filter context versus query context — the single highest-value distinction

Most search engines let a clause be evaluated in one of two ways:

| | Query context | Filter context |
|:--|:--|:--|
| Purpose | Compute *how well* a document matches, for ranking | Compute *whether* a document matches, yes/no |
| Cacheable | Not usefully — relevance is often per-query | Yes — a filter's result set is cacheable and reusable across queries |
| Cost | Scored per candidate document | Cheap set-membership check, often via a cached bitset |

**A clause that does not need to affect ranking — a status flag, a tenant or ownership
restriction, a date range, a permission check — belongs in filter context, not query context.**
Putting it in query context forces the engine to score every candidate against a clause whose
answer is always just yes or no, and forfeits the caching that would otherwise make repeated
filtering nearly free. This is frequently the single highest-value finding available in a
search-heavy codebase: a full-text query correctly built, wrapped in unnecessary scoring
overhead for clauses that were never about relevance in the first place.

---

## 3. Deep pagination is worse here than anywhere else in this skill

`databases/relational.md` and `databases/document.md` both note that offset-based pagination
degrades with depth. In a distributed search engine it degrades faster and for an additional
reason: the index is typically sharded, and satisfying "give me results 10,000–10,010" requires
**every shard** to compute and sort its own top `(offset + size)` candidates, ship all of them to
a coordinating node, which then merges and re-sorts across shards and discards all but the final
page. The wasted work scales with the offset **and** multiplies by the number of shards involved
— worse than the single-node offset cost this pattern already has elsewhere.

This makes an uncapped or deep `from`/`offset`-style pagination parameter one of the most
severe, most mechanically checkable findings available in this category: the fix (a cursor-based
"search after the last seen sort value" approach, or a purpose-built export/scroll mechanism for
genuinely bulk access) is well-established, and the failure mode without it is not merely "slow"
— many engines impose a hard cap on how deep offset pagination can go at all, so this can also
surface as an outright error once someone requests a page deep enough.

---

## 4. Mapping/schema decisions made at index-creation time are hard to undo

As with document stores, the schema (here: the field **mapping**) decided when an index is
created determines what queries can be efficient later, and changing it typically requires
reindexing all data, not an in-place migration.

- **Analyzed versus exact-match fields are not interchangeable.** A field indexed for full-text
  matching (tokenized, lowercased, stemmed) cannot efficiently support exact-match filtering or
  sorting the way a purpose-built exact-match field can, and vice versa — full-text search
  against an exact-match field will not do what's expected at all. Storing the same value both
  ways (a common, correct pattern) costs index size and write time in exchange for supporting
  both access patterns; storing it only one way and then needing the other is a modeling gap,
  not a tuning problem.
- **Nested or object structures change what a query can ask.** Flattened, denormalized documents
  support fast top-level filtering; deeply nested structures that need to match "this sub-object
  where several of its own fields all match together" require the engine's nested-query support
  specifically, and using the wrong document shape for that access pattern is expensive to fix
  after the fact.
- **A field that will never be searched or filtered on does not need to be indexed at all** —
  storing it only for retrieval (not indexing it) avoids paying index-build and index-size cost
  for a field nothing ever queries against.

---

## 5. Aggregations can consume large amounts of memory, not just CPU

Aggregating (faceting, grouping, computing statistics) over a field requires the engine to build
an in-memory structure mapping every distinct value in scope to its aggregated result. This
scales with **cardinality** — the number of distinct values — not with the number of matching
documents the way a filter does.

**A high-cardinality field used in an aggregation (a user ID, a raw timestamp, a unique
identifier) is a plausible memory-exhaustion pattern**, structurally similar to an unbounded
`GROUP BY` in a relational engine but often with a harder failure mode: some engines evict or
reject the aggregation once its supporting structures exceed a memory budget, converting the
performance concern into a request failure. Bucketing a naturally high-cardinality field (into
ranges, into a lower-cardinality derived value) before aggregating is the usual structural fix,
decided at query- or mapping-time, not something a runtime setting can undo cheaply.

---

## 6. Segments, refresh, and near-real-time visibility

Most search engines share the same append-only, immutable-segment, background-merge storage
mechanism described in `databases/wide-column.md` §1 — a written document is not searchable
until a periodic **refresh** makes it visible, and each refresh creates a new small segment that
the background merge process eventually consolidates.

- **Forcing an immediate refresh per write** (to make a just-written document searchable right
  away) trades indexing throughput for search freshness: every forced refresh creates a new
  segment, and a high write rate combined with per-write refreshing multiplies segment count
  far beyond what the same write volume would produce with the default periodic refresh.
- **More, smaller segments mean more work per query** — a query must check every segment, so an
  index accumulating many small unmerged segments (from aggressive refreshing, or from merge
  falling behind write volume) degrades read latency over time with no application code change,
  the same symptom as the storage-level bloat problems in other categories, arrived at by a
  different mechanism.
- **Genuine near-real-time requirements should be scoped narrowly** — to the specific write path
  that needs it — rather than applied as a global setting, which pays the cost everywhere for a
  freshness requirement that usually applies to very little of the traffic.

---

## 7. Retrieval cost: returning more than was asked for

- **Returning the full original document** for every hit when only a few fields are needed costs
  transfer and deserialization the same way over-fetching does anywhere else — most engines can
  return only selected stored fields instead of the whole original document.
- **Highlighting** (marking matched terms within returned text) re-examines the matched text at
  query time and is not free, especially against large text fields; requesting it for every
  result on a high-volume endpoint when only a few results are ever shown to a user is a
  real, checkable cost.
- **Leading-wildcard or unanchored pattern queries** (matching text that starts with an unknown
  prefix) generally cannot use the term structure efficiently and are the search-engine
  equivalent of an unindexed relational scan — checkable directly from the query construction in
  code, no plan required.

---

## 8. Sharding is a capacity decision made early

Shard count is usually fixed at index-creation time and is expensive to change without
reindexing — the same category of early, hard-to-reverse decision as a wide-column partition
key or a relational table's physical layout.

- **Too many small shards** multiplies per-shard overhead (each shard carries its own resource
  footprint) without a matching benefit, and every query must fan out to every shard regardless
  of how little data each one holds.
- **Too few, oversized shards** limits how much a query can parallelize across nodes, slows
  recovery and rebalancing, and concentrates load on whichever nodes hold the large shards.
- Neither extreme is fixable by adding query-time optimization; it is a capacity-planning
  decision revisited by reindexing, not by tuning.

---

## 9. Evidence available from a search engine

Follow `databases/universal.md` §8; search-specific evidence to look for:

| Evidence | Question it answers |
|:--|:--|
| Query profiling / explain output | Whether a clause ran in query or filter context, and roughly how many candidates were scored |
| Slow-query log | Which queries are actually expensive in production |
| Field mapping definitions | Whether a field is analyzed, exact-match, indexed, or stored-only |
| Segment count and merge statistics | Whether background merging is keeping up, and whether refresh frequency is creating excess segments |
| Shard count and size per index | Whether the sharding decision fits the data volume and query concurrency |

Reading query profiles, mappings, and segment/shard statistics is generally safe on production;
a forced merge or reindex operation used purely to test a hypothesis is not-safe-on-production
without confirming its resource cost first.

---

## 10. What to look for in a review

- Clauses that do not affect ranking (status, tenant, date-range, permission checks) built in
  query context instead of filter context.
- Deep offset-based pagination with no cursor-based alternative.
- Fields queried in a way their mapping does not support (exact match against an analyzed
  field, or full-text search against an exact-match field).
- High-cardinality fields used in aggregations with no bucketing.
- Forced or very frequent refreshes applied globally rather than scoped to the paths that
  genuinely need near-real-time visibility.
- Full original documents or highlighting requested where only a few fields are ever shown.
- Leading-wildcard or unanchored pattern queries.
- Shard count and size relative to actual data volume and query concurrency.

## 11. What not to conclude

- Do not assume a slow query is a matching problem before checking whether it's a scoring or
  ranking cost — a broad, unfiltered query context clause is often the real cause even when the
  returned page is small.
- Do not recommend more shards, or fewer, without evidence about current data volume and query
  concurrency — both directions have real costs.
- Do not invent engine-specific settings (refresh interval defaults, memory circuit-breaker
  thresholds); state that a technology file would be needed for that and mark specifics unknown.
- Do not treat every aggregation as expensive — cardinality is what matters, not the mere
  presence of an aggregation.
