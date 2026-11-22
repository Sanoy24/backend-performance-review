# Backend fixes without a symptom — does the method find the fix in a full review?

**Status: pre-registered on 2026-11-22; not yet run.** This design is committed before any
review exists. Results will be appended below the line at the end, without editing anything
above it except this status line.

## Why this run exists

In the [backend performance-fix test](../backend-fix-transfer/design.md), both arms got a
one-line symptom. With it, the skill and a plain request localized the maintainers' fixes about
equally often: 7 against 6 hunk hits out of 10. That test's own write-up names the gap: the
symptom pointed both arms at the slow operation, and a review with no symptom, the skill's
normal setting, was not tested.

A full review is where the skill's structure could matter most. It is meant to find critical
paths and rank them by workload and growth, not by what the user already suspects. A plain
request has no such procedure.

The question: **with no symptom, does a full review following the skill point at the code a
maintainer later sped up more often than a plain full review by the same model?**

## Design

| | |
|:--|:--|
| Cases | The ten cases of the backend performance-fix test, unchanged: same repositories, base commits and patches. [`manifest.json`](manifest.json) records each patch's SHA-256 from that test's manifest |
| Arms | **Treatment:** the skill, exported from `main` when this design merges, with [`treatment-prompt.txt`](treatment-prompt.txt), a full-repository review request. **Control:** the same model, no skill files, with [`control-prompt.txt`](control-prompt.txt). Neither prompt names a symptom, an operation, or any hint |
| Inputs per run | A fresh read-only checkout of the case's base commit. No patch, PR text, title or symptom |
| Runs | One per case per arm: 20 runs. Arm order alternates by case, as in the earlier test |
| Model | `claude-sonnet-5-5` in every run, read from transcript metadata. If the 20 runs did not all use one model, the test is void |
| Isolation | As in the earlier test. Each reviewer gets only its slot and checkout. Neither arm may read `benchmark/`; the control may not read any skill file; no run may use the network. Transcripts are checked afterwards |

## What is measured

The measures are scored as in the earlier test, by `backend_fix_transfer.py score --window 20`
against each case's patch. The primary measure is the **hunk hit**. **File hit** and
**top-finding file hit** are secondary. Control findings are transcribed by a separate blind
agent under the same literal rule. A sensitivity reading resolves abbreviated paths, reported
outside the rules.

Descriptive, outside the decision rules:

- whether the hit finding names the maintainer's mechanism;
- the rank of the first hit finding;
- findings per run;
- tokens and time;
- treatment validator results;
- per case and arm, the change from the symptom test.

## Threats to validity, stated now

- **The key is narrow.** A full review that finds a different real problem scores a miss. Low
  hit rates in both arms are expected and are not by themselves evidence of poor reviews.
- **The tibiadata case may not be a bottleneck** (see the earlier test's follow-up). It stays in,
  unchanged, because the case list is fixed.
- **Same model, same cases.** The earlier test's reviews were in other agents' contexts. No
  agent here sees them.
- **Large repositories.** Some checkouts have about a thousand source files, so a full review must
  choose where to look. That choice is exactly what is measured.
- **n = 10, one run each, one model.** Directional.

## Decision rules, fixed now

**The method helps localization in a full review** if treatment hunk hits are at least **2 more**
than control hunk hits, out of 10.

**The plain request does better** if control hunk hits are at least 2 more than treatment hunk
hits. That is reported with the same prominence.

**Otherwise:** no measurable difference at n = 10.

Separately, as an absolute reading of the treatment arm alone, the bar is lower than with a
symptom, because nothing points the review at the fix. A **file hit in at least 4 of 10** cases
means a full review following the method finds maintainers' fixes. **1 or fewer** means it does
not. Anything in between is inconclusive.

---
