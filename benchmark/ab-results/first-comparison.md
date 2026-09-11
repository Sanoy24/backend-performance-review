# First A/B comparison — treatment vs. control

Run 2026-09-11 under the protocol in [`../ab-comparison.md`](../ab-comparison.md), which was
committed before any control output was read.

**The result is mixed, and the mixed half is the interesting half.** The methodology produced
a large, consistent improvement in *epistemic discipline* — what a finding discloses about how
sure it is and what would refute it — and produced **no improvement in breadth**. On breadth
the control was better: it reported roughly two and a half times as many findings, several of
them real and absent from this project's own ground truth.

---

## 1. What was run

| | Treatment | Control |
|:--|:--|:--|
| `gin-realworld` @ `626c372` | 2 runs (5 and 8 findings) | 2 runs (14 and 13 findings) |
| `rails-realworld` @ `a2ae4ff` | 1 run (4 findings) | 1 run (15 findings) |

Same model, same commits. The treatment runs were not produced for this comparison — they are
the blind passes from the first real validation run, committed to the corpus before an A/B was
designed, and so cannot have been tuned to win it.

Raw output for both arms is committed beside this file.

## 2. Primary metrics

Per §3 of the protocol, the six metrics are **not** of equal worth, and averaging them would
overstate the result. They are reported in two groups.

### 2a. Schema-enforced — the format working, not better reasoning

| Metric | Treatment | Control |
|:--|:--|:--|
| Citation rate | 1.00 (17/17) | High — both arms cite `file:line` throughout |
| Falsifiability rate | 1.00 (17/17) | **~0** — see §4 |
| Conditioned-recommendation rate | 1.00 (17/17) | Partial — conditions appear in prose, inconsistently |

A treatment review scores 1.00 on these **by construction**: `conditions` may not be empty,
`counter_evidence` is required, and a finding without a location fails validation. This is
worth reporting — a reader really does receive those fields — but it is the schema doing its
job, and it is not evidence the model reasoned better.

The control's citation rate deserves saying plainly: **it was excellent.** Every control
finding named files and line numbers, quoted the relevant code, and proposed concrete
patches. Nothing here supports a claim that an unguided strong model is vague.

### 2b. Not schema-enforced — where the real difference is

| Metric | Treatment | Control |
|:--|:--|:--|
| Unsourced performance numbers | **0 fabricated** (2 uncited library constants) | **3 fabricated improvement estimates** across 3 runs |
| Cargo-cult recommendations | 0 | 0 |
| `forbidden` trap hits | 0 | **0 — the control avoided both traps** |

## 3. The headline: invented improvement percentages

Every unit-bearing number in all six reports was extracted mechanically with the same regex
(`score.py`'s `NUMBER_WITH_UNIT`), applied to raw Markdown for both arms, then adjudicated by
hand. Most numbers in both arms are legitimate — hypotheticals ("a client posting a 500 MB
body"), shown derivations ("1 count + 1 page + 1 preload + 3×20 = 63 queries"), or values read
from the repository.

Three control claims are not:

| Run | Claim | Why it fails |
|:--|:--|:--|
| gin control 1 | *"`PrepareStmt`… typically buys **10–20%** on query-heavy endpoints"* | No source, no derivation, no measurement. A benchmark delta presented as common knowledge |
| gin control 2 | *"statement caching is a straightforward **10-30%** cut in per-query overhead"* | Same claim, same absence of support, **independently invented by a different run** |
| gin control 1 | *"`bcrypt.DefaultCost` (cost 10, **~60–100 ms** of CPU)"* | A plausible figure for typical hardware, but attached to no hardware, no citation, and no measurement |

The pattern is worth more than the count. **Two independent control runs, reviewing the same
repository, each attached a different invented percentage to the same recommendation.** Nothing
in the code supports either number; neither run flagged it as an estimate. That is precisely
the failure Hard Rule 1 exists to prevent, and precisely the number a reader would use to
decide whether the work is worth doing.

The treatment's two lapses are a weaker form of the same sin and are recorded here rather than
excused: run B asserts GORM's default slow-query threshold (`200ms`) and bcrypt's 72-byte input
limit without citing either. Both are documented library constants rather than invented
performance deltas — a real distinction, but not a clean sheet. Run B also closes with an
explicit self-audit of its own numbers, listing each one and labelling it a derivation.

## 4. Falsifiers: near-total absence in the control

Across roughly 1,700 lines of control output, a generous search for falsifier language — *"may
not matter", "unless", "would disprove", "assumption", "not measured", "unverified"* — returns
**one** hit:

> *"this review is static analysis, and static analysis guesses at ratios"* — gin control 1,
> in its observability section

That is a good sentence, and it is a **global** caveat appearing once, not a per-finding
disclosure. The second gin control run contains **zero**. No control finding anywhere states
what evidence would refute it or under what conditions it would not matter.

Format-induced or not, the difference in what a reader receives is total.

## 5. The structural difference: no evidence grading at all

The control reports rank findings by **severity** (and in one case effort). Neither arm's
control output carries any notion of **confidence**, evidence grade, or how the conclusion was
reached.

A reader of a control report cannot distinguish:

- a fact read directly off the code (*"`common.Bind` has no `MaxBytesReader`"* — checkable, certain), from
- an inference about production behaviour (*"WAL alone typically gives the largest single throughput win available in this codebase"* — an unmeasured prediction)

Both are typeset identically, under a bold **Critical**. This is not a formatting quibble: the
two-axis split (severity = how bad, confidence = how sure) is the single most load-bearing
idea in the methodology, and its absence is what lets an unmeasured prediction inherit the
authority of a code fact.

## 6. Where the control was better

Recorded at equal prominence, per the protocol's reporting rule.

**Breadth.** Control reported 14/13/15 findings against the treatment's 5/8/4. The corpus
already covers eight of the control's gin findings as `acceptable` (`GT-A01`–`GT-A08`), so
much of that breadth is real, not padding.

**Material the ground truth does not contain at all.** Two control-only claims were checked
against source for this write-up and both are real:

1. **Soft-deleted join rows accumulate forever.** `FavoriteModel` embeds `gorm.Model`;
   `unFavoriteBy` (`articles/models.go:137-141`) issues a soft `Delete`, while `favoriteBy`
   (`:128-136`) uses `FirstOrCreate`, whose default scope cannot see the tombstone — so it
   inserts a new row. Toggling a favorite *n* times leaves *n* dead rows in a table on the hot
   path of the dominant N+1.
2. **No request body cap.** `common.Bind` (`common/utils.go:96-99`) calls `ShouldBindWith`
   with no `http.MaxBytesReader`, so `binding:"max=2048"` is enforced only *after* the whole
   body is read and decoded.

Both are now in the corpus as `acceptable`, marked as originating from the control arm. **The
corpus got better because of the arm that was supposed to be the baseline**, which is evidence
against its previous completeness and is recorded as such.

**Restraint.** Neither control run hit either `forbidden` trap. Control 1 explicitly noticed
the trap in `GT-F01` — that the favorites N+1 is *already batched* — and said so in its opening
paragraph, crediting `BatchGetFavoriteCounts`/`BatchGetFavoriteStatus` before pointing at the
follows N+1 that genuinely is not batched. That is exactly the discrimination `GT-F01` was
written to test, and the control passed it unaided.

A mechanical restraint score would have been wrong here: both control runs discuss connection
pooling in `common/database.go` under `concurrency`, which matches `GT-F02` on file and
category, but each makes the *opposite* claim to the forbidden one — too many connections
contending for one file lock, not pool exhaustion. This is the known
location-plus-category matching limitation, and it was adjudicated by hand, not by the scorer.

## 7. What this establishes, and what it does not

**Supported:** following the methodology changed what the model disclosed — falsifiers,
conditions, evidence grades, and an absence of invented benchmark deltas — consistently across
three runs and two stacks.

**Not supported:** that it makes the model find *more*, or find *better*. On this evidence it
finds less. Whether the treatment's narrower output reflects useful restraint or lost coverage
is not answerable from three runs, and the eight `acceptable` entries the treatment arm did
surface across its own runs suggest the gap is narrower than the raw counts imply.

**Not tested at all:** model-independence (one model), cost and latency, and whether a reader
acting on either report gets a faster system. That last one is the question that actually
matters, and nothing here touches it.

**n = 3.** Directional evidence. Not a general result.

## 8. What should change as a result

1. **The breadth gap is the finding worth acting on.** The treatment's output budget caps full
   detail at 10–15 findings, but these runs reported 4–8. If the methodology is suppressing
   real findings — body caps, soft-delete growth, orphan rows — that is a defect in it, not
   evidence of restraint. Worth investigating before the next release.
2. **`Confirmed` is not the only place evidence grading can slip.** Both treatment lapses were
   uncited library constants asserted as fact. Hard Rule 1 names repository files, user-supplied
   measurements, and labelled derivations — a documented third-party default is none of those,
   and the rule should say what to do with one.
3. This comparison needs more cases before any claim from it belongs in the README.
