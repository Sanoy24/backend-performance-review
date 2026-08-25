# Key-value datastores

Load after `databases/universal.md`, before any engine-specific file.

Key-value stores are optimized for access by key. Their performance model is simple, which
means most findings are about **usage that is not access by key** — and about key design,
which is the only real design decision available.

Covers both persistent key-value stores and in-memory stores used as caches or as primary
data. Cache-specific reasoning (hit rate, invalidation, staleness) lives in
`distributed/caching.md`; this file is about the store itself.

---

## 1. Key design is the data model

With no query planner and no secondary access path to rely on, the key encodes everything
the application can efficiently ask for.

- **A key that cannot be constructed from what the caller knows is useless.** Access
  patterns must be enumerated first; the key naming scheme follows from them.
- **Composite keys** encode a hierarchy and enable prefix-based grouping, where the store
  supports it.
- **Key size matters** at scale: keys are stored, transferred, and often held in memory
  alongside values. Long descriptive keys multiply across millions of entries.

**Access that is not by key is the finding.** Scanning to find entries, iterating the whole
keyspace to filter, or maintaining a secondary index by hand are all signs that either the
key design is wrong or the workload belongs in a different category of store. Report it as
a design finding.

Where the store offers iteration or scanning primitives, note that they are usually intended
for maintenance rather than request paths — using them per request is a scalability cliff.

---

## 2. Value size and shape

- **Large values** cost network transfer on every access and, in memory-resident stores,
  consume the scarcest resource. Fetching a large value to read one field of it is the
  key-value equivalent of a missing projection.
- **Storing a structure as a serialized blob** forces read-modify-write for any partial
  update: an extra round trip, plus a lost-update race between concurrent writers. Where the
  store offers structured types with field-level operations, using a blob instead is a
  finding.
- **Very small values in very large numbers** carry per-entry overhead that can exceed the
  data itself.
- **Unbounded collection values** — a list or set that grows with activity — have the same
  shape as the unbounded embedded array in a document store: growing read cost, growing
  memory, and an eventual hard limit.

---

## 3. Round trips dominate

Individual operations are typically fast enough that network round trips are the dominant
cost. Therefore:

- **Per-key operations in a loop** are the characteristic performance bug. Multi-key
  operations and pipelining exist to solve exactly this, and the improvement is usually
  large because the per-operation work was never the cost.
- **Read-modify-write cycles** are two round trips plus a race. Atomic operations, where
  available, are both faster and correct.
- **Chatty patterns across a network hop** — a handful of dependent lookups per request —
  add up on the critical path even when each is individually fast.

Where the store is distributed across nodes, multi-key operations may be constrained to keys
that live together. Note the constraint rather than assuming a batched form is always
available.

---

## 4. Memory and eviction

For memory-resident stores, memory is the binding resource, and behavior at the limit is the
most important configuration question in the system.

- **What happens when memory fills?** Evict by some policy, reject writes, or fail. These
  are radically different failure modes and the choice must match the use: eviction is
  correct for a cache and data loss for a system of record.
- **Is the eviction policy appropriate to the access pattern?** A recency-based policy suits
  workloads with temporal locality; it suits a uniform-random access pattern poorly.
- **Do entries have expiry?** Entries written without expiry accumulate. A store used as a
  cache with no TTL and no eviction policy will eventually fill, and the failure arrives
  suddenly.
- **Is memory usage monitored against the limit?** Without it, the first symptom is the
  failure itself.

A store used as a cache but configured to reject writes when full is a particularly sharp
finding: it turns a cache miss, which should degrade gracefully, into an error.

---

## 5. Single-threaded and blocking semantics

Many key-value stores execute commands one at a time per node. Where that is the case:

- **A single expensive command blocks every other client**, however brief the store's typical
  operation. Whole-keyspace operations, large collection operations, and expensive scripts
  are the usual culprits.
- **Latency is therefore coupled across all users of the store.** One misbehaving code path
  degrades every other one — a `system-wide` blast radius by construction.

Whether this applies is engine-specific; confirm it in the engine's reference file rather
than assuming. But when it does apply, it converts many "small" findings into severe ones,
and it is worth checking early.

---

## 6. Distribution and hot keys

- **Skew.** Effective capacity is set by the busiest node, not the average. A key that
  receives a large share of traffic saturates one node while the rest idle, and adding nodes
  does not help.
- **Monotonic keys.** Keys derived from timestamps or sequences concentrate writes on
  whichever node owns the current range.
- **Cross-node operations.** Multi-key operations spanning nodes are typically slower or
  unsupported.
- **Rebalancing** consumes bandwidth and can degrade latency while it runs.

Hot-key problems are among the few that cannot be solved by adding capacity. The fixes are
structural: split the key, add a local layer in front of it, or change the access pattern.

---

## 7. Persistence and durability

- Durability options range from none to synchronous-on-write, with the usual latency trade.
- Background snapshotting or log rewriting consumes I/O, CPU, and sometimes a memory spike
  proportional to the dataset — a periodic tail-latency cause that is invisible in
  application code.
- **A store treated as durable but configured otherwise** is a correctness finding
  discovered during performance review. Report it; it is more important than whatever
  latency question prompted the review.

---

## 8. What to look for in a review

- Access patterns that are not access by key.
- Per-key operations in loops where multi-key or pipelining exists.
- Read-modify-write where an atomic operation exists.
- Large values fetched whole to read part of them.
- Unbounded collection values.
- Behavior at the memory limit, eviction policy, and whether entries expire.
- Expensive whole-keyspace or large-collection operations on request paths.
- Hot keys and monotonic key patterns.
- Whether durability configuration matches how the data is treated.

## 9. What not to conclude

- Do not assume a key-value store is fast enough that usage patterns do not matter — round
  trips and hot keys dominate, and neither is fixed by the store being fast.
- Do not recommend adding nodes for a hot-key problem.
- Do not assume atomic or multi-key operations are available; confirm in the engine reference.
- Do not treat it as a cache if the application treats it as a system of record — establish
  which it is before recommending eviction or TTL.
