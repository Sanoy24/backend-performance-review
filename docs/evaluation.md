# Evaluation

How this skill is tested, and what has actually been tested so far.

The distinction matters. A skill whose strongest rule is "never assert what you have not
verified" cannot exempt itself from that rule, so this page separates **checks that have been
run** from **checks that are specified but not yet executed**.

---

## Status

| Category | Status |
|:--|:--|
| Architecture self-check | **Run** — passing as of v0.1.0 |
| Tooling checks | **Run** — passing as of v0.1.0 |
| Behavioral evaluation against public repositories | **Specified, not yet executed** |

Behavioral evaluation is the real test. Until it has been run, the honest claim about this
skill is that it is *internally consistent and structurally sound*, not that it is *proven to
produce good reviews*. That is stated in the README's limitations section as well.

---

## 1. Architecture self-check

Cheap, mechanical, run every release.

| Check | Method | v0.1.0 |
|:--|:--|:--|
| Registry parses; every entry has `load` and `tier` | `parse_registry()` over `registry.yaml` | Pass — 37 entries |
| Every referenced file exists | Path check against each `load` entry | Pass — 0 unresolved |
| Category and principle files name no products | `grep -riE 'postgres\|mysql\|mongodb\|redis\|cassandra\|dynamodb\|neo4j\|elasticsearch\|kafka'` over `databases/`, `principles/`, `runtimes/` | Pass — 0 matches |
| Every technology file has all seven sections | Read | Pass — 3 files |
| Category file ordered before technology file in every `load` | Read registry | Pass |
| README support table matches the registry | Compare | Pass — 3 deep / 24 conceptual / 10 generic |
| No reference file grossly exceeds the ~400 line soft cap | `wc -l` | Pass |
| Examples are outside `skills/` | Path check | Pass |
| Priority matrix internally consistent across `SKILL.md`, `rubrics.md`, README | Read | Pass |

Commands for the mechanical subset are in `CONTRIBUTING.md` §9.

### Questions the self-check is really asking

- Is the methodology genuinely technology-agnostic, or has an engine leaked into it?
- Are technology concerns isolated, or is a technology file restating its category?
- Does adding a database require only a file plus a registry row?
- Does every reference file have a distinct purpose, or do two overlap?
- Do the rubrics produce reproducible priorities?

---

## 2. Tooling checks

| Check | v0.1.0 |
|:--|:--|
| `detect_stack.py` runs on Python 3 with stdlib only | Pass — verified on 3.13 |
| Emits valid JSON | Pass |
| Correctly detects a Python + FastAPI + Postgres + Redis + Celery + Docker + Kubernetes repository | Pass — all seven signals, correct tiers |
| Resolves references in category-before-technology order | Pass |
| **Does not read files matching secret patterns** | Pass — a `.env` containing `postgresql://user:...@db/app` was reported as present, and the `postgresql://` string did not appear in match evidence, confirming the file was never read |
| Reports partial scans in `warnings` rather than failing silently | Pass — budget and file-count caps |

The secret-handling check is the one worth re-running on every change to the scanner. The
script is intended to be safe to point at an unfamiliar production repository.

---

## 3. Behavioral evaluation — the real test

**Not yet executed.** This section is the specification.

Reasoning through hypothetical scenarios in context is not evidence. The skill must be run
against real repositories and its output scored.

### Method

Select 4–6 **public** repositories. Run the skill on each. Score the output.

### Scoring

| Criterion | What it detects |
|:--|:--|
| Stack detected correctly | Discovery works on real layouts, not just tidy ones |
| Correct references loaded, and no others | Routing works; context is not wasted |
| Known issue found | The methodology finds real things |
| **No number invented** | The single most important criterion. Any unsourced figure is a failure regardless of how good the rest is |
| No cargo-cult recommendation | The anti-pattern rules hold under pressure |
| Validation plan present and executable | Recommendations are falsifiable |
| Unknowns reported honestly | Confidence caps are respected |
| Priorities match the matrix | Rubrics are applied, not bypassed |

### Required cases

Three of these are chosen specifically to catch failure modes that a "find the bug" test
cannot:

1. **A repository with a known, documented performance issue.** Does it find it, and rank it
   appropriately?
2. **A repository with no significant performance problem.** *The most important case.* Does
   the skill correctly return few or zero findings and deliver unknowns plus a measurement
   plan — or does it manufacture findings to fill the report? A skill that cannot stay quiet
   is not usable at scale, because every review becomes noise.
3. **A repository on a `generic`-tier stack.** Does it degrade gracefully — category
   inference, universal principles, explicit unknowns — or does it fabricate engine behavior?
4. **A repository with runtime evidence committed** (benchmarks, load tests, query plans).
   Does it find and use them, and does it correctly reach `Confirmed` confidence with a
   citation?
5. **A change-scoped review of a real pull request.** Does it stay scoped, or does it review
   the whole repository anyway?
6. **A repository with contradictory configuration** — pool size inconsistent with worker
   count, or heap larger than the container limit. Does it do the arithmetic?

Case 6 is the cheapest high-value check: the answer is objectively verifiable from files, so
there is a right answer to compare against.

### Recording results

For each run, record: repository and commit, mode, references loaded, findings with scores,
any fabrication, any cargo-cult recommendation, and time or token cost. Publish the summary
here. Failures are more interesting than successes and should be recorded in at least as much
detail.

Where skill-evaluation tooling is available in the target agent environment, these cases
should be wired up as an eval suite so they run on every change to the methodology.

---

## 4. Regression protection

Once behavioral evaluation exists, the highest-value guard is a **false-positive corpus**: for
every reported false positive, add the triggering code shape to a fixture set and confirm the
skill no longer reports it.

False positives are the failure mode that erodes trust fastest. A reviewer who reads three
irrelevant findings stops reading the fourth, and the one that mattered was the fourth.

---

## 5. Known evaluation gaps

Stated rather than left implicit:

- No inter-run consistency measurement. The same repository reviewed twice may produce
  different findings; the rubrics are designed to make rankings reproducible, but that has not
  been measured.
- No comparison against a human expert baseline.
- No measurement of context cost per review, which matters for whether the routing design is
  actually paying off.
- No evaluation of the change-scoped mode's scoping accuracy.

Contributions that close any of these are welcome, and are worth more than additional
reference content.
