# Graph datastores

Load after `databases/universal.md`, before any engine-specific file.

Graph engines are optimized for traversing relationships from a known starting point. Their
performance model inverts the relational one: cost is driven by **how far and how wide you
traverse**, not by how many rows a predicate matches. A query that reads like a two-line
pattern can still touch millions of nodes if nothing bounds its depth or fan-out.

Nothing here is engine-specific.

---

## 1. The core mechanism: index-free adjacency

Most native graph engines store each node with direct pointers to its relationships, so
traversing from a node to its neighbors is a pointer-following operation whose cost does not
depend on total graph size — it depends only on how many relationships that node has. This is
the property that makes graph engines fast for what they're built for, and it inverts the usual
intuition: **finding a starting point** (locating a node by a property) needs an index and
behaves like any indexed lookup; **traversing from it** does not, and behaves like following a
linked list.

Two consequences follow directly:

- A query with **no indexed starting point** — one that must scan all nodes of a label to find
  matches before traversing — pays a relational-style full-scan cost before the graph-native
  part even begins. This is usually the single most impactful thing to check: does every
  traversal begin from an indexed property lookup?
- Once traversal starts, **cost is governed by the shape of the graph reached, not by an index**.
  A query with an indexed start and a runaway traversal is not helped by any index at all — the
  index got you to the starting line, not around the track.

---

## 2. Traversal depth is the dominant cost factor

Where a relational engine's cost scales with rows examined, a graph traversal's cost scales
combinatorially with **depth × average fan-out per hop**. This is the graph-native form of the
growth question every review asks:

| Pattern | Growth |
|:--|:--|
| Fixed number of hops, small fan-out | Roughly linear, bounded |
| Fixed number of hops, high fan-out | Multiplies per hop — `fan-out^depth` |
| **Unbounded** hop count (variable-length path, no upper bound) | No upper bound on cost at all |
| Shortest-path / all-paths across a large, densely connected region | Can be worse than exponential in practice |

**An unbounded variable-length traversal is the graph equivalent of a query with no `LIMIT`** —
except the failure mode is often worse, because the search space grows multiplicatively with
depth rather than linearly with a result-set size. A pattern like "friends of friends of
friends, however many hops that takes" is not a query, it's an open-ended search, and it should
be treated as a finding whenever it has no depth bound.

Depth bounds are cheap to add and dramatically change worst-case behavior, which makes an
unbounded traversal a strong `quick-win` candidate once found.

---

## 3. Supernodes: the hot-key problem's graph form

A **supernode** — a node with a very large number of relationships, such as a popular tag, a
celebrity account, or a shared root entity — breaks the assumption that traversal cost is
bounded by "a few relationships per hop." Any traversal that passes through a supernode
inherits its full fan-out at that hop, regardless of how selective the rest of the pattern is.

This is structurally the same failure as a hot partition key or a hot row in other categories:
it cannot be fixed by adding capacity, because the problem is concentrated at one node, not
spread across the dataset. Look for it wherever the domain model has an inherent power-law
shape — categories, tags, "follows" or "friends" relationships, organizational hierarchies with
a shared root.

Mitigations are structural, not tuning: cap fan-out considered at query time (e.g., sample or
limit relationships examined from a known-large node), model the high-degree relationship
differently (an intermediate node, a separate index) so it isn't traversed the same way as a
normal edge, or exclude known supernodes from open-ended traversal patterns explicitly.

---

## 4. Query shape: patterns that scale badly

- **Cartesian products from independent match patterns.** Matching two unrelated patterns in
  one query without a connecting relationship between them produces their cross product before
  any filtering — the graph-query equivalent of a join with no join condition, and just as
  expensive.
- **Traversing before filtering.** Applying a property filter after expanding several hops means
  every intermediate node was visited whether or not it could ever satisfy the filter. Pushing
  the filter as early in the pattern as possible — ideally at the starting node — bounds work
  the same way an early `WHERE` does in a relational plan.
- **Returning whole nodes or whole paths when only a few properties are needed.** The traversal
  itself is cheap; materializing and transferring full node/relationship objects for every
  visited element is not, especially at depth.
- **Counting or aggregating by first collecting.** Counting matches by returning them all and
  counting in application code pays full transfer cost for a number, the same anti-pattern as
  every other category.
- **Repeating an identical sub-traversal across branches of a larger query** instead of computing
  it once and reusing the result.
- **Undirected traversal where direction is known.** Relationships in most graph models have a
  direction; traversing without constraining it doubles the effective fan-out at every hop for
  no benefit when the domain only ever needs one direction.

---

## 5. Data modeling is the primary lever

As with document stores, the modeling decision matters more than query tuning, and it is made
once, early, and is expensive to change later.

- **What is a node versus what is a property.** Modeling a frequently-traversed-to value as a
  property on a large node means every access to it requires loading that node; modeling it as
  its own connected node allows direct, indexed traversal to it instead. The right choice
  depends on whether the value is a traversal target or merely descriptive data.
- **What is a relationship versus what is a node with two relationships.** A many-to-many
  association with its own properties (a "purchased" relationship with a timestamp and
  quantity, for instance) usually needs to be its own node if it will ever be traversed *from*,
  not just observed as an edge label.
- **Relationship type granularity.** One generic relationship type overloaded for many meanings
  forces every traversal of it to filter by a property afterward, which defeats index-free
  adjacency's advantage — the engine cannot skip irrelevant relationships during traversal if
  they are all the same type. Distinct relationship types let a traversal request only the
  edges that could possibly match.
- **Denormalization by precomputed relationship.** Materializing a frequently-needed multi-hop
  relationship as a direct edge (a precomputed "friend of friend" relationship, for instance)
  trades write-time cost and storage for read-time traversal depth reduction — the same
  materialized-view trade-off as any other category, just expressed as an edge instead of a
  table.

---

## 6. Indexes in a graph context

Universal index reasoning (`databases/universal.md` §3) applies to **starting points**, not to
traversal itself:

- An index on the property used to locate the starting node(s) of a traversal is usually the
  single highest-value index in a graph workload — without it, every traversal begins with a
  full label scan.
- Composite and existence constraints follow the same selectivity reasoning as any index: a
  property with few distinct values does not benefit much from being indexed.
- Indexes do not help mid-traversal. An index cannot make an unbounded or high-fan-out
  traversal cheaper once it has begun; only the traversal's own shape and depth bound can.

---

## 7. Writes, transactions, and consistency

- Graph writes that touch a node and its relationships together are usually transactional at
  the node/relationship-set level; a write that must update many relationships atomically (for
  instance, rebalancing edges around a node) costs proportionally to how many are touched, the
  same amplification concern as any bulk operation elsewhere.
- Concurrent writes to the same node or its relationships contend the same way a hot row does in
  a relational engine — a frequently-updated supernode is a write hot-spot as well as a
  read-traversal hot-spot.
- Some graph engines separate "index" consistency from "graph" consistency under certain
  configurations (an index update that is not immediately visible to traversal, for instance);
  where that applies it is an engine-specific behavior to verify rather than assume, and belongs
  in a technology file once one exists for the engine in question.

---

## 8. Evidence available from a graph engine

Follow `databases/universal.md` §8's general reasoning; graph-specific evidence to look for:

| Evidence | Question it answers |
|:--|:--|
| Query plan / profile output | Whether the traversal started from an index or a label scan, and roughly how many nodes/relationships were visited |
| Degree or fan-out statistics on a node/label | Whether a supernode is plausible for this domain |
| Slow-query log | Which traversals are actually expensive in practice |
| Index listing | Whether the properties traversals start from are indexed at all |

Plan or profile inspection is generally safe on production for read traversals; treat any
command that executes a write-shaped traversal for profiling purposes as not-safe-on-production
unless wrapped in a transaction that is rolled back.

---

## 9. What to look for in a review

- Whether every traversal begins from an indexed property, or from a full label scan.
- Any variable-length or "however many hops" traversal with no upper bound on depth.
- Domain entities with an inherent power-law shape — likely supernodes — and whether traversals
  can reach them unguarded.
- Independent match patterns combined with no connecting relationship (a cartesian product).
- Filters applied after several hops of traversal rather than at the starting node.
- Whole nodes or paths returned where a few properties would do.
- Overloaded, generic relationship types standing in for several distinct meanings.
- Undirected traversal where the domain only ever needs one direction.

## 10. What not to conclude

- Do not assume traversal is cheap merely because "graph databases are fast" — that claim is
  true only for bounded, well-started traversals; an unbounded one is not helped by the engine's
  architecture at all.
- Do not recommend an index for a problem that is actually about traversal depth or fan-out;
  indexes only address finding the starting point.
- Do not assume a supernode exists without evidence of the domain's shape — check degree
  statistics or ask, rather than asserting it.
- Do not invent engine-specific behavior for a graph engine with no technology file — apply
  this category file and mark specifics as unknown, per the skill's graceful-degradation rule.
