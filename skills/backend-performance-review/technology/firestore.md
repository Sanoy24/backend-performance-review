# Cloud Firestore

Load after `databases/universal.md` and `databases/document.md`.

This file contains **only what those files do not give you**. Document design, embedding
versus referencing, projection, and the index fundamentals of a document store are covered
there and are not repeated here.

---

## 1. Detection signals

Dependencies: `firebase-admin` (the server-side Admin SDK, in any language),
`@google-cloud/firestore` (Node.js), `google-cloud-firestore` (Python),
`com.google.cloud:google-cloud-firestore` or `com.google.firebase:firebase-admin` (JVM),
`cloud.google.com/go/firestore` (Go), `Google.Cloud.Firestore` (.NET).

Other signals: `firestore.indexes.json` and `firestore.rules` (both committed by the
Firebase CLI), `firebase.json`, `.firebaserc`, Firestore emulator configuration in test
setup or CI, `FIRESTORE_EMULATOR_HOST` in environment templates.

**Record whether the database is in Native mode or Datastore mode** (§6) — they are the
same underlying service with materially different query semantics and limits, and a claim
about one can be wrong for the other.

---

## 2. What differs from the document baseline

### Cost is charged per document read, which makes it the binding constraint rather than latency

Firestore bills reads, writes, and deletes per **document**, not per query or per byte. A
query matching 5,000 documents bills 5,000 reads whether the application uses all of them or
displays ten. This inverts the usual review priority for this engine: a pattern can be
perfectly fast and still be the most important finding in the report, purely on cost — which
is why `databases/universal.md`'s framing of cost as a dimension applies here with unusual
force.

The checkable consequences in application code:

- **A query without a `limit()`** bills for everything it matches, and its cost grows with
  the collection.
- **Client-side filtering after a broad query** bills for every document fetched, including
  those immediately discarded — the fetch already happened.
- **Reading a document to check existence** costs the same as reading it to use it.
- **A real-time listener bills per document delivered**, including on the initial snapshot
  and on every subsequent change delivered to each attached client — a listener on a
  frequently-written collection, attached by many clients, multiplies cost by both.

### Every query must be backed by an index, and composite indexes are declared by hand

Single-field indexes are created automatically for every field; anything requiring a
composite index (multiple equality/range fields, or an equality plus an ordering) must be
declared explicitly, and Firestore **fails the query with an error** rather than falling
back to a scan. This is a real advantage for reviewability — there is no such thing as a
silent full-collection scan — and it means `firestore.indexes.json` is a directly readable
statement of every non-trivial query the application intends to run.

The cost sits on the other side: automatic single-field indexing applies to **every field
of every document**, including large fields never queried, and each index entry is written
on every document write. **Index exemptions** (per-field, declared in the same file) are the
documented mechanism for opting out, and a collection with large or high-cardinality
never-queried fields and no exemptions is paying write throughput and storage for indexes
nothing reads.

### A single document has a sustained write-rate ceiling of roughly one per second

This is a documented soft limit, and it is architectural rather than a tuning target: a
counter, a "last updated" aggregate, or any single document written on every request will
throttle regardless of how the rest of the system is provisioned. The documented workaround
is a **distributed (sharded) counter** — spreading writes across N shard documents and
summing on read — which trades read cost for write throughput.

Because this is a per-document limit rather than a collection-wide one, it does not appear
under load testing that spreads writes across many documents, and then appears sharply in
production the moment a genuine hot document exists. A schema containing a single document
that every request updates is a checkable, high-severity finding from the data model alone.

### Monotonic values in document IDs or indexed fields create hotspots on the underlying key range

Firestore stores data in lexicographically ordered key ranges that split as they grow. A
sequentially increasing document ID, or an indexed strictly-increasing timestamp field,
concentrates writes on the range holding the highest keys — the same mechanism described for
CockroachDB in `technology/cockroachdb.md`, appearing here through document ID and index
design rather than primary-key DDL. Automatic ID generation avoids it; an explicit
sequential scheme reintroduces it.

Firestore additionally documents a **ramp-up rule** for traffic to a new collection
(commonly cited as the "500/50/5" rule: start around 500 operations per second and increase
by 50% every five minutes), because range splitting needs time to catch up with load. A
launch or migration plan that expects to go from zero to full traffic instantly is working
against this.

### Transactions, batches, and the absence of a join

Batched writes and transactions cap at **500 operations**, and a transaction that reads
contended documents will retry. There is no server-side join: retrieving related data means
one read per referenced document, which — given per-document billing — makes the document
category file's embedding-versus-referencing decision a *cost* decision here, not only a
latency one. Aggregation is limited to the dedicated `count()`/`sum()`/`avg()` aggregation
queries (§6); anything else means reading the documents.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| Query Explain (`explain` / `explainAnalyze` options on a query, current SDKs) | Index selection, documents scanned versus returned, and billed read count for a query | **safe-on-production** for `explain` (plan only); `explainAnalyze` executes the query and bills for it |
| Cloud Monitoring `firestore.googleapis.com/document/read_count` / `write_count` / `delete_count` | Actual billed operation counts over time, broken down by type | **safe-on-production** |
| Cloud Monitoring `firestore.googleapis.com/api/request_latencies` | Server-side request latency distribution | **safe-on-production** |
| Firebase console → Usage tab | Reads/writes/deletes per day against quota and cost | **safe-on-production** |
| `firestore.indexes.json` (in the repository) | Every declared composite index and field exemption — readable without touching the database at all | **safe-on-production** |
| `gcloud firestore indexes composite list` | Composite indexes actually deployed, to compare against what the repository declares | **safe-on-production** |
| Key Visualizer (Firestore) | Heat map of access across the key range — the direct evidence for a hotspot finding | **safe-on-production** |
| Firestore emulator with debug logging | Exact query and read counts for a code path, locally, with no billing | **safe-on-production** (does not touch production at all) |

The emulator deserves particular emphasis for this engine: because the primary risk is
billed read volume rather than latency, counting reads for a code path locally is both free
and more conclusive than inferring cost from production aggregates.

---

## 4. Common failure modes and their symptoms

| Symptom | Firestore-specific cause to check first |
|:--|:--|
| Bill grows far faster than user count or stored data | Queries without `limit()`, client-side filtering after broad fetches, or listeners delivering documents to many clients |
| Writes to one document throttle at a low, stable rate | The ~1 write/second per-document sustained limit — a hot counter or aggregate document |
| Sudden latency and errors on a newly launched collection under real traffic | Traffic ramped faster than range splitting can keep up with (the documented ramp-up guidance) |
| A query fails outright with "The query requires an index" | Missing composite index — this is the engine refusing to scan, not a bug |
| Write latency grows as documents gain fields | Automatic single-field indexing on every field; missing index exemptions |
| A transaction retries repeatedly or times out | Contention on documents the transaction reads; contended-document retry is expected behavior |
| Costs spike from a screen nobody reported as slow | Real-time listeners re-delivering documents on every change, billed per document per client |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Per-field index exemptions (`firestore.indexes.json`) | Lower write amplification and storage versus losing the ability to query or order by that field |
| Composite index set | Query capability versus index storage and per-write maintenance cost |
| `limit()` on every list query | Bounded, predictable read cost versus needing explicit pagination handling |
| Sharded counter width | Write throughput on a hot aggregate versus N reads to compute the total |
| Listener scope (query breadth and client count) | Real-time responsiveness versus per-document-per-client billing |
| TTL policies on collections | Automatic cleanup of expired documents versus delete operations being billed |
| Security rules complexity | Access control expressiveness versus the document reads that rules performing `get()`/`exists()` lookups themselves incur — rule-driven reads are billed |

The last row is easy to miss: security rules that call `get()` or `exists()` to check a
related document perform real, billed reads on every request they evaluate.

---

## 6. Version differences worth knowing

- **Native mode versus Datastore mode** is the most consequential distinction and is fixed
  at database creation. Datastore mode is the older Cloud Datastore semantics (different
  query and index behavior, no real-time listeners, no offline SDK support); Native mode is
  what current Firestore documentation describes. A claim about one may be wrong for the
  other — confirm which is deployed before applying anything above.
- **Aggregation queries** (`count()`, later `sum()` and `avg()`) were added after the
  original API; before them, any count meant reading every matching document. Code that
  maintains its own counter documents may predate the aggregation support and be carrying an
  obsolete workaround — one that also collides with the per-document write limit above.
- **Query Explain** is a comparatively recent addition; older SDK versions have no
  equivalent, and cost analysis on such a codebase has to come from Cloud Monitoring or the
  emulator instead.
- **Firestore's multi-database support** (more than one database per project) is newer than
  much existing material; a repository targeting a named non-`(default)` database is using
  it, which is worth noting since some tooling assumes the default.

**Confirm version- and mode-specific claims against the current documentation for the
deployed configuration.**

---

## 7. What this file does NOT cover

- Document design, embedding versus referencing, projection, and index fundamentals in a
  document context — see `databases/document.md`.
- Firebase client-SDK concerns: offline persistence, local cache behavior, and mobile/web
  bundle size — this skill reviews backends (see README's scope).
- Security-rules authoring and correctness, beyond noting that rule-driven document lookups
  are billed reads.
- Firestore's backup/export/import mechanics and their cost model.
- The pricing numbers themselves, which vary by region and change over time — this file
  names *what* is billed per document, never a rate.
- Datastore mode's own query and index semantics in detail.
- Specific numeric threshold recommendations beyond the documented limits and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
