# Benchmark

Measuring whether this skill is any good, rather than asserting it.

A project whose strongest rule is "never assert what you have not verified" cannot exempt
itself from that rule. [docs/evaluation.md](../docs/evaluation.md) records nine independent
blind passes across eight repositories, which is real evidence — but every comparison in it
was made by a human reading two reports side by side. That is why nine expensive runs have
produced exactly two inter-run consistency data points.

This directory is the machinery for doing it by computation instead.

---

## What is here

```
benchmark/
├── ground-truth/     expert annotations, one file per repository at one commit
└── scoring/score.py  precision, recall, calibration, restraint, stability
```

`ground-truth/` is empty on purpose. Annotations are the expensive part, and committing
placeholder ones would put fabricated truth into a repository whose first rule is not to
fabricate. See "What to annotate first" below.

## Running it

```
python benchmark/scoring/score.py score --truth ground-truth/<repo>.json --review <review>.json
python benchmark/scoring/score.py score --truth ... --review ... --json
python benchmark/scoring/score.py stability --review run-a.json --review run-b.json
```

The review file is the machine-readable output described in
[schemas/review.schema.json](../schemas/review.schema.json), which a review emits alongside
its Markdown report (report template §10).

## What it measures, and why separately

| Metric | The failure it detects |
|:--|:--|
| Precision / recall / F1, **per category** | "Good at N+1, poor at concurrency" is actionable. One blended number hides it |
| Severity calibration | Finding the right issue and calling everything `Critical`. Scored exact / within one / large disagreement |
| Confidence calibration | Whether `High` predicts correctness. If it does not, the label is decoration |
| Confidence ceiling violations | Claiming `Confirmed` where no runtime artifact exists. Being right does not license overstating how you know |
| Recommendation accuracy | Correctly identifying a problem and then proposing the wrong fix — a distinct failure, tracked apart from finding accuracy |
| Restraint | Reporting a known non-problem. On a healthy repository this is the entire test |

## The three buckets

Ground truth is not one list of "the right answers". It is three, and the split is the part
worth understanding:

| Bucket | Meaning | Scoring |
|:--|:--|:--|
| `expected` | A competent review must find this | Miss → false negative |
| `acceptable` | Real, but not required | Scored neither way |
| `forbidden` | Must **not** be reported. A pattern that looks like a problem and provably is not | Reported → false positive |

Without `acceptable`, a review would be punished for finding something real that the
annotator overlooked — which would train the corpus toward the annotator's blind spots.

Without `forbidden`, a healthy repository could not be expressed at all. **An empty
`expected` plus a populated `forbidden` is how "the correct answer here is nothing" is
written down**, and that is the single most important case this benchmark can hold.

Every `forbidden` entry must state `why_not` as a fact about the code — the counter-evidence
a good review would have found. Without that requirement the corpus would encode the
annotator's taste as truth.

## What to annotate first

In priority order, and the first one is not a tie:

1. **A repository whose correct answer is zero findings.**
   [docs/evaluation.md §3.7](../docs/evaluation.md) records that across four real
   repositories, *none* produced a literal zero-finding result. The project's own required
   case 2 calls this "the most important case" and it has never been cleanly satisfied. A
   system that cannot be shown to resist manufacturing findings has not demonstrated the
   property it is built around. **Still open** — nothing in the corpus below satisfies this;
   every one of them has at least one real `expected` finding.

2. **The repositories already blind-passed.** Those runs are proto-ground-truth that has
   already been paid for: they record locations, mechanisms, severities, and in several
   cases bugs later confirmed and fixed. Retro-annotating them costs a fraction of
   commissioning new cases. Mark these `"method": "blind-pass-derived"` — truth derived from
   a run rather than established independently is weaker evidence, and the annotation has to
   say so. **All eight independently blind-passed repositories are now done**
   (`ground-truth/*.json`), plus one change-scoped case:
   - `gin-realworld.json` — the N+1 traced through the serializer chain (§3.1, §3.9, §3.18),
     plus two `forbidden` traps for patterns that look like the same shape and aren't (an
     already-batched favorites query, an SQLite pool-config non-contradiction).
   - `fastapi-full-stack-template.json` — unbounded pagination (§3.2, §3.10), plus two
     `forbidden` traps for the detection false positives that evaluation itself found and
     fixed (a frontend `node_modules` misattributed as the backend runtime; Go framework
     names matching inside an English word and inside shell `echo` commands).
   - `urlshortener.json` — the one repository with real `Confirmed`-grade runtime evidence
     (committed Go benchmarks, actually run): a genuine P0 missing-timeout finding, and a
     `forbidden` trap for the *tempting* finding the benchmark itself argues against
     (optimizing a sub-200ns operation nowhere near the real bottleneck).
   - `github-signature-verifier.json` — the closest attempt yet at a case-2 candidate
     (near-minimal, stateless), still not clean: a real caching gap, with severity corrected
     from `Medium` to `High` between two independent passes (§3.7 → §3.8) on evidence the
     first pass had already read and set aside. Encodes that correction as the ground truth,
     making this file also a worked example of what severity-calibration scoring is for.
   - `spring-boot-realworld.json` (JVM) — an unbatched GraphQL N+1 with a working REST
     counter-example in the same codebase proving the fix is a known pattern, plus a missing
     index provable from the migration file alone.
   - `realworld-axum-sqlx.json` (Rust) — five findings, including an unbounded full-table
     scan the codebase's own source comment calls a DoS vector, and a `spawn_blocking`
     offload correctly identified as the *right* pattern rather than a defect (`forbidden`).
   - `aspnetcore-realworld.json` (.NET) — a synchronous EF Core transaction wrapping every
     request inside an `async` handler, with precise `file:line` citations throughout; the
     only file in this batch with a pinned commit in the source record.
   - `node-express-realworld-prisma-postgres.json` (Node.js/Prisma) — an unbounded relation
     fetch where the efficient `_count` form is already computed in parallel and silently
     discarded in favor of the expensive one, plus a `forbidden` trap for declining to
     recommend clustering with no host core count anywhere in evidence.
   - `gin-realworld-pr49-change-scoped.json` — the one change-scoped case (§3.6): ground
     truth for a real, merged PR, testing that a review credits a genuine fix (`forbidden`)
     while still catching the untouched, structurally identical half of the same problem one
     call deeper (`expected`) — the "sits directly adjacent to" rule in `SKILL.md` §Modes.

   `forbidden` is intentionally sparse or empty on four of these files (JVM, Rust, .NET,
   Node's clustering item aside) — the source write-ups for that batch describe what was
   correctly declined mostly in prose, without pinning an exact file:line in every case, and
   inventing one to fill the field would violate the same no-invented-facts discipline this
   corpus exists to hold reviews to. A thinner `forbidden` list is a more honest annotation
   than a fabricated one.

   The second independent pass used for stability measurement (§3.18, §3.21 in prose form)
   now has a real, live counterpart: see "The first real validation run" below.

3. **Repositories with misleading signals** — an obvious smell that is harmless, a real
   problem somewhere unexpected, a technology named in a manifest but never used.

4. **Repositories with interacting bottlenecks**, where each issue is moderate alone and
   severe combined.

## The first real validation run

Everything above this section, until this point, was retro-annotated from
`docs/evaluation.md`'s prose — real evidence, but written before this schema existed and
before the harness could score anything. The measurement system's first genuine end-to-end
test came after the 1.0.0 milestone landed: two fresh `general-purpose` agents, each with no
memory of this project's own findings and explicitly forbidden from reading
`docs/evaluation.md`, `docs/review-response.md`, or `benchmark/ground-truth/`, were each given
only `SKILL.md` and a freshly cloned target repository, and asked to produce a full review —
Markdown report and schema-valid JSON — against the complete, updated skill.

**Two independent reviews of `gin-realworld`.** The dominant finding (the per-article
follow-status N+1) was reproduced by both, matching the location/mechanism reproducibility
`docs/evaluation.md` §3.9 and §3.18 already established by hand. Once `gin-realworld.json`'s
ground truth was expanded to cover what both runs actually found (see below), each run scored
**precision 1.0, recall 1.0** against it.

**One fresh review of `rails-realworld`** — Ruby on Rails, a stack never independently
blind-passed in this project's history — as an E4 (zero-findings repository) attempt. It did
not succeed: four real findings were produced, two of them spot-verified directly against the
source before being accepted into `rails-realworld.json` (a self-referential `has_many`
correctness bug confirmed verbatim in `app/models/article.rb`; an unclamped `params[:limit]`
confirmed verbatim in the controller). Consistent with `docs/evaluation.md` §3.7 — no
repository reviewed in this project's history has yet produced a literal zero-finding result.

**Two real problems in the harness were found and fixed by this run, not hypothesized in
advance:**

- **Location matching was too brittle for a mechanism spanning a call chain.** Both
  `gin-realworld` runs found the identical N+1 bug and cited *opposite ends* of the same call
  chain — one the query-issuing method's definition, the other the call site that would
  actually need to change to fix it. Neither citation is more correct. Scoring one as
  canonical and the other as a miss would have been scoring the harness's location
  convention, not the reviews. Fixed generally: `ground-truth.schema.json` gained
  `also_locations` (a list of alternative locations a ground-truth item accepts, alongside
  its primary `location`), and `score.py`'s matcher checks all of them. Covered by
  `tests/test_scoring_harness.py`.
- **`stable_id_agreement` is not yet a reliable signal.** The two `gin-realworld` runs agreed
  on the dominant finding's location (once fixed above), severity within one level, and
  recommendation — and still had **0% `stable_id` agreement**, because `SKILL.md`/the schema
  describe `stable_id` as "derived from root cause, file, symbol, and mechanism" without
  mandating a canonical hashing algorithm. Two independently-run agents computing "a hash"
  from the same inputs are not guaranteed to produce the same bytes. `score.py stability` now
  documents this explicitly (a `caveats` field in its output, not just a docstring) rather
  than silently reporting a number that looks meaningful and isn't yet. Specifying a canonical
  algorithm is open work — see `docs/roadmap.md`.

Also real, and instructive on its own: a third finding legitimately different from what a
`forbidden` trap ruled out (SQLite's single-writer lock causing `SQLITE_BUSY` without
`busy_timeout`, distinct from the pool-*size*/exhaustion framing the trap's `why_not`
actually addresses) was incorrectly caught as a restraint failure by category+location
matching alone. Fixed by adding the correct `acceptable` item rather than by loosening the
trap — the trap's original claim is still correctly ruled out; a different, real claim at the
same file just needed its own entry. `gin-realworld.json` now carries 8 `acceptable` items,
each traced to a specific, verified claim from one of the two runs, in a deliberate order
(specific, symbol-bearing items before the broad, symbol-less missing-indexes item) so a
specific finding at a shared location is not accidentally absorbed by a broader one first.

## Adding a case

1. Pick a public repository and pin a commit. Annotations are only true at a commit.
2. Write `ground-truth/<name>.json` against
   [schemas/ground-truth.schema.json](../schemas/ground-truth.schema.json). CI validates
   every file in this directory against it.
3. State how the truth was established in `annotation.method`. `documented-issue` and
   `injected-defect` are the strongest; `blind-pass-derived` is the weakest and must be
   labelled.
4. Fill `forbidden` even when the repository has real problems. The traps are where
   precision is actually measured.
5. Run a review, keep its JSON, and score it.

## What this does not do

It does not run reviews. Each one is an agent invocation against a real repository, which is
too expensive and too non-deterministic for CI. The harness scores output that already
exists — so the loop is: run a review by hand, commit its JSON, score it by machine. Only
the last step is automated, and it is the step that was previously done by eye.
