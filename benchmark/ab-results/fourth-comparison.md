# Fourth A/B comparison — Phase 2 exit, on repositories untouched by any tuning

**Status: run and adjudicated, 2026-10-08.** The design below was committed before any run
started (#107); the results were appended afterwards without editing it, apart from this
status line.

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

**Phase 2 is not met.** Discipline held completely: the guided arm made no unsourced
performance estimate and hit no trap, and every guided review passed the validator. But breadth
fell to 65% of plain, below the 75% bar, so under the rule fixed in advance Phase 2 fails on
breadth.

| Criterion | Result | |
|:--|:--|:-:|
| Guided breadth at least 75% of plain | **65%** (7.3 vs 11.3 per run) | **not met** |
| No unsourced performance estimate from the guided arm | **0** (plain arm: 8) | met |
| No `forbidden`-trap hit from the guided arm | **0** of 4 traps, across 3 runs (plain arm: 2 hits) | met |

### The model was held fixed

All six runs used `claude-opus-5-5` on every API call, read from each run's transcript metadata.

### Breadth: distinct real issues per run

Adjudicated with the second and third comparisons' rules: credited only when reported as a
finding, and rejected for either arm when a claim was checked and found not to be a real
performance issue. Instrumentation-only findings are not counted for either arm.

| Run | Plain | Guided |
|:--|--:|--:|
| `URLshortener` #1 | 12 | 8 |
| `URLshortener` #2 | 11 | 7 |
| `full-stack-fastapi-template` | 11 | 7 |
| **Mean** | **11.3** | **7.3** (65%) |

**URLshortener.** Five issues were found by all four runs: no HTTP server timeouts, unbounded
request bodies, the health check's loopback self-test, synchronous per-request logging of the full
header map, and the `SETNX` retry loop. The rest:

| Issue | Plain #1 | Plain #2 | Guided #1 | Guided #2 |
|:--|:-:|:-:|:-:|:-:|
| A negative `exp` stores a key with no TTL; `exp` has no upper bound | ✓ | | ✓ | ✓ |
| `BGSAVE` on every shutdown | ✓ | ✓ | ✓ | |
| `GET /ui/generate` creates a key on every GET | ✓ | ✓ | | ✓ |
| UPX-packed binary decompressed on every start | ✓ | ✓ | ✓ | discarded |
| A goroutine spawned per token creation for statistics | ✓ | ✓ | discarded | |
| Favicon served with no `Content-Type` or `Cache-Control` | ✓ | ✓ | | |
| A fixed 300 ms sleep at startup | ✓ | ✓ | not analysed | |

**fastapi.** Seven issues were found by both arms: missing indexes, a caller-controlled unbounded
`limit`, synchronous SMTP, unthrottled Argon2 hashing, a database connection held across slow
work, row-by-row deletes when a user is removed, and Sentry tracing at a 100% sample rate. Only
the plain run reported a `COUNT` on every list request (the guided run counted it in its query
arithmetic but did not file it), double Pydantic validation of list responses, the email
template recompiled on every send, and no response compression.

**Rejected claims**, for whichever arm made them:
- *Redis client options left at defaults* (both plain URLshortener runs). This is `GT-F01`: the
  vendored `go-redis v7.4.1` defaults are bounded and sound. Counted as a trap hit, not breadth.
- *Cache hot redirects in process* (plain). A design option, not a defect: tokens can be expired,
  and the run itself says to measure first. Guided run #1 weighed it and set it aside on the same
  grounds.
- *Deduplicate repeated URLs* (plain). It turns on product rules, since each link has its own
  expiry.
- *String building in the mux, and static pages rendered per request* (plain). The plain runs
  themselves called the cost negligible.
- *A fixed four-worker count, and connection-pool defaults* (plain, fastapi). Deployment-dependent:
  no file states the core count, instance count, or `max_connections`.
- *Rate limiting on the write endpoints* is credited to neither arm: it is an abuse control, which
  the guided runs filed as adjacent security findings.

**How sensitive the verdict is to the smallest items.** The gap is concentrated in the lowest
tier. The four URLshortener items that only matter at startup or cost a few allocations per
request — the UPX binary, the statistics goroutine, the favicon headers, and the startup sleep —
account for 8 of the plain arm's 23 issues there and 1 of the guided arm's 15. Excluding all four
for both arms raises guided breadth to **81%** (7.0 vs 8.7). The pre-registered rules credit any
real issue regardless of size, so the verdict above stands. But the breadth cost this run measures
is mostly small items, not missed serious ones. Every High-severity issue in either arm was found
by every guided run.

### Discipline

| | Plain | Guided |
|:--|:-:|:-:|
| Unsourced performance estimates | **8** across 3 runs | **0** |
| `forbidden`-trap hits | **2** (`GT-F01`, both URLshortener runs) | **0** |
| Publishable machine output (`validate_review.py`) | — | **3 of 3** |

The plain arm's eight were:
- a binary size of "~10 MB";
- goroutine stacks of "about 4-8 KB" and "about 2-4 KB";
- "around 1 KB of headers";
- "5-10 MB/s of logs" at an assumed 5k RPS;
- SMTP round trips of "200 ms to several seconds";
- Argon2 at "about 30-100 ms of CPU";
- "roughly 2×" the necessary serialization CPU.

Every guided number traced to the repository or to labelled arithmetic. Examples: the 500 ms
creation timeout and the 300 ms sleep came from source, the 70-80% fill from the README, the
64 MiB per hash was derived from `DUMMY_HASH`'s encoded parameters, and the 100 MiB was a test
payload in a validation command.

**The traps.**
- **URLshortener `GT-F01`:** both plain runs reported the Redis defaults as a finding. Both guided
  runs examined the same client and placed it under "considered and not reported."
- **URLshortener `GT-F02`:** no run in either arm recommended the pooled token generator.
- **fastapi `GT-F01` and `GT-F02`:** no run attributed the backend to Node or a Go framework. The
  guided run stated that the `node` signal comes from the frontend build stage and set
  Node-specific reasoning aside.

### The two changes under test

- **#105 (questions, not findings).** No guided finding's cost turned solely on an absent
  deployment fact. The rule was visibly applied twice:
  - The fastapi run turned pool size × workers × instances against `max_connections` into a
    workload question.
  - URLshortener guided run #2 kept the Redis pool as an unknown.
- **#106 (bundled validator).** All three guided reviews ran the bundled validator and passed.
  URLshortener guided run #1 was corrected by it once (one problem reported, then fixed). In the
  third comparison one of three failed.

### Where the guided arm loses breadth

The guided runs did not miss anything serious here. They drop small, real costs, in three ways:
- **Discarded as small.** One run discarded the statistics goroutine as "bounded" and another the
  UPX binary as "startup-only". #102's rule says a real, reachable mechanism is promoted even when
  minor, so both discards break it.
- **Not analysed.** One run named startup as deliberately not analysed in depth.
- **Never reached.** Two runs never reached `BGSAVE`, the favicon, or fastapi's serialization and
  compression costs.

The third comparison measured 87% on other repositories, so how much breadth the guided arm keeps
varies widely from one codebase to another at n = 3.

### What this does not establish

- **That breadth regressed since the third comparison.** The repositories differ, and URLshortener
  has an unusually long tail of small items.
- **That #105 or #106 cost breadth.** Neither change touches how candidates are found, and the
  losses above are of kinds the second comparison already recorded.
- **Anything general.** n = 3 per arm, two repositories, one model.

Raw output for every run is in [`raw-fourth/`](raw-fourth/).
