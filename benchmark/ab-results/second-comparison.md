# Second A/B comparison — one pinned model

**Status: run and adjudicated, 2026-10-07.** The design below was committed before any run
started (PR #100); the results were appended afterwards without editing it, apart from this
status line.

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

**The breadth cost is real.** With the model held fixed, the guided arm found 69% as many
distinct real performance issues as the plain arm, below the 75% line fixed in advance. The
discipline result also replicates: the plain arm invented performance figures and stated no
falsifiers, and the guided arm did neither.

### The model was held fixed

Every one of the six runs used `claude-opus-5-5` on every API call, read from each run's own
transcript metadata. The same check settles the first comparison's open question: its
treatment ran on `claude-sonnet-5` and its control on `claude-opus-5`. The confound reported
there is now **confirmed**, not suspected.

### Breadth: distinct real issues per run

Raw finding counts are not comparable: the methodology requires merging findings that share a
root cause, while the plain reports bundle several items under one heading. Each report was
therefore mapped onto a common list of distinct issues for its repository, every issue was
checked against the source, and a run was credited only for issues it reported as findings.
Issues seen but placed in `considered_not_reported`, mentioned only as context, or filed as a
correctness finding were not credited. The same rules applied to both arms.

| Run | Plain | Guided |
|:--|--:|--:|
| `gin-realworld` #1 | 11 | 6 |
| `gin-realworld` #2 | 12 | 7 |
| `rails-realworld` | 9 | 9 |
| **Mean** | **10.7** | **7.3** (69%) |

Two observations matter more than the mean:

1. **The whole gap is in gin.** On Rails the arms tied 9–9, each catching one issue the other
   missed: the guided run found Puma's default 16 threads against a 5-connection database pool
   (verified: Puma 3.4.0, no config file, `pool: 5`); the plain run found one-row-at-a-time
   cascading deletes.
2. **The guided arm caught the high-severity issues.** Every issue the plain reports rated
   Critical or High in gin was found by the guided arm too — the per-item N+1, missing
   indexes, unbounded page sizes, and writes on read paths — with one exception: guided run #1
   did not report SQLite's journal, timeout and pool configuration as a finding of its own.

What the guided arm missed in gin, all verified against the source:

| Issue | Plain runs | Guided runs | Why the guided arm missed it |
|:--|:-:|:-:|:--|
| Favorite/follow soft-deletes accumulate without bound | 2/2 | 0/2 | Mentioned once as context for the index finding, never reported |
| Filtered lists fetch in several steps and page before sorting | 2/2 | 0/2 | Run #1 filed it as a correctness finding only |
| Saving a comment re-saves its loaded associations | 2/2 | 0/2 | Never surfaced |
| Feed loads full user rows to build `IN` lists | 2/2 | 0/2 | **Seen and dropped** in both runs' `considered_not_reported` |
| Count recomputed on every list request | 1/2 | 0/2 | **Seen and dropped** by run #1 |

One plain-arm claim was **rejected** in adjudication: both plain gin runs reported Gin's debug
mode as a cost. It is not a per-request one — debug mode adds route-registration logging at
startup, and the per-request access log runs in either mode. Guided run #1 examined exactly
this and correctly left it out ("Debug mode only adds route-registration logging"). It was
first counted for the plain arm and removed on checking, which moved the result from 65% to
69%; the verdict is unchanged.

That splits the gap into **two distinct failure modes**, which call for different fixes:

- **Over-filtering:** real issues are found, then judged not worth reporting. A selection or
  materiality problem.
- **Missed discovery:** real issues are never surfaced at all. A coverage problem: write paths
  and table growth over time were not examined.

### Discipline: the first comparison's result replicates

| | Plain | Guided |
|:--|:-:|:-:|
| Unsourced performance estimates | **3** across 3 runs | **0** |
| Hedge or falsifier language in the reports | **0** lines of 826 | Every finding (schema-required) |
| Schema-valid machine output | — | 3 of 3 |

The three unsourced estimates: bcrypt "roughly 50–100 ms per call" and "about 50–100 ms of CPU"
(one in each gin plain run), and SQLite statements at "~20–100 µs" each. Every unit-bearing
number in all six reports was extracted with `score.py`'s own pattern and adjudicated by hand.
The guided arm's numbers were a test script's 500 ms delay and a 5000 ms busy timeout, both read
from the repositories. As in the first comparison, falsifiability is enforced by the guided
arm's schema, so that row shows the format working, not better reasoning; the unsourced-estimate
row is the behavioural one.

### What this decides

- **Phase 2 (breadth recovery) goes ahead.** Its premise is no longer an artefact of a model
  mismatch.
- **It now has a diagnosis.** Over-filtering and missed discovery are separate problems. The
  first is a change to how candidates are judged material; the second is a change to what the
  coverage sweep examines — write paths and table growth over time.
- **The cost of fixing it must not be the discipline.** Phase 2's own exit criterion already
  says so: unsourced-number and trap performance must not regress.

**n = 3 per arm, two repositories, one model.** Directional, not a general result. Raw output
for every run is in [`raw-second/`](raw-second/).
