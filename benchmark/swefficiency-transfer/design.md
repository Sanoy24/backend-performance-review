# SWE-fficiency transfer test — does the method find the code an expert sped up?

**Status: pre-registered on 2026-11-14; not yet run.** This design is committed before any review
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

*Not yet run.*
