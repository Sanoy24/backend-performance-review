# SWE-fficiency transfer test — does the method find the code an expert sped up?

**Status: pre-registered on 2026-11-14; run on 2026-11-14 with the skill exported from `main` at
`581cf0c`; results below.** This design is committed before any review
exists. Results will be appended below the line at the end, without editing anything above it
except this status line.

## Why this run exists

Every comparison so far has used answer keys written in this project, partly from skill-guided
runs, so they can favour the skill by construction (`ab-comparison.md` §2). SWE-fficiency
(arXiv 2511.06090) offers an outside answer: each task pairs a repository snapshot and a slow
workload with the patch an expert wrote to speed it up. Where the expert changed the code is a
fact this project had no hand in.

The repositories are scientific Python libraries, not web backends. So this does not measure how
well the skill reviews backends. It measures how far its method transfers outside its domain, and
whether that method beats asking plainly there.

The question: **given the slow workload, does a review following the skill point at the code the
expert changed more often than a plain review by the same model?**

## Design

| | |
|:--|:--|
| Dataset | `swefficiency/swefficiency`, test split, 498 tasks across 9 repositories. Hub revision `7fbfe6c`, parquet conversion `d6950c8`; each parquet file's SHA-256 is in the manifest. Materials are CC-BY 4.0 per the project's README |
| Tasks | Nine, one per repository, chosen by `swefficiency_transfer.py select` with its defaults: instances ordered by SHA-256 of their id, first whose patch changes 1–3 source files. The rule was fixed in #127, before the dataset was read; this list is its output, not a choice |
| Arms | **Treatment:** the skill, exported from `main` once #127 has merged, with `treatment-prompt.txt`. **Control:** the same model, no skill files, with `control-prompt.txt` |
| Inputs per run | A read-only checkout of the task's repository at its `base_commit`, fetched alone (`--depth 1`) so later history, including the expert's commit, is absent. The task's workload as `workload.py` in a sibling directory. No patch, no test patch, no problem statement, no hints |
| Prompts | Both give the same facts about the task. The control keeps the A/B comparison's wording, adapted from "backend codebase" to "the code that workload exercises", and asks for file and line so its findings can be scored. The treatment asks for the skill's report and `review.json`. Both forbid running code: the library is not built, and the skill is read-only |
| Runs | One per task per arm: 18 runs. Arm order alternates by task, as listed in the manifest |
| Model | `claude-sonnet-5-5` in every run, read from each run's transcript metadata. If the 18 runs did not all use one model, the test is void |
| Isolation | Each reviewer gets only its slot, the checkout and the workload. Neither arm may read `benchmark/`, `docs/evaluation.md` or `docs/review-response.md`; the control may not read any skill file; no run may use the network. Transcripts are checked afterwards for reads and writes outside those paths |
| Frozen inputs | [`manifest.json`](manifest.json) holds the SHA-256 of both prompts, every task's patch and workload (in [`tasks/`](tasks/)), and the dataset files. `.gitattributes` keeps them byte-exact |

The dataset's `speedup` column is 0.0 for all 498 rows in this conversion, so it is not used.

## What is measured

All scoring is by `swefficiency_transfer.py score` with `--window 20`, against the patch in
`tasks/`. Only changed source files count; tests, docs and benchmark files in a patch never do.

- **Hunk hit (primary):** at least one finding's location falls inside a changed hunk of the
  base file, widened by 20 lines.
- **File hit:** at least one finding names a changed source file.
- **Top-finding file hit:** the first finding, by priority then listed order, names a changed
  source file.
- **Descriptive, outside the decision rules:** whether the hit finding's mechanism matches what
  the patch changed, findings per run, total tokens and elapsed time, whether each treatment
  `review.json` passes the bundled validator, and whether any treatment run declined the target
  as out of scope.

**Control transcription.** The control writes prose. Its findings are transcribed into
`{"findings": [{"id", "location": {"file", "line"}}]}` by the A/B rule: transcribe literally,
never repair. A finding gets the first file and line it states; a file named without a line gets
no line; a finding that names no file gets none. Control findings carry no priority, so its top
finding is the first listed. Treatment findings are scored from `review.json` as written.

## Threats to validity, stated now

- **Contamination.** The expert patches are public upstream commits that the model may have seen
  in training. This affects both arms equally and cannot be removed. Fetching only the base commit
  keeps the fix itself out of the checkout.
- **The workload is a strong hint.** It names the slow call. That is how the benchmark frames the
  task, and both arms get it, but it narrows the search more than a real review would.
- **A finding near the right lines is not the same as the right diagnosis.** Hit rates score
  location only. Mechanism agreement is reported, but outside the rules.
- **The domain is wrong for the skill on purpose.** A loss here would not show that the skill
  reviews backends badly; a win would not show that it reviews them well.
- **n = 9 tasks, one run each, one model.** Directional, not a general result.

## Decision rules, fixed now

**The method helps localization on this benchmark** if treatment hunk hits are at least
**2 more** than control hunk hits, out of 9.

**The plain request does better** if control hunk hits are at least 2 more than treatment hunk
hits. That is reported with the same prominence.

**Otherwise:** no measurable difference in localization at n = 9.

Separately, as an absolute reading of the treatment arm alone: a **file hit in at least 5 of
9** tasks means the method's localization transfers to this domain; **2 or fewer** means it does
not; anything in between is inconclusive.

---

## Results

**Answer: no measurable difference in localization, and the method transfers.** The treatment
hit a changed hunk in 6 of 9 tasks and the control in 5, a difference of 1, under the
pre-registered 2. Neither "does better" rule is met. On its own, the treatment named a changed
source file in **8 of 9** tasks, which meets the "transfers" rule (at least 5).

The control's score depends on how bare filenames are read (see the sensitivity reading below).
Under it, the control leads by 1 instead. Either way the difference stays under 2, so the
answer does not change.

### Runs

All 18 runs used `claude-sonnet-5-5` on every model call, read from each run's transcript
metadata. Every treatment `review.json` passed the bundled validator. No run used the network,
and every checkout was unmodified afterwards. Reports, control transcriptions and per-run scores
are in [`raw/`](raw/).

### Summary by arm

| | Treatment | Control |
|:--|:-:|:-:|
| **Hunk hit (primary)** | **6 of 9** | **5 of 9** |
| File hit | 8 of 9 | 5 of 9 |
| Top-finding file hit | 8 of 9 | 5 of 9 |
| Findings per run | 1.3 (12 in all) | 4.1 (37 in all) |
| Treatment `review.json` valid | 9 of 9 | — |
| Total tokens per run, mean | 83.6k | 55.6k |
| Elapsed per run, mean | 1.9 min | 0.8 min |

### Per task

`F`/`H`/`T` mark a file hit, hunk hit and top-finding file hit as scored; `-` marks a miss.

| Task | Expert changed | Treatment | Control |
|:--|:--|:-:|:-:|
| `astropy-16222` | `Angle._wrap_at`: return early when no wrapping is needed | `- - -` | `- - -` |
| `dask-7023` | cache `Array.shape` | `F H T` | `- - -` |
| `matplotlib-26899` | `get_tightbbox`: stop measuring every Axes twice | `F - T` | `F H T` |
| `numpy-12575` | `find_duplicate`: O(n²) scan to a counter | `F H T` | `F H T` |
| `pandas-50168` | per-element `Timestamp(...).tz_localize` to per-zone masks | `F H T` | `F H T` |
| `xarray-9429` | `group.copy(..., deep=False)` | `F H T` | `- - -` |
| `scikit-learn-28064` | thread the per-feature threshold search in `fit` | `F H T` | `F H T` |
| `scipy-22660` | hoist pybind property lookups out of the per-column loop | `F H T` | `F H T` |
| `sympy-25452` | route symbolic `inv()` through `DomainMatrix` | `F - T` | `- - -` |

### The sensitivity reading

The control wrote a bare or partial filename for 19 of its 37 findings, such as `core.py:1240` after naming
`dask/array/core.py` earlier in the report. Transcribed literally, as pre-registered, a bare name
matches no patch path. This decides three control misses: dask, xarray and sympy.

As a sensitivity reading, outside the rules, a bare name was resolved when it is a suffix of
exactly one changed file. The control then has **7** hunk hits, 8 file hits and 7 top-finding
file hits, against the treatment's 6, 8 and 8. The treatment is unaffected, because the skill's
schema requires a repository-relative path. Strict scoring partly measures whether a review says
where the code is precisely enough to act on, and that favours the skill. Localization by intent
does not differ.

### Did the hit finding name the expert's mechanism?

This is descriptive, outside the decision rules.

- **Both arms, same mechanism:** numpy, pandas, scikit-learn and scipy. Each arm's top finding
  is the expert's change.
- **dask:** both arms named recomputing `shape` on every access, which is what the expert's
  `cached_property` removes. The control's hit needs the sensitivity reading.
- **matplotlib:** both arms named the expert's mechanism, every Axes measured twice in
  `Figure.get_tightbbox`. The treatment located it at the artist-list builder (`figure.py:1669`),
  41 lines before the changed hunk, so it is a file hit only. The control located it inside the
  hunk.
- **xarray:** the treatment's top finding is the expert's fix, a deep copy of the group's
  coordinates in `groupers.py:187`. The control mentioned `groupers.py:187` only in passing, as
  "small, fixed-cost overheads" under its second finding, and did not name the deep copy. Its top
  finding was a Python loop in `groupby.py` that the expert left alone.
- **sympy:** both arms blamed generic Gaussian elimination with full simplification in `inv()`.
  The expert's fix bypasses that path through `DomainMatrix`. The finding's line falls outside
  the changed hunks, which add new functions elsewhere in the file.
- **astropy:** both arms blamed the coordinate parser promoting unit-spherical input. Neither
  looked at `Angle._wrap_at`. This is the one task where both arms missed the same way.

So on mechanism, the arms agree on eight tasks. The treatment found one fix the control
underrated (xarray). The control placed one shared diagnosis more precisely (matplotlib).

### Cost

The treatment used about 1.5 times the tokens and 2.4 times the elapsed time, and reported a
third as many findings. On this benchmark, its narrower reports put the expert's file first in 8
of 9 tasks, against the control's 5, or 7 under the sensitivity reading.

### Deviations and notes

- **Control reports could not always be written.** In four control runs (numpy, scipy,
  scikit-learn, matplotlib), the agent runtime rejected or skipped the `Write` of `report.md` and
  the report came back as text. Each `report.md` in `raw/` was recovered verbatim: from the
  rejected `Write` call's own content, or for matplotlib from the run's final message. Nothing in
  a report was edited.
- **Transcription was done by a separate agent.** The coordinator saw the treatment scores before
  the controls were transcribed. To keep that from steering the transcription, a separate
  `claude-sonnet-5-5` agent transcribed all nine control reports. It saw only the reports and the
  literal rule, not the patches, the scores or the repositories. Its rule calls are recorded with
  each `transcription.json`. The bare-filename issue above is its literal output, not a repair.
- **One write outside a run's own output.** The scikit-learn treatment run ran
  `sed -i 's/"schema_version":"2.0"/.../' out/*-treatment/review.json`. The glob reached every
  treatment output, but only its own file contained that exact string, so no other file's
  content changed.
- **Schema version guessed.** Two treatment runs first wrote `"schema_version": "2.0"`, apparently
  from the skill's release number. The validator rejected it, and they corrected it to `1.0`.
  The schema, template and validator all say `1.0`. The skill does not state the version in
  `SKILL.md` itself, which may be worth adding.
- **Parallel dispatch.** Runs went out in two waves: each task's first arm in manifest order,
  then its second arm once the first had finished. Arms ran in parallel across tasks, never
  within one.
- **Reads of the run root.** Three treatment runs ran their bundled validator from the run's
  parent directory. Transcripts show no read of another run's output.

### What this does and does not show

On nine scientific-Python tasks with an outside answer key, a reviewer following the skill found
the file an expert later sped up in 8 of 9. A plain request to the same model, given the same
workload, found it about as often once bare filenames are resolved. So the method transfers
outside backends, but it did not localize better than asking plainly. What it did differently:
fewer findings, full paths, the expert's file first more often, and one fix the plain request
underrated.

The workload pointed both arms at the slow call. A review with no workload, the skill's normal
setting, was not tested. Neither was more than one run per task, or any other model.
