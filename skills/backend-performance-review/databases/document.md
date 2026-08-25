# Document datastores

Load after `databases/universal.md`, before any engine-specific file.

Document stores keep semi-structured records retrieved as whole units. Their performance is
governed less by query tuning than by **document design**: the shape of the document decides
which access patterns are cheap and which are impossible to make cheap.

Nothing here is engine-specific.

---

## 1. Document design is the performance model

In a relational store, the schema is normalized and the query decides the shape of the
result. In a document store, the document shape decides which queries are efficient. This
inverts where review effort goes: **look at the documents before looking at the queries.**

### Embedding versus referencing

| | Embedding | Referencing |
|:--|:--|:--|
| Read of the whole aggregate | One operation | Multiple operations, or an application-side join |
| Write of a sub-item | Rewrites the containing document | Writes one small document |
| Growth of a sub-collection | Document grows without bound | Bounded document size |
| Consistency across the aggregate | Natural, single-document | Requires care across documents |
| Duplication | Data duplicated across documents | Single copy |

The decisive question is whether the embedded collection is **bounded**. An embedded array
that grows with user activity — comments, events, log entries, order lines for a long-lived
account — is one of the most reliably severe findings in a document store:

- The document grows without limit.
- Every update rewrites a growing document.
- Every read transfers the whole thing, whether or not the array is needed.
- The engine may eventually refuse the document at a size limit, turning a performance
  problem into an outage with no code change.

Report unbounded embedded arrays as `scalability-risk` at minimum, and as a current finding
if there is evidence they are already large.

The reverse error also occurs: **over-referencing**, where a naturally bounded aggregate is
split into several documents, producing an application-side join on every read. That is an
N+1 whose cause is the data model rather than the code.

---

## 2. Reading only what is needed

- **Projection.** Without it, the whole document is read and transferred. On documents with
  large embedded content this is often the single largest avoidable cost, and it is visible
  in code.
- **Partial updates.** Updating a field should not require reading, modifying, and writing
  the entire document. Read-modify-write in application code is both slower and a lost-update
  hazard where an atomic field update exists.
- **Array handling.** Returning an entire large array to use a few elements. Where the engine
  supports slicing or filtering array elements server-side, doing it client-side transfers
  everything.

---

## 3. Indexes in a document context

Universal index reasoning applies, with additions specific to nested and flexible data:

- **Nested field indexes** work but require the query to reference the same path. Small
  differences in path shape between write and query code produce silently unindexed queries.
- **Multi-valued (array) indexes** generate an index entry per element, so write cost scales
  with array length. A large array with an indexed field is expensive on every write.
- **Compound index leading-field rules apply**, exactly as elsewhere.
- **Optional fields.** Documents in the same collection can differ in shape, so an index may
  cover only some documents. Queries filtering on a field that many documents lack may
  behave differently than expected — check whether the engine's index semantics for missing
  fields match what the query assumes.
- **Index count.** Flexible schemas invite index proliferation, since every new access pattern
  is one field away. Each still costs on every write.

---

## 4. Queries that scale badly

- **Unindexed field predicates** — an examination of the whole collection.
- **Leading-wildcard or unanchored text matching** — generally cannot use an ordinary index.
- **Deep-offset pagination** — same cost profile as elsewhere; the engine must produce and
  discard skipped documents.
- **Multi-stage aggregation pipelines that filter late.** Filtering and projecting as early
  as possible reduces what every later stage must process. A pipeline that sorts or joins
  before narrowing is doing avoidable work at every subsequent stage.
- **Sorts without index support**, which must materialize the result set in memory and can
  fail or spill at size.
- **Application-side joins in a loop** — the document-store form of N+1, usually caused by
  over-referencing.
- **Queries whose shape the document design never anticipated.** This is the design finding:
  no index makes an inherently cross-document, cross-shape query cheap. Report it as a data
  model issue, not a tuning issue.

---

## 5. Writes and update cost

- **Document growth on update.** If a document grows beyond the space allocated for it, the
  engine may need to relocate it — which also touches every index pointing at it. Repeated
  growth of the same documents is a write-amplification pattern worth flagging.
- **Whole-document rewrites** where an atomic field-level update exists.
- **Unbatched bulk writes** — per-document round trips where a bulk API exists.
- **Write concern / acknowledgement level** is a latency-versus-durability trade. Both
  extremes are findings if chosen by default rather than by requirement: weakest risks data
  loss, strongest costs latency on every write.

---

## 6. Consistency and read routing

- Reads directed to secondaries trade freshness for capacity, with the same read-after-write
  hazard as any replicated system.
- Multi-document atomicity, where offered, is generally more expensive than single-document
  atomicity. A data model that requires it frequently is a model worth questioning.
- Where the engine offers tunable read/write consistency levels, the choice belongs in the
  finding's `Trade-offs`, not in an unstated default.

---

## 7. What to look for in a review

- Embedded arrays with no bound on growth — check first, it is the highest-value item here.
- Over-referencing producing application-side joins in loops.
- Missing projections on documents with large fields.
- Read-modify-write where an atomic update exists.
- Indexed fields inside large arrays.
- Aggregation pipelines that filter or project late.
- Sorts and predicates with no index support.
- Query shapes the document design cannot serve efficiently.
- Acknowledgement and read-preference settings chosen by default.

## 8. What not to conclude

- Do not recommend normalizing or denormalizing without identifying the access pattern that
  drives it.
- Do not assume schema flexibility means schema design does not matter — it matters more.
- Do not claim a query is unindexed without checking index definitions in migrations, setup
  scripts, or infrastructure code.
- Do not treat a document store as a relational store with different syntax; the trade-offs
  differ in kind, not degree.
