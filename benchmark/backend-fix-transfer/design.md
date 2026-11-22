# Backend performance-fix test — does the method find the code a maintainer sped up?

**Status: pre-registered on 2026-11-22; run on 2026-11-22 with the skill exported from `main` at
`8aa114d`; results below.** This design is committed before any
review exists. Results will be appended below the line at the end, without editing anything
above it except this status line.

## Why this run exists

Every backend comparison so far has scored reviews against answer keys written in this project,
partly from skill-guided runs (`ab-comparison.md` §2). The SWE-fficiency test had an outside key,
but its repositories are scientific Python libraries, not backends. This test uses outside keys
from backend services. Each case is a merged pull request in an open-source service whose own
maintainers fixed a performance problem. Where they changed the code is a fact this project had
no hand in.

There are no human annotators for the held-out protocol (`benchmark/README.md`). So this is not
a held-out case set under `dataset.py`'s rules, and it does not replace one. It scores where a
review looks, not how it rates what it finds.

The question: **given a one-line symptom, does a review following the skill point at the code
the maintainer changed more often than a plain review by the same model?**

## Design

| | |
|:--|:--|
| Cases | Ten, chosen by `benchmark/backend_fix_transfer.py` as merged in #134–#138, through the steps below. The list is the rules' output, not a choice |
| Pool | GitHub repositories with topic `self-hosted`, `backend`, `api`, `rest-api`, `saas` or `microservice`, in one of ten backend languages. Each has at least 100 stars, was pushed since 2026-09-01, has a permissive license and is at most 150 MB. That gives 1,065 repositories, frozen in [`selection/pool.json`](selection/pool.json) and ordered by SHA-256 of their names. The first 300 were collected in two batches of 150 |
| Pull requests | Merged between 2026-01-01 and 2026-10-31, with a performance term in the title and no revert, test, benchmark, docs or dependency term. Each changes 1–3 source files and at most 400 source lines; tests, docs, migrations, generated and vendored code never count |
| Service checks | At the base commit of each repository's first eligible PR, the skill's `detect_stack.py` must route a web framework plus a datastore or cache, with at most 3,000 source files. Then a blind Sonnet classifier must call the repository a deployable backend service. It sees only the README excerpt and top-level listing ([`selection/service-decisions.json`](selection/service-decisions.json)) |
| Fix check | A blind Sonnet classifier reads each eligible PR's title, description and changed paths, and nothing else. It answers whether the PR fixes a server-side performance problem ([`selection/fix-decisions.json`](selection/fix-decisions.json)). The first yes per repository, in hash order, is the case. Repositories are taken in hash order until there are ten |
| Symptom | One sentence per case, written by a separate Sonnet agent from the PR's title and description only. It names the observed symptom and condition, never the cause, fix, a file or an identifier. `check-symptom` found no changed file or hunk identifier in any of the ten ([`selection/symptoms.json`](selection/symptoms.json)) |
| Arms | **Treatment:** the skill, exported from `main` when this design merges, with [`treatment-prompt.txt`](treatment-prompt.txt). **Control:** the same model, no skill files, with [`control-prompt.txt`](control-prompt.txt). Each prompt's `{symptom}` is replaced by the case's symptom |
| Inputs per run | A read-only checkout of the repository at the case's base commit, fetched alone (`--depth 1`) so the fix is absent. No patch, PR text, title or hint beyond the symptom |
| Runs | One per case per arm: 20 runs. Arm order alternates by case, as listed in the manifest |
| Model | `claude-sonnet-5-5` in every run, read from transcript metadata. If the 20 runs did not all use one model, the test is void |
| Isolation | Each reviewer gets only its slot and its checkout. Neither arm may read `benchmark/`, `docs/evaluation.md` or `docs/review-response.md`; the control may not read any skill file; no run may use the network. Transcripts are checked afterwards for reads and writes outside those paths |
| Frozen inputs | [`manifest.json`](manifest.json) holds the SHA-256 of both prompts, the pool, and every case's patch (in [`tasks/`](tasks/)). `.gitattributes` keeps them byte-exact |

### The ten cases

| Case | Language | The maintainer's change |
|:--|:--|:--|
| `relay-panel-43` | Rust | shard the UDP forwarder's session map |
| `tibiadata-api-go-630` | Go | zero-allocation case-insensitive lookups in validation |
| `dnb-backend-508` | Node | remove per-item `populate` in list endpoints |
| `arcane-4106` | Go | resolve GitOps compose paths once per list request |
| `astracode-42` | Go | batch official test-case execution in the judge worker |
| `pokeapi-1688` | Python | one query for the evolution-chain serializer |
| `commafeed-2089` | Java | batch the entry existence check on feed refresh |
| `flexmeasures-2641` | Python | validate storage constraints without `DataFrame.eval` |
| `gatus-1541` | Go | add PostgreSQL indexes for result queries |
| `claude-code-agent-monitor-332` | Node | index `events.tool_name` for the analytics query |

## What is measured

All scoring is by `backend_fix_transfer.py score` with `--window 20`, against the patch in
`tasks/`. Only changed source files count.

- **Hunk hit (primary):** at least one finding's location falls inside a changed hunk of the
  base file, widened by 20 lines.
- **File hit:** at least one finding names a changed source file.
- **Top-finding file hit:** the first finding, by priority then listed order, names a changed
  source file.
- **Descriptive, outside the decision rules:**
  - whether the hit finding's mechanism matches what the patch changed;
  - findings per run;
  - total tokens and elapsed time;
  - whether each treatment `review.json` passes the bundled validator.

**Control transcription.** The control writes prose. A separate Sonnet agent transcribes its
findings into `{"findings": [{"id", "location": {"file", "line"}}]}`, seeing only the reports and
the literal rule: transcribe literally, never repair. A finding gets the first file and line it
states. A file named without a line gets no line, and a finding that names no file gets none.
Control findings carry no priority, so its top finding is the first listed. Treatment findings
are scored from `review.json` as written. Both prompts ask for a file's path from the repository
root, so the bare-filename gap seen in the SWE-fficiency test should be smaller. A sensitivity
reading that resolves a bare name matching exactly one changed file is reported as before,
outside the rules.

## Threats to validity, stated now

- **Two symptoms say more than a bug report would.** `tibiadata-api-go-630` says lookups are
  slow "regardless of letter case", which points near the changed code. `pokeapi-1688` says the
  request "causes many database queries", the symptom an N+1 produces. Both passed the
  mechanical check, and both arms get them. They are kept as written, because the coordinator
  knows the fixes and any edit would not be blind.
- **Contamination.** The fixes are public commits from 2026. The model may have seen some.
  This affects both arms equally.
- **The symptom narrows the search.** That is the point of the design, but it is more than a
  full review gets.
- **One fix is not the only problem.** A review that finds a different real issue scores a
  miss. Hit rates measure agreement with the maintainer, not review quality.
- **Location, not diagnosis.** Mechanism agreement is reported, outside the rules.
- **Model judgements in selection.** Three steps used a blind model: service, fix and symptom.
  Each saw only what is listed above and no review. Their outputs are committed.
- **n = 10, one run each, one model.** Directional, not a general result.

## Decision rules, fixed now

**The method helps localization on backend fixes** if treatment hunk hits are at least **2 more**
than control hunk hits, out of 10.

**The plain request does better** if control hunk hits are at least 2 more than treatment hunk
hits. That is reported with the same prominence.

**Otherwise:** no measurable difference in localization at n = 10.

Separately, as an absolute reading of the treatment arm alone: a **file hit in at least 5 of 10**
cases means the method localizes real backend fixes; **2 or fewer** means it does not; anything
in between is inconclusive.

## Deviations before registration

These changes were made after candidates had been seen, but before any case was fixed, any
symptom was written, or any review existed:

1. **The first pool was replaced.** It held the most-starred repositories per language. Of its
   first 200, the detector passed 27 and the classifier found 4 deployable services. Their
   matched PRs were mostly not server-side fixes, such as a chart option matched on "latency"
   and a service-worker image cap. The service-topic pool replaced it (#138).
2. **The detector alone admitted libraries.** Frameworks, test libraries and CLIs passed it,
   because it reads every dependency declaration. The blind service classifier was added
   (#136).
3. **Title keywords admitted features and frontend work.** The blind fix check was added (#138).
4. **Django's `tests.py` counted as source.** `pokeapi-1688`'s patch changes
   `pokemon_v2/tests.py`. The source rule now excludes `tests.py`, `test.py` and `conftest.py`,
   with a test. No case's selection changed: the PR still changes one source file.
5. **One detector check used a sibling commit.** For `astracode-42`, the detector and service
   checks ran at the base of that repository's first eligible PR (#41), which the fix check
   rejected. The case is #42, reviewed at its own base commit.

One collection error (`zelloptt/zello-channel-api`, a diff that could not be fetched) left that
repository with no pull requests. It fell outside the ten either way.

---

## Results

**Answer: no measurable difference in localization, and the method localizes real backend
fixes.**

- **Hunk hits:** the treatment hit a changed hunk in 7 of 10 cases and the control in 6. The
  difference of 1 is under the pre-registered 2, so neither "does better" rule is met.
- **File hits:** on its own, the treatment named a changed source file in **9 of 10** cases. That
  meets the "localizes" rule of at least 5.
- **Path reading:** as in the SWE-fficiency test, the control's strict score depends on how
  abbreviated paths are read. Under the sensitivity reading the control leads by 1 instead. Either
  way the difference stays under 2, so the answer does not change.

### Runs

All 20 runs used `claude-sonnet-5-5` on every model call, read from transcript metadata.

- Every treatment `review.json` passed the bundled validator.
- No run used the network, or read or wrote another run's files.
- Each checkout was read-only.

Reports, treatment reviews, control transcriptions and per-run scores are in [`raw/`](raw/).

### Summary by arm

| | Treatment | Control |
|:--|:-:|:-:|
| **Hunk hit (primary)** | **7 of 10** | **6 of 10** |
| File hit | 9 of 10 | 8 of 10 |
| Top-finding file hit | 8 of 10 | 8 of 10 |
| Findings per run | 2.1 (21 in all) | 6.6 (66 in all) |
| Treatment `review.json` valid | 10 of 10 | — |
| Total tokens per run, mean | 121.2k | 81.4k |
| Elapsed per run, mean | 3.6 min | 1.4 min |

### Per case

`F`/`H`/`T` mark a file hit, hunk hit and top-finding file hit as scored; `-` marks a miss.

| Case | Maintainer's change | Treatment | Control |
|:--|:--|:-:|:-:|
| `relay-panel-43` | shard the UDP session map | `F H T` | `F H T` |
| `tibiadata-api-go-630` | zero-allocation case-insensitive lookups | `- - -` | `F H T` |
| `dnb-backend-508` | remove per-item `populate` in list endpoints | `F H T` | `- - -` |
| `arcane-4106` | resolve compose paths once per list request | `F H T` | `F H T` |
| `astracode-42` | batch official test-case execution | `F H T` | `F H T` |
| `pokeapi-1688` | one query for the evolution-chain serializer | `F H T` | `F H T` |
| `commafeed-2089` | batch the entry existence check | `F - T` | `- - -` |
| `flexmeasures-2641` | validate constraints without `DataFrame.eval` | `F H T` | `F H T` |
| `gatus-1541` | add PostgreSQL indexes | `F H -` | `F - T` |
| `claude-code-agent-monitor-332` | index `events.tool_name` | `F - T` | `F - T` |

### The sensitivity reading

The control was asked for each file's path from the repository root. It still abbreviated some
paths:

- `badge.service.js`;
- `B/feed/...`, after defining `B/` as a prefix;
- `specific_postgres.go`.

Transcribed literally, as pre-registered, those match no patch path. This decides two control
misses: `dnb-backend-508` and `commafeed-2089`.

As a sensitivity reading, outside the rules, a path was resolved when it is a suffix of exactly
one changed source file. The control then has **8** hunk hits, 10 file hits and 9 top-finding
file hits, against the treatment's 7, 9 and 8.

- `B/feed/FeedRefreshUpdater.java` is not a suffix of any path, so it stays unresolved. The
  commafeed control hit comes from a later finding that names `FeedRefreshUpdater.java` alone.
- The treatment is unaffected, because the skill's schema requires a repository-relative path.

### Did the arms name the maintainer's mechanism?

This is descriptive, outside the decision rules.

- **Both arms, same mechanism, near the top:** arcane, astracode, pokeapi, flexmeasures,
  dnb-backend and commafeed.
  - In dnb-backend, the treatment's top finding is award evaluation on every read, with the
    per-badge query loop inside it. The control's second finding is that loop on its own.
- **relay-panel:** the maintainer sharded the session map and throttled the tracker touch.
  - The control's second finding, rated High, is exactly that.
  - The treatment named it as its third finding, Low and P3, with "profile first; change nothing
    yet". Its top finding was a reader-task leak the maintainer did not change.
- **tibiadata:** the maintainer replaced linear `EqualFold` scans with zero-allocation map
  lookups.
  - The control's top finding is those scans, with a map index as the fix.
  - The treatment read the same functions and reported no findings, reasoning that `EqualFold`
    does not allocate and the lists are scanned once per request.
  - This is the case where the symptom hinted most strongly, and the method declined the target.
- **gatus:** both arms named the missing PostgreSQL indexes.
  - The treatment ranked a key-listing query first and the indexes second. That made it a hunk
    hit but not a top-finding hit.
  - The control ranked the indexes first, but located them at the table definition, line 60. The
    indexes were added at line 120. That made it a hunk miss.
- **Agent-Monitor:** both arms' top finding is the missing `tool_name` index. Both located it at
  the query (`server/db.js:1961`), not where the index was added (line 404), so both miss the
  hunk the same way.

So on mechanism, the control named the maintainer's change in all ten cases, and ranked it first
or second in each. The treatment named it in nine, and ranked it low in one.

### Cost

The treatment used about 1.5 times the tokens and 2.5 times the elapsed time. It reported about a
third as many findings: 2.1 per run against 6.6. Its narrower reports put the changed file first
in 8 of 10 cases, the same as the control.

### Deviations and notes

- **Every report write was refused.** The agent runtime rejected the `Write` of `report.md` in
  all 20 runs. Each report in `raw/` was recovered verbatim from the refused call's own content.
  Nothing in a report was edited.
- **Transcription was done by a separate agent.** The coordinator saw the treatment scores and
  every report before the controls were transcribed. To keep that from steering the
  transcription, a separate `claude-sonnet-5-5` agent transcribed all ten control reports. It saw
  only the reports and the literal rule, not the patches, scores or repositories. Its judgement
  calls are in [`raw/transcriber-notes.txt`](raw/transcriber-notes.txt).
- **Run-root commands.** Five treatment runs changed directory to the run root to call the
  bundled validator on their own `review.json`. Transcripts show no read or write of another
  run's files.
- **Validator iterations.** Two treatment runs (arcane and Agent-Monitor) fixed schema errors the
  validator reported, then validated again. That is the skill's intended loop.
- **Parallel dispatch.** Runs went out in two waves of ten: each case's first arm in manifest
  order, then each case's second arm once all first arms had finished. The two arms of one case
  never ran at the same time.

### What this does and does not show

On ten real backend performance fixes chosen by rule, a reviewer following the skill found the
file a maintainer later changed in 9 of 10 cases, given a one-line symptom. A plain request to the
same model did about as well: one hunk hit fewer when scored strictly, one more once abbreviated
paths are resolved. So the method localizes backend fixes, but it did not localize them better
than asking plainly.

It did differ in three ways:

- Its reports were about a third as long, and always gave full paths.
- In two cases it ranked the maintainer's change lower, or declined it, where the plain request
  put it near the top.
- It cost more.

The SWE-fficiency test found the same pattern outside backends.

The symptom pointed both arms at the slow operation. A review with no symptom, the skill's normal
setting, was not tested. Neither was more than one run per case, nor any other model.
