# Fourth A/B comparison — Phase 2 exit, on repositories untouched by any tuning

**Status: pre-registered, not yet run.** This design was committed before any run in this
comparison started. Results will be appended below the line at the end, without editing
anything above it except this status line.

## Why this run exists

The [third comparison](third-comparison.md) recovered breadth (87% of plain, against a 75% bar)
and the guided arm invented no figures, but Phase 2 was not met: both guided Node runs reported a
deployment-dependent finding that repository's `forbidden` trap declines. Two changes followed:

- **#105** — a mechanism whose cost depends entirely on a fact the repository does not contain
  becomes a workload question, never a finding.
- **#106** — the review validator now ships inside the skill, and `SKILL.md` tells the agent to
  run it before finishing; in the third comparison one guided review was unpublishable.

#105 was written in response to the Node runs, so re-testing on Node would be circular. This run
uses two repositories that played no part in any comparison or tuning so far, both with
`forbidden` traps, so the restraint half of the exit criterion is tested as hard as breadth.

## Design

| | |
|:--|:--|
| Cases | `slytomcat/URLshortener` @ `34b4cb38ca972ebcc831ddc72395f979e1672b8a` (Go, Redis) — 2 guided, 2 plain. `fastapi/full-stack-fastapi-template` @ `8063fe54f17d19f01720e055103af9cad3d8f55d` (Python, FastAPI) — 1 guided, 1 plain |
| Held out from | Every comparison and every tuning change so far (#102, #105). Both repositories appear in this project's development corpus from earlier blind passes, so they are unseen by the tuning, not by the project |
| Model | Every arm pinned explicitly; the model actually used is read from each run's transcript metadata. If the six arms did not all run on one model, the comparison is void |
| Guided | `skills/backend-performance-review` at `87c239c` (main after #105 and #106), copied in isolation. It is now self-contained, including its schemas and validator, so the guided arm receives the skill directory and nothing else |
| Plain | The exact prompt in [`../ab-comparison.md`](../ab-comparison.md) §1 |
| Isolation | Neither arm may read this repository. The guided copy contains no ground truth, results, or evaluation documents |
| Order | Sequential, alternating arms: url P1, url G1, fastapi P, fastapi G, url P2, url G2 |

### A known defect in the answer key, recorded before the runs

`urlshortener.json` was annotated without a pinned commit, and its trap locations name files that
do not exist in this repository's history (`token.go`; Redis options under `service.go`). Both
mechanisms are present at the pinned commit under other files:

- **GT-F01** (Redis client options left at library defaults, which are sound for the vendored
  `go-redis/redis/v7 v7.4.1`) — `dataBaseRedis.go:36`, `redis.NewUniversalClient`.
- **GT-F02** (a pooled token generator that the repository's own comment and benchmark show is
  *slower*) — `shortToken_test.go:130-145`.

Trap hits are therefore adjudicated by meaning — does a run claim what the trap says is false? —
not by the recorded file. Mechanical location matching would miss both.

## What is measured

- **Breadth:** distinct real issues per run, adjudicated against source with the rules used in
  the second and third comparisons.
- **Discipline:** unsourced performance estimates (every unit-bearing number extracted with
  `score.py`'s pattern and adjudicated by hand), and hits on the four `forbidden` traps across the
  two repositories.

Two further observations are recorded but do not decide the outcome:

- **Publishable output (#106):** how many guided reviews pass `validate_review.py`.
- **Deployment-dependent findings (#105):** any guided finding whose cost turns solely on a
  deployment fact no file states.

## Decision rules, fixed now

The same rules as the third comparison:

- **Breadth held** if the guided arm's mean count of distinct real issues is **at least 75%** of
  the plain arm's.
- **Discipline held** if the guided arm makes **no** unsourced performance estimate and **no**
  `forbidden`-trap hit, judged strictly: reporting what a trap declines is a hit, however
  carefully conditioned.
- **Phase 2 is met only if both hold.**

n = 3 per arm. Directional, not a general result. Reported with the same prominence either way.

---

## Results

*Not yet run.*
