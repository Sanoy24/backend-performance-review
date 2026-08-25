# Evaluation

How this skill is tested, and what has actually been tested so far.

The distinction matters. A skill whose strongest rule is "never assert what you have not
verified" cannot exempt itself from that rule, so this page separates **checks that have been
run** from **checks that are specified but not yet executed**.

---

## Status

| Category | Status |
|:--|:--|
| Architecture self-check | **Automated in CI** — `scripts/check_repo_invariants.py`, every push/PR |
| Tooling checks | **Run** — passing as of v0.1.0 |
| Behavioral evaluation against public repositories | **Round 1 run** — 2 of 6 required cases covered; 3 real bugs found and fixed |

Behavioral evaluation is the real test, and it has now produced its first real result: running
`detect_stack.py` against two unmodified public repositories found three genuine bugs — two in
`registry.yaml`'s match tokens and one in the parser itself — none of which the architecture
self-check could have caught, because that check only verifies the registry is internally
consistent, not that it matches real files correctly. Round 1 is partial: it covers 2 of the 6
required cases from §3 below, is not blind (the same person who wrote the skill selected the
repositories and read the code), and used manual reasoning rather than an actual skill
invocation end-to-end. The remaining cases, and a truly independent run, are open work — see
§3.4.

---

## 1. Architecture self-check

Automated: `scripts/check_repo_invariants.py`, run on every push and pull request by
`.github/workflows/checks.yml`. It replaced a set of manual, ad-hoc commands that lived
directly in `CONTRIBUTING.md` — consolidating them into one real, mutation-tested script
closed the gap where those commands could silently drift out of sync with the invariants they
were meant to check.

| Check | v0.1.0 |
|:--|:--|
| Registry parses; every entry has `load` and `tier` | Pass — 37 entries |
| Every referenced file exists | Pass — 0 unresolved |
| Database category files name no specific products | Pass — 0 matches, scoped to `databases/*.md` per the non-derivable-content rule |
| Every technology file has all seven required sections | Pass — 3 files |
| Category file ordered before technology file in every `load` | Pass |
| Published tier summary matches the registry's actual counts | Pass — 3 deep / 24 conceptual / 10 generic |
| No reference file grossly exceeds the ~400 line soft cap | Pass |
| Examples are outside `skills/` | Pass |
| Priority matrix identical in `SKILL.md` and `README.md` | Pass |
| `detect_stack.py` imports only the standard library | Pass — verified by AST inspection, and separately by running it under a bare Python 3.8 with `-I` |

The checker was mutation-tested before being trusted: four deliberate regressions (a leaked
product name, a mismatched priority matrix, reversed registry load order, a missing technology
section) were each introduced into a scratch copy of the repository and confirmed to produce
the expected failure, then reverted.

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

Reasoning through hypothetical scenarios in context is not evidence. The skill must be run
against real repositories and its output scored. This section specifies the method, then
reports Round 1's actual results against two real, unmodified public repositories.

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
cannot. **Status reflects Round 1** (§3.1–§3.3); unmarked cases are still open.

1. **A repository with a known, documented performance issue.** Does it find it, and rank it
   appropriately? — **Partially covered.** Round 1 found a real, verified N+1 in
   `gin-realworld` (§3.1), but by manual code tracing, not by an externally documented issue
   report. Treat this as "a real bug found by the methodology," not "a known issue confirmed."
2. **A repository with no significant performance problem.** *The most important case.* Does
   the skill correctly return few or zero findings and deliver unknowns plus a measurement
   plan — or does it manufacture findings to fill the report? A skill that cannot stay quiet
   is not usable at scale, because every review becomes noise. — **Not covered.** Both Round 1
   repositories had at least one real finding. Neither is a clean test of restraint. Open.
3. **A repository on a `generic`-tier stack.** Does it degrade gracefully — category
   inference, universal principles, explicit unknowns — or does it fabricate engine behavior?
   — **Covered, adjacent finding.** `gin-realworld` is Go (conceptual) + SQLite (conceptual) —
   no `deep`-tier engine at all — and the review correctly reasoned about it without fabricating
   engine-specific facts. See §3.1.
4. **A repository with runtime evidence committed** (benchmarks, load tests, query plans). —
   **Not covered.** Neither repository had any. Open.
5. **A change-scoped review of a real pull request.** — **Not covered.** Both runs were full
   reviews. Open.
6. **A repository with contradictory configuration** — pool size inconsistent with worker
   count, or heap larger than the container limit. Does it do the arithmetic? — **Checked and
   found no contradiction** in either repository (§3.1, §3.2) — a real check with a genuine
   negative result, not a skipped one.

Case 6 is the cheapest high-value check: the answer is objectively verifiable from files, so
there is a right answer to compare against.

### 3.1 Round 1, repo A — `gothinkster/golang-gin-realworld-example-app`

Commit `626c372d259472148d93303f74aa9b9a1cdcef24`, cloned unmodified. Go + Gin + GORM +
SQLite — no `deep`-tier engine, chosen specifically to exercise case 3.

**Detection.** `detect_stack.py` correctly identified SQLite (relational, conceptual), Go
(conceptual), and — before the fixes below — two false positives: a `task-queue` broker
signal and a spurious extra `rest` match token. References loaded were exactly the category
and application files appropriate to that stack; no document-store, no graph, no message-
broker methodology beyond what the false positive pulled in.

**Bugs found (all fixed, see §3.3):**

- The `task-queue` signal fired on the bare token `rq`, which matched inside a base64 hash
  fragment in `go.sum` (`h1:hxrqLVvrK65+...`) — nothing to do with a task queue.
- The `rest` signal's `koa` token matched case-insensitively inside another `go.sum` hash
  fragment (`...kOa...`).

**A real N+1, found by tracing the actual call graph, not by pattern-matching a method name.**
`ArticleModel.favoritesCount()` and `.isFavoriteBy()` each issue a query and look, in
isolation, like the canonical N+1 shape. Reading only that far would have been a false
positive: `ArticlesSerializer.Response()` (`articles/serializers.go:115-139`) already batches
favorite counts and status with `BatchGetFavoriteCounts`/`BatchGetFavoriteStatus` *before* its
per-article loop, and calls `ResponseWithPreloaded`, not the naive `Response()`. **That half is
correctly not a finding** — a shallower review would have reported it anyway and been wrong.

Tracing one level deeper found the real one: each article's author is rendered through
`ArticleUserSerializer.Response()` → `users.ProfileSerializer.Response()`
(`users/serializers.go:24-40`), which calls `myUserModel.isFollowing(self.UserModel)`
(`users/models.go:121-129`) — and that method issues its own `db.Where(...).First(...)` query,
**once per article, per request**, on both `GET /articles` and `GET /articles/feed`
(`articles/routers.go:61-85`). The favorite-count N+1 was fixed by a previous contributor; the
structurally identical follow-status N+1 one layer down was not. This is exactly the shape
`application/data-access.md` §2 and §10 warn about — the caller trace has to go through the
serializer, not stop at the handler.

- Severity: position critical-path, frequency per-article-per-request, growth O(n) in page
  size, blast radius system-wide (shares the connection pool with every other request). →
  **High**.
- Confidence: **High** — the call chain is unambiguous from the code; no workload assumption
  is load-bearing for the *existence* of the extra query, only for how much it matters.
- Priority: **P1** per the matrix (High severity × High confidence).

**Config arithmetic (case 6), checked, no contradiction found.** `common/database.go` sets
`SetMaxIdleConns(10)` in `Init()` (production) and `SetMaxIdleConns(3)` in `TestDBInit()`
(tests) — different values for different contexts, not an inconsistency. `SetMaxOpenConns` is
never set in either. `application/connection-pools.md`'s arithmetic assumes a networked engine
with a server-side connection ceiling; SQLite is an embedded, file-backed, single-writer engine
with no such ceiling to exceed, so the arithmetic does not transfer and reporting it as a
pool-sizing finding would have been a category error. Correctly not reported.

### 3.2 Round 1, repo B — `fastapi/full-stack-fastapi-template`

Commit `8063fe54f17d19f01720e055103af9cad3d8f55d`, cloned unmodified. Python + FastAPI +
SQLModel + PostgreSQL + Docker, chosen to exercise the `deep`-tier Postgres path and a
full-stack monorepo (frontend + backend in one repo).

**Detection, and the third bug.** Postgres detection was correct and precise (deep tier,
matched on `psycopg`/`postgresql://` etc.). But `node` also fired, matched only on
`package.json` and `node_modules` — which exist because this repo has a `frontend/` directory.
**For a backend performance review, that is a false attribution of the backend runtime**, not
merely a harmless extra match: those two tokens cannot distinguish a Node.js backend from
front-end tooling sitting in the same repository. Separately, `gin-gonic/gin` and
`labstack/echo` — Go web framework names — matched the `rest` signal via the bare words `gin`
and `echo`: `gin` inside the English word "logging" in `compose.override.yml`, and `echo`
inside ordinary shell `echo` commands in CI workflow YAML. Neither Go framework is anywhere in
this Python/TypeScript repository.

**A fourth finding, unrelated to the bugs: independent confirmation of the pagination pattern.**
`GET /items` (`backend/app/api/routes/items.py:13-25`) declares `limit: int = 100` with no
`Query(le=...)` upper bound. A default is not an enforced maximum — a caller can request an
arbitrarily large page. This is the identical pattern documented in
`docs/examples/fastapi-postgres.md`'s PERF-001-adjacent reasoning and in
`application/api.md` §1, found here independently in a real, actively maintained template
repository with no connection to this project. That is a meaningfully stronger validation of
the pattern's real-world frequency than either the synthetic example or a single occurrence
would be.

- Severity: critical-path, per-request, O(n) in table size with no cap, blast radius limited to
  this endpoint (no evidence of a shared-resource interaction here). → **Medium**.
- Confidence: **High** — the missing bound is a property of the route declaration.
- Priority: **P2**.

**Config arithmetic (case 6), checked, no contradiction found.** A `Dockerfile` and
`compose.yml` exist; no explicit container memory limit or application-side pool-size setting
was found in the scanned files, so there was nothing to check the arithmetic against. Recorded
as an unknown, not asserted as fine — the absence of a contradiction is not the same claim as
the absence of a limit.

### 3.3 Bugs found, and the fixes applied

Both of the following were discovered *by* the evaluation the architecture self-check could not
have caught, because both are about whether the tool matches real files correctly, not whether
the registry is internally well-formed:

1. **Registry match-token collisions** (`registry.yaml`). Three tokens were short or common
   enough to match unrelated content: `rq` and `koa` inside base64 hash fragments in lockfiles,
   and `gin`/`echo` inside ordinary English words and shell commands. Fixed by requiring the
   fully-qualified module path for the Go frameworks (`gin-gonic/gin`, `labstack/echo`,
   `gofiber/fiber`), narrowing the task-queue signal to `django-rq`/`python-rq`, and quoting
   `koa` as `"koa":` to require the exact dependency-declaration shape. The `node` signal's
   `package.json`/`node_modules` ambiguity in full-stack repos was not removed — doing so would
   lose real detection value — but is now flagged in the registry's `notes` field, which
   surfaces in the report's scope section.
2. **The registry parser silently dropped escaped quotes** (`scripts/detect_stack.py`,
   `_strip_quotes`). A token written as `"\"node\":"` — intended to match the literal text
   `"node":` in a `package.json` — parsed to the four literal characters `\"node\":` (with the
   backslashes preserved), which never matches anything in a real file. **This token had been
   silently dead since v0.1.0**; nothing exercised it because the `node` signal's other, broader
   tokens always fired alongside it, masking the failure. Fixed by unescaping `\"` to `"` after
   stripping the outer quotes. Re-verified: the corrected token now parses to the exact literal
   `"node":`, and the same fix made the newly-added `"koa":` token work correctly on the first
   try rather than needing its own follow-up fix.

Bug 2 is the more important of the two: bug 1 produced visible, self-correcting noise (a wrong
detection you'd notice); bug 2 produced invisible under-detection (a signal that looked present
in the registry but could never fire), which is a worse failure mode precisely because nothing
about the output made it apparent.

### 3.4 What Round 1 does not establish

Stated plainly, matching the discipline this project asks of its own findings:

- **This was not a blind evaluation.** The same person who authored the skill selected the
  repositories, read the code, and wrote up the results. An evaluator unfamiliar with the
  skill's internals, or an independent agent given only `SKILL.md` and told to review these
  repositories, would be a stronger test and has not been run.
- **The full skill workflow was not invoked end-to-end.** `detect_stack.py` was run for real;
  the subsequent review reasoning was performed manually against the same evidence-first
  standard the methodology requires, not by dispatching the actual `SKILL.md` procedure to an
  agent and grading its output.
- **Cases 2, 4, and 5 remain open** — no-problem repo, committed-benchmark repo, change-scoped
  PR review. Case 2 in particular is the highest-priority gap: it is the one case that tests
  whether the skill can stay quiet, and nothing in Round 1 tests it.
- **Two repositories is a small sample.** Both are Round 1, not a completed evaluation.

### Recording results

For each run, record: repository and commit, mode, references loaded, findings with scores,
any fabrication, any cargo-cult recommendation, and time or token cost. Failures are more
interesting than successes and should be recorded in at least as much detail — §3.3 is the
template for that.

Where skill-evaluation tooling is available in the target agent environment, these cases
should be wired up as an eval suite so they run on every change to the methodology.

---

## 4. Regression protection

The highest-value guard is a **false-positive corpus**: for every reported false positive, add
the triggering code shape to a fixture set and confirm the skill no longer reports it. Round 1
(§3.3) produced the first three entries:

1. A lockfile hash fragment containing the substring `rq` must not trigger the `task-queue`
   signal (regression test for the `go.sum` base64-collision bug).
2. A CI YAML file using shell `echo` commands, and a compose file containing the word
   "logging", must not trigger the `rest` signal's Go-framework detection (regression test for
   the `gin`/`echo` bare-word collision).
3. A quoted registry match token containing an escaped inner quote (`"\"x\":"`) must parse to
   the literal `"x":`, not to the four characters `\"x\":` (regression test for the parser's
   `_strip_quotes` unescaping bug).

None of these three has an automated test yet — they are recorded here as the specification for
one. Adding it is a small, well-scoped contribution: three fixture snippets and an assertion
against `parse_registry()` / `detect()`.

False positives are the failure mode that erodes trust fastest. A reviewer who reads three
irrelevant findings stops reading the fourth, and the one that mattered was the fourth. Bug 2 in
§3.3 is the sharper warning: a false *negative* (a signal that silently never fires) produces no
irrelevant output to notice at all, and is caught only by deliberately checking that a claimed
detection capability actually detects something.

---

## 5. Known evaluation gaps

Stated rather than left implicit:

- **Case 2 (no significant problem) is untested.** The single highest-priority gap — see §3.4.
- **Cases 4 and 5 are untested** — a repository with committed runtime evidence, and a
  change-scoped PR review.
- **Round 1 was not blind.** The author selected the repositories and read the code; an
  independent reviewer, or an agent given only `SKILL.md`, has not been tried.
- **Round 1 tested detection and manual analysis, not the full skill invocation end-to-end.**
- No inter-run consistency measurement. The same repository reviewed twice may produce
  different findings; the rubrics are designed to make rankings reproducible, but that has not
  been measured.
- No comparison against a human expert baseline.
- No measurement of context cost per review, which matters for whether the routing design is
  actually paying off.
- No automated regression test yet for the three fixture cases in §4.

Contributions that close any of these are welcome, and are worth more than additional
reference content.
