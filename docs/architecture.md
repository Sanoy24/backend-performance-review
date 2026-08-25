# Architecture

Why the repository is shaped this way. Read this before making a structural change.

---

## 1. The problem being solved

An agent asked to review backend performance has two strong failure modes, and both come from
the same place — no discipline about evidence, and no model of workload:

- **The generic checklist.** Correct, universal, unactionable advice with no relationship to
  the code in front of it.
- **Confident fabrication.** Invented latencies, imagined query plans, recommendations that
  would be right for someone else's system.

Every structural decision below exists to make one of those harder.

---

## 2. Layered knowledge

```
                      SKILL.md
        doctrine · workflow · rubrics · routing
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   methodology       principles      application/
   how to review   why things are     databases/
                        slow           runtimes/
                                      distributed/
                                    infrastructure/
                                          │
                                     technology/
                            engine specifics only
```

The layers answer different questions, and the separation is load-bearing:

| Layer | Question | Changes when |
|:--|:--|:--|
| `SKILL.md` | How do I conduct a review? | The methodology changes — rarely |
| `methodology/` | How do I do this phase? | The methodology changes |
| `principles/` | Why is anything slow? | Almost never — this is physics and queueing |
| Layer files | How does this manifest here? | New patterns emerge |
| `technology/` | What is specific to this engine? | Every new engine — often |

**The frequently changing layer is the leaf.** Adding an engine touches one file plus one
registry row. That is the extensibility property the whole structure exists to provide.

---

## 3. Two rules that keep the tree from collapsing

Without these, a growing reference tree becomes a set of near-identical checklists — the exact
outcome the methodology forbids, merely distributed across directories so it looks
architectural.

### Category files never name a product

If `databases/relational.md` names an engine, its content belongs in a technology file. This
keeps category reasoning transferable: an unfamiliar relational engine still gets the full
category analysis.

Enforced by a grep in `CONTRIBUTING.md` §9.

### Technology files contain only non-derivable content

A technology file may contain only what its category file does not already give you. If
`technology/postgres.md` explains what an index is, it is wrong.

This is checked by reading, not by grep, and it is the most common reason a pull request is
sent back. The seven-section structure required of technology files exists to make it obvious:
the section is called "what differs from the category baseline", so content that does not
differ has nowhere to go.

---

## 4. Routing: `registry.yaml`

With roughly thirty reference files, ad-hoc selection fails. The registry maps detection
signals to reference files and declares a support tier.

```yaml
- signal: postgres
  kind: datastore
  category: relational
  match: [psycopg, asyncpg, "postgres://", pgx, npgsql, ...]
  load: [databases/universal.md, databases/relational.md, technology/postgres.md]
  tier: deep
```

Three properties matter:

- **`load` is ordered.** Category before technology, always — so engine specifics land on top
  of a foundation rather than replacing it.
- **It is the single source of truth for support tiers.** The README table is generated from
  it, so documented support cannot drift from actual content.
- **It is machine-readable.** `detect_stack.py` parses it, so detection and routing cannot
  disagree with each other.

`principles/` files are routed by a table in `SKILL.md` rather than by the registry, because
they are selected by the *question being asked* rather than by the detected stack.

---

## 5. Why priority is derived

The rubric has exactly two scored axes — severity and an evidence-graded confidence — and
priority is read off a published matrix.

An earlier draft of the specification carried four overlapping taxonomies with no rubric for
any of them. That has two failure modes, both observed in practice:

- **Drift.** With no written criteria, everything becomes "High". Severity stops carrying
  information.
- **Irreproducibility.** Two reviews of the same code produce different rankings, and neither
  can be argued with.

Deriving priority also removes a subtle bias: it prevents effort from leaking into impact. A
cheap fix is tagged `quick-win` and sequenced early, but its priority is unchanged, because
priority answers "how much does this matter", not "how easy is it".

---

## 6. Why observability is inventoried first

Available evidence sets the ceiling on the confidence any finding can reach. Discovering at
the end of a review that nothing could have been `Confirmed` wastes the whole review.

So Phase 1 asks what metrics, traces, benchmarks, load tests, and query plans exist, and the
report states which of three states the repository is in — well instrumented, partially, or
uninstrumented. That single line tells a reader how much to trust everything after it.

Observability *recommendations* still come at the end. Only the inventory moves early.

---

## 7. Why zero findings must be allowed explicitly

A workflow that runs discovery → analysis → findings, with no defined exit for "nothing
material here", will produce findings. Completion pressure fills the section, and the filler
is speculative — which violates the anti-cargo-cult rule the same document sets out.

So the permission is stated explicitly in `SKILL.md`, `rubrics.md` carries a worked example
that correctly finds nothing, and the report template's "Considered and not reported" section
gives discarded candidates somewhere to go.

---

## 8. Why an out-of-scope issue gets a full write-up but never a performance score

A review reading code for performance will sometimes turn up something real that isn't a
performance question at all — a timing-unsafe comparison, an end-of-life runtime, a SQL
injection risk. Two opposite mistakes are both live here, and behavioral evaluation
(`docs/evaluation.md` §3.8) hit the first one in practice before this rule existed: a real
finding was scored `Informational`/P3, which is defined as "no current or projected impact" —
true of a hygiene note, false of a real vulnerability. Scoring it that way reads as "safe to
deprioritize," which is worse than not mentioning it, because it looks like the issue was
considered and dismissed rather than never evaluated on its own terms.

The other mistake is reducing it to a one-line mention once it's out of the performance rubric.
That under-serves it in the opposite direction: it was found with the same evidence discipline
as any performance finding, and deserves the same rigor — Problem, Evidence, Recommendation,
Trade-offs, Validation — not a footnote.

The fix keeps both halves: full write-up, own section ("Adjacent findings — outside performance
scope"), own IDs (`SEC-`/`COR-`/`MAINT-`, never `PERF-`), classified on `Kind` and the existing
evidence-grade `Confidence` scale (a domain-neutral "how sure are we," which transfers fine) plus
a plain-language `Risk` note — deliberately *not* a CVSS-style score, because this skill has no
dedicated security or correctness methodology, and manufacturing that rigor would be exactly the
kind of dishonesty the evidence-first rule forbids elsewhere. It always names the dedicated
review that would actually assess it, because pointing at the right tool is honest; pretending
to be that tool is not.

---

## 9. Why examples live outside `skills/`

Example content leaks. An example report containing a fabricated `p99 = 340ms` does two kinds
of damage: it violates the evidence rule in the repository's own artifacts, and it risks that
figure being pattern-matched into a real report.

`docs/examples/` is not on any reference path, so an example can never be loaded as part of a
review. Examples are documentation for humans deciding whether to adopt the skill.

The same reasoning drives the rule that examples must demonstrate *restraint* — an explicit
unknown, and a plausible optimization deliberately not recommended. An example that only finds
things teaches the wrong behavior even when nobody loads it.

---

## 10. Why the detection script exists

Detection is mechanical: read manifests, match substrings, resolve references. Prose
instructions telling an agent to do that are followed inconsistently and cost tokens each
time.

The script is deliberately constrained:

- **Stdlib only, no network.** It must be safe to run on an unfamiliar repository without
  installing anything.
- **Read-only, and blind to secrets.** It reports that a `.env` exists; it never reads it.
- **An accelerator, never a dependency.** Every path it automates has a manual fallback
  documented in `methodology/discovery.md`. A skill that breaks when Python is missing would
  be worse than one that never had a script.

It parses a deliberately small YAML subset rather than depending on a parser. That is a
trade: the registry format must stay simple, and extending the format means extending the
reader. The alternative — a third-party dependency — would have compromised the "runs
anywhere" property, which matters more.

---

## 11. Constraints on future changes

- `SKILL.md` stays tight. Its content persists in context for the whole session, so every line
  is a recurring cost. Detail belongs in reference files that load on demand.
- Reference files stay under roughly 400 lines. Beyond that they are usually restating a
  category file.
- Adding a technology must not require touching `SKILL.md` or the methodology. If it does, the
  routing layer has a gap — fix the routing rather than special-casing the engine.
- Changes to the rubrics or the finding schema are **breaking**, because previously issued
  severities stop being reproducible. See `CHANGELOG.md`.
- Prefer changes that make the agent discard more candidates over changes that make it find
  more. Precision is the scarce resource.
