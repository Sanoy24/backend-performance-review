# Fifth A/B comparison — the low-tier sweep, on repositories outside every earlier comparison

**Status: pre-registered, not yet run.** This design was committed before any run in this
comparison started. Results will be appended below the line at the end, without editing
anything above it except this status line.

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

*Not yet run.*
