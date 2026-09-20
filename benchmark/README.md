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
├── dataset.json       case split and annotation-version registry
├── dataset.py         registry validation and held-out eligibility gate
├── ground-truth/     annotations, one file per repository/case
├── scoring/score.py  precision, recall, calibration, restraint, stability
├── ab-comparison.md  protocol: does the methodology beat asking the model plainly?
└── ab-results/       results of that comparison, once adjudicated
```

[`ab-comparison.md`](ab-comparison.md) is the one that answers the question the rest of this
directory exists to serve. Precision and recall say how good a review is; only a control arm
says whether the *methodology* is what made it good.

`ground-truth/` currently contains ten treatment-derived development cases, not a locked
evaluation set. Annotations are the expensive part; a missing held-out set is more honest
than relabeling an existing review as independent truth. See "What to annotate first" below.

## Dataset split and annotation versions

Run `python benchmark/dataset.py` to validate the versioned registry. It currently reports
ten `development` cases, zero `held_out` cases, and `held_out_ready: false`. The dataset
version is independent of the scorer and skill versions. Each case's annotation history
records its content hash and origin, normalizing CRLF to LF across platforms. A changed
truth file fails validation until a new annotation version records the new hash, reason,
and affected run IDs. Repository name,
source URL, and commit live in the truth file. License metadata is explicitly unverified
for the historical cases; several also lack a full commit SHA. They remain useful
development fixtures, not held-out accuracy evidence.

`score.py score --truth ...` is explicitly labeled exploratory in text and JSON output.
It identifies registered development truth by path or current annotation digest, and
rejects registered held-out truth (including an unchanged copy). Use `--dataset` when
scoring against a non-default registry. This is an accidental-misuse safeguard, not a
security boundary or proof that an unregistered truth file is independent. The guarded
`score.py evaluate --case <id> --review <review.json> --json` command accepts only a
pre-registered held-out case with independent annotation origin, a full commit, documented
license metadata, and a schema-valid review of that same commit. A human must verify the
recorded license evidence before using a case publicly. The command rejects review files
under the historical `benchmark/ab-results/` treatment directory. No committed case is
eligible yet. Reviewer isolation and provenance still require an independent run protocol;
the command alone does not make a review blind.

Held-out registrations must also record a timezone-aware `pre_registered_at` and a
`pre_registration_evidence_url`. Every held-out annotation version needs a `recorded_at`;
version times must increase, and the baseline must exist before pre-registration. The
evaluator requires the review's `generated_at` to follow both the registration and the
current annotation version. This prevents an older review from being silently scored
against later truth. The evidence URL and timestamps are audit hooks, not proof of a
blind run; an independent reviewer must check the cited registration record.

Scoring also emits `case_outcome`: whether a review abstained, whether that agrees with the
annotation's required findings, how many known false-positive traps it avoided, and whether
a change-scoped `UNKNOWN` verdict was declared or derived. An empty `expected` list means
only that the annotation requires no finding; it does not prove the repository is healthy
or that the review examined enough code. `UNKNOWN` correctness and free-text candidate
rejection correctness remain `null` until independently adjudicated. Do not turn those
nulls into successes in aggregate results.

### Post-run candidate adjudication

After a review is frozen, an independent engineer may map each
`considered_not_reported` entry to ground truth with
`--rejection-adjudication <adjudication.json>` on either `score` or `evaluate`.
Run the command once without that option and copy `adjudication_fingerprints` from its
JSON output. The adjudication has this shape (the IDs here belong to the example fixture):

```json
{
  "schema_version": 1,
  "truth_content_sha256": "<from adjudication_fingerprints>",
  "review_content_sha256": "<from adjudication_fingerprints>",
  "adjudicator": "independent reviewer name or stable identifier",
  "adjudicated_at": "2026-09-20T12:00:00Z",
  "decisions": [
    {"candidate_index": 0, "judgment": "acceptable_nonreport", "ground_truth_id": "GT-A01", "reason": "Real but not required."},
    {"candidate_index": 1, "judgment": "correct_rejection", "ground_truth_id": "GT-F01", "reason": "No query filters on status."}
  ]
}
```

Candidate indices are zero-based positions in the frozen review. Every candidate needs
exactly one decision. `correct_rejection` must name an unreported `forbidden` item;
`acceptable_nonreport` must name an unreported `acceptable` item;
`incorrect_rejection` must name a missed `expected` item. `unresolved` needs a reason
but no ground-truth ID. A ground-truth item cannot be credited twice. The scorer
checks both content digests and all mappings. The reported `correct_rate` is correct
trap rejections divided by correct plus incorrect rejections; acceptable omissions and
unresolved candidates are excluded. The timestamp must follow the review's `generated_at`
when that field is present. A named adjudicator and these mechanical checks record
provenance, but cannot by themselves prove the adjudicator was independent.

## Running it

```
python benchmark/scoring/score.py score --truth ground-truth/<repo>.json --review <review>.json
python benchmark/scoring/score.py score --truth ... --review ... --json
python benchmark/scoring/score.py stability --review run-a.json --review run-b.json
```

The review file is the machine-readable output described in
[schemas/review.schema.json](../schemas/review.schema.json), which a review emits alongside
its Markdown report (report template §10).

### Reference ablations

`reference_ablation.py` prepares and compares three paired reference bundles: shared
methodology plus category references, then matching technology references, then the rest of
the routed context. It does **not** run a model or claim that a larger bundle is better.

```
python benchmark/reference_ablation.py --manifest <local-manifest.json> --plan-only
python benchmark/reference_ablation.py --manifest <local-manifest.json> --export-dir <new-run-directory>
python benchmark/reference_ablation.py --manifest <local-manifest.json>
```

The first command freezes the prompt and each bundle by SHA-256 before reviews are run.
It requires a full 40- or 64-character Git commit SHA in each ground-truth case; historical
placeholder or abbreviated commits are not eligible until re-annotated at a verified commit.
Before exporting, give each case one or more trials with an `id` and an `order` containing
`category_only`, `category_technology`, and `full_routed` exactly once; rotate that order
across repeats. Export writes a new directory of `case/trial/slot-N` packages. Hand a fresh
reviewer **only their slot directory** and a separate checkout of the target repository at
the pinned commit. Each slot contains the common prompt, its allowed reference bundle, the
review template and schemas, and the stable-ID helper. Include `SKILL.md`, shared
methodology, and rubrics in the manifest's `common` tier; assign only relevant category,
technology, and remaining routed files to the other tiers. Keep `coordinator.json` private: it maps opaque
slots back to arms. No package contains the ground truth or the other arms' references.
The exporter refuses to overwrite an existing directory. It does not clone the target,
start review agents, or collect model usage.

Supply three schema-valid reviews per trial, from the same model, prompt, pinned repository,
and scope, with actual prompt/context-token usage, reference tokens counted by a named
tokenizer, elapsed time, and cost provenance. Record a named human audit of unsupported
claims. Then run the third command to report gained, lost, and changed expected findings
alongside resource costs. Its reference-quality score is the change in expected matches
minus false positives and manually adjudicated unsupported claims; `acceptable` findings
are neutral.
It withholds that score while an unmatched finding or ambiguous match needs adjudication.

This is a tier-level comparison, not proof about any one file. Test suspected low-value
references with a targeted leave-one-out comparison before shortening or removing them.
No empirical three-arm ablation has been recorded yet; the existing treatment/control
reports cannot be relabelled as these arms.

### Matching is order-independent

The scorer treats compatible ground-truth items and findings as a bipartite graph. For each
bucket, it chooses a maximum-cardinality assignment first, then maximizes specificity in this
order: primary location, an exact symbol on both sides, an exact normalized file path, and the
primary category. `expected` has priority over `acceptable`, which has priority over
`forbidden`; that bucket order is part of benchmark semantics, while list order inside any
bucket is not.

The JSON result's `matching` object records the selected pairs, explains every unmatched item,
and exposes equal-cardinality/equal-specificity alternatives in `ambiguities`. The text report
prints those alternatives as `AMBIGUOUS MATCHING` so an annotator can adjudicate them rather
than letting traversal order decide silently.

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
- **The original `stable_id_agreement` result is obsolete and not reproducible.** The two
  `gin-realworld` runs reported 0% agreement because agents invented IDs before the project
  shipped its canonical file + symbol + category algorithm. The scorer now uses canonical
  `stable_id` as its primary multiset identity, preserves repeated IDs instead of collapsing
  them, and reports file/category overlap only as an explicitly approximate diagnostic. The
  historical 0% figure cannot be recomputed honestly: run A's machine-readable JSON was not
  retained, a loss already documented in `docs/evaluation.md`; reconstructing its exact
  locations and symbols from prose would fabricate an input. No replacement live-data number
  is claimed until two canonical-ID review artifacts exist.

Also real, and instructive on its own: a third finding legitimately different from what a
`forbidden` trap ruled out (SQLite's single-writer lock causing `SQLITE_BUSY` without
`busy_timeout`, distinct from the pool-*size*/exhaustion framing the trap's `why_not`
actually addresses) was incorrectly caught as a restraint failure by category+location
matching alone. Fixed by adding the correct `acceptable` item rather than by loosening the
trap — the trap's original claim is still correctly ruled out; a different, real claim at the
same file just needed its own entry. `gin-realworld.json` now carries 8 `acceptable` items,
each traced to a specific, verified claim from one of the two runs. The maximum-cardinality,
maximum-specificity assignment now makes their JSON order irrelevant; specific,
symbol-bearing items win over broad, symbol-less alternatives explicitly.

The two committed treatment reports that contain machine-readable JSON were recomputed after
this change. Their primary counts did not change: `gin-treatment-2` remains 1 true positive,
7 tolerated, 0 false positives, and 0 false negatives; `rails-treatment-1` remains 4 true
positives, 0 tolerated, 0 false positives, and 0 false negatives.

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
