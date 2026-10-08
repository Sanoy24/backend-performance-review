# Third A/B comparison — breadth recovery, on repositories it was not tuned on

**Status: run and adjudicated, 2026-10-08.** The design below was committed before any run
started (#103); the results were appended afterwards without editing it, apart from this
status line.

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

**Phase 2 is not met.** Breadth recovered, and the guided arm invented no performance figures,
but both guided runs on the Node repository reported the finding its `forbidden` trap says to
decline. Under the rule fixed in advance, breadth gained alongside a trap hit counts as failure.

| Criterion | Result | |
|:--|:--|:-:|
| Guided breadth at least 75% of plain | **87%** (11.0 vs 12.7 per run) | met |
| No unsourced performance estimate from the guided arm | **0** (plain arm: 5) | met |
| No `forbidden`-trap hit from the guided arm | **2 of 2** Node guided runs reported it | **not met** (strict reading) |

### The model was held fixed

All six runs used `claude-opus-5-5` on every API call, read from each run's transcript metadata.

### Breadth: distinct real issues per run

Adjudicated with the second comparison's rules: credited only when reported as a finding, and
rejected for either arm when a claim was checked and found not to be a real performance issue.

| Run | Plain | Guided |
|:--|--:|--:|
| `node-express` #1 | 13 | 9 |
| `node-express` #2 | 12 | 11 |
| `spring-boot` | 13 | 13 |
| **Mean** | **12.7** | **11.0** (87%) |

On Spring Boot the arms tied, each catching two real issues the other missed.

| Only the guided run | Only the plain run |
|:--|:--|
| `default-statement-timeout=3000` is in seconds — about 50 minutes, so no timeout at all. The plain run read it as milliseconds and wrote "reasonable as a guard. Keep it." | The JWT parser is rebuilt on every request |
| Deleting an article leaves orphaned rows that grow without bound (the plain run noted this only under correctness) | The favorite-count queries join `articles` unnecessarily |

The guided arm caught the orphaned rows through the new "growth over time" sweep area, but missed
the rebuilt parser despite the new "fixed work on every request" area.

**Rejected claims**, for whichever arm made them:
- *JWT filter running twice.* Refuted: the filter extends `OncePerRequestFilter`, which skips a
  second invocation in the same request. A guided run examined this and correctly discarded it.
  The remaining single indexed user lookup per authenticated request was not counted for either
  arm, as in the second comparison.
- *An ineffective cache flag* and *`select *` on single-row lookups* (plain): a setting and an
  idiom with no current cost.
- *`count(1)` instead of `exists`* (plain). For favorites the pair is a primary key, so at most
  one row matches. `follows` has no key or index at all, so the lookup's real cost is that
  missing index — already counted under indexes for both arms — not the choice of `count`.
- *`NODE_ENV` not set to production, static files mounted after the router* (plain): the app is a
  JSON API with no views, where production mode's gains do not apply.

### Discipline

| | Plain | Guided |
|:--|:-:|:-:|
| Unsourced performance estimates | **5** across 3 runs | **0** |
| Publishable machine output (`validate_review.py`) | — | **2 of 3** |

The plain arm's five: compression gains "often 5–10×" and "typically 70–85% smaller", bcrypt
"roughly 50–150 ms" and "about 50–100 ms" of CPU, and query counts cut "by about 50–70%". The
guided arm's numbers traced to the repositories (the 3000 s timeout, seed sizes, a page-size
bound of 1000), to labelled arithmetic ("3000 s ÷ 60 = 50 minutes"), or, in one case, to an
uncited library default (body-parser's 100 kb limit).

The unpublishable guided review (`spring-boot`) ordered one workload question out of decision
order — a format slip, not a reasoning one. The agent believed the file valid because it wrote
its own checker: the project's validator lives at the repository root, outside the skill, so an
installed skill cannot run it.

### The trap, and why both readings are recorded

`node-express-realworld-prisma-postgres.json` `GT-F01` forbids reporting that the service runs as a
single process, because doing so requires assuming the host's core count, and says "the correct
response is to decline the finding, not to report it."

- **Both guided runs reported it** (`PERF-009`), so under the trap as written they hit it. That is
  the reading this verdict uses.
- **Neither invented a core count.** Both made the finding explicitly conditional — "Only if
  production gives one container multiple cores … unknown"; "neither is evidenced in the
  repository" — at Low/Medium and Medium/Low.
- **Both plain runs hit it harder,** recommending clustering or more instances with no condition.
- **The trap predates the evidence now in the repository.** It was annotated against an earlier,
  unpinned state with no infrastructure files; the pinned commit has a `Dockerfile` whose
  `CMD [ "node", "api" ]` shows a single process directly. The core count is still unknown.

The trap and #102's disposition rule pull in opposite directions. #102 says a real, reachable
mechanism is promoted even when minor; this one is real, but whether it costs anything depends
entirely on a deployment fact the code does not contain. That conflict is not resolved here:
changing either rule after seeing this result would be fitting the rule to the outcome. It has to
be decided, with a reason written down, before the next comparison.

### What this does not establish

- **That #102 caused the breadth gain.** The second comparison measured 69% on different
  repositories. Separating the change's effect from the repositories' difficulty would need the
  previous skill run on these same repositories.
- **Anything general.** n = 3 per arm, two repositories, one model.

Raw output for every run is in [`raw-third/`](raw-third/).

---

## Decision recorded after this run (2026-10-08)

*Appended after the results; nothing above was changed.*

The trap/rule conflict is decided in favour of the trap's intent: **a mechanism whose cost
depends entirely on a fact the repository does not contain becomes a decision-changing workload
question, never a finding.** One process per container is the defining example: it costs
something only if the container has more than one core, and no file says whether it does.

The reason: the project's first rule is that nothing in a review rests on a fact the reviewer
does not have. A carefully conditioned finding still presents a problem as present in the code,
when what the code actually supports is a question. The breadth rule from #102 stands for
everything the code itself shows; this carves out only the case where the deciding fact is absent.
If a manifest, deployment file, or supplied measurement states the fact, the mechanism is judged
on that evidence like any other.

This does not change this comparison's verdict, which stands as recorded. It is the rule the
next comparison will be held to.
