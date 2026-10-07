# Second A/B comparison — one pinned model

**Status: pre-registered, not yet run.** This design was committed before any run in this
comparison started. Results will be appended below the line at the end, without editing
anything above it.

## Why this run exists

The [first comparison](first-comparison.md) reported that unguided agents found about 2.5×
more issues than guided ones. A later check showed the two arms were probably **not on the
same model**: the treatment output records `claude-sonnet-5`, while the control was dispatched
later without an explicit model and recorded none. A stronger control model could explain the
whole breadth gap on its own. Phase 2 of the product plan ("breadth recovery") rests on that
gap, so it has to be measured properly before anything is built on it.

This run answers one question: **with the model held fixed, does the methodology cost
breadth?**

## Design

| | |
|:--|:--|
| Cases | `gin-realworld` @ `626c372` — 2 treatment, 2 control. `rails-realworld` @ `a2ae4ff` — 1 treatment, 1 control. The same cases and commits as the first comparison |
| Model | Every arm is dispatched with the model **pinned explicitly**. The model actually used is recorded from each run's own transcript metadata, not from the agent's output, which a prose-only control will not contain. If the six arms did not all run on one model, the comparison is void and is reported as void |
| Treatment | The skill as released at tag `v2.0.0`, given as an isolated copy of `skills/backend-performance-review` plus `schemas/`. This is the current methodology, newer than the one the first comparison's treatment used, so this run tests the methodology as it ships today; it is not a replay |
| Control | The exact plain prompt in [`../ab-comparison.md`](../ab-comparison.md) §1 |
| Isolation | Neither arm may read this repository. The treatment's copy contains no ground truth, results, or evaluation documents |
| Order | Sequential, one run at a time, alternating arms: gin C1, gin T1, rails C, rails T, gin C2, gin T2. Sequential to avoid the rate-limit failures that voided an earlier run; alternating so neither arm consistently runs first |

## What is measured

The protocol's metrics are unchanged — primary discipline metrics (§3), with
unsourced-number and cargo-cult rates carrying the weight, and secondary ground-truth
metrics (§4).

Breadth is measured two ways, for both arms, under the same rules:

1. **Raw finding count** per run.
2. **Adjudicated real findings** per run. Each finding is checked against the source, using
   the protocol's adjudication procedure (§5). A finding counts if the code shows it to be a
   real performance issue, whether or not the answer key already contains it.

## Decision rule, fixed now

The methodology is judged **not materially worse on breadth** if the treatment's mean number of
adjudicated real findings is **at least 75%** of the control's. Below 75%, the breadth cost is
judged real, and Phase 2's breadth-recovery work goes ahead.

With three runs per arm this is directional, not a general result. Whatever the outcome, it is
reported here with the same prominence.

---

## Results

*Not yet run.*
