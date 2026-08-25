# Object storage

Load after `databases/universal.md`, before any engine-specific file.

Object storage (S3-compatible and equivalent blob stores) has no query planner, no secondary
index, and no partial update — its performance model differs from every other category this
skill covers. Most findings here are about **request count and object shape**, not query
tuning, because there is no query to tune.

---

## 1. Request count, not data volume, is the dominant cost dimension

Every operation is an HTTP round trip with its own latency and, on most services, its own
metered cost — independent of how much data that request actually moves. A workload doing many
small per-object operations pays for each one individually in a way a datastore with an
efficient multi-get does not.

- **Looping over objects one at a time where a batch primitive exists** (batch delete, batch
  retrieval where supported) is the direct analogue of the "per-key operations in a loop" finding
  `databases/key-value.md` names — and often more expensive here, because the per-request
  overhead includes a full HTTP round trip, not just a protocol-level operation.
- **Request count compounds with concurrency limits and rate limits** that many services impose
  per prefix or per account — a workload issuing a large burst of small requests can be
  throttled well before it is bandwidth- or CPU-bound.

---

## 2. Key and prefix design is the only access structure

There is no secondary index and no filtering by content. Objects are retrieved by exact key, and
enumerated by key prefix where listing is supported.

- **Access that is not by exact key or bounded prefix is the finding** — the same principle
  `databases/key-value.md` states for key-value stores, applying here without a query layer to
  fall back on at all.
- **Prefix design is frequently used to emulate hierarchy** ("folders"), and listing walks keys
  in lexicographic order under that prefix. A prefix scheme where all objects share a single
  narrow prefix (a timestamp-leading key, for instance) can concentrate request load on whichever
  internal partition serves that prefix range — the object-storage form of the hot-partition
  problem this skill's other partitioned stores exhibit.
- **A key that cannot be constructed from what the caller knows is useless**, exactly as in
  `databases/key-value.md` §1 — if finding an object requires listing and filtering rather than
  direct key construction, the key design is wrong for the access pattern.

---

## 3. Objects are immutable — every "update" is a full replacement

There is no partial, field-level, or in-place update. Overwriting an object means transferring
the entire new object, regardless of how small the actual change is.

- **Repeatedly rewriting a large object to change a small part of it** pays full-object transfer
  cost on every write — the sharper, harder form of the "storing a structure as a blob forces
  read-modify-write" finding `databases/key-value.md` §2 names generically. Here, there is no
  field-level operation to fall back on at all; the mismatch is structural, not just a missed
  optimization.
- A workload whose primary pattern is frequent partial updates to large objects is using the
  category against its grain — per `databases/universal.md` §1, this is a design finding, not a
  tuning one. The fix is usually a different category (a document or relational store for the
  frequently-mutated structured data, with object storage reserved for genuinely large, rarely-
  modified blobs), not tuning the write path.

---

## 4. Large objects need multipart handling, and it is a hard limit, not a graduated cost

Most services impose a maximum size for a single-request upload, and multipart upload (splitting
an object into independently-uploaded parts, assembled afterward) exists to work around it, not
merely to improve performance. An object at or beyond this ceiling **fails outright** without
multipart handling — the same hard-cap shape a fixed item-size limit produces in a key-value
store, though the ceiling here is far larger. Multipart upload also provides real,
additional benefits below that ceiling — resumability after a partial failure, and parallel part
upload for throughput — worth surfacing as available machinery even when the hard limit isn't
the immediate concern.

Symmetrically, **downloading an entire large object to use only part of it** (a byte range, a
specific record inside a larger file) is the object-storage form of "fetching a large value to
read one field of it." Ranged retrieval, where supported, is the direct fix.

---

## 5. Listing does not scale like a point lookup, and there is no server-side filter beyond prefix

A listing operation returns keys page by page, optionally scoped by a prefix and a delimiter —
that is the entire filtering capability available server-side. Any additional filtering (by
content, by metadata not part of the key, by anything the storage layer itself can't evaluate)
happens **after** listing, client-side, across however many pages the unfiltered listing
produces.

- **A broad or unbounded listing operation used as a way to find something** is the object-store
  equivalent of an unbounded scan — and it is worse here than in an engine with even a basic
  index, because there is no server-side predicate to narrow it beyond the prefix itself.
- **This is the sharpest instance of "the workload belongs in a different category of store"**
  in this file: a design whose primary access pattern is broad listing followed by client-side
  filtering has outgrown object storage's model. The fix is almost always an external index — a
  row per object in a datastore with an actual query layer, storing the key as a pointer — not a
  cleverer prefix scheme.

---

## 6. Request and egress cost are first-class dimensions distinct from latency

Unlike most datastores this skill covers, object storage commonly has an explicit, metered cost
model on **three separate axes**: per-request cost, storage cost, and egress (data-transfer-out)
cost. A pattern can be entirely fast and still be a real finding on cost grounds alone:

- **Repeatedly serving the same large object to many callers with nothing caching or fronting
  it** generates egress cost proportional to every single retrieval — a legitimate finding even
  where individual latency looks fine.
- **Storage tier/class mismatches** — frequently-accessed data left in a tier priced for
  infrequent access (or the reverse) — trade retrieval latency and per-retrieval cost against
  storage cost. This file does not name specific vendor tier products, but the trade-off itself
  (access frequency versus storage-cost tier) is a real, checkable question regardless of engine.

A review that only asks "is this fast" for an object-storage-heavy workload, without also asking
"how many requests, and how much data leaves the system, does this pattern generate," is
incomplete for this category specifically.

---

## 7. Read-after-write consistency is engine- and operation-specific — confirm, don't assume

Object stores have historically varied, and continue to vary, in whether a read immediately
after a write (especially an overwrite or delete) is guaranteed to reflect it, versus being
eventually consistent for some operation types. This is squarely engine-specific and has changed
over time across the industry — this file will not assert a default in either direction.

A workload that depends on immediately reading back a just-written or just-overwritten object,
without having confirmed the actual guarantee the specific engine provides for that operation, is
a correctness-adjacent risk (`SKILL.md` rule 8) that a performance-focused investigation
frequently surfaces first, because a consistency gap often first presents as "stale data" during
exactly this kind of review.

---

## 8. What to look for in a review

- Per-object operations in a loop where a batch primitive exists.
- Key and prefix design: can every object actually be found by constructing its key, or does the
  application rely on listing and filtering?
- Full-object rewrites for small logical changes, especially on large objects.
- Large objects handled without multipart upload, and full-object downloads where a byte range
  would do.
- Broad or unbounded listing operations used as a discovery mechanism.
- Request count and egress volume, evaluated as their own cost dimension, separate from latency.
- Storage tier/class choice against actual access frequency.
- Whether the application's consistency assumptions match the engine's actual, confirmed
  guarantees for the specific operations it relies on.

## 9. What not to conclude

- Do not treat object storage as a general-purpose database with a missing index — it has no
  query layer by design, and the fix for a query-shaped access pattern is a different category
  of store, not a workaround within this one.
- Do not recommend tuning a listing operation that is fundamentally a client-side-filtered scan;
  recommend an external index.
- Do not assume a specific read-after-write consistency guarantee without confirming it for the
  engine and operation in question.
- Do not evaluate an object-storage access pattern on latency alone; request count and egress are
  independent, real cost dimensions for this category specifically.
