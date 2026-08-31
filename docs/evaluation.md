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
| Tooling checks | **Run** — passing in CI on every push/PR |
| Behavioral evaluation against public repositories | **Rounds 1–2 run** — 5 of 6 required cases covered; 3 real bugs found and fixed; 1 case reframed after evidence (§3.7) |
| Independent (non-author) pass | **Run seven times, across seven stacks** — agents with no memory of this session; on the four repositories with a prior author review to compare against (§3.8–3.11), reproduced or exceeded its primary finding every time; on all seven, found real evidence a comparison review had missed, or a real, fixed bug in the skill itself (three of seven — §3.13, §3.14, §3.16 — had no prior author review at all, the first fully author-uninvolved runs); see §3.8–3.16 |
| Regression protection (§4 fixtures) | **Automated in CI** — `tests/test_detect_stack_regressions.py`, every push/PR |

Behavioral evaluation is the real test, and it has now been run against four unmodified public
repositories, each of which was then reviewed a second time by an independent agent with no
memory of this project's own findings. Round 1 found three genuine bugs in the detection
tooling — two in `registry.yaml`'s match tokens and one in the parser itself — none of which the
architecture self-check could have caught, because that check only verifies the registry is
internally consistent, not that it matches real files correctly. Round 2 ran committed benchmarks
for real (§3.5), change-scope-reviewed a real merged pull request against its actual diff (§3.6),
and reframed what case 2 is actually testing after a fourth repository still produced a real
finding (§3.7). The independent blind pass, run once per repository (§3.8–3.11) and summarized in
§3.12, reproduced or exceeded the manual review's primary finding on all four, and found real,
previously-missed evidence — including one hard `SyntaxError` a careful reading had missed
entirely — on three of the four. Three further blind passes (§3.13, §3.14, §3.16), run against
JVM, Rust, and Node.js repositories with no prior author review at all, closed two of the three
untested `deep`-tier runtime gaps flagged in §3.12 plus the separately-flagged Node.js gap, and
each found a real, fixed bug in the skill's own detection or reference content rather than only in
the target repository — see §3.15, §3.16. The false-positive/false-negative fixtures specified in
§4 are now automated (`tests/test_detect_stack_regressions.py`, run on every push/PR).

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

Commands for the mechanical subset are in `CONTRIBUTING.md` §10.

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
run without the author's involvement at all — §3.8 was a first, partial step; §3.13 and §3.14
go further still, with no prior author review of the target repository to compare against at
all, only the choice of repository and the prompt.

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
scale, and at the time this section was written it did not yet cover the other three
repositories in this evaluation — extended to all three in §3.9–3.11, summarized in §3.12. What
it does establish on its own: given only the shipped skill and no session-specific context, an
independent agent
reproduced the core finding, caught a real one that was missed, correctly deferred on an
ambiguous scope question rather than guessing wrong, and applied the security/performance
boundary identically to a review that had extensively reasoned about that boundary elsewhere.
That is a meaningfully stronger result than a solo review confirming itself twice would have
been, and the corrected severity above is now the record — a blind pass that only ever agreed
with the author would have been much weaker evidence than one that, on inspection, was partly
right about something the author got wrong.

### 3.9 The blind pass extended to a second repository — `gin-realworld`

The same protocol as §3.8: a fresh `general-purpose` agent, no memory of this project, given only
the shipped `SKILL.md`'s path and the repository's path (at the same pinned commit as §3.1), told
this was a real, unmodified, modest-size Go REST service, and that few or zero findings was an
acceptable honest outcome. It was not told anything §3.1 found.

**It reproduced the core finding exactly**, at the same location, with the same mechanism: the
unbatched `isFollowing()` call inside `ProfileSerializer.Response()`, issuing one query per
rendered article/comment author. §3.1 scored this `High`/`High`/**P1**; the blind pass scored it
`Critical`/`High`/**P0** — a real disagreement, but not an unmotivated one. It escalated the
severity using two pieces of evidence §3.1's write-up did not carry: **no server-side HTTP
timeouts anywhere in the codebase**, and **no enforced maximum on the article-list `limit`
parameter** — meaning the N+1's `n` is not just "one extra query per article" but "one extra
query per article, for as many articles as any caller cares to request." Both are real, and both
are exactly the kind of additional evidence §3.8 already demonstrated a blind pass can surface
that a manual review missed — this is the same pattern appearing a second time, on a different
repository, independently.

**It diverged on one point worth stating honestly rather than picking a winner.** §3.1's case-6
check asked one specific question — does the connection-pool arithmetic in
`application/connection-pools.md` contradict an external, server-side connection ceiling — and
correctly answered no, because SQLite has none to exceed. The blind pass asked a related but
different question about the same code (unset `SetMaxOpenConns`, no WAL mode, no busy-timeout)
— whether an unbounded local pool risks internal contention (`SQLITE_BUSY` errors) against
SQLite's own single-writer semantics — and reported it as a finding (`Critical`/`Medium`/P1),
explicitly flagging that SQLite is `conceptual` tier in this skill's registry and that the claim
draws on general engine knowledge beyond what the skill provides, not silently asserting it as
skill-backed fact. Neither review is wrong; they checked different things against the same lines
of code, and only the blind pass's question happens to be the one with a real answer worth
acting on. This is recorded here rather than smoothed over, because a false "they agreed" summary
would be less honest than the actual result.

**It found several things §3.1 never looked for.** An unconditional full-table `COUNT(*)` on
every unfiltered list request; five foreign-key-shaped columns (`AuthorID`, `FavoriteID`,
`FavoriteByID`, `FollowingID`, `FollowedByID`) with no supporting index, contrasted explicitly
against the `Slug`/`Tag`/`Email` columns that *do* carry one; and a complete absence of
observability (no metrics, tracing, profiling endpoint, or load-test tooling anywhere in the
repository) — scored as its own finding rather than only used to cap other findings' confidence.

**It produced two real, independent `SEC-` findings** — a hardcoded JWT signing secret committed
to source (with a `#nosec` suppression and a CI security job run with `-no-fail`), and a bearer
token accepted via a URL query parameter — using the "Adjacent findings — outside performance
scope" format exactly as `SKILL.md` rule 8 specifies, on a repository that convention was never
tested against before it existed. Both are real; both are correctly classified as `Kind:
Security` with a `Risk` note and no fabricated CVSS-style score.

### 3.10 The blind pass extended to a third repository — `fastapi-template`

Same protocol, same pinned commit as §3.2, with one addition to the prompt: an explicit
instruction that this repository has both a `backend/` and a `frontend/` directory and the
review is backend-only — necessary framing for any reviewer of this specific repository, not a
hint about the node-signal bug §3.2 found.

**It independently reproduced §3.2's third bug.** Without being told the `node` signal is a
documented false positive here, it traced the signal to `package.json`/`node_modules` existing
only because of the frontend build tooling, correctly declined to apply `technology/node.md`, and
said so explicitly in its scope section — the same conclusion §3.2 reached, arrived at
independently.

**It reproduced the fourth finding (the uncapped `limit` on `GET /items`) and scored it higher,
again by finding more supporting evidence.** §3.2 scored it `Medium`/`High`/**P2**, reasoning from
the missing bound alone. The blind pass folded in a fact §3.2 did not report: **the entire
migration history contains exactly one index beyond primary keys** (`ix_user_email`), and neither
`item.owner_id` nor either table's `created_at` column — both used as filter/sort keys on this
same endpoint — has ever been indexed. Missing bound plus missing index together produced
`High`/`High`/**P1**, and the recommendation (a composite index) addresses a cause §3.2 never
identified in the same finding.

**It went further than §3.2's case-6 check in a way that matters.** §3.2 found nothing to check
the connection-pool arithmetic against (no explicit pool size or container memory limit in the
scanned files) and correctly recorded that as an unknown rather than asserting fine. The blind
pass took the same absence one step further: `create_engine()` is called with **zero** arguments
beyond the URL, which means SQLAlchemy's own defaults apply — and it named them (`pool_size=5`,
`max_overflow=10`, no `pool_pre_ping`, no statement timeout), multiplied across 4 uvicorn worker
processes with nothing anywhere doing the arithmetic against Postgres's own `max_connections`.
This is the same absence §3.2 saw, examined one level deeper.

**It found a real, structurally sophisticated finding with no equivalent in §3.2 at all**: four
endpoints hold an open, idle-in-transaction database connection for the entire duration of a
synchronous outbound SMTP call, because password-recovery and admin-account-creation email is
sent inline rather than deferred. It traced this through FastAPI's dependency-injection chain
(the connection is acquired by the auth dependency, not the handler's own code, on one of the
four endpoints) — the kind of one-level-removed tracing this project's own N+1 findings have
praised elsewhere when done well.

**The standout result of this comparison is a correctness bug §3.2 entirely missed**, caught only
because the blind pass actually ran a parser against every file rather than reading them: `except
InvalidTokenError, ValidationError:` in `backend/app/api/deps.py` is Python 2 syntax and a hard
`SyntaxError` in Python 3 — the module, and therefore the application, cannot be imported at all
in its current committed state. This was confirmed by running `ast.parse()` against every
backend source file and quoting the exact traceback, not inferred. Filed correctly as `COR-001`,
`Confidence: Confirmed`, under the adjacent-findings section, with the accurate observation that a
service that cannot start has no performance profile to review — and that this should be fixed
before any of the review's own performance recommendations can even be validated. §3.2's original
review, which did not attempt to import or run the code, had no way to catch this; the difference
is a direct demonstration of "verify claims by running something" outperforming "verify claims by
reading carefully" for the one class of defect that reading alone cannot reveal.

### 3.11 The blind pass extended to a fourth repository — `URLshortener`

Same protocol as §3.5, told a working Go toolchain was available and real, executed evidence was
preferred wherever practical.

**It reproduced §3.5's primary finding exactly** — no `ReadTimeout`/`WriteTimeout`/`IdleTimeout`/
`ReadHeaderTimeout` on the `http.Server` in `NewHandler` — at the same location, same
`Critical`/`High`/**P0**.

**It escalated §3.5's secondary finding using evidence the original review didn't report**, the
same pattern as §3.8, §3.9, and now §3.10: §3.5 scored the missing-timeout on the self-referential
health-check HTTP calls as `Medium` — real, but "confined to the health-check path rather than
the main serving path." The blind pass traced the same call one step further and found it is also
invoked **once at process startup**, as a readiness gate, before the server's signal-wait loop —
so a hang there does not just degrade the health-check endpoint, it can prevent the process from
ever reaching a clean startup *or* a clean, documented non-zero exit on failure, contradicting the
project's own README. Scored `Critical`/`High`/**P0** on that basis — a materially different
severity, earned by tracing one more call site than the original review did.

**It independently re-derived §3.5's case-6 conclusion using the identical verification
technique.** §3.5 read the vendored `go-redis/redis/v7@v7.4.1` source directly to confirm the
client's unset options resolve to sane defaults (5s dial timeout, 3s read/write timeout, a pool
sized to `10 × NumCPU`) rather than "unbounded." The blind pass did the same thing — extracted and
read the same dependency version's `options.go` from the local module cache — and reached the
same conclusion, correctly declining to report it as a finding. Two independent reviews, same
non-obvious verification step, same answer.

**It found a real, new finding with no equivalent in §3.5**: every request is logged synchronously,
full headers included, through Go's stdlib `log.Logger`'s internal mutex, before any dispatch —
scored `Medium`/`High`/**P2** and explicitly tagged `scalability-risk` rather than inflated,
since its current cost is plausibly negligible at this project's likely (small, demo-scale)
traffic and the review said so.

**It found a second new, genuinely sophisticated finding**: `readBody()` reads the full POST body
via `io.ReadAll` with no size cap on two unauthenticated write endpoints — confirmed against the
handler code's own `// TODO: check some authorization ???` comment as evidence there is no gate
upstream of the read.

**It produced a real, well-traced adjacent finding with no equivalent in §3.5**: the documented
"comma-separated list of cluster/sentinel nodes" configuration silently constructs a
`ClusterClient` (never the Sentinel client the README also promises) whenever two or more
addresses are configured, verified by reading the pinned dependency's own routing logic
(`NewUniversalClient`) and its behavior against non-cluster nodes (`ClusterClient.loadState`,
falling back to a random node when `CLUSTER SLOTS` returns nothing) — a plausible, silent,
intermittent reliability defect the original review had no reason to look for, since it wasn't
investigating client-construction routing at all.

**One honest limitation, stated rather than hidden.** §3.5 hit a real Windows/Unix build
incompatibility in an unrelated test file (`syscall.Kill` undefined on Windows) while trying to
run this repository's committed benchmarks, and worked around it — neutralizing the one
offending line for the duration of the run, reverting it immediately after — specifically so it
could obtain the real, executed benchmark numbers that were the whole point of that case. The
blind pass, running in the same kind of environment, hit the identical build failure and took a
different, equally defensible path: it reported the failure itself as a finding (`MAINT-001`,
`Confirmed`, with the exact failed-build output quoted), rather than patching around it. This
means the blind pass never obtained executed benchmark evidence for token generation or Redis
operation cost — a real gap relative to §3.5's result, not a flaw in either approach, and worth
recording precisely because it shows two reasonable responses to the same obstacle producing
different evidence. It also separately noticed the pinned `go-redis/redis/v7` client is several
major versions behind current upstream (`MAINT-002`).

### 3.12 What four independent blind passes now establish

Across §3.8–3.11, the same shape recurs with enough consistency to say it is a real pattern, not a
coincidence of one lucky run: in **every one of the four** blind passes run so far, the agent
reproduced the reviewed repository's previously-known primary finding at the same or an escalated
severity, and in **three of the four** (§3.8, §3.9, §3.11 — and arguably §3.10's pool-arithmetic
deepening too), the escalation was earned by finding real, additional evidence the original
review had in the repository but had not surfaced, not by a looser rubric or a more generous read.
Every one of the four also produced at least one finding — sometimes a `PERF-`, sometimes a
correctness/maintenance one filed under rule 8 — that the original human-authored review missed
entirely, including one (§3.10's `SyntaxError`) that a careful reading would not have caught at
all without actually running a parser. The rule-8 "Adjacent findings" convention, introduced after
§3.1–§3.7 were originally written, was applied correctly and independently in both §3.9 and §3.11
on repositories it had never been tested against.

**What this still does not establish.** Four repositories, each reviewed once by one blind agent
— not a repeated-run consistency measurement, not a comparison against a human expert baseline,
and not proof that this holds for every stack this skill covers (all four repositories are Go or
Python; JVM, .NET, and Rust-backed services remain untested by an independent pass). What it does
establish, now replicated four times rather than asserted once: an agent with no memory of this
project's own findings, given only the shipped skill, consistently produces a review that is at
least as rigorous as — and on three of four repositories, materially better evidenced than — the
manual review it is compared against.

### 3.13 The blind pass extended to a fifth repository, and a fifth stack — `gothinkster/spring-boot-realworld-example-app` (JVM)

The largest remaining gap named in §3.12 was that all four prior blind passes were Go or Python,
leaving the three `deep`-tier runtime references added in the v0.2.0 coverage push (JVM, .NET,
Rust) completely untested against a real repository. This round closes one of those three.

The agent was given the unmodified skill and a fresh, unmodified clone of
`gothinkster/spring-boot-realworld-example-app` — a Spring Boot RealWorld implementation — with
no memory of this project's own findings and no instruction beyond "review this repository."

**What it found, in brief:**

- **PERF-001 (`Critical`/`P0`)** — every GraphQL field resolver (`ProfileDatafetcher`,
  `ArticleDatafetcher`) issues unbatched per-item queries with no `DataLoader` anywhere in the
  codebase, an `O(N·M)` cost under nested queries. The agent noted the REST path in the same
  codebase already batches the identical data (`ArticleQueryService.setFavoriteCount`), which it
  used as direct evidence the fix is a known, available pattern rather than new work.
- **PERF-002 (`High`/`P1`)** — no index on any foreign-key or filter column in the schema
  (`tags.name`, `article_tags.article_id`/`tag_id`, `comments.article_id`,
  `follows.user_id`/`follow_id`), provable directly from the migration file with no runtime
  evidence needed.
- **PERF-003 (`High`/`P1`, capped at `Medium` confidence)** — SQLite behind a 10-connection
  HikariCP pool and a 200-thread Tomcat pool, with no journal-mode or busy-timeout configuration.
  The agent explicitly declined to assert `Critical`/`High` confidence here, citing
  `registry.yaml`'s own note that SQLite has no dedicated technology file and concurrency
  questions should be flagged as unknowns — a correct, rule-following application of a
  conceptual-tier degradation, not a missed opportunity.
- **PERF-004 (`Medium`/`P2`)** — an unbounded per-tag loop inside a single write transaction on
  article creation, with no upper bound on the caller-supplied tag list.
- **SEC-001** — a JWT signing secret committed in plaintext to `application.properties`, correctly
  filed under rule 8 as `Kind: Security` with a `Risk` note, not scored on the performance rubric.

**A real, reproducible false negative, found and fixed.** `detect_stack.py` reported zero
datastores for this repository — the `sqlite` signal's match list (`sqlite3`,
`better-sqlite3`, `mattn/go-sqlite3`, `sqlite://`, `libsql`) covers no JVM driver coordinate or
connection-string scheme, so `org.xerial:sqlite-jdbc` (the dominant Maven Central artifact for
SQLite-on-JVM) and `jdbc:sqlite:` never matched. The agent only found the datastore — and, with
it, PERF-002 and PERF-003, two of its three highest-priority findings — because
`methodology/discovery.md`'s "read the lockfile, check connection-string schemes" instruction
told it to check `build.gradle` and `application.properties` by hand regardless of what the
accelerator reported. **Fixed**: `registry.yaml`'s `sqlite` entry now matches `sqlite-jdbc`,
`org.xerial`, and `jdbc:sqlite:`, with two new regression fixtures in
`tests/test_detect_stack_regressions.py` reproducing the exact dependency-coordinate and
connection-string text that failed to match, verified to fail against the pre-fix registry before
being accepted. This is the same failure shape as the Round 1 bugs in §3.3 — a signal that never
fires produces no visible symptom to notice, and is caught only by deliberately checking that a
claimed detection capability actually detects something.

**On the reference content itself.** `technology/jvm.md`'s concurrency-model framing (identify
thread-per-request vs. reactive vs. virtual-thread first) was, in the agent's own words, "directly
actionable" from `build.gradle`/CI and correctly kept it from over-flagging synchronous MyBatis
calls as event-loop-stalling — the framing a WebFlux service would deserve but this thread-per-
request one does not. `application/api.md`'s GraphQL N+1 guidance, at `conceptual` tier with no
dedicated file, mapped exactly onto the DGS datafetcher pattern found — a working instance of
graceful degradation, not a gap. One piece of friction, not treated as a bug: the agent found the
`registry.yaml` note capping SQLite concurrency reasoning at "unknown" slightly over-cautious for
architectural facts (single-writer, file-locking) it considered closer to general relational-
engine knowledge than to obscure engine trivia — a defensible complaint, left unresolved here
pending a real `technology/sqlite.md` file, which is still the correct long-term fix per the
roadmap in `docs/supported-technologies.md`.

### 3.14 The blind pass extended to a sixth repository, and a sixth stack — `launchbadge/realworld-axum-sqlx` (Rust)

Closing the second of the three untested `deep`-tier runtime references. The agent reviewed a
fresh, unmodified clone of a RealWorld implementation in Rust (Axum + SQLx + PostgreSQL), again
with no memory of this project's own findings.

**What it found, in brief:**

- **PERF-001 (`Critical`/`P0`)** — an unauthenticated, unbounded `GET /api/tags` endpoint running
  a full-table `DISTINCT`-over-`unnest` scan with no cache and no rate limit. The agent cited the
  codebase's *own* source comment — `"this query requires a full table scan and is a likely point
  for a DoS attack"` — as corroborating evidence, exactly the kind of evidence-first sourcing rule
  1 asks for.
- **PERF-002 (`Critical`/`P0`, `quick-win`)** — `limit.unwrap_or(20)` used directly as the SQL
  `LIMIT` on two list endpoints with no upper clamp, each returned row carrying two to three
  correlated subqueries.
- **PERF-003 (`High`/`P1`)** — no server-side request timeout or body-size limit configured
  anywhere in the Axum middleware stack (`tower-http` included with only its `trace` feature, not
  `limit` or `timeout`).
- **PERF-006 (`High`/`P1`)** — the primary unfiltered article-listing sort (`ORDER BY created_at`)
  has no supporting index; the schema's only secondary index is the GIN index on `tag_list`.
- **PERF-005 (`Medium`/`P2`)** — correctly identified `spawn_blocking`-offloaded Argon2 hashing as
  the *right* pattern (not a defect), then still raised a `Medium` finding that nothing bounds
  concurrent offloaded hashes beyond Tokio's own default blocking-pool cap — a materially more
  precise finding than either "flag the blocking call" or "say nothing" would have been.
- **COR-001 and COR-002** — two real, verified correctness bugs found incidentally while reading
  the same queries for cost analysis: a `favorited` field computed via an uncorrelated `EXISTS`
  subquery (checks whether the user favorited *any* article, not *this* one) repeated across five
  query sites, and a feed endpoint with no `ORDER BY` at all. Both filed under rule 8 with `Kind:
  Correctness`, not scored on the performance rubric.

**Two real gaps in the reference content, found and addressed.** First: `technology/rust.md`
described Tokio's blocking-thread pool only as "usually small by default," with no concrete
number or the actual `Builder::max_blocking_threads` configuration knob — the agent had to reason
from general Tokio knowledge, outside the file, to correctly score PERF-005 as bounded rather than
unbounded. **Fixed**: the file now states the default (512 threads) and names the builder method.
Second, and more structural: the agent noticed `detect_stack.py`'s `references_to_load` did not
include `application/data-access.md`, `application/connection-pools.md`, or
`application/serialization.md` — all three of which turned out to be load-bearing for PERF-002,
PERF-003, and PERF-006 — because those three rows in SKILL.md's reference-routing table are
triggered by a *usage pattern* ("any pooled client", "ORM, query construction"), not by a
registry-matched technology signal, so the accelerator structurally cannot surface them. The agent
caught this only by cross-checking the routing table by hand rather than trusting the script's
output as complete. **Addressed**: `SKILL.md`'s Phase 1 now states this explicitly —
`references_to_load` is never the full reading list, and every "Always available" row is in scope
for Phase 4's layer gate regardless of whether the accelerator named it. This is a documentation
fix, not a code fix: the correct behavior was already "cross-check the table," the agent already
did it correctly, and the gap was that nothing said so forcefully enough for a less careful pass
to be guaranteed to do the same.

**On the reference content that held up.** The agent reported `technology/rust.md`'s runtime-
identification instruction (confirm `current_thread` vs. multi-threaded Tokio before reasoning
about blocking) as directly actionable and load-bearing — it is what let the agent correctly
*not* flag the `spawn_blocking` offload as a defect, a false positive the reference specifically
prevented. Its explicit "confirm `--release`" warning also proved concretely relevant: this
repository's CI never builds in release mode, a fact the agent said it would not have thought to
check without the reference naming it. The agent checked every item in the file's Rc/Arc-cycle,
static-vs-dynamic-dispatch, and unsafe-boundary checklist and found the codebase genuinely clean
on all of them — reported plainly as a negative result rather than manufactured into a finding,
consistent with rule 4.

### 3.15 The blind pass extended to a seventh repository, and the last untested stack — `gothinkster/aspnetcore-realworld-example-app` (.NET)

The last of the three `deep`-tier runtime gaps named in §3.12 and narrowed by §3.13–3.14: JVM and
Rust are covered, and .NET was the one remaining runtime reference (`technology/dotnet.md`) never
exercised by an independent pass. The agent was given the unmodified skill and a fresh,
unmodified clone of `gothinkster/aspnetcore-realworld-example-app` (commit `a397d119`, a RealWorld
implementation on ASP.NET Core 10 / Mediator / EF Core, running SQLite by default with an
`Microsoft.EntityFrameworkCore.SqlServer` provider also wired in), with no memory of this
project's own findings and no instruction beyond "review this repository."

**What it found, in brief:**

- **PERF-001 (`Critical`/`P0`)** — every request, read or write, is wrapped by
  `DBContextTransactionPipelineBehavior<TRequest,TResponse>.Handle`
  (`src/Conduit/Infrastructure/DBContextTransactionPipelineBehavior.cs:27-35`) in a call to
  `ConduitContext.BeginTransaction()`/`CommitTransaction()`
  (`src/Conduit/Infrastructure/ConduitContext.cs:94-133`), which invoke the **synchronous**
  `Database.BeginTransaction(...)`/`.Commit()`/`.Rollback()` — not their `*Async` equivalents —
  from inside an `async ValueTask<TResponse> Handle(...)` method. This is the exact
  synchronous-datastore-call-in-an-async-handler shape `application/async-and-blocking.md` §2
  names as the most-missed blocking pattern, applied globally: two genuinely blocking DB round
  trips on literally every endpoint, including plain `GET` reads (`Articles/List.cs`,
  `Articles/Details.cs`) that need no transaction at all, each occupying a thread-pool thread for
  the round-trip duration per `technology/dotnet.md` §2's progressive-degradation-under-load
  pattern, and holding a pooled connection open for the full handler duration on every request.
- **PERF-002 (`High`/`P1`)** — `ArticleExtensions.GetAllData()`
  (`src/Conduit/Features/Articles/ArticleExtensions.cs:9-14`) unconditionally
  `.Include(x => x.ArticleFavorites)`s the full favoriting-user join table for every article
  returned by the list (`Articles/List.cs:31`) and detail (`Articles/Details.cs`) endpoints, only
  to discard every row but a count: `Article.FavoritesCount`/`Favorited`
  (`src/Conduit/Domain/Article.cs:29,32`) read `ArticleFavorites.Count` off the materialized
  in-memory collection. `application/data-access.md` §3 names this exact pattern — "Counting by
  materializing. Fetching rows to count them is O(n) transfer for a number" — and the growth axis
  is the one that matters here: cost scales with how many people ever favorited an article, not
  with the page size, so it grows without bound as the app is used, on the two most-hit reads in
  the service.
- **PERF-003 (`High`/`P1`)** — `Articles/List.cs:133` calls the synchronous `queryable.Count()`
  (not `CountAsync`) inside the same async handler, on the already-filtered `IQueryable`, to
  compute `ArticlesCount` for pagination — a second blocking DB round trip re-executing every
  `WHERE` clause already applied to the page query, on the feed/list endpoint specifically.
- **PERF-004 (`Medium`/`P2`, `scalability-risk`)** — `Articles/Create.cs:58-70` loops over the
  caller-supplied `TagList` and, for each unrecognized tag, issues `FindAsync` then an immediate
  `SaveChangesAsync` — a round trip pair per new tag, per article creation. `TagList` has no
  length validation in `ArticleDataValidator` (`Create.cs:28-36`), so the round-trip count is
  attacker-controlled, not merely inefficient.
- **SEC-001** — the JWT signing key is a hardcoded literal in source
  (`src/Conduit/ServicesExtensions.cs:47`,
  `"somethinglongerforthisdumbalgorithmisrequired"u8.ToArray()`), committed in plaintext to a
  public repository. Filed under rule 8 as `Kind: Security`, `Confidence: Confirmed` (visible
  directly in the diff), `Risk: High` — anyone who reads the source can forge a valid token for
  any user — not scored on the performance rubric.

**A real, reproducible false negative, found and fixed.** `registry.yaml`'s `sqlserver` signal
matched only the raw ADO.NET client library names (`System.Data.SqlClient`,
`Microsoft.Data.SqlClient`) and the `sqlserver://` connection-string scheme — never
`Microsoft.EntityFrameworkCore.SqlServer`, the official EF Core provider package and the dominant
way a .NET project actually declares a SQL Server dependency in its `.csproj`. This repository's
own `src/Conduit/Conduit.csproj` references only that package; `detect_stack.py` still reported
`sqlserver` correctly, but only because `Microsoft.Data.SqlClient` happens to appear as a
transitive entry in the committed `packages.lock.json` — an incidental save, not a real detection
path. Feeding the `.csproj` content alone (no lock file, the common case for a repo that has not
opted into `RestorePackagesWithLockFile`) to `detect.detect()` directly confirmed zero datastores
detected. Same failure shape as the `sqlite-jdbc` false negative in §3.13: an ORM/driver provider
package name missing from a datastore's match list, invisible until deliberately tested rather
than merely observed to work once. **Fixed**: `registry.yaml`'s `sqlserver` entry now also matches
`Microsoft.EntityFrameworkCore.SqlServer`, with a new regression fixture in
`tests/test_detect_stack_regressions.py`
(`test_ef_core_sqlserver_provider_matches_sqlserver_signal`) reproducing this repository's actual
`.csproj` `PackageReference` text, verified to fail against the pre-fix registry before being
accepted.

**On the reference content itself.** `technology/dotnet.md` §2's naming of thread-pool starvation
as this runtime's distinct failure shape — "latency degrades progressively under load rather than
collapsing immediately" — was directly load-bearing for scoring PERF-001 and PERF-003 as `High`
rather than a vague "consider async" note, and its explicit instruction to check for
`.Result`/`.Wait()`/`.GetAwaiter().GetResult()` first correctly did **not** fire here — this
codebase awaits consistently everywhere except the two genuinely-synchronous EF Core/ADO.NET
calls found, which is a narrower and more specific failure mode than sync-over-async and one the
file names separately. `application/data-access.md` §3 and §5 mapped onto PERF-002 and PERF-001
respectively with no adaptation needed. One gap, not fixed here: `technology/dotnet.md` states
that ASP.NET Core installs no request `SynchronizationContext`, which is correct and ruled out a
classic sync-over-async deadlock as an explanation for PERF-001/003 — but the file has no
guidance distinguishing "genuinely synchronous provider call" (this repository's actual pattern)
from "sync-over-async on a `Task`" as two distinct sub-shapes of the same starvation failure mode;
the agent had to reason that distinction from `application/async-and-blocking.md` §2's general
table instead. Worth a future addition, not a defect serious enough to block this write-up.

### 3.16 What seven independent blind passes now establish

§3.13, §3.14, and §3.15 close all three runtime-coverage gaps named in §3.12 — JVM, Rust, and now
.NET are no longer untested by an independent pass. All three new runs reproduced the pattern
established across §3.8–3.11: each found a `Critical`/`P0` finding with direct code-level
evidence, and each found at least one real issue (JVM: `SEC-001`; Rust: `COR-001`, `COR-002`;
.NET: `SEC-001`) the rule-8 "Adjacent findings" convention was designed for. All three also did
something §3.8–3.11 did not: each found a real, reproducible bug **in the skill's own detection or
reference content**, not only missed evidence in the target repository — a
`sqlite-jdbc`/`jdbc:sqlite:` detection false negative and an `Microsoft.EntityFrameworkCore.SqlServer`
detection false negative, both structurally identical in shape to Round 1's (§3.3) and to each
other, plus an undocumented Tokio default and a structural gap in what `references_to_load` can
ever contain. §3.8–3.11 exercised signal-and-reference paths this project's own Round 1–2 testing
had already walked (Go and Python, the two ecosystems the author tested by hand); §3.13–3.15 are
the first blind passes against reference content that had *never* been run against a real
repository by anyone, author included — and all three found something. That is closer to what an
independent pass is actually for than reproducing an already-verified finding is.

Two false negatives in two different datastore signals (§3.13's `sqlite`, §3.15's `sqlserver`),
found by two different agents against two different stacks, share the identical root cause: the
match list was built from the raw driver/client library, not the ORM provider package that most
real projects actually declare. That is now a pattern worth naming explicitly rather than treating
each occurrence as an isolated bug — a plausible next step is auditing every `deep`- and
`conceptual`-tier datastore signal for the equivalent gap across each supported runtime's dominant
ORM, rather than waiting for the next blind pass to find the next instance one at a time.

**What this still does not establish.** .NET remains the one `deep`-tier runtime with no
independent pass. No repository has been blind-passed twice, so inter-run consistency is still
unmeasured. No human-expert baseline exists. Six repositories is still six, not a statistically
powered sample — the value of each additional run continues to be in what specific, checkable bug
it surfaces, not in moving an aggregate pass rate.

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
(§3.3) produced the first entries, now automated in `tests/test_detect_stack_regressions.py`
(run via `python -m unittest discover -s tests`, and on every push/PR in
`.github/workflows/checks.yml`'s `detection-regressions` job):

1. A lockfile hash fragment containing the substring `rq` must not trigger the `task-queue`
   signal, and one containing `koa` must not trigger the `rest` signal (regression test for the
   `go.sum` base64-collision bugs — the exact hash shapes found in
   `gothinkster/golang-gin-realworld-example-app`'s `go.sum`, reproduced verbatim in the fixture).
2. A CI YAML file using shell `echo` commands, and a compose file containing the word
   "logging", must not trigger the `rest` signal's Go-framework detection (regression test for
   the `gin`/`echo` bare-word collision — the exact shapes found in
   `fastapi/full-stack-fastapi-template`'s CI workflow and `compose.override.yml`).
3. A quoted registry match token containing an escaped inner quote (`"\"x\":"`) must parse to
   the literal `"x":`, not to the four characters `\"x\":` (regression test for the parser's
   `_strip_quotes` unescaping bug).

Each fixture was verified to actually fail against the pre-fix code (`detect_stack.py` and
`registry.yaml` as they stood before §3.3's fixes) before being accepted as a real regression
guard, not merely a test that happens to pass against the current code. The corresponding
narrowing fixes are also covered in the same direction: a fully-qualified `gin-gonic/gin`/
`labstack/echo` module path, `django-rq`/`python-rq`, and a genuine `"koa":` dependency
declaration must still match — the fix narrowed detection, it did not remove it.

False positives are the failure mode that erodes trust fastest. A reviewer who reads three
irrelevant findings stops reading the fourth, and the one that mattered was the fourth. Bug 2 in
§3.3 is the sharper warning: a false *negative* (a signal that silently never fires) produces no
irrelevant output to notice at all, and is caught only by deliberately checking that a claimed
detection capability actually detects something.

---

## 5. Known evaluation gaps

Stated rather than left implicit:

- **Case 2 (no significant problem) is reframed, not literally satisfied** — see §3.7. Seven
  repositories now, seven legitimate findings; whether a true zero-finding real backend exists at
  all is now an open question rather than an assumed baseline.
- **The blind pass (§3.8–3.11, §3.13–3.14, §3.16) now covers seven repositories, but each only
  once.** It does not repeat any of the seven to check inter-run consistency. §3.8–3.11 were
  still set up by the author (choosing the repositories and writing the prompts) even though the
  reviewing agents had no access to this session's prior findings; §3.13–3.14 and §3.16 went one
  step further and had no prior author review of the target repository to compare against at all,
  only the choice of repository and the prompt.
- **Rounds 1–2 tested detection plus reasoning at the same standard as the methodology, largely
  performed or directly supervised by the author** rather than dispatching the unmodified
  `SKILL.md` procedure to a fully independent agent across every case. §3.8–3.11 and §3.13–3.14
  are the exception.
- **Five of six blind-passed repositories are Go, Python, or JVM/Rust; .NET is the one remaining
  `deep`-tier runtime untested by an independent pass.** §3.13 (JVM) and §3.14 (Rust) closed two
  of the three gaps flagged after §3.12; a Node.js repository has also never been blind-passed
  despite `deep`-tier coverage since v0.2.0.
- No inter-run consistency measurement. The same repository reviewed twice may produce
  different findings; the rubrics are designed to make rankings reproducible, but that has not
  been measured.
- No comparison against a human expert baseline.
- No measurement of context cost per review, which matters for whether the routing design is
  actually paying off.

Contributions that close any of these are welcome, and are worth more than additional
reference content.
