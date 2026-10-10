# First reference ablation — do the technology references earn their tokens?

**Status: pre-registered, amended once before any result was scored (see Amendment), not
yet run.** This design was committed before any run started. Results will be appended below the
line at the end, without editing anything above it except this status line.

## Why this run exists

Every review loads the shared methodology plus category references, and then, for each
detected technology, a technology file (`technology/python.md`, `technology/postgres.md`, …).
Those files are the most expensive part of the skill to write and maintain, and they cost every
review context tokens. Nothing has yet measured whether they change what a review finds.

`benchmark/reference_ablation.py` was built for this question and has never been run against a
model. This is its first empirical use, on one case, to answer it directionally:

1. **Does adding the technology tier change the real issues a review finds?**
2. **Does adding the remaining routed references (the full tier) change them further?**
3. **At what context cost?**

## Design

| | |
|:--|:--|
| Case | `fastapi/full-stack-fastapi-template` @ `8063fe54f17d19f01720e055103af9cad3d8f55d` (Python, FastAPI, Postgres) |
| Arms | `category_only` (shared methodology + category references, 20 files, about 48.5k tokens); `category_technology` (+ `python.md`, `postgres.md`, `rest.md`; 23 files, about 58.8k); `full_routed` (+ `infrastructure/resources.md`, `docker.md`; 25 files, about 63.4k). Token counts are characters ÷ 4, an approximation |
| Bundles | Frozen by SHA-256 before any run, as printed by `--plan-only`:<br>`category_only` `41bb4fd5c29afa7a68bb2538358ddd286b843812dc337683d0c7e0a85f4b03e5`<br>`category_technology` `cbb9b0e88c2c46da6ead16516d1ab9e8fe7655b25428c04681755216f5eb9708`<br>`full_routed` `07289a82e30ff4fdf934d2516fab2e19e60649b89b2b9c9465592750ebc4b172`<br>prompt `efdd77aacd05c600a113220c8ac1ac32b892068b56a7df07f63ebfa13a2d5f5f` |
| Manifest | [`first-ablation-manifest.json`](first-ablation-manifest.json), with the prompt in [`first-ablation-prompt.txt`](first-ablation-prompt.txt). Tiers were curated for an earlier pilot that was never run, before any output existed |
| Trials | Three repeats, execution order rotated: (C, CT, F), (F, C, CT), (CT, F, C) |
| Model | `claude-sonnet-5-5` (amended from `claude-opus-5-5`; see Amendment). Every arm pinned explicitly; the model actually used is read from each run's transcript metadata. If the nine runs did not all use one model, the ablation is void |
| Skill | `main` at `4bcd905`, exported by `reference_ablation.py --export-dir`. Each opaque slot holds only its arm's references plus the shared helpers, including the bundled validator (#120) |
| Isolation | Each reviewer receives only its slot directory and a separate read-only checkout of the target. No slot contains ground truth, another arm's references, or the mapping from slots to arms |
| Order | Sequential, in slot order within each repeat |

The FastAPI template was also used in the [fourth A/B comparison](../ab-results/fourth-comparison.md).
That does not bias the arms against each other: all three run the same skill on the same code,
and differ only in which references they can read.

## What is measured

- **Breadth (primary):** distinct real performance issues per run, adjudicated against source
  with the rules used in the A/B comparisons. Credit is given only when an issue is reported as
  a finding. A claim checked and found not to be a real performance issue is rejected for every
  arm. Instrumentation-only findings are not counted. The adjudicator knows which arm wrote which
  report; it is not blind. To limit that, a separate model lists each report's findings from the
  reports alone, without the slot-to-arm mapping. Every rejection is then applied to all arms
  alike, with the source fact that settled it written down.
- **Technology-specific issues:** issues whose mechanism depends on Python, Postgres, or REST
  semantics specifically (for example a Postgres planner or locking behaviour, or a Python
  runtime behaviour), labelled from the issue list, with the reason recorded.
- **Discipline:** unsourced performance estimates (every unit-bearing number extracted with
  `score.py`'s pattern and adjudicated), and hits on the case's two `forbidden` traps.
- **Cost:** total tokens per run, as reported by the agent runtime, and elapsed time.
- **Publishable output:** whether each `review.json` passes the bundled validator.

### What this run cannot produce

The harness's official reference-quality score requires two things this run does not have:
- **A named human audit of unsupported claims.** The audit here is done by the maintainer's
  agent, not a person.
- **Billing-sourced cost.** Only the runtime's token totals are available.

It also withholds the score while any finding is absent from the answer key, which will be true
of every run here. The official score is therefore reported as **withheld**, not estimated. The
nine reviews are kept so a human auditor can complete it.

## Decision rules, fixed now

For each added tier, compared with the tier below it:

- **The tier earns its cost** if either:
  - its mean count of distinct real issues is higher by **at least 1.0**; or
  - it finds at least one technology-specific real issue in **at least 2 of 3** runs that the
    lower tier finds in **at most 1 of 3**.

  In both cases it must make no more unsourced estimates or trap hits than the lower tier.
- **Otherwise:** no measurable benefit on this case at n = 3. That is **not** a decision to
  remove any file. The harness's own guidance requires a targeted leave-one-out comparison before
  shortening or removing a reference.

n = 3 per arm, one case, one model. Directional, not a general result. Reported with the same
prominence either way.

## Amendment, before any result was scored

The design first pinned every arm to `claude-opus-5-5`. Two runs (repeat 1, slots 1 and 2)
completed on that model, and a third was stopped part-way. To keep the cost of the experiment
down, the model was changed to `claude-sonnet-5-5` for all nine runs. The two completed runs
are set aside and not scored. The coordinator saw each run's short completion summary (its
finding count and titles) but did not adjudicate either report, and they are not mixed with
the Sonnet runs, so the one-model rule above still holds. The slots were re-exported with the
new model named in each `TASK.md`. The bundle and prompt hashes above are unchanged, because
the references and prompt are unchanged. Nothing else in the design changed.

---

## Results

*Not yet run.*
