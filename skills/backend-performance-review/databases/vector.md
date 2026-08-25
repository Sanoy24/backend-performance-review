# Vector datastores

Load after `databases/universal.md`, before any engine-specific file.

Vector datastores are optimized for similarity search over high-dimensional embeddings: given a
query vector, find the vectors nearest to it by some distance measure. The property that makes
this category unique among everything else in this skill is that its core index is
**deliberately approximate** — every other category's indexes are exact-match structures that
either find the right answer or don't exist for that query at all; a vector index is built
specifically to trade a small, tunable amount of correctness for a very large speed gain over
checking every vector directly. Reasoning about performance here means reasoning about that
trade explicitly, not assuming an index just "works" the way it does elsewhere.

Nothing here is engine-specific.

---

## 1. Approximate nearest-neighbor search is the mechanism, and the trade is the point

Finding the true nearest vectors to a query by checking every stored vector (a full, exact
scan) is correct but scales linearly with collection size and dimension — fine for a small
collection, prohibitive at scale. Approximate nearest-neighbor (ANN) indexes — commonly built as
a navigable graph over the vectors, or as a clustering of the space into partitions searched
selectively — answer the same question far faster by deliberately not checking everything, which
means **the result is not guaranteed to contain the true nearest vectors, only vectors highly
likely to be near enough.**

This is not a bug to fix; it is what an ANN index is for. But it means recall (the fraction of
true nearest neighbors actually returned) is not a fixed property of the data the way an exact
index's correctness is — it is a **tunable, reviewable setting**, and a review that never asks
what recall this system is actually achieving, and whether that's adequate for what the results
are used for, has skipped the central question this category raises.

---

## 2. The recall/latency/memory triangle

Every lever available in a vector index trades among three quantities: how often the true
nearest neighbors are actually found (recall), how long a query takes, and how much memory the
index structure occupies. Improving one, without changing anything else, costs one or both of
the others:

| Lever | Raises recall | Cost |
|:--|:--|:--|
| Search breadth (how many candidates the index examines per query — often called the search-time candidate count) | Yes | Query latency rises with it |
| Index connectivity / cluster count at build time | Yes, up to a point | Memory footprint, and index build time, rise with it |
| Vector compression (reducing per-vector memory, e.g. via quantization) | No — typically costs some recall | Saves memory substantially |

**The search-breadth parameter is usually the single most consequential, most overlooked,
most directly checkable setting in a vector-search codebase.** It is a per-query knob, often left
at whatever the client library's default happens to be, and that default was not chosen with
this application's recall requirement in mind. A review should treat "what is this parameter set
to, and was that a deliberate choice" as a first-class question, the same way pool sizing is a
first-class question for connection pools — the inputs are just as checkable, and the consequence
of an unexamined default is just as real.

Recall cannot be assessed from code alone; it requires either an evaluation against known
ground-truth neighbors or the engine's own recall-estimation tooling where one exists. A review
without either should say plainly that recall is unmeasured and unknown, rather than assuming the
default configuration is adequate.

---

## 3. Filtering combined with similarity search — the most common severe failure mode

Real applications almost always need "find similar items, but only among items matching some
condition" (a category, an owner, a permission boundary), not pure unfiltered similarity search.
How that combination happens is the single most consequential design decision in this category
after the recall/latency/memory trade itself, and getting it wrong produces a distinctive,
easy-to-miss bug:

- **Filter, then search** (restrict to matching items first, search only among them): correct in
  principle, but if the filter is highly selective, the ANN index may have very few qualifying
  candidates to work with in the region it actually searches, degrading effective recall — or, in
  engines that can't efficiently restrict traversal to a filtered subset, degrading toward a full
  scan of the filtered set, losing the index's speed advantage entirely for that query.
- **Search, then filter** (find the top-K nearest neighbors first, discard any that don't match
  the filter): straightforward to implement, and **the most common way this goes wrong in
  practice** — if the filter is selective, it can discard most or all of a small top-K result set,
  even though genuinely relevant matching items exist further down the true ranked list that
  simply weren't fetched. The visible symptom is exactly "I asked for 10 results and got 2,"
  which reads as a bug report long before anyone connects it to how filtering was implemented.
  The naive fix — fetch a much larger candidate set before filtering, to leave enough after
  discarding — trades query cost for correctness, and how much larger is itself a real, checkable
  parameter, not a guess.

Whichever approach a codebase uses, **check whether the fetch count or search breadth was sized
with the filter's expected selectivity in mind**, or whether it was chosen for the unfiltered
case and never revisited once filtering was added.

---

## 4. Dimensionality is a cost multiplier, usually fixed by the embedding model

Every similarity computation costs roughly proportional to the vector's dimension, and index
memory scales with dimension times collection size. Dimension is normally dictated by whichever
embedding model produced the vectors, not chosen independently — but it is still worth checking
whether the dimension in use is actually necessary for the task, since a larger, more expensive
embedding model than the application needs is a real, if less common, avoidable cost, and
reducing dimensionality (where an embedding model or a post-processing step supports it) trades
some representational fidelity for meaningfully less memory and faster comparisons.

---

## 5. Memory residency matters more here than in most other categories

Graph-based and cluster-based ANN structures are typically designed to be traversed with
effectively random access patterns — good for an in-memory structure, unusually costly if any
significant part of it has to come from disk, far more so than the sequential or range-friendly
access patterns most other categories tolerate reasonably well when memory is tight. **Whether
the index actually fits in available memory is one of the most consequential checks available in
this category**, in the same spirit as `databases/universal.md` §5's working-set reasoning, but
with a sharper cliff: a vector index that has just outgrown memory does not degrade gracefully,
it can turn a graph traversal into a scatter of individual disk reads.

Quantization (§2) is the usual lever for bringing a large collection's memory footprint back
under budget without reducing the vector count, at some recall cost — worth naming as an option
whenever memory pressure and an oversized index coincide, rather than jumping straight to
"add more memory" without checking whether compression would remove the pressure entirely.

---

## 6. Index build cost and insertion patterns

Building a graph-based or cluster-based index is nontrivial work, generally more expensive than
inserting into an exact-match index elsewhere, and it is common practice to bulk-load vectors
first and build (or rebuild) the index afterward rather than maintaining it incrementally through
a large initial load. A workload that inserts vectors one at a time into an already-large index
— rather than batching — pays index-maintenance cost per insert that a bulk build would have
amortized, the same batching principle as anywhere else in this skill, just with a steeper
per-operation cost here because of what maintaining the index structure actually involves.

Rebuilding an index because a build-time parameter (connectivity, cluster count) needs to change
is a real, sometimes substantial cost, and belongs in `Trade-offs` whenever a recommendation
would require it — it is closer to a relational reindex than to changing a runtime setting.

---

## 7. Distance metric: a modeling fact, not a tuning knob

The distance or similarity measure (commonly cosine similarity, Euclidean distance, or a raw dot
product) must match the space the embedding model was actually trained to produce meaningful
results in. This is fundamentally a correctness question — a mismatched metric produces
plausible-looking but wrong rankings, not merely a slower query — so a mismatch belongs in the
"Adjacent findings — outside performance scope" section (`SKILL.md` rule 8) rather than being
scored as a performance finding. The performance-relevant fact worth noting here is narrower:
the metrics in common use have different computational costs (a normalized dot product is
typically cheaper than a full distance computation), which is a legitimate minor factor but a
distant second to getting the metric *right* for the embedding space in use.

---

## 8. Evidence available from a vector engine

Follow `databases/universal.md` §8; vector-specific evidence to look for:

| Evidence | Question it answers |
|:--|:--|
| Search-breadth / candidate-count parameter in client code or config | Whether it was set deliberately or left at a default |
| Recall evaluation output, if any exists | Whether achieved recall is actually known, rather than assumed |
| Filter-then-search vs. search-then-filter implementation | Which failure mode in §3 applies |
| Index memory footprint vs. available memory | Whether the index plausibly fits in memory |
| Insert pattern (batch vs. per-item) in write-path code | Whether index build cost is being amortized or paid per insert |

Reading configuration, code paths, and memory footprint is generally safe on production; running
a full recall evaluation against a large collection can be resource-intensive and should be
sized or scheduled with that in mind rather than run casually against a live system.

---

## 9. What to look for in a review

- The search-breadth/candidate-count parameter: present, deliberately set, and appropriate to
  this application's recall needs — or an unexamined default.
- Whether filtering combined with similarity search is filter-then-search or search-then-filter,
  and whether the fetch size accounts for the filter's selectivity.
- Whether the vector index's memory footprint has been checked against available memory.
- Per-item inserts into an already-large index where a batched build would amortize the cost.
- Whether recall has ever actually been measured, or is simply assumed adequate.
- Dimensionality relative to what the application's task actually requires.

## 10. What not to conclude

- Do not treat a low or unknown recall as an outright defect without checking whether it was a
  deliberate speed/memory trade for this application's tolerance — but do treat "recall was never
  measured at all" as a real gap, not a non-issue.
- Do not recommend a distance-metric change as a performance fix; a metric mismatch is a
  correctness issue and belongs in the out-of-scope findings section, not the performance one.
- Do not recommend quantization or a smaller embedding dimension without checking whether memory
  is actually the constraint — compressing vectors that already fit comfortably in memory trades
  away recall for no real benefit.
- Do not invent engine-specific parameter names, defaults, or memory-overhead figures; state that
  a technology file would be needed for that and mark specifics unknown.
