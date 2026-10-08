# Third A/B comparison — breadth recovery, on repositories it was not tuned on

**Status: pre-registered, not yet run.** This design was committed before any run in this
comparison started. Results will be appended below the line at the end, without editing
anything above it except this status line.

## Why this run exists

The [second comparison](second-comparison.md) found, on one pinned model, that guided reviews
reported 69% as many distinct real issues as plain ones — below the 75% line — through two
failures: real minor issues discarded, and write paths and data growth never examined. #102
changed the methodology to address both.

That change was written against the gin misses, so measuring it on gin would only show it fixed
what it was aimed at. This run measures it on repositories that played no part in the tuning,
and asks two questions:

1. **Is guided breadth now at least 75% of plain breadth?** That is the Phase 2 exit criterion.
2. **Did discipline survive?** Recovering breadth by loosening the evidence rules would be a
   regression, not a fix.

## Design

| | |
|:--|:--|
| Cases | `gothinkster/node-express-realworld-example-app` @ `30b68e1e881462b2f4164ea09ab4c4f5699c7b0b` (TypeScript, Prisma) — 2 guided, 2 plain. `gothinkster/spring-boot-realworld-example-app` @ `ee17e31aafe733d98c4853c8b9a74d7f2f6c924a` (Java) — 1 guided, 1 plain |
| Held out from | The tuning in #102, which used only `gin-realworld` misses. Both repositories appear in this project's development corpus from earlier blind passes, so they are held out from *this change*, not unseen by the project |
| Model | Every arm pinned explicitly; the model actually used is read from each run's transcript metadata. If the six arms did not all run on one model, the comparison is void |
| Guided | `skills/backend-performance-review` at `e815b98` (main after #102), copied in isolation with the review schemas only |
| Plain | The exact prompt in [`../ab-comparison.md`](../ab-comparison.md) §1 |
| Isolation | Neither arm may read this repository. The guided copy contains no ground truth, results, or evaluation documents |
| Order | Sequential, alternating arms: node P1, node G1, spring P, spring G, node P2, node G2 |

## What is measured

- **Breadth:** distinct real issues per run, adjudicated against source with the rules used in
  the second comparison — credited only when reported as a finding; issues left in
  `considered_not_reported`, mentioned as context, or filed only as correctness are not credited.
  A claim checked and found not to be a real performance issue is rejected for either arm.
- **Discipline:** unsourced performance estimates (every unit-bearing number extracted with
  `score.py`'s pattern and adjudicated by hand), and hits on the `forbidden` trap recorded in
  `node-express-realworld-prisma-postgres.json`.

## Decision rules, fixed now

- **Breadth recovered** if the guided arm's mean count of distinct real issues is **at least 75%**
  of the plain arm's.
- **Discipline held** if the guided arm makes **no** unsourced performance estimate and **no**
  `forbidden`-trap hit.
- Phase 2 is met only if **both** hold. Breadth recovered at the cost of discipline counts as a
  failure, not a success.

n = 3 per arm. Directional, not a general result. Reported with the same prominence either way.

---

## Results

*Not yet run.*
