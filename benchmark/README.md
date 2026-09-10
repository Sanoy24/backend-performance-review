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
   property it is built around.

2. **The eight repositories already blind-passed.** Those runs are proto-ground-truth that
   has already been paid for: they record locations, mechanisms, severities, and in several
   cases bugs later confirmed and fixed. Retro-annotating them costs a fraction of
   commissioning new cases. Mark these `"method": "blind-pass-derived"` — truth derived from
   a run rather than established independently is weaker evidence, and the annotation has to
   say so.

3. **Repositories with misleading signals** — an obvious smell that is harmless, a real
   problem somewhere unexpected, a technology named in a manifest but never used.

4. **Repositories with interacting bottlenecks**, where each issue is moderate alone and
   severe combined.

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
