# Fifth A/B comparison — the low-tier sweep, on repositories outside every earlier comparison

**Status: run and adjudicated, 2026-10-09.** The design below was committed before any run
started (#111); the results were appended afterwards without editing it, apart from this
status line.

## Why this run exists

The [fourth comparison](fourth-comparison.md) held discipline completely, but guided breadth was
65% of plain against a 75% bar, so Phase 2 was not met. The gap was almost entirely in small
items. Some were real mechanisms discarded as "bounded" or "startup-only", and others sat in
areas the sweep never reached: response encoding, startup, shutdown, and build. #110 changed
the methodology to address both.

#110 was written against the fourth comparison's misses, so measuring it on those repositories
would only show it fixed what it was aimed at. This run uses repositories outside every
comparison so far, and asks the same two questions as before:

1. **Is guided breadth at least 75% of plain breadth?**
2. **Did discipline survive?** A wider sweep that brings back invented figures, or files
   out-of-scope concerns as performance findings, would be a regression, not a fix.

## Design

| | |
|:--|:--|
| Breadth cases | `gothinkster/rails-realworld-example-app` @ `a2ae4fff78b3337683cea988c023281dcb5551b9` (Ruby on Rails) — 2 guided, 2 plain. `gothinkster/aspnetcore-realworld-example-app` @ `a397d1197b22edeffa4d2563fa5f4f7f11d0b254` (C#, ASP.NET Core) — 1 guided, 1 plain |
| Trap case | `antonosmond/github-signature-verifier` @ `1239b2c335773e00897999c90e50c2b16160e4f1` (Node.js on AWS Lambda) — 1 guided, 1 plain. Used for discipline only, not breadth: it is 229 lines of JavaScript, and its two `forbidden` traps test whether out-of-scope concerns are kept out of the performance findings |
| Held out from | Every comparison and every tuning change so far (#102, #105, #110). All three repositories appear in this project's development corpus from earlier blind passes, so they are unseen by the tuning, not by the project |
| Model | Every arm pinned explicitly; the model actually used is read from each run's transcript metadata. If the eight arms did not all run on one model, the comparison is void |
| Guided | `skills/backend-performance-review` at `2b33405` (main after #110), copied in isolation. It is self-contained, so the guided arm receives the skill directory and nothing else |
| Plain | The exact prompt in [`../ab-comparison.md`](../ab-comparison.md) §1 |
| Isolation | Neither arm may read this repository. The guided copy contains no ground truth, results, or evaluation documents |
| Order | Sequential, alternating arms: rails P1, rails G1, aspnetcore P, aspnetcore G, rails P2, rails G2, verifier P, verifier G |

### Known defects in the answer keys, recorded before the runs

- `github-signature-verifier.json` was annotated without a pinned commit. Its `GT-F02` names
  `terraform/main.tf`, which does not exist; the `nodejs4.3` runtime it describes is declared at
  `terraform/lambda.tf:14` at the pinned commit. Its `GT-F01` (`src/index.js`, the
  non-constant-time signature comparison at line 46) is correct.
- `aspnetcore-realworld.json` records an abbreviated commit, `a397d119`, which resolves uniquely
  to the full SHA above.

Trap hits are adjudicated by meaning, not by the recorded file.

## What is measured

- **Breadth:** distinct real issues per run on the two breadth cases, adjudicated against source
  with the rules used in the second, third, and fourth comparisons. Credit is given only when an
  issue is reported as a finding; issues left in `considered_not_reported`, mentioned as context,
  or filed only as correctness are not credited. A claim checked and found not to be a real
  performance issue is rejected for either arm. Instrumentation-only findings are not counted.
- **Discipline:** unsourced performance estimates in all eight reports (every unit-bearing number
  extracted with `score.py`'s pattern and adjudicated by hand), and hits on the two `forbidden`
  traps in `github-signature-verifier.json`.

Three further observations are recorded but do not decide the outcome:

- **The same smallest-items sensitivity as the fourth comparison:** breadth with startup-only and
  per-allocation items excluded for both arms.
- **Publishable output:** how many guided reviews pass `validate_review.py`.
- **Deployment-dependent findings (#105):** any guided finding whose cost turns solely on a
  deployment fact no file states.

## Decision rules, fixed now

The same rules as the third and fourth comparisons:

- **Breadth held** if the guided arm's mean count of distinct real issues on the breadth cases is
  **at least 75%** of the plain arm's.
- **Discipline held** if the guided arm makes **no** unsourced performance estimate and **no**
  `forbidden`-trap hit, judged strictly: filing what a trap declines as a performance finding is
  a hit, however carefully conditioned. Reporting it as an adjacent, non-performance finding is
  what the traps ask for, and is not a hit.
- **Phase 2 is met only if both hold.**

n = 3 per arm for breadth, 1 per arm for the trap case. Directional, not a general result.
Reported with the same prominence either way.

---

## Results

**Phase 2 is met.** Guided breadth was 89% of plain, above the 75% bar. The guided arm made no
unsourced performance estimate and hit neither trap. All four guided reviews passed the
validator.

| Criterion | Result | |
|:--|:--|:-:|
| Guided breadth at least 75% of plain | **89%** (11.3 vs 12.7 per run) | met |
| No unsourced performance estimate from the guided arm | **0** (plain arm: 6) | met |
| No `forbidden`-trap hit from the guided arm | **0** of 2 traps (plain arm: 2 hits) | met |

### The model was held fixed

All eight runs used `claude-opus-5-5` on every API call, read from each run's transcript
metadata. Guided Rails run #2 was attempted twice. The first attempt stopped partway on a
session usage limit (HTTP 429) before writing its report. Its partial JSON was deleted unread,
and the run was restarted from scratch with the same prompt. The second attempt is the one
recorded here.

### Breadth: distinct real issues per run

Adjudicated with the earlier comparisons' rules: credit is given only when an issue is reported
as a finding, and a claim checked and found not to be a real performance issue is rejected for
either arm. Instrumentation-only findings are not counted.

| Run | Plain | Guided |
|:--|--:|--:|
| `rails-realworld` #1 | 10 | 11 |
| `rails-realworld` #2 | 12 | 10 |
| `aspnetcore-realworld` | 16 | 13 |
| **Mean** | **12.7** | **11.3** (89%) |

**Rails.** All four runs found the same ten issues:
- per-row queries when serializing articles (tags, `favorited?`, `following?`)
- an unbounded `limit`
- per-row author queries on the comment list
- an unpaginated comment list
- no index on the `created_at` sort column
- a `COUNT(*)` on every list request
- SQLite as the production database
- the tag cloud aggregating every tagging on every request
- cascading deletes one row at a time
- `:debug` logging in production

Beyond those ten:
- **Guided run #1 only:** an unbounded client tag list written one tag at a time inside the write
  lock.
- **Plain run #2 only:** the tag filter's `LOWER(name) LIKE` lookup, which cannot use the name
  index, and `require 'rails/all'` loading unused frameworks into every worker.

**ASP.NET Core.** Thirteen issues were found by both arms:
- missing indexes on slug, username, email, and created-at
- article reads joining every favorite and tag row
- a synchronous full count on every list
- a transaction around every request, reads included, on single-writer SQLite (the guided
  finding prescribes WAL mode)
- profile reads loading the viewer's whole follow graph
- comment create and delete loading every comment
- every SQL statement logged to two console sinks
- the feed loading the viewer's whole follow list
- an unbounded comment list
- one save per tag, plus slug probing, on article create
- the whole tags table read on every call
- an unbounded article `limit`

The plain run also reported:
- redundant round trips on favorite, follow, and edit (the guided run mentioned them only in a
  table)
- no response compression (the guided run named it only as context)
- a `JwtSecurityTokenHandler` built per token

**Rejected claims**, for whichever arm made them:
- *Unthrottled bcrypt or HMAC login* (plain). Rate limiting is an abuse control, credited to
  neither arm, as in the fourth comparison. The fast HMAC password hash is a security defect,
  which the guided run filed as one.
- *A Devise/Warden lookup on every request* (plain, Rails). Without a session cookie, Warden's
  strategies issue no query.
- *A per-request current-user lookup by username* (plain, ASP.NET Core). It is one lookup per
  authenticated request, not counted for either arm, as in the third comparison; the missing
  username index it runs against is already counted under indexes.
- *No HTTP caching* (plain, Rails). A design option, not a defect.
- *Duplicate favorites and follows, and a stale favorites count* (plain). Correctness issues.
  The guided runs filed the duplicates as such.
- *The unfavorite reload* (guided). It is needed to return the updated counter.
- *The ActiveRecord pool of 5 against Puma's thread count* (plain, Rails). Deployment-dependent:
  nothing in the repository sets Puma's threads, and a launch command commonly does. Both guided
  runs asked it as a workload question (#105).
- *Legacy MVC routing and Swagger served in production* (plain, ASP.NET Core). No per-request
  cost shown.

**The smallest-items sensitivity** (pre-registered as an observation). Excluding the
startup-only and per-allocation items — `rails/all` and the per-token JWT handler, both
plain-only — gives **94%** (11.3 vs 12.0).

### Discipline

| | Plain | Guided |
|:--|:-:|:-:|
| Unsourced performance estimates | **6** across 4 runs | **0** |
| `forbidden`-trap hits | **2** | **0** |
| Publishable machine output (`validate_review.py`) | — | **4 of 4** |

The plain arm's six were:
- bcrypt at "about 100–250 ms of CPU", in both Rails runs
- an SSM call adding "about 20-100 ms"
- a Lambda invoke at "typically 10-50 ms"
- webhook payloads "often 10-200 KB"
- a TLS setup at "often 10-40 ms"

Every guided number traced to the repository, for example the 5000 ms busy timeout and the 128 MB
memory size, or to labelled arithmetic. One guided report states this directly: "No runtime metric
in this report was estimated or assumed."

The first-pass classification of the five breadth-case reports was done by a smaller model
(`claude-haiku-4-5`) and then spot-checked against the extracted numbers. The verifier reports
were classified by hand.

**The traps** (`github-signature-verifier.json`):
- **`GT-F02` (end-of-life `nodejs4.3` runtime).**
  - The plain run filed it inside a Medium-Low performance finding, claiming a "much slower V8".
    That is a hit.
  - The guided run filed it as `MAINT-001`, an adjacent finding.
- **`GT-F01` (non-constant-time signature comparison).**
  - The plain run listed it under its Low "small inefficiencies" performance finding while saying
    "`===` isn't a performance problem". That is a hit under the strict reading, softened by its
    own disclaimer.
  - The guided run filed it as `SEC-001`, an adjacent finding.

### What this establishes, and what it does not

- **Phase 2's exit criterion is met on this run**, as pre-registered: breadth above the bar, with
  discipline held completely. This is the first comparison to meet both.
- **Not that #110 caused the improvement.** The fourth comparison measured 65% on different
  repositories. Separating the change from the repositories' difficulty would need the previous
  skill run on these same repositories.
- **Not anything general.** n = 3 per arm for breadth and 1 for the traps, three repositories,
  one model.
- **The breadth cost has not disappeared.** On ASP.NET Core the guided run still trailed by
  three, and two of those three were issues it saw and named only as context.

Raw output for every run is in [`raw-fifth/`](raw-fifth/).
