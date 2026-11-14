# Revised `dotnet.md` check — does the scoring guidance reach reviews?

**Status: pre-registered on 2026-11-14; not yet run.** This design is committed before any run.
Results will be appended below the line at the end, without editing anything above it except
this status line.

## Why this run exists

The [second ablation](second-ablation.md) found that `technology/dotnet.md` changed neither which
issues were found nor how they were rated. Every run that reported the synchronous EF Core
transaction (`GT-001`) or `Count()` (`GT-003`) scored its blast radius `endpoint` or `service`.
The revision (#131) names those synchronous methods. It also states that a blocking call in an
ASP.NET Core request path has blast radius `system-wide`, because every request's continuations
share one `ThreadPool`, and leaves severity to the rubric.

The question: **with the revised file, do reviewers score `GT-001` and `GT-003` as
`system-wide`, without finding fewer real issues?**

This checks whether the guidance is followed. It does not test whether `system-wide` is the
right score. The answer key that rates these issues Critical and High is blind-pass-derived and
cannot settle that.

## Design

| | |
|:--|:--|
| Case | `gothinkster/aspnetcore-realworld-example-app` @ `a397d1197b22edeffa4d2563fa5f4f7f11d0b254`, as in the second ablation |
| Arm | Only the `full_routed` arm of [`second-ablation-manifest.json`](second-ablation-manifest.json), exported from the #131 branch. Since the second ablation (`7835069`), three exported files have changed: `dotnet.md` (the revision), `SKILL.md` (one sentence stating `"schema_version": "1.0"`, #130) and the `registry.yaml` notes for `dotnet` (helper only, not a reference). The prompt (`efdd77aa…`) and every other file are byte-identical. Bundle `full_routed` SHA-256, frozen now by `--plan-only`: `f294fd6c2fc02c73d294058b8cab46aeaf93cfc74b5aeb4202d83b232818af80` |
| Comparison | The second ablation's three `full_routed` runs (`r1-s3`, `r2-s1`, `r3-s2`), which used the old file. They are historical, not concurrent: the comparison is before and after, not randomized |
| Runs | Three, one from each repeat's `full_routed` slot |
| Model | `claude-sonnet-5-5` in every run, read from transcript metadata. If not all three used it, the check is void |
| Isolation | As in the second ablation: each reviewer gets only its slot and a separate read-only checkout of the target. Transcripts are checked afterwards for reads and writes outside them |

## What is measured

Findings are read from each run's `review.json`. Findings are matched to `GT-001`–`GT-004` by
mechanism and location, using the second ablation's rules.

- **System-wide scoring (primary):** for each run, whether every reported `GT-001` and `GT-003`
  finding has `impact.blast_radius` = `system-wide`. A run that reports neither counts as not
  meeting it.
- **Detection (guardrail):** expected issues found per run, and distinct real issues per run.
  The second rule is adjudicated against source and credited by the second ablation's rule.
- **Descriptive, outside the rules:**
  - severity and priority of `GT-001`/`GT-003` findings;
  - whether a finding names thread-pool starvation;
  - unsourced performance estimates;
  - validator result;
  - tokens and elapsed time.

Before, with the old file: system-wide scoring in **0 of 3** runs. Expected issues per run
were 3, 4 and 4 (mean 3.67). Distinct real issues were 8, 9 and 9 (mean 8.67).

## Decision rules, fixed now

**The guidance reaches reviews** if system-wide scoring holds in **at least 2 of 3** runs, and
the guardrail holds: mean expected issues at least **3.0**, and mean distinct real issues at
least **7.67** (one below before).

**The guidance does not reach reviews** if system-wide scoring holds in at most 1 of 3 runs.
The revision would then need rework before it is relied on.

**The guidance costs detection** if system-wide scoring holds in at least 2 of 3 runs but the
guardrail fails. That is reported with the same prominence.

## Threats to validity

- **Before and after, not concurrent.** The earlier runs were a month earlier, with the same
  model id.
- **`SKILL.md` also changed.** The added sentence is about the JSON schema version, not scoring,
  so it is not expected to move blast radius.
- **n = 3, one case, one model.** Directional.
- **Adjudication is not blind.** The adjudicator wrote the revision. Blast radius is read from
  `review.json` as written, so the primary measure needs no judgement. Matching and distinct
  issues use the second ablation's written rules.
- **The answer key does not appear in the revised file**, and no slot contains it.

---
