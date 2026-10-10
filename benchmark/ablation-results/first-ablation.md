# First reference ablation — do the technology references earn their tokens?

**Status: run on 2026-10-10; results below. Pre-registered, and amended once before any result
was scored (see Amendment).** This design was committed before any run started. Results will be appended below the
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

**Answer: on this case, neither added tier changed what the review found.** Every run, in every
arm, reported the same four real issues. Neither tier meets either "earns its cost" rule. That
is *no measurable benefit on this case at n = 3*, not a decision to remove any file (see the
decision rules above).

### Runs

All nine runs used `claude-sonnet-5-5` on every model call, read from each run's transcript
metadata. Every `review.json` passed the bundled validator. The reports are in
[`raw-first/`](raw-first/). Slots were opaque to the reviewers; the mapping, from the export's
coordinator file, is:

| Repeat | Slot 1 | Slot 2 | Slot 3 |
|:--|:--|:--|:--|
| 1 | `category_only` | `category_technology` | `full_routed` |
| 2 | `full_routed` | `category_only` | `category_technology` |
| 3 | `category_technology` | `full_routed` | `category_only` |

### Summary by arm

| | `category_only` | `category_technology` | `full_routed` |
|:--|:--|:--|:--|
| Distinct real issues per run | 4, 4, 4 (mean 4.0) | 4, 4, 4 (mean 4.0) | 4, 4, 4 (mean 4.0) |
| Findings reported per run | 3, 4, 4 | 4, 3, 4 | 4, 4, 4 |
| Technology-specific real issues | both, in 3 of 3 runs | both, in 3 of 3 runs | both, in 3 of 3 runs |
| Unsourced performance estimates | 0 | 0 | 0 |
| Trap hits (`GT-F01`, `GT-F02`) | 0 | 0 | 0 |
| Validator passed | 3 of 3 | 3 of 3 | 3 of 3 |
| Total tokens per run | 113,875; 110,896; 105,409 (mean 110,060) | 114,355; 107,813; 111,572 (mean 111,247) | 115,863; 104,627; 95,025 (mean 105,172) |
| Elapsed per run | 3.5, 3.9, 3.9 min (mean 3.8) | 3.4, 3.2, 3.5 min (mean 3.4) | 4.4, 4.0, 3.2 min (mean 3.9) |

Runs are listed in repeat order. Tokens are the agent runtime's total per run. Cost from billing
was not available, so the official reference-quality score stays **withheld**, as pre-registered.

### The four issues

Each was checked against the source at `8063fe5`. All nine runs reported all four.

1. **No index on `item.owner_id`.** `backend/app/models.py:97` declares the foreign key with no
   index, and no migration creates one. `GET /items/` filters, counts and sorts on it for every
   non-superuser, and user deletion deletes items by it. *Technology-specific:* PostgreSQL does
   not create an index for a foreign-key column, unlike MySQL's InnoDB.
2. **No upper bound on `limit`.** `backend/app/api/routes/items.py:14` takes `limit: int = 100`
   with no `le=`, and `GET /users/` has the same shape. This is the answer key's `GT-001`; every
   run matched it. Several runs also named OFFSET pagination and the count query per page.
   Those were credited as part of this issue, not separately.
3. **A pooled connection is held across Argon2 verification.** `get_db` yields one session per
   request. `crud.authenticate` queries the user, which checks out a connection and opens a
   transaction, and then runs Argon2 verification (a dummy one for unknown emails) before the
   session closes. *Technology-specific:* it depends on SQLAlchemy's session holding its
   connection until the session closes, and on FastAPI running `def` handlers on a shared thread
   pool.
4. **SMTP is sent inline while the session is open.** `recover_password` and `create_user` call
   `send_email`, which sends synchronously, after a database query and before the session closes.

Two runs, `category_only` repeat 1 and `category_technology` repeat 2, combined issues 3 and 4 in
one finding. That finding names both mechanisms, so both were credited, and the rule was applied
alike to every arm. Counting such a finding once instead would give means of 3.67, 3.67 and 4.0,
a largest difference of 0.33, still under the 1.0 threshold.

### What was checked and not rejected

- **"No SMTP timeout."** Every run said the app sets no timeout in `smtp_options`. That is true
  of the repository. The pinned library, `emails` 1.1.2, applies a 5-second socket timeout by
  default (`DEFAULT_SOCKET_TIMEOUT = 5` in `emails/backend/smtp/backend.py`), so the wait is
  bounded. Two runs, one `category_only` and one `full_routed`, said the library default was
  unverified. The statement was not rejected for any arm, because it describes the repository
  accurately, and issue 4 does not depend on it.
- **Numbers.** `score.py`'s pattern found unit-bearing numbers in five runs. Every one is from the
  code or a shown derivation: `65536 KiB = 64 MiB` from the Argon2 parameters in `DUMMY_HASH`,
  and "2x" for the two statements (count and page) per request. None is unsourced.
- **Traps.** No run attributed the backend to Node.js, Gin or Echo. The only matches for "node"
  were query-plan node types.

### One secondary difference, outside the decision rules

The full tier stated the PostgreSQL-specific reason for issue 1 (foreign keys are not indexed
automatically) in 3 of 3 runs. Each lower tier stated it in 1 of 3. Every run found issue 1, so
this does not change breadth. It is also unlikely to come from the full tier's extra files: those
are `infrastructure/resources.md` and `technology/docker.md`, and `technology/postgres.md` is in
both upper tiers. At n = 3 this is noise until shown otherwise.

### Why cost barely changed

The bundles differ by about 15k tokens (48.5k to 63.4k, characters ÷ 4), but mean tokens per run
differ by under 6.1k, and the largest bundle had the lowest mean. Reviewers read references as
they need them, not all at once, so bundle size is a ceiling on context cost, not the cost itself.

### Deviations and notes

- **Model amended before scoring.** See the Amendment. The two Opus runs are set aside unscored.
- **Extraction error.** The separate model (`claude-haiku-4-5`) that listed findings duplicated
  one finding in `category_technology` repeat 2 under a new ID. Scoring used each run's
  `review.json`, which is authoritative, and every listed finding was checked against it.
- **Directory listings.** Some reviewers ran `ls` on the shared output directory, which shows
  other runs' file names (`r1-s1.md` and so on). The names do not reveal arms. Each run's
  transcript was checked: none opened another run's output, another slot, the coordinator file,
  or this repository.
- **`detect_stack.py` is not in the slots.** The export does not ship the stack detector that
  `SKILL.md` mentions, in any arm, so every reviewer identified the stack by reading the
  repository. It affects all arms alike. It is a gap in the harness.
- **The validator caught a mistake.** One reviewer first wrote `schema_version` "2.0"; the bundled
  validator rejected it and the reviewer corrected it, as #120 intended.
- **Adjudication was not blind.** As pre-registered, the adjudicator knew the mapping. All four
  issues were found by all nine runs, so no judgement call separated the arms.

### What this does and does not show

On one Python, FastAPI and Postgres template, with one model, the Python, Postgres and REST
references, and the routed infrastructure and Docker references, did not change which real issues
were found. The category references alone were enough for all four, including the two whose
mechanisms depend on Postgres or Python. This case is small, and its issues are visible in the
code. Technology references may matter more where the problem lies in engine or runtime behaviour
that the code does not show. Before shortening or removing any file, the harness asks for a
targeted leave-one-out comparison, on a case chosen because a technology reference should matter
there.
