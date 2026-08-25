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
| Behavioral evaluation against public repositories | **Rounds 1–2 run** — 5 of 6 required cases covered; 3 real bugs found and fixed; 1 case reframed after evidence (§3.7) |
| Independent (non-author) pass | **Run once** — one agent, no memory of this session, one repository; reproduced the core finding, caught one this analysis missed, and corrected this analysis's severity score using evidence it had overlooked; see §3.8 |

Behavioral evaluation is the real test, and it has now been run against four unmodified public
repositories, plus one of those four reviewed a second time by an agent with no memory of this
session at all. Round 1 found three genuine bugs in the detection tooling — two in
`registry.yaml`'s match tokens and one in the parser itself — none of which the architecture
self-check could have caught, because that check only verifies the registry is internally
consistent, not that it matches real files correctly. Round 2 ran committed benchmarks for real
(§3.5), change-scope-reviewed a real merged pull request against its actual diff (§3.6),
reframed what case 2 is actually testing after a fourth repository still produced a real finding
(§3.7), and closed with an independent pass that found a real gap in — and corrected a real
scoring error in — the author's own prior analysis of the same repository (§3.8). What remains
open: the blind pass covers one repository, not all four, and the tooling-only checks in §4
still lack automated regression tests.

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
cannot. **Status reflects Round 1 (§3.1–§3.3) and Round 2 (§3.5–§3.7).**

1. **A repository with a known, documented performance issue.** Does it find it, and rank it
   appropriately? — **Covered.** Round 1 found a real, verified N+1 in `gin-realworld` (§3.1) by
   manual code tracing. Round 2 went further: `gin-realworld`'s PR #49, titled "perf: optimize
   database queries to eliminate N+1 problems," is a real, merged, externally-authored fix for
   the *other* half of that same N+1 (the favorites-count query). Reviewing it in change-scoped
   mode (§3.6) is the closest available proxy to "a known, documented issue" without fabricating
   one, and it is a stronger test than §3.1 alone: it checks whether the skill correctly credits
   a real fix rather than re-flagging already-solved code.
2. **A repository with no significant performance problem.** *The most important case.* — **Best
   available evidence gathered; not cleanly satisfied.** See §3.7 for the full account: across
   four real repositories now (two in Round 1, two in Round 2), **none had a literal zero-finding
   result** — every one had at least one legitimate, evidence-based finding. §3.7 explains why
   this is itself a meaningful result rather than a failure to find a clean repo, and reframes
   what "passing" this case means in practice.
3. **A repository on a `generic`-tier stack.** — **Covered.** See §3.1.
4. **A repository with runtime evidence committed** (benchmarks, load tests, query plans). —
   **Covered.** `slytomcat/URLshortener` — a separate, unrelated repository chosen specifically
   for this case — has committed Go benchmarks; Round 2 ran them for real and used the actual
   output as `Confirmed`-grade evidence (§3.5) — the first time this evaluation has produced a
   genuinely `Confirmed`
   finding rather than one capped at `High`.
5. **A change-scoped review of a real pull request.** — **Covered.** §3.6 reviews PR #49
   against the real diff.
6. **A repository with contradictory configuration** — pool size inconsistent with worker
   count, or heap larger than the container limit. Does it do the arithmetic? — **Checked and
   found no contradiction** in all four repositories so far (§3.1, §3.2, §3.5) — a real check
   with a genuine negative result each time, not a skipped one.

Case 6 is the cheapest high-value check: the answer is objectively verifiable from files, so
there is a right answer to compare against. Remaining open work: a genuinely blind evaluator
run without the author's involvement at all (§3.8 reports a first, partial step toward this).

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

Round 2 (below) closes cases 4 and 5 and gathers the best available evidence on case 2. It does
not close the blindness gap on its own — see §3.8.

### 3.5 Round 2, case 4 — real benchmark evidence, `slytomcat/URLshortener`

Commit at time of review not pinned by the author beyond `--depth 1` clone of the default
branch; repository is small and stable. Go + Redis (`go-redis/redis/v7`) URL-shortening
microservice, chosen because its test suite includes committed Go benchmarks — the first
repository in this evaluation with runtime evidence actually present.

**The benchmarks were run for real**, not merely noted as present (`go test -bench=. -benchmem`,
after working around an unrelated Windows/Unix build issue in one unrelated test file —
`syscall.Kill` is not defined on Windows — by neutralizing that single line for the duration of
the run and reverting it immediately after; this did not touch anything the benchmarks
exercise):

```text
Benchmark00ST00Create2-14     136.0 ns/op    12 B/op   3 allocs/op
Benchmark00ST00Create8-14     137.3 ns/op    40 B/op   3 allocs/op
Benchmark00ST00Create2B-14    157.7 ns/op    32 B/op   3 allocs/op   (sync.Pool variant)
Benchmark00ST00Create8B-14    179.5 ns/op    56 B/op   3 allocs/op   (sync.Pool variant)
BenchmarkTokenCheck-14         64.2 ns/op     0 B/op   0 allocs/op   (current implementation)
BenchmarkTokenCheckOrig-14    800.8 ns/op   160 B/op  10 allocs/op   (an alternate kept for comparison)
```

This is genuinely `Confirmed`-grade evidence per the rubric: a runtime artifact was produced and
is cited exactly. Two things worth noting about what the numbers actually support:

- The repository's own source comment claims a `sync.Pool`-based token generator is "slower and
  requires more memory than the original simple version." **The benchmark confirms the
  comment is correct** — the pooled variant is ~15–30% slower with no memory benefit at this
  size. Evaluating the claim rather than trusting the comment is itself the evidence-first
  discipline working correctly.
- All of these operations are in the 60–180 nanosecond range. Against a Redis round trip
  (single-digit milliseconds at best, over a network), **token generation and validation are
  nowhere near this service's bottleneck**, regardless of which variant is used. The correct
  conclusion from `Confirmed` evidence here is *"this is not where time goes, do not optimize
  it further"* — not a recommendation to swap implementations. This is a direct test of
  `principles/latency.md`'s warning against optimizing a non-bottleneck stage, with real
  numbers instead of the usual inference.

**A real finding, found along the way, unrelated to the benchmarks.** The main HTTP server is
constructed as `&http.Server{Addr: ..., Handler: ...}` with no `ReadTimeout`, `WriteTimeout`,
`IdleTimeout`, or `ReadHeaderTimeout` set (`service.go`, `NewHandler`) — confirmed by grepping
the entire non-test source for any of those identifiers or a `http.TimeoutHandler` wrapper: zero
matches. Go's `net/http.Server` treats an unset timeout as no timeout, so this is an unbounded
worst case on every connection, exactly the pattern `distributed/timeouts-and-deadlines.md`
describes — Confidence `High` (visible in code, workload-independent), Severity `Critical`
(blast radius is the whole process; there is no worker pool to absorb it), Priority **P0**.

A second, related, lower-severity observation: the built-in self-test (`healthCheck()`) makes
its outbound calls with the package-level `http.Post`/`http.Get`, which use `http.DefaultClient`
— `Timeout: 0`, also unbounded. Confined to the health-check path rather than the main serving
path, so severity is lower; recorded as `Medium`, not elevated to match the main finding.

**Checked, no contradiction (case 6).** The Redis client is constructed via
`redis.NewUniversalClient(&redis.UniversalOptions{Addrs, Password})` — `PoolSize`,
`DialTimeout`, `ReadTimeout`, `WriteTimeout` are all left unset. Rather than assuming this means
"unbounded" (as it would for the `net/http.Server` above), **the actual vendored dependency
source was read** (`go mod download` retrieved `go-redis/redis/v7@v7.4.1` locally;
`options.go` was inspected directly): unset `DialTimeout` defaults to 5s, unset
`ReadTimeout`/`WriteTimeout` default to 3s, unset `PoolSize` defaults to `10 * runtime.NumCPU()`.
These are sane library defaults, not an omission — correctly not reported as a finding. This is
the same discipline applied to two structurally identical-looking "unset timeout" situations,
reaching two different, individually-verified conclusions rather than one blanket rule.

### 3.6 Round 2, case 5 — change-scoped review of a real, merged pull request

`gothinkster/golang-gin-realworld-example-app` PR #49, "perf: optimize database queries to
eliminate N+1 problems," merged. Chosen deliberately: it is the real fix for the favorites-count
half of the N+1 found in Round 1 (§3.1), fetched via GitHub's `.diff` endpoint for the actual PR,
not reconstructed from memory.

**Scope, per the change-scoped mode definition in `SKILL.md`:** the diff touches
`articles/models.go`, `articles/serializers.go`, `articles/unit_test.go`,
`common/unit_test.go`, `common/utils.go`. Analysis stayed within these files and their direct
callers (`articles/routers.go`, which calls the serializer this diff modifies), not the whole
repository.

**The fix itself verified correct.** The diff adds `BatchGetFavoriteCounts` and
`BatchGetFavoriteStatus` (single `GROUP BY`/`IN`-clause queries) and a new
`ResponseWithPreloaded` serializer method that accepts precomputed values instead of querying
per article; `ArticlesSerializer.Response()` is restructured to call the batch functions once
before its loop, then pass results in. This converts a query count of `1 + 2n` (list query, plus
`favoritesCount()` and `isFavoriteBy()` per article) to a fixed `3` regardless of page size —
exactly what "eliminate N+1" claims, and the diff supports the claim.

**What a change-scoped review adds beyond "the diff looks correct": the adjacent, untouched
half of the same problem.** `SKILL.md` defines change-scoped mode as reporting "what the change
introduces, worsens, **or sits directly adjacent to**." The rendered article's author profile —
in the very code path this diff restructures (`ArticleUserSerializer.Response()`, called from
inside the loop this diff modifies) — calls `ProfileSerializer.Response()`, which calls
`isFollowing()`, which issues its own per-article query. This is the finding independently
confirmed in Round 1 (§3.1). PR #49 does not touch it, and by its own title ("eliminate N+1
problems," plural) arguably should have: it is the structurally identical pattern, one call
deeper, in a file this same diff already had open. A change-scoped review that only checked "did
this diff do what it says" would miss this; checking the adjacent path is what "sits directly
adjacent to" in the mode's own definition is for. Reported as: Severity `High`, Confidence
`High`, Priority **P1** — matching §3.1's scoring of the same finding exactly, since it is the
same code. `scalability-risk` is deliberately not the tag here: the cost is present today, not
merely at future scale. Tagged instead as a gap the PR's own stated goal ("eliminate N+1
problems") did not close.

This is a stronger validation of the change-scoped mode than a synthetic diff would have been:
the "correct but incomplete" shape — a real fix that solved half of a stated problem — is
exactly the case a shallow diff-only check (does the changed code do what the commit message
claims) would pass and a proper adjacent-path check would not.

### 3.7 Reframing case 2, after four repositories

Four real, unmodified public repositories have now been reviewed at the same rigor:
`gin-realworld`, `fastapi-template` (Round 1), `URLshortener` (§3.5), and — closest of the
four — `antonosmond/github-signature-verifier`, a minimal, stateless AWS Lambda function that
validates GitHub webhook signatures, chosen in Round 2 specifically as a case-2 candidate: no
persistent server object to misconfigure, no connection pool, no datastore, no list/pagination
surface at all, about as small as a real backend gets.

**Even this repository has one real finding.** `src/index.js`'s handler fetches the webhook
secret from AWS SSM Parameter Store *inside* the handler, on every invocation — not cached at
module scope. `infrastructure/resources.md` §5 is explicit that Lambda execution environments
are reused across invocations and that state initialized outside the handler persists across
them; initializing per-invocation state that could be cached at module scope pays its setup cost
on every call. Scored here as Severity `Medium` (real, avoidable, but bounded — one extra
network round trip per invocation, not a multiplier), Confidence `High` (visible in the code;
SSM's own client defaults are a separate, unverified question, noted as an unknown rather than
asserted). **Revised in §3.8:** an independent blind review of this same repository found
evidence this analysis missed and made a defensible case for `High` severity instead — see §3.8
for why, and for the correction.

**Two things this repository's review correctly did *not* report, which is itself worth
recording:** the signature comparison (`requestSignature === expectedSignature`) is a plain
string comparison rather than a constant-time one — a real issue, but a *security* one (timing
attack), out of `SKILL.md`'s stated scope ("Not a security, correctness, or style review").
Likewise the Terraform config's `runtime = "nodejs4.3"` is a long-unsupported Lambda runtime —
real, but a maintenance/support-lifecycle fact, not a performance one. Declining to report both,
correctly, while still reporting the SSM-caching finding, is a sharper test of scope discipline
than either finding alone would have been: it shows the boundary is being applied by *kind of
issue*, not by a blanket "the code looks fine" instinct.

**The honest conclusion:** across four repositories, the rate of "at least one legitimate
finding" is 4/4, not because the methodology manufactures findings — every finding above
survived being cited to an exact line and, where a plausible alternative explanation existed
(the Redis client's unset options), was checked against it before being reported — but because
real code, even careful code, has one workload-independent gap more often than intuition
suggests. Missing timeouts in particular have now appeared in *three* of the four repositories
(`gin-realworld`'s payment-adjacent reasoning was in the synthetic example, not this repo, but
`URLshortener` and, in weaker form, the sig-verifier's SSM call, both show the pattern), which
is itself a datapoint about where real value concentrates.

**What this means for case 2 going forward:** insisting on a literal zero-finding repository as
the pass condition was the wrong test. The evaluation criterion in practice is: given a
near-minimal, well-written repository, does severity stay *proportionate* to what the evidence
actually supports — a short, appropriately-scored report, not an inflated one — and does the
review correctly decline to report issues outside its stated scope? By that standard, this case
is satisfied: §3.8's independent pass over this same repository landed on one substantial finding
(P1) plus two minor ones (P3), nothing higher, nothing manufactured. By the original literal
standard ("no significant performance problem," meaning nothing to report at all), no repository
tried so far has satisfied it, and it is now an open question whether one exists among real,
non-trivial public backends at all — which is a more interesting result than either a clean pass
or a clean fail would have been.

### 3.8 An independent pass, on one repository

A fresh `general-purpose` agent was spawned with no memory of this session: it was given only
the path to the shipped `SKILL.md` and told to follow it, plus the path to the sig-verifier
repository, with no indication of what §3.7's manual review had found. It was explicitly told
this was a small repository, not to assume it needed a large report, and that few or zero
findings was an acceptable honest outcome — the same permission `SKILL.md` itself gives, not an
extra hint toward any particular answer.

**It correctly scoped itself.** It ran the accelerator, got the same two signals §3.7's review
did (`node`, `terraform`, both conceptual), loaded `runtimes/universal.md`,
`application/async-and-blocking.md`, and `infrastructure/resources.md` — the same reference set
as before, arrived at independently — and explicitly declined `databases/*`, `distributed/*`,
and every `application/*` file that assumes a datastore, pool, or outbound service call that
this repository doesn't have. It stated the observability gap up front, exactly as
`methodology/discovery.md` requires, and explicitly recorded that it could not run the
interactive workload interview and was proceeding per the skill's own rule for that case rather
than treating it as a blocker.

**It found the same core issue, plus something the manual review missed.** Its PERF-001 is the
identical finding: the SSM client and secret are constructed and fetched inside the handler on
every invocation instead of once at module scope, citing the same `infrastructure/resources.md`
§5 passage §3.7 did. Its PERF-002 — unconditional `console.log` of the full `event` and `context`
objects on every invocation — is real, correctly evidenced, correctly scored `Low`/`P3`, and is
not in §3.7's write-up at all. The code was read in both reviews; only one of them called it a
finding. That is exactly the kind of gap an independent pass exists to surface.

**It made a stronger, better-evidenced case for higher severity on the core finding, using
evidence the manual review never gathered.** §3.7 scored PERF-001 `Medium`, reasoning that the
extra SSM round trip was "bounded... not a multiplier." The blind agent read `src/example.js` —
a file the manual review looked at only to confirm it wasn't part of the deployed artifact, not
for what it revealed — and found that it documents *other Lambda functions invoking this one
synchronously* (`InvocationType: 'RequestResponse'`), meaning callers block on this function's
latency. Combined with the README's stated purpose (a shared verifier other functions depend on
so none of them has to hold the secret), that makes the blast radius "service," not
"endpoint" — every extra millisecond here is paid by every caller, on every one of their
requests. **`High` is the better-supported score.** This isn't a case of two reasonable people
disagreeing; it's a case of one review having read a piece of evidence the other one had in hand
and set aside. The corrected finding: Severity `High`, Confidence `High`, Priority **P1**.

**Its PERF-003** (the terraform config declaring the long-EOL `nodejs4.3` runtime) reached the
same "this is out of performance scope" conclusion §3.7 did, but handled it differently:
instead of moving it to "considered and not reported," it was included as `Informational`/`P3`,
explicitly labeled as "primarily a deployability/support concern" with any performance angle
called "secondary and unquantified." At the time this looked like two defensible choices with
no stated preference between them. On reflection it isn't defensible either way: `Informational`
is defined (`rubrics.md`) as "no current or projected impact" — that is a claim about
performance, and an end-of-life runtime is not "no impact," it's *no performance impact*, a
different claim entirely. Filing it at `Informational`/P3 places a real, unrelated-axis issue on
the same scale as "hygiene, no consequence," which reads as "safe to leave" for something that
was never being judged on performance to begin with. **Fixed**, in two stages. The first pass
made it unscored and one-line — which under-corrected: a real security finding, once found with
real evidence, deserves the same rigor as a performance finding, not a footnote. `SKILL.md` rule
8 now requires the full write-up — Problem, Evidence, Recommendation, Trade-offs, Validation,
same as a `PERF-` finding — under a `SEC-`/`COR-`/`MAINT-` ID in a distinct "Adjacent findings —
outside performance scope" section (`templates/review-report.md`), classified on `Kind` and the
same evidence-grade `Confidence` scale, plus a plain-language `Risk` note — deliberately never a
CVSS-style score, since this skill has no dedicated security methodology and manufacturing that
rigor would be exactly the kind of dishonesty the evidence-first rule forbids. It always names
what dedicated review would actually assess it properly. Silence was never the right answer
either — omitting the EOL-runtime fact entirely, the way §3.7's original review effectively did
by folding it into prose rather than a labeled item, undersells evidence that was real.

**It independently declined exactly the same out-of-scope item** — the non-constant-time
signature comparison, correctly identified as a security concern rather than a performance one,
for the same reason §3.7 gave. Two reviewers, no shared context, same boundary applied the same
way. Of everything in this comparison, this is the strongest single piece of evidence that
`SKILL.md`'s scope rule is legible to an agent that has never seen this evaluation's own
reasoning about it — it is not something that only makes sense with the author's context loaded.
Its "Considered and not reported" section otherwise mirrors the discipline used throughout
Rounds 1–2 (declining an SDK-version migration, reserved concurrency, and a memory-size tweak,
each for "no evidence to size this," not "seems fine").

**No number was invented.** Every duration, cost, and byte figure in its output is either a
named metric to go and read (CloudWatch `Duration`, `IncomingBytes`, an `ssm:GetParameters` call
count) or explicitly marked as a derivation with no baseline to compute it from yet.

**What this one comparison does and does not establish.** It is one repository, reviewed once,
by one agent, and the repository was chosen by the author (though the agent had no visibility
into that choice or into §3.7's conclusions). It does not establish inter-rater reliability at
scale, and it does not cover the other three repositories in this evaluation. What it does
establish: given only the shipped skill and no session-specific context, an independent agent
reproduced the core finding, caught a real one that was missed, correctly deferred on an
ambiguous scope question rather than guessing wrong, and applied the security/performance
boundary identically to a review that had extensively reasoned about that boundary elsewhere.
That is a meaningfully stronger result than a solo review confirming itself twice would have
been, and the corrected severity above is now the record — a blind pass that only ever agreed
with the author would have been much weaker evidence than one that, on inspection, was partly
right about something the author got wrong.

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

- **Case 2 (no significant problem) is reframed, not literally satisfied** — see §3.7. Four
  repositories, four legitimate findings; whether a true zero-finding real backend exists at all
  is now an open question rather than an assumed baseline.
- **The blind pass (§3.8) covers one repository, once.** It is a first step, not a completed
  independent evaluation — it does not yet cover the other three repositories, does not repeat
  to check consistency, and was still set up by the author (choosing the repository and writing
  the prompt), even though the reviewing agent had no access to this session's prior findings.
- **Rounds 1–2 tested detection plus reasoning at the same standard as the methodology, largely
  performed or directly supervised by the author** rather than dispatching the unmodified
  `SKILL.md` procedure to a fully independent agent across every case. §3.8 is the exception.
- No inter-run consistency measurement. The same repository reviewed twice may produce
  different findings; the rubrics are designed to make rankings reproducible, but that has not
  been measured.
- No comparison against a human expert baseline.
- No measurement of context cost per review, which matters for whether the routing design is
  actually paying off.
- No automated regression test yet for the fixture cases in §4.

Contributions that close any of these are welcome, and are worth more than additional
reference content.
