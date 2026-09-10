# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## What counts as a breaking change

Versioning a skill is not quite versioning a library. The following are **major/breaking**,
because they change what a consumer or a previously issued report can rely on:

- Renaming the skill, or its frontmatter `name`.
- Removing or renaming a reference file path that `registry.yaml` or `SKILL.md` points to.
- Changing finding-schema field names, or the report's section structure.
- Changing rubric definitions such that previously issued severities or priorities are no
  longer reproducible.
- Removing a technology's support tier, or demoting one.

Adding a technology reference, adding a registry entry, promoting a tier, or deepening an
existing reference file is a **minor** change. Corrections and clarifications that do not
change scoring are **patch**.

Breaking changes carry a migration note in the entry.

## Release cadence

What a change *is* (major/minor/patch, above) is separate from *when it ships*. From v0.6.0
onward, work accumulates on `develop` and a release is cut deliberately, at a milestone, rather
than every time `main` moves — see [CONTRIBUTING.md §2](CONTRIBUTING.md#2-branching-and-pull-requests).
Patch and minor releases still happen when something genuinely warrants shipping sooner (a
detection bug that silently drops a signal, for instance); they are just no longer the default
response to a batch of merges. Between releases, version numbers stay at the last released
value and `[Unreleased]` accumulates.

---

## [Unreleased]

### Added — infrastructure batch, completing the 1.0.0 milestone

- **`kubernetes`, `docker`, `serverless`, and `terraform` promoted to `deep` tier — the last
  batch.** All 39 detection signals are now `deep`. No signal in `registry.yaml` is
  `conceptual` or `generic` any more.
  - **`technology/kubernetes.md`** — the CFS-quota accounting-period mechanism specifically
    behind CPU throttling (a container can be throttled within a single ~100ms period even
    while the node has spare capacity, which is why bursty multi-threaded workloads are
    disproportionately affected relative to their average CPU usage), QoS-class-driven
    eviction order under memory pressure, HPA's metrics-polling lag and deliberate
    scale-down stabilization window, `startupProbe` as the specific fix for a slow-starting
    container (rather than loosening `livenessProbe`), and namespace-level `ResourceQuota`/
    `LimitRange` as a ceiling above an individual pod's own limits.
  - **`technology/docker.md`** — PID 1 signal-handling as the specific, checkable reason
    graceful shutdown can silently not happen despite being implemented in application code
    (no init process, or a shell-wrapped `CMD` absorbing `SIGTERM`), the related zombie-
    process-reaping gap, layer-caching and image size as a startup-latency (not request-time)
    cost, copy-on-write cost on first write to a file inherited from an image layer, and
    `HEALTHCHECK` as a mechanism independent of — and easy to double-count against — an
    orchestrator's own probes.
  - **`technology/serverless.md`** — a deliberately comparative file across five platforms
    (AWS Lambda, Google Cloud Functions, Azure Functions, Vercel, Netlify), the fifth
    umbrella technology file after vector stores, object storage, task queues, and REST
    frameworks. Covers per-platform warm-instance mitigation levers (provisioned/reserved
    concurrency on Lambda are two different knobs solving two different problems, easy to
    conflate), Lambda VPC-attachment's distinct ENI-provisioning cold-start cost, invocation-
    duration ceilings that vary by an order of magnitude or more across platforms, and the
    module-scope/handler split's per-platform and per-language expression.
  - **`technology/terraform.md`** — not a runtime, so this file is shaped differently from
    every other technology file in this project: a map from provider resource types (across
    AWS, GCP, and Azure) to the exact arguments `infrastructure/resources.md`'s arithmetic
    needs (container memory/CPU, replica bounds, connection ceilings, concurrency limits,
    timeouts), plus how `count`/`for_each` versus autoscaling arguments, module layering, and
    per-environment `.tfvars` can hide or redirect which value is actually live.
  - `docs/supported-technologies.md`, README.md, and `docs/roadmap.md` updated: coverage is
    now 39 deep / 0 conceptual / 0 generic, and the "Technology promotion candidates" section
    is retained empty (rather than removed) as the eventual home for a genuinely new engine,
    framework, or platform, with the umbrella-file precedent documented for reuse.

### Added — frameworks batch (1.0.0 milestone)

- **`graphql`, `grpc`, and `rest` promoted to `deep` tier** — the frameworks batch of the
  1.0.0 "leave nothing below deep" milestone. Only the infrastructure batch (`kubernetes`,
  `docker`, `serverless`, `terraform`) remains below `deep`.
  - **`technology/graphql.md`** — the resolver-tree execution model that makes GraphQL's
    N+1 structural rather than incidental (every field in a selection set resolves
    independently; there is no "forgot to join" version of this), DataLoader's
    request-scoped batching mechanism (collapses sibling resolver calls within one tick, not
    within a single resolver), the caller-controlled-query-shape risk (unbounded nesting and
    alias-multiplied breadth) that has no REST equivalent, and federation's per-subgraph
    fan-out.
  - **`technology/grpc.md`** — HTTP/2 stream multiplexing inverting ordinary
    connection-pool-sizing reasoning (one connection now often serves many concurrent calls,
    which means an L4 load balancer sees one flow rather than N requests and can pin all of a
    client's traffic to one backend), Protobuf message-shape cost (nesting and field
    cardinality, not wire size alone, dominate (de)serialization CPU), streaming-call state
    lifetime, and automatic deadline propagation.
  - **`technology/rest.md`** — a deliberately comparative file across all fifteen frameworks
    the `rest` signal matches (FastAPI, Flask, Django, Express, NestJS, Koa, Gin, Echo,
    Fiber, Spring Boot, Actix, Axum, Laravel, Rails, ASP.NET Core), following the umbrella
    precedent `technology/vector-stores.md`/`object-storage.md`/`task-queues.md` set. Names
    which concurrency model each framework actually uses (single event loop vs.
    thread/process/goroutine-per-request vs. async-task-based) rather than writing generic
    concurrency advice true of none of them specifically — the same bar
    `docs/roadmap.md` sets for every umbrella file. Also covers Fiber's non-`net/http`
    foundation (a real correctness footgun from request-object reuse, not just a performance
    note), PHP-FPM's process-per-request model, and Rails' app-server-dependent concurrency.
  - `docs/supported-technologies.md`, README.md, and `docs/roadmap.md` updated to match;
    coverage is now 35 deep / 4 conceptual / 0 generic.

### Changed — BREAKING

- **The finding format gains four required fields, and the report gains a machine-readable
  form.** Both are breaking under this project's own definition: the finding schema changes,
  and the report's section structure changes. A report issued before this change is still
  readable, but it is not comparable field-by-field against one issued after it, and it
  cannot be scored by the tooling this unlocks.

  New required fields on every finding:

  | Field | Why it is required rather than optional |
  |:--|:--|
  | `Root cause` | Merging symptoms into one finding was already mandatory (`methodology/bottleneck-analysis.md` §2). Recording *which* cause makes the merge auditable instead of merely asserted, and makes "three symptoms, one problem" legible in the output |
  | `Counter-evidence` | The review already applied an alternative-explanation test and an intent test while discarding candidates, but left no trace of either, so a reader could not tell a candidate that survived scrutiny from one that was never scrutinized. An empty list now positively asserts that a search happened and found nothing |
  | `Why this might not matter` | Required at Medium severity and above, where a false positive costs the most. Stating the strongest case against a finding is the cheapest defense against manufacturing one |
  | `Alternatives` | Required at Medium severity and above. Guards the failure where the first plausible fix is the only one considered — usually the one that masks work rather than removing it |

  Migration: reports produced under the previous format remain valid as documents; re-run
  the review to produce a comparable one. Findings carried forward by hand need the four
  fields added, and `Counter-evidence` should be filled in honestly rather than backfilled
  as empty.

### Added

- **`schemas/finding.schema.json` and `schemas/review.schema.json`** — the report's
  machine-readable form. The Markdown report stays the primary artifact and stays
  authoritative; the JSON is the same content in a shape that can be diffed, scored, and
  compared across runs. Almost every measurement this project wants — precision and recall,
  severity and confidence calibration, run-to-run stability, historical comparison,
  cross-model comparison — was blocked on having a stable record to compare, which is why
  this landed before the benchmark corpus rather than after it.
  - Carries reproducibility metadata (commit, skill version, detector version, model), a
    completeness block, an assumption ledger, and root-cause grouping.
  - `stable_id` is derived from root cause, file, enclosing symbol, and mechanism — never
    from a line number — so the same unfixed problem keeps its identity across a refactor.
  - The severity/confidence → priority matrix is deliberately **not** duplicated into the
    schema. It stays published in `SKILL.md` alone and is enforced from there, so the matrix
    exists in one authoritative place rather than three.
- **`scripts/json_schema_lite.py`** — a small standard-library JSON Schema validator
  covering only the subset these schemas use. Chosen over a `jsonschema` dependency so that
  `python scripts/check_repo_invariants.py` still works on a clean checkout with nothing
  installed, matching the constraint the bundled detector already lives under. Extending the
  schemas may mean extending this reader — the same trade `detect_stack.py` documents for
  its YAML subset.
- **`docs/examples/review.example.json`** — a worked, schema-valid review. It lives in
  `docs/examples/` so it can never be loaded as a reference (`docs/architecture.md` §9), and
  it contains no invented runtime numbers: the finding is scored on growth derived from the
  code, its validation states a direction rather than a magnitude, and one of its
  discarded candidates is an index that would have added write cost for no read benefit.
- **`docs/review-response.md`** — a disposition for each of the 140 recommendations in an
  external review of this project: adopted, already present, reframed, deferred, or
  declined, each with a reason. The largest category is work that already existed under
  another name, which is itself worth recording.
- **Counter-evidence is now a named, recorded step** (`methodology/bottleneck-analysis.md`
  §4). The discipline partly existed already — §3's "alternative-explanation test" and
  "intent test" are counter-evidence search — but it happened invisibly and left no trace,
  so a reader could not distinguish a finding that survived scrutiny from one that was never
  scrutinized. It is now explicit about what to look for per hypothesis type, and about what
  must change when something turns up: counter-evidence that *bounds* a problem reduces
  **Severity**, while counter-evidence that *obscures* it reduces **Confidence**. The two
  are not interchangeable, and collapsing them loses the distinction the whole two-axis
  rubric rests on.
- **Detection and recommendation are now explicitly separated**
  (`methodology/bottleneck-analysis.md` §5). Deciding the answer is a cache makes
  cache-shaped evidence easier to find, and quietly relaxes the counter-evidence search
  because the conclusion is already comfortable. Naming a technology while establishing a
  mechanism is now called out as a recommendation wearing a finding's clothes.
- **Findings that interact are scored on the combination**
  (`methodology/bottleneck-analysis.md` §2). Three independently moderate issues sharing one
  constrained resource can be severe together. Guarded against inflation: the interaction has
  to be mechanical and stateable, not "both are about the database."
- **The workload model gains `DERIVED` and `MEASURED`** (`methodology/workload.md` §3),
  completing the assumption ledger. It already separated known from assumed from unknown;
  arithmetic previously had nowhere to go and could pass for a fact. `DERIVED` must show its
  inputs, and an empty `MEASURED` list is now stated rather than implied — it is the line
  that explains why almost nothing in a static review is `Confirmed`.
- **The workload interview is now adaptive** (`methodology/workload.md` §2). The
  seven-question cap stays; *which* seven is now driven by where Phase 1 found risk, and
  ranked by how much the answer would move the ranking rather than by how natural the
  question is to ask. Reviews now also close with the specific unanswered questions that
  would change the result, which is more useful than "more information is needed".
- **Review completeness** (report template §2). Repository coverage, critical paths analyzed
  against identified, technology support, runtime evidence, and an overall **review
  confidence that is explicitly not finding confidence** — every finding can be `High` while
  the review is `Low`, which means "what I looked at, I am sure about; I did not look at
  much." Unknowns are now split by cause: no evidence existing is a gap the user can close,
  an unsupported technology is a gap in this skill, and presenting them identically misleads.
  Without this block, a zero-finding result reads as "everything is healthy" — a much
  stronger claim than the one the review is entitled to make.
- **Investigation economy** (`methodology/discovery.md` §5). How much to spend on a candidate
  by what it could be worth, expanding on evidence rather than on a fixed file budget, asking
  before recommending an expensive diagnostic, and — the part most often missing — explicit
  conditions under which stopping is correct and complete. Continuing until something turns
  up is how a review manufactures findings.
- **Precision and recall are traded off per priority band** (`rubrics.md` §5). High precision
  at P0/P1, where a wrong finding costs a team a day and costs the review its credibility for
  everything after it; recall acceptable at P3. Uncertainty now explicitly pushes a candidate
  *down* a band, never up, and inflating severity to get something read is named as
  destroying the only thing that makes the scale useful.

### Added — change-scoped review as the flagship

- **A derived verdict** — `PASS` / `WARN` / `FAIL` / `UNKNOWN` — and
  `methodology/change-scoped.md`, which the mode previously lacked entirely despite the
  README calling it the mode most worth running continuously. Like priority, the verdict is
  read off a table rather than chosen: `FAIL` requires a `High`/`Critical`-severity finding
  at `High` or `Confirmed` confidence, so a hypothesis cannot block a merge.
  - **`UNKNOWN` never collapses into `PASS`.** They are different claims — "I looked and
    found nothing" versus "I could not look" — and conflating them converts a gap in the
    review into a false assurance the reader has no way to detect. `UNKNOWN` outranks
    findings, and never fails a job under any setting, because a change the review could not
    analyze is not the author's fault.
  - The file also covers the expansion boundary (stop when expansion stops touching the
    change), and **crediting fixes rather than re-flagging them** — a diff that removes an
    N+1 still contains the N+1 in its removed lines, and reading direction before scoring is
    the difference between a useful check and one teams route around.
- **Repository performance policy** (`.performance-policy.yml`), with one rule kept
  deliberately load-bearing: **a policy violation is not a measured performance problem**,
  and the two are never reported as the same thing. One is a fact about a limit the team
  chose; the other is a claim about production behavior. Conflating them lets an arbitrary
  threshold read as a measurement. A budget expressed against a baseline nobody measured is
  reported as `not-evaluable` — never as met. SLOs and business criticality enter here too,
  affecting sequencing only, never severity.
- **`scripts/to_sarif.py`** — SARIF 2.1.0 output, with three deliberate mappings:
  - **`level` comes from priority, not severity**, because SARIF has one axis and this
    methodology has two. Mapping severity alone would render a `High`/`Low`-confidence
    hypothesis as an `error` beside a confirmed regression.
  - **`stable_id` becomes `partialFingerprints`**, so a GitHub alert survives the code moving.
    A line-number fingerprint produces a fresh alert every time someone adds an import above.
  - **Adjacent `SEC-`/`COR-`/`MAINT-` findings are excluded by default.** This project has no
    security methodology, and publishing them into code scanning would present them as the
    output of a scanner it is not — a team could read a clean dashboard as evidence of a
    security review that never happened. `--include-adjacent` exists, and still emits them at
    `note` regardless of stated risk.
- **`scripts/validate_review.py`** — schema validation plus the cross-field rules a schema
  cannot express: priority against the published matrix, root-cause references, duplicate
  ids, and the rule that `Confirmed` confidence requires cited runtime evidence a review
  claiming none cannot have.
- **`scripts/pr_comment.py`** — a compact comment that keeps what compression usually loses:
  each finding's `Conditions`, its "why this might not matter", a coverage table so a `PASS`
  can be read for what it is, and policy violations visually separated from findings. Zero
  findings renders as "nothing material was found in what was reviewed" rather than as a
  clean bill of health.
- **`action.yml`** — a composite GitHub Action, plus [docs/github-action.md](docs/github-action.md).
  It does the deterministic half — validate, convert, comment, surface the verdict — and
  deliberately **does not run the review**, which needs a model, credentials, and a budget
  that belong to the caller. `fail-on` defaults to `never`: a static review with no runtime
  evidence is advice, and a check that blocks a release on its first false positive gets
  marked non-required and then deleted. The documented path is to earn the gate.
  - Two bugs were found and fixed before this shipped, both from GitHub running `shell: bash`
    with `set -e`: a `[ test ] && append` argument builder aborted the step whenever the flag
    was off, and the verdict gate failed the job precisely when the verdict was *acceptable*.
    The second is the kind of bug that looks like the check working.
- **Sixteen tests for the publishing path**, covering the ways a verdict can lie, fingerprint
  stability across a moved finding, zero findings producing valid empty SARIF, and adjacent
  findings staying out of the security dashboard.

### Added — measurement

- **`benchmark/scoring/score.py`** — scores a machine-readable review against expert ground
  truth. Standard library only. It reports precision, recall, and F1 **per category** (a
  single blended number hides "good at N+1, poor at concurrency"), severity calibration as
  exact / within-one / large disagreement, confidence calibration as claimed-versus-actually-
  correct, confidence-ceiling violations, recommendation accuracy **tracked separately from
  finding accuracy**, and restraint failures. A `stability` subcommand turns the hand-diffing
  in `docs/evaluation.md` §3.18 and §3.21 into a computed overlap figure, so the remaining
  six repositories can be checked cheaply rather than by eye.
- **`schemas/ground-truth.schema.json`** — the annotation format, with **three buckets rather
  than one list**, which is the part that matters:
  - `expected` — must be found; a miss is a false negative.
  - `acceptable` — real but not required; scored neither way. Without this, a review would be
    penalized for finding something real the annotator overlooked, which would train the
    corpus toward the annotator's blind spots.
  - `forbidden` — must **not** be reported, each with a required `why_not` stated as a fact
    about the code. This is where precision is actually measured, and **an empty `expected`
    with a populated `forbidden` is how a healthy repository gets written down** — the case
    `docs/evaluation.md` §3.7 records as never cleanly obtained across four repositories, and
    the one this project's central claim rests on.
- **`benchmark/README.md`** — what to annotate first and why, in priority order. The
  zero-findings repository is first. `benchmark/ground-truth/` ships empty on purpose:
  annotations are the expensive part, and committing placeholder ones would put fabricated
  truth into a repository whose first rule is not to fabricate.
- **Twenty-nine tests for the harness**, built the way the invariant checker was — by trying
  to fool it. They cover a review that misses everything, one that manufactures a finding on
  a healthy repository, one that finds the right problem and recommends Redis anyway, one
  that calls a Medium issue Critical, and one that claims `Confirmed` with no runtime
  artifact. One of them found a real bug in the matcher before it was ever used: a basename
  fallback matched `orders/service.py` against `users/service.py`. Matching is now
  suffix-only, because a wrong match is silent while an unmatched annotation prints a MISSED
  line somebody can correct.
- **The ground-truth corpus, seeded from all eight independently blind-passed repositories
  recorded in `docs/evaluation.md`**, plus one change-scoped case, nine files total:
  `gin-realworld.json` (the N+1 traced through the serializer chain, §3.1/§3.9/§3.18),
  `fastapi-full-stack-template.json` (unbounded pagination, §3.2/§3.10), `urlshortener.json`
  (the one repository with real `Confirmed`-grade runtime evidence — the benchmarks were
  actually run, §3.5/§3.11), `github-signature-verifier.json` (a severity corrected from
  `Medium` to `High` between two independent passes on evidence the first pass had already
  read and set aside, §3.7 → §3.8 — itself a worked example of what severity calibration
  exists to catch), `spring-boot-realworld.json` (JVM — an unbatched GraphQL N+1 with a
  working REST counter-example in the same codebase, §3.13), `realworld-axum-sqlx.json`
  (Rust — five findings including a full-table scan the codebase's own source comment calls
  a DoS vector, and a `spawn_blocking` offload correctly *not* flagged as blocking, §3.14),
  `aspnetcore-realworld.json` (.NET — a synchronous EF Core transaction wrapping every
  request inside an `async` handler, §3.15), `node-express-realworld-prisma-postgres.json`
  (Node.js/Prisma — an unbounded relation fetch where the already-computed efficient form is
  silently discarded in favor of the expensive one, §3.16), and
  `gin-realworld-pr49-change-scoped.json` (ground truth for a real, merged PR, testing that a
  review credits a genuine fix while still catching the untouched half of the same problem
  one call deeper, §3.6).
  - Every `forbidden` trap is a false positive the underlying blind pass actually encountered
    and rejected, not a hypothetical one; four files carry a thin or empty `forbidden` list
    rather than one, because their source write-ups describe what was correctly declined
    only in prose without pinning an exact file:line — inventing a location to fill the field
    would violate the same no-invented-facts discipline this corpus exists to hold reviews to.
  - Scored end-to-end against hand-constructed reviews to confirm the harness works on real
    corpus data: a manufactured false positive on one `forbidden` trap correctly halves
    precision and is named in the restraint report; a partial review covering 3 of the
    5-finding Rust file correctly scores 0.6 recall with a per-category breakdown that
    separates a 0.0-recall category from a 1.0-recall one in the same result.
  - Case 2 (a repository whose correct answer is zero findings) remains open: none of the
    nine files satisfy it, consistent with `docs/evaluation.md` §3.7's finding that no
    repository reviewed so far has produced a literal zero-finding result.
  - `benchmark/ground-truth/.gitkeep` removed — the directory documented as "empty on
    purpose" no longer is.
  - Not yet annotated: the second independent pass used for stability measurement (§3.18,
    §3.21) — that needs a second, distinct `review.json` scored with `score.py stability`
    against the first, not a ground-truth file.

### Added — detection

- **Four-level evidence strength on every detection** (`detect_stack.py`). The script already
  graded evidence two ways; `direct` / `indirect` / `weak` / `ambiguous` makes the
  distinction that actually matters visible: a file that *declares* an engine
  (`schema.prisma`'s `provider`, `knexfile.js`'s `client`, a connection scheme in a manifest)
  versus a dependency that merely *implies* one. A declared driver is not proof the datastore
  is reached at runtime, and the review should confirm an indirect match against a real
  import before believing it. Each signal also carries a deterministic `confidence`, capped
  below 1.0 — detection is evidence for a human to verify, not a conclusion.
  - `ambiguous` covers the specific class of bug behavioral evaluation found twice: a short,
    collision-prone token matching only inside a lockfile, where base64 hashes live.
  - The older `weak_evidence` boolean is unchanged and still emitted, so nothing consuming
    it breaks.
  - One bug in the grading was caught by running the detector against this repository: a
    connection-scheme token was graded `direct` even when its only match was in an arbitrary
    YAML file, promoting a documentation mention to the grade of a real datasource
    declaration. Fixed, with a regression test.
- **Service topology for monorepos** (`detect_stack.py`, `methodology/discovery.md` §2). A
  repository with a runtime manifest in more than one subdirectory is not one stack, and the
  flattened union across all of them describes no single service accurately — it routes
  reference files for engines half the services never touch, and scopes findings to "the
  repository" when the reader needs to know which service to fix. `services[]` now carries
  per-service detection and its own `references_to_load`; `workspace_markers` reports
  `pnpm-workspace.yaml`, `lerna.json`, `turbo.json`, `nx.json`, `go.work`, and `rush.json`.
  Nested manifests collapse into their parent service rather than becoming peers.
- **Nineteen new regression tests**, extending the existing corpus rather than starting a
  parallel one:
  - the evidence grades, including the grading bug above;
  - service topology, including nesting and the single-service case;
  - **secret safety, now automated** — `docs/evaluation.md` §2 calls this the check worth
    re-running on every scanner change, and it was previously manual. A real `.env`
    containing a real connection string is scanned, reported present, and proven never read;
  - **hostile repository content** — a README carrying prompt-injection text must produce
    byte-identical detection to one without it, an injected manifest comment must not
    suppress a signal, and a planted `registry.yaml` must not reroute reference loading.
    Repository content is data, never instructions; for a skill pointed at arbitrary
    third-party code, that is a security property and now has tests.
- Three new repository invariants, all mutation-tested before being trusted, per the
  practice `docs/evaluation.md` §1 sets:
  - the schema's `severity`, `confidence`, `priority`, and `category` enums must match
    `SKILL.md` in both directions, so a rubric edit cannot silently leave the schema behind;
  - the worked example must validate against the schema, and every finding in it must carry
    the priority the published matrix derives;
  - the report template must reference the schema, since a schema nothing points at is one
    nobody will keep current.
- `sqs` and `task-queue` promoted to `deep` tier — the fourth batch of the 1.0.0 goal, and
  the point at which **no signal in the registry is `generic` tier any more**:
  - **`technology/sqs.md`** — visibility timeout as the specific lease mechanism behind
    redelivery, Standard's documented at-least-once/best-effort-ordering behavior as
    correctness-relevant rather than a bug, FIFO's per-message-group-ID throughput ceiling,
    and long polling as both a cost and a message-completeness question (short polling can
    under-report what's actually on the queue, not just waste requests).
  - **`technology/task-queues.md`** — a comparative file across Celery, Sidekiq, BullMQ,
    RQ, Dramatiq, Hangfire, Temporal, and Asynq. Leads with backing store as the throughput/
    failure-mode determinant (Hangfire's SQL-backed default sharing the application's own
    database and connection pool, unlike every Redis/broker-backed alternative), and a
    table of default retry behavior that varies enormously and counterintuitively across
    libraries (Sidekiq retries up to 25 times over ~21 days by default; BullMQ/RQ don't
    retry at all unless configured). Flags Temporal explicitly as a durable-execution engine
    rather than a simple job queue, rather than forcing it into the same comparison table.
  - Detection audit found `sqs` had never matched the boto3 Python client, AWSSDK.SQS
    (.NET), or the AWS SDK v2 Java/Gradle coordinate at all — fixed with tokens quoted the
    same way the Node.js `pg` fix was, since bare `sqs` was already known to collide with
    base64 lockfile hashes (the same class the `task-queue` signal's own `rq` removal
    documents).
- PHP and Ruby promoted to `deep` tier — the third batch of the 1.0.0 goal, and the point
  at which **every runtime signal in the registry is `deep`**:
  - **`technology/php.md`** — the shared-nothing, tear-down-per-request execution model as
    the fact that changes what "startup cost" means for this runtime (in-process caches
    don't survive between requests; persistent DB connections trade that cost against a
    real state-leakage risk), OPcache as the single most consequential and most overlooked
    setting, Composer's autoloader production mode, PHP-FPM worker-pool sizing as a direct
    analogue of connection-pool sizing, and the state-leakage risk newer persistent-process
    runtimes (Swoole, RoadRunner, FrankenPHP) reintroduce by inverting that model.
  - **`technology/ruby.md`** — the GVL as the fact determining whether threads or processes
    actually fix a given bottleneck (I/O concurrency vs. CPU parallelism), copy-on-write
    memory sharing across forked workers and how Ruby's own GC has historically undermined
    it, GC heap-growth tuning after boot, Sidekiq concurrency's same GVL constraint applied
    to background jobs, and YJIT's default-on status as of Ruby 3.3.
- `vector-store` and `object-storage` promoted to `deep` tier — the second batch of the
  1.0.0 goal, and the point at which **every datastore signal in the registry is `deep`**,
  including both multi-vendor umbrellas:
  - **`technology/vector-stores.md`** — a comparative file across Pinecone, Weaviate,
    Qdrant, Milvus, Chroma, pgvector, FAISS, and LanceDB. Leads with the table
    `docs/supported-technologies.md` named as the actual gap: what the search-breadth
    parameter is really called in each engine (`ef`, `ef_search`, `nprobe`, `efSearch`,
    `search_ef`, `hnsw.ef_search`...). Also covers per-engine filter-integration mitigations,
    and deployment model as a first-class axis — FAISS as an in-process library with no
    network round trip and no built-in filtering, LanceDB as disk-first by design rather
    than assuming memory residency, and pgvector inheriting the Postgres query planner.
  - **`technology/object-storage.md`** — a comparative file across S3-compatible services,
    GCS, Azure Blob, and MinIO. Resolves the read-after-write consistency question the
    category file deliberately declines to answer, per provider (S3: strong since December
    2020; GCS and Azure: strong since launch; MinIO: depends entirely on the self-hosted
    deployment). Also covers multipart/block/compose mechanics and their real limits per
    provider, archive-tier retrieval as an availability trap rather than only a cost one,
    and MinIO as the one provider where underlying infrastructure is a legitimate review
    question at all.
  - Detection audit found `object-storage` had never been checked per-runtime at all (it
    was `conceptual` tier since inception): all 9 cases tested across Go/.NET/JVM/Node for
    S3/GCS/Azure Blob failed before this promotion. Fixed with 9 new tokens.
  - Also fixed: `pgvector`'s `CREATE EXTENSION vector` detection (a bare migration with no
    package-manifest dependency at all) and LanceDB's renamed Node.js package
    (`@lancedb/lancedb`, formerly `vectordb`).
- Six datastore engines promoted to `deep` tier — the first batch of the 1.0.0 goal of
  leaving no signal below `deep`, and the point at which **every datastore engine in the
  registry is `deep`**:
  - **CockroachDB** (`technology/cockroachdb.md`) — application-visible serializable retry
    errors as required handling rather than an error path, sequential-key hot ranges, the
    Raft consensus floor under write latency, distributed execution, and follower reads.
    Also records the discovery trap that it speaks the PostgreSQL wire protocol, so a repo
    using it names only Postgres drivers and fires the `postgres` signal instead.
  - **Couchbase** (`technology/couchbase.md`) — the KV-versus-N1QL cost gap, resident ratio
    and cache-miss ratio as the health metric, primary indexes masking missing indexes as
    silent full scans, vBucket distribution, and per-operation durability levels.
  - **Cloud Firestore** (`technology/firestore.md`) — per-document read billing as the
    binding constraint rather than latency, mandatory composite indexes alongside
    write-amplifying automatic single-field indexing, the ~1 sustained write/second
    per-document ceiling, monotonic-key hotspots, and security rules incurring billed reads.
  - **Neo4j** (`technology/neo4j.md`) — page cache versus heap as the split that decides
    whether index-free adjacency is actually fast, indexes anchoring a query rather than
    accelerating traversal, the `Eager` and `CartesianProduct` plan hazards, dense-node
    thresholds, and `neo4j://` versus `bolt://` routing.
  - **Amazon Neptune** (`technology/neptune.md`) — three query languages over two data
    models, cluster/reader/instance endpoint routing (and reader-endpoint DNS pinning), the
    instance class as the only memory lever, the bulk loader versus per-row inserts, and
    query timeouts turning unbounded traversals into errors.
  - **InfluxDB** (`technology/influxdb.md`) — the 1.x/2.x/3.x generational split as the fact
    that precedes every other claim (3.x substantially removes the cardinality wall that
    dominates 1.x/2.x reasoning), `inmem` versus TSI deciding whether cardinality growth OOMs
    or merely degrades, shard-duration/retention alignment, and unbatched line-protocol
    writes.

### Fixed

- Detection tokens widened for four of the six engines above, audited per-runtime before
  promoting rather than after: `cockroachdb` (`cockroachlabs`, `crdb_internal`), `couchbase`
  (`gocb`, `com.couchbase`, `CouchbaseNetClient`, `couchbases://`), `firestore`
  (`firestore.indexes.json`), `neo4j` (`Neo4j.Driver`), and `influxdb` (`influxd`,
  `org.influxdb`, `com.influxdb`). The `neptune` entry gained a note that its `gremlin`,
  `sparql`, and `opencypher` tokens name query languages rather than the engine.

### Changed

- Branching model: trunk-based development replaced by a two-branch model — `develop` (the new
  default branch, where work lands) and `main` (the last released state, and the only branch
  tags are cut from), with `hotfix/` branches from `main` back-merged into `develop`. The
  previous model named the exact condition for revisiting it — shipping versioned releases that
  need work to continue independently of them — and that condition now holds: six releases
  landed in the project's first weeks, several cutting a version for what was really one
  afternoon's work. See [CONTRIBUTING.md §2](CONTRIBUTING.md#2-branching-and-pull-requests) and
  the release-cadence note above.

## [0.6.0] — 2026-09-10

### Added

- Oracle Database promoted to `deep` tier: `technology/oracle.md`, covering bind-variable/
  shared-pool hard-parse storms, undo-based read consistency and `ORA-01555`, PL/SQL
  context-switch cost versus `BULK COLLECT`/`FORALL`, bitmap-index OLTP locking risk,
  sequence-`CACHE` contention, and RAC Cache Fusion wait events. The registry's driver
  match list was also widened per-runtime (Go `godror`, .NET `Oracle.ManagedDataAccess`,
  and the `jdbc:oracle:thin:@` connection scheme), rather than repeating the incomplete-
  match-list pattern this session already fixed for six other engines.
- `docs/evaluation.md` §3.21, a second inter-run-consistency blind pass — this time on
  `spring-boot-realworld-example-app` (JVM), a different repository and stack than §3.18's
  `gin-realworld` (Go). All five of the original blind pass's findings (§3.13) reproduced by
  location and mechanism; the priority matrix again applied with zero disagreements. New
  result: two findings' severity escalated between the two runs because SQLite was promoted
  to `deep` tier in between, and the later run cited the new `technology/sqlite.md` directly
  as its reason — direct evidence a tier promotion measurably improves review quality, not
  just changes it.
- `docs/evaluation.md` §3.20, a real-tokenizer context-cost measurement replacing §3.19's
  byte-count proxy: four synthetic stack-breadth scenarios (single-signal to five
  `deep`-tier signals across four categories at once), each routed by actually running
  `detect_stack.py` rather than a hand-assembled file list, tokenized with `tiktoken`'s
  `cl100k_base` encoding. Result: 25.4% of the naive full-corpus baseline for the minimal
  scenario, up to 48.6% for the maximal one — a measured range in place of §3.19's single
  anchored point. Closes #27.

### Fixed

- Driver-coverage audit of every `deep`-tier signal against a real dependency declaration
  per supported runtime (not a hand-picked sample) found nine false negatives of the same
  shape as the already-fixed `sqlite-jdbc` and EF Core SqlServer gaps: a signal's
  `match:` list covering the driver a promotion happened to be tested against, but not the
  dominant driver in every runtime that signal claims to support. Fixed: `postgres` (Go
  `lib/pq`; Node.js `pg`, closing #40), `mysql` (.NET `Pomelo.EntityFrameworkCore.MySql`),
  `sqlite` (.NET `Microsoft.EntityFrameworkCore.Sqlite`; Rust `rusqlite`), `redis` (JVM
  `io.lettuce`, Spring Boot's default Redis client), `rabbitmq` (JVM
  `spring-boot-starter-amqp`; Rust `lapin`), `memcached` (Python `pylibmc`). The audit
  itself is now a committed regression fixture
  (`tests/test_detect_stack_regressions.py::DriverCoverageTests`) run in CI, so a future
  promotion missing its dominant driver in some runtime fails the same way rather than
  waiting to be found by accident during an unrelated evaluation pass.
- The same audit found two cases of a different shape: Node.js's Sequelize and Knex name no
  specific engine in `package.json` at all — the dialect lives only in a separate config
  file (`config/config.json`, `knexfile.js`). `detect_stack.py` now reads both, the same way
  it already reads Prisma's `schema.prisma`: Sequelize's dialect field spells most engines
  out in full (already-covered tokens) except `sqlite`, added as a bare token; Knex's
  abbreviated Postgres client id (`client: 'pg'`) is caught via quoted-literal tokens, the
  same technique used for the Node.js `pg` package fix above.
- `Pipfile`/`Pipfile.lock`, found incidentally while making the fix above: stored
  capitalized in `CONTENT_FILES` while every lookup lowercases the filename first, so a
  real Pipfile's content was silently never read at all, since project creation.

## [0.5.0] — 2026-09-09

### Added

- `technology/clickhouse.md`, promoting the `clickhouse` registry signal from
  `conceptual` to `deep`. Covers MergeTree engine-variant correctness/timing trade-offs
  (`ReplacingMergeTree`/`SummingMergeTree`/`AggregatingMergeTree`/`CollapsingMergeTree`),
  the `ORDER BY`-vs-`PARTITION BY` distinction, the sparse primary index's
  `index_granularity` floor on point-lookup cost, mutations as asynchronous whole-part
  rewrites, "too many parts" as a specific named insert-saturation failure, the default
  join algorithm's in-memory right-hand-table limit, data-skipping secondary indexes, and
  materialized views as insert-triggered rather than query-time. Closes #20.
- `technology/sqlserver.md`, promoting the `sqlserver` registry signal from `conceptual`
  to `deep`. Covers automatic lock escalation (~5,000 locks per statement), RCSI/snapshot
  isolation as an opt-in departure from the locking `READ COMMITTED` default, tempdb as a
  single shared resource behind row versioning/temp tables/spills, parameter sniffing as a
  distinct mechanism from stale statistics, heap forwarding-pointer fragmentation, and
  `WITH (NOLOCK)`'s correctness risk (not just staleness) under a concurrent page split.
  Closes #21.
- `technology/memcached.md`, promoting the `memcached` registry signal from `conceptual`
  to `deep`. Covers the absence of persistence, replication, and clustering as
  architectural (not configurable) facts, client-side sharding/hashing as what decides a
  topology change's blast radius, slab-allocation calcification, per-slab-class segmented
  LRU eviction, `cas` as the only concurrency primitive (no richer data structures to fall
  back on), the multi-threaded worker-thread model as a direct contrast to Redis's
  single-threaded execution, and the fixed item-size ceiling. Also fixes a pre-existing
  registry gap: the `memcached` entry's load list was missing `databases/universal.md`,
  unlike every other datastore/cache entry. Closes #22.
- `technology/sqlite.md`, promoting the `sqlite` registry signal from `conceptual` to
  `deep`. Covers the whole-database-file single-writer lock (a lock in *kind*, not just
  duration, unlike every other relational engine covered), WAL mode as buying concurrent
  readers but never concurrent writers, why a larger connection pool provides no
  additional write throughput here, the database file's location on a network filesystem
  as a correctness precondition rather than a performance question, type affinity making
  an implicit-conversion index miss more likely than the category file's general warning
  suggests, and the absence of any background process (no autovacuum, no auto-`ANALYZE`,
  no checkpoint daemon) that every client-server engine in this skill's coverage has.
  Closes #23.

Coverage is now 19 `deep` · 18 `conceptual` · 2 `generic`, up from 15/22/2. Every engine
named in the roadmap's "Technology promotion candidates" list at the start of this
release cycle is now `deep`.

## [0.4.0] — 2026-09-09

### Added

- `technology/cassandra.md`, promoting the `cassandra` registry signal (also matching
  ScyllaDB) from `conceptual` to `deep`. Covers compaction-strategy trade-offs
  (STCS/LCS/TWCS), engine-specific tombstone thresholds and `gc_grace_seconds`, read repair
  and hinted handoff, lightweight transactions (Paxos) as an expensive anti-pattern when
  overused, `ALLOW FILTERING`/multi-partition `BATCH` as checkable CQL-text red flags, and
  where ScyllaDB's shard-per-core/no-GC architecture diverges from Cassandra's JVM-based one.
  Closes #19.
- `technology/elasticsearch.md`, promoting the `elasticsearch` registry signal (also matching
  OpenSearch and, partially, Solr) from `conceptual` to `deep`. Covers named circuit breakers
  (request/fielddata/parent), heap-vs-OS-page-cache sizing, the three storage layers
  (`_source`, doc values, stored fields), `scroll`/`search_after`/point-in-time as three
  deep-pagination mechanisms with different resource trade-offs, the bulk API's own thread
  pool and its `429`/`EsRejectedExecutionException` rejection mode, and node-role topology as
  a distinct bottleneck surface. Solr's ZooKeeper-based coordination and admin surface are
  explicitly scoped out beyond a brief note. Closes #18.

Coverage is now 15 `deep` · 22 `conceptual` · 2 `generic`, up from 13/24/2.

### Fixed

- The "update CHANGELOG under Unreleased" checklist item existed only in
  `docs/extending.md`, not in `.github/PULL_REQUEST_TEMPLATE.md`'s actual review gates or
  `CONTRIBUTING.md` §6 — both the Cassandra and Elasticsearch PRs above skipped it as a
  result. Added the gate to both, and refreshed `docs/roadmap.md`'s stale
  "Technology promotion candidates" list (Elasticsearch and Cassandra were still listed
  after their own promotion) and its "Project infrastructure" section (both items —
  Markdown link checking, cross-platform CI — had already shipped).
- `scripts/check_repo_invariants.py`: two new checks so this class of drift fails CI
  instead of passing silently — a `deep`-tier registry entry with no `technology/` file in
  its load list (or vice versa: a non-`deep` entry loading one), an orphaned
  `technology/*.md` file nothing loads, and `docs/roadmap.md` still naming an
  already-`deep` signal as a promotion candidate.

## [0.3.0] — 2026-08-31

### Added

- Documented installation for OpenCode, Google Antigravity, and OpenAI Codex CLI in
  `docs/installation.md` §4 and `README.md` — no packaging change was needed, since all three
  either read the existing `.claude/skills/<name>/SKILL.md` layout directly (OpenCode) or use
  the same `.agents/skills/<name>/SKILL.md` convention as each other (Antigravity, Codex CLI).
  Checked against each tool's published documentation, not run end-to-end against a local
  install of any of the three — see the caveat added to the conventions verification record.
- `distributed/multi-tenancy.md` — the one v2 domain named in the original build specification
  (`guideline.md` §12.2) with no coverage at all until now: isolation models (silo, pool, bridge,
  shared-everything), noisy-neighbor mechanics on a shared connection pool/cache/thread pool, the
  presence-or-absence of per-tenant fairness as the single highest-value check, and the
  tenant-size-skew failure mode where a schema tuned for the median tenant fails at the outlier.
  Routed from `SKILL.md`'s reference-routing table and from `methodology/discovery.md`'s
  shared-resources checklist.
- `principles/resources.md` §8, "Observability overhead" — the cost of instrumentation itself
  (hot-path log volume, unbounded metric-label cardinality, trace sampling rate as a coverage/cost
  trade-off, APM/agent per-request overhead, the self-monitoring feedback loop), closing the gap
  where the finding-format's `Category: observability` had no reference file whose findings were
  expected to carry it — see the routing-coverage invariant below, which is what surfaced this gap.
- A `Category` column in `SKILL.md`'s reference-routing table, and
  `scripts/check_repo_invariants.py`'s new `check_category_routing_coverage` check enforcing that
  every value in the finding-format `Category:` enum resolves to at least one routing-table row.
  Mutation-tested by removing `observability` from the table and confirming the check fails with
  the expected message before restoring it. Closes a real gap the existing architecture self-check
  did not catch: `observability` and `cost` findings had nowhere to route to.
- File-level match provenance in `scripts/detect_stack.py`. `scan()` now returns per-file records
  instead of one flattened corpus string, and `detect()` attributes every match to the specific
  file(s) it came from, grading a signal `weak_evidence: true` when every match for it landed only
  in a non-manifest YAML file (a CI workflow, a k8s values file, arbitrary docs) rather than a real
  dependency manifest, lockfile, or matching filename. Self-scanning this repository previously
  reported roughly thirty spurious signals (Cassandra, Oracle, PHP, Kubernetes, and more) with no
  way to tell they were all matching inside `registry.yaml` itself — the one file in the repo that
  necessarily contains every match token in the system by construction. They are now all correctly
  flagged weak and traced to that one file, and `main()` surfaces a single actionable warning
  rather than silently returning them as ordinary detections. `detect()` still accepts a plain
  string corpus for backward compatibility, so the ten existing regression tests pass unchanged;
  five new tests cover the grading logic. `methodology/discovery.md` now tells the agent how to
  read the new field.
- Two further independent blind passes, closing two of the three untested `deep`-tier
  runtime-coverage gaps named in `docs/evaluation.md` §3.12: JVM
  (`gothinkster/spring-boot-realworld-example-app`, §3.13) and Rust
  (`launchbadge/realworld-axum-sqlx`, §3.14) — see Evaluation below.
- `docs/examples/node-mongo-redis.md` — the second worked example, delivering the "Planned"
  Node.js + MongoDB + Redis example named in v0.2.0. Synthetic per the examples policy (§16 of
  `guideline.md`): an Express + Mongoose + Redis/BullMQ service with a per-post N+1 query loop, a
  synchronous `bcrypt.hashSync` call blocking Node's single event-loop thread for every
  concurrently-open connection on that process (not just the request that triggered it), a
  connection-pool-arithmetic check against a cited MongoDB Atlas tier limit, and a `KEYS`-based
  cache invalidation contending with a BullMQ queue sharing the same Redis instance. Carries the
  required explicit unknowns and four deliberately-declined optimizations with stated reasons.

### Fixed

- `registry.yaml`'s `sqlite` signal never matched `org.xerial:sqlite-jdbc` (the dominant Maven
  Central artifact for SQLite on the JVM) or the `jdbc:sqlite:` connection-string scheme — found
  during the JVM blind pass (§3.13), which located the datastore only because
  `methodology/discovery.md`'s "check connection-string schemes regardless of the accelerator's
  output" instruction told it to read `application.properties` by hand. An agent that trusted the
  accelerator's zero-datastore result would have skipped `databases/relational.md` entirely and
  missed two of that review's three highest-priority findings. Fixed, with two new regression
  fixtures in `tests/test_detect_stack_regressions.py`, verified to fail against the pre-fix
  registry before being accepted, per this project's own regression-fixture discipline (§4).
- `technology/rust.md` described Tokio's blocking-thread pool only as "usually small by default,"
  with no concrete default or the `Builder::max_blocking_threads` configuration knob — found
  during the Rust blind pass (§3.14), which had to reason from general Tokio knowledge outside the
  file to correctly score a finding as bounded rather than unbounded. Now states the default (512
  threads) and names the builder method.
- `SKILL.md` Phase 1 now states explicitly that `references_to_load` is necessarily partial: it
  structurally cannot include an "Always available" routing-table row triggered by a *usage
  pattern* (`application/data-access.md`, `application/connection-pools.md`,
  `application/serialization.md`) rather than a registry-matched technology signal. Found during
  the Rust blind pass, which caught the resulting gap in that specific review only by
  cross-checking the routing table by hand rather than trusting the accelerator's output as
  complete — a documentation fix ensuring a less careful pass is told to do the same.

### Evaluation

- `docs/evaluation.md` §3.13–§3.15: two further independent blind passes (JVM, Rust), each with no
  memory of this project's own findings and — unlike every prior blind pass — no prior author
  review of the target repository to compare against at all, only the choice of repository and
  the prompt: the first fully author-uninvolved runs in this evaluation's history. Both reproduced
  the pattern established across §3.8–§3.11 (a `Critical`/`P0` finding with direct code-level
  evidence, at least one real correctness or security issue filed correctly under rule 8's
  "Adjacent findings" convention on a repository that convention had never been tested against),
  and both additionally did something none of §3.8–§3.11 did: found a real, now-fixed bug in the
  skill's own detection or reference content, not only missed evidence in the target repository.
  Status table, required-cases table, and known-gaps list (§5) updated accordingly; only .NET
  remains among the `deep`-tier runtimes with no independent pass.

## [0.2.0] — 2026-08-26

### Added

- `technology/node.md` — the first per-runtime technology reference, promoting Node.js from
  `conceptual` to `deep`. Covers what `runtimes/universal.md`'s taxonomy deliberately leaves
  generic: event-loop phase ordering and microtask-starvation as a distinct failure mode from a
  single blocking call, the libuv thread pool's small default size and the specific operations
  that share it, the concrete mechanisms (`cluster`, `worker_threads`) behind the runtime's
  "parallelism: no, or restricted" dimension, and V8's default heap ceiling as a concrete,
  checkable instance of the container-memory-limit interaction the taxonomy describes
  abstractly.
- `technology/python.md` — promotes Python (CPython) to `deep`. The WSGI-vs-ASGI concurrency-
  model split as the single fact to establish before scoring anything (the identical blocking
  call is a bounded capacity cost under one model and a `Critical`, system-wide-for-that-worker
  finding under the other); the GIL as the concrete mechanism behind the runtime's parallelism
  dimension, including why "we added threads to a CPU-bound job and got no speedup" is expected
  behavior, not a bug to chase; reference counting as the primary (not generational-only)
  memory-reclamation path; and the monkey-patched-cooperative-concurrency failure mode where one
  unpatched dependency blocks a whole `gevent`/`eventlet` worker silently.
- `technology/jvm.md` — promotes JVM (Java/Kotlin) to `deep`. The thread-per-request vs.
  reactive-event-loop vs. virtual-thread (JDK 21+) concurrency-model split that determines the
  severity of an identical blocking call, including carrier-thread pinning as the specific way
  virtual threads' cheap-blocking property gets silently defeated; JIT warm-up as a first-class,
  structural performance dimension rather than a footnote; the real trade-off between GC
  algorithms (Parallel, G1, ZGC/Shenandoah) instead of one fixed collector; and off-heap/metaspace
  memory as invisible to heap-only monitoring, including the pre-JDK-8u191/JDK-10
  container-awareness gap as a real, dangerous default on older images.
- `technology/go.md` — promotes Go to `deep`. The M:N goroutine scheduler that makes a blocking
  call mostly (not entirely) cheap — with the two failure modes that survive it: pre-1.14
  cooperative-preemption starvation from a tight CPU-bound loop, and unbounded goroutine spawning,
  which nothing in the language bounds on its own; goroutine leaks (a goroutine blocked forever on
  a channel) as this runtime's most distinctive resource-growth pattern; `GOMAXPROCS` defaulting
  to host CPU count as a specific, checkable container-CPU-quota mismatch; and the GC's
  budget-based (`GOGC`/`GOMEMLIMIT`) tuning model, a genuinely different trade-off shape from the
  JVM's choice between collector algorithms.
- `technology/dotnet.md` — promotes .NET (the modern, cross-platform CLR) to `deep`. Thread-pool
  starvation from sync-over-async (`.Result`/`.Wait()`/`.GetAwaiter().GetResult()`) as the CLR's
  core concurrency failure mode, with its own distinct symptom shape (progressive latency
  degradation from the thread pool's slow hill-climbing ramp-up, not an immediate collapse); the
  narrower sync-over-async deadlock condition, which depends on `SynchronizationContext` capture
  and is correspondingly less likely under ASP.NET Core's default hosting than under classic
  ASP.NET; value-type boxing as a checkable, silent reintroduction of GC pressure a struct was
  meant to avoid; and Server-vs-Workstation GC as the throughput/footprint trade-off, alongside a
  genuinely positive note that .NET Core 3.0+ already reads container cgroup limits by default
  (unlike the JVM's and Go's historical gaps in this area).
- `technology/rust.md` — promotes Rust to `deep`, completing the six per-runtime references
  planned for this coverage push. The absence of a garbage collector entirely (deterministic
  RAII-based reclamation), with `Rc`/`Arc` reference cycles as the one leak vector that survives
  it since Rust has no cycle-detecting collector; the fact that the async runtime (almost always
  Tokio) is a third-party ecosystem choice rather than a language or stdlib feature; the narrower
  blast radius of a blocked worker thread under Tokio's default multi-threaded scheduler versus a
  genuinely single-threaded event loop, with `spawn_blocking` as the idiomatic fix; static
  dispatch via monomorphization as the default, with `dyn Trait` as an explicit, checkable opt-in;
  and the recurring evaluation mistake of drawing conclusions from a debug build instead of
  `--release`.
- `technology/mysql.md` — promotes MySQL/MariaDB to `deep`. InnoDB's clustered-index primary-key
  design (the single highest-value InnoDB-specific check: a random/UUID primary key fragments the
  table on every insert, unlike a heap-organized engine), `REPEATABLE READ` gap/next-key locking
  as a specific, checkable source of contention and deadlocks that a `READ COMMITTED`-default
  mental model under-predicts, buffer-pool sizing conventions that are not interchangeable with
  Postgres's `shared_buffers` guidance, and the query-cache-as-write-bottleneck hazard on pre-8.0
  deployments.
- `technology/dynamodb.md` — promotes DynamoDB to `deep`, completing both roadmap-listed
  `conceptual`-engine promotions. Partition-key-driven hot partitions as the concrete mechanism
  behind DynamoDB's throughput distribution (with adaptive capacity's real but bounded
  mitigation); `Scan`'s per-item-examined billing model as the reason a request-path scan is a
  severe finding here specifically, not just a slow one; a Global Secondary Index's separate
  capacity and how its throttling propagates back to the base table's writes; the hard 400 KB
  item-size ceiling versus the graduated "large values cost more" reasoning generic key-value
  stores get; the eventual-vs-strong consistency cost trade-off, including that GSIs support
  eventually consistent reads only; and TTL's best-effort (not precision) deletion timing.
- `technology/kafka.md` — promotes Kafka/Redpanda to `deep`, the first message-broker reference.
  Consumer-group rebalancing as the concrete mechanism behind partition-bounded parallelism, with
  blast radius differing sharply between the eager and cooperative-sticky protocols; exceeding
  `max.poll.interval.ms` as the specific way a still-alive, still-processing consumer gets evicted
  and triggers a group-wide rebalance; Kafka's log-not-queue retention model (disk usage driven by
  retention configuration and write rate, independent of consumer speed) versus log compaction for
  changelog topics; `acks` as a named durability/latency spectrum, with `acks=1` as a
  correctness-adjacent finding on data treated as durable; and why increasing partition count on
  an existing topic breaks per-key ordering continuity rather than being a free scaling lever.
- `technology/rabbitmq.md` — promotes RabbitMQ to `deep`, completing both planned message-broker
  references. The cluster-wide memory-alarm mechanism, by which one backed-up classic queue can
  stall publishing on every unrelated queue on the same node — the single most severe,
  RabbitMQ-specific failure mode in this skill's broker coverage; why a single queue (not
  partitioned by default, unlike Kafka) becomes its own throughput ceiling once consumers stop
  being the bottleneck; exchange routing's per-binding publish-time cost, distinct from
  downstream fan-out cost; prefetch/QoS as the concrete knob behind competing-consumer
  throughput and fairness; and `consumer_timeout` closing the channel outright, rather than
  silently rebalancing the way Kafka's `max.poll.interval.ms` does.
- `databases/object-storage.md` — the last category file, closing the gap flagged since v0.1.0's
  follow-up work. Request count (not data volume) as the dominant cost dimension for an API with
  no query planner; key/prefix design as the only access structure, with unbounded listing
  followed by client-side filtering as the sharpest instance of "the workload belongs in a
  different category of store" this skill describes; full-object immutability making every
  update a complete rewrite, with no field-level operation to fall back on; multipart upload as a
  hard-limit workaround, not just a performance tool; and request/egress cost as a first-class
  dimension distinct from latency, unlike every other category this skill covers. Adds an
  `object-storage` signal (S3-compatible, GCS, Azure Blob, MinIO) at `conceptual` tier.
- `tests/test_detect_stack_regressions.py` and a `detection-regressions` CI job: automates the
  false-positive/false-negative fixture corpus specified in `docs/evaluation.md` §4 — the
  `rq`/`koa`-in-lockfile-hash and `gin`/`echo`-bare-word collisions, and the parser's
  `_strip_quotes` escaped-quote bug. Each fixture reproduces the actual collision text found
  during behavioral evaluation (not a synthetic worst case) and was verified to fail against the
  pre-fix code before being accepted as a real regression guard.
- `scripts/check_repo_invariants.py` and `.github/workflows/checks.yml`: the structural
  invariants CONTRIBUTING.md commits this project to (registry integrity, category-file
  product-neutrality, technology-file structure, priority-matrix consistency, published tier
  counts, stdlib-only detection script) are now checked automatically on every push and pull
  request, and were mutation-tested against four deliberate regressions before being trusted.
- `SKILL.md` rule 8 and an "Adjacent findings — outside performance scope" report section: a
  real security, correctness, or maintenance issue noticed while reviewing for performance now
  gets the same full write-up as a performance finding (Problem, Evidence, Recommendation,
  Trade-offs, Validation) under a `SEC-`/`COR-`/`MAINT-` ID, classified on `Kind`, the existing
  evidence-grade `Confidence` scale, and a plain-language `Risk` note — never `Severity` or
  `Priority`, and never a fabricated CVSS-style score.
- `databases/graph.md` — the first new datastore category since v0.1.0: index-free adjacency,
  traversal-depth growth, supernodes as the graph-native form of a hot key, and cartesian
  products from unconnected match patterns. Neo4j promoted from `generic` to `conceptual`;
  Amazon Neptune added at `conceptual`.
- `databases/wide-column.md` — covers both sub-models the category actually contains:
  partition-oriented (partition/clustering key design, unbounded-partition risk, consistency-
  level arithmetic) and column-oriented analytical (scan cost, sort-key pruning, batched-insert
  requirements), sharing a common LSM/merge-tree storage-mechanism section. Cassandra/ScyllaDB
  and ClickHouse promoted from `generic` to `conceptual`.
- `databases/search.md` — filter context vs. query context as the single highest-value search
  finding (a non-ranking clause scored instead of cached), why deep offset pagination is worse
  in a sharded search index than anywhere else in this skill, mapping decisions made at
  index-creation time, and aggregation cardinality as a memory-exhaustion risk distinct from
  relational `GROUP BY`. Elasticsearch/OpenSearch/Solr promoted from `generic` to `conceptual`.
- `databases/time-series.md` — series cardinality as the category's defining failure mode (the
  one modeling mistake in this skill that degrades writes, queries, and memory simultaneously),
  the tag-vs-field decision that creates or prevents it, retention/downsampling as the default
  answer to unbounded growth, and out-of-order writes as a distinctly more expensive case than
  in-order ingestion. InfluxDB promoted from `generic` to `conceptual`.
- `databases/vector.md` — the last of the five datastore category files planned for v0.1.0's
  follow-up. Approximate nearest-neighbor search as the one category in this skill whose index
  is deliberately inexact (every other category's indexes are exact-match); the
  recall/latency/memory triangle and the search-breadth parameter as its central, most
  overlooked tunable; filter-then-search vs. search-then-filter as the most common severe
  failure mode ("asked for 10 results, got 2"); and memory residency as a sharper cliff here
  than elsewhere because ANN structures are traversed close to randomly. Distance-metric
  mismatch is explicitly scoped to the "Adjacent findings" section as a correctness issue, not a
  performance one. Pinecone/Weaviate/Qdrant/Milvus/Chroma/pgvector/FAISS/LanceDB promoted from
  `generic` to `conceptual`. Coverage is now 3 deep / 31 conceptual / 4 generic.

### Fixed

- `rubrics.md`: `Informational` severity was being used, in practice, as a place to park
  out-of-scope security/maintenance findings — but `Informational` is defined as "no current or
  projected impact," a performance claim, and applying it to a real vulnerability reads as "safe
  to deprioritize." See the "Adjacent findings" addition above.
- `.claude-plugin/marketplace.json` and `.claude-plugin/plugin.json` were missing
  `metadata.description` and `author` respectively — caught by running `claude plugin validate`
  for real rather than relying on documentation alone. The full marketplace-add/install/list
  cycle was then run against the live repository and confirmed working end-to-end.

Found during behavioral evaluation against real public repositories (see `docs/evaluation.md`
§3):

- `detect_stack.py`: quoted registry match tokens containing an escaped inner quote (e.g.
  `"\"node\":"`) were never unescaped, so they never matched anything.
- `registry.yaml`: `rq`, `koa`, `gin`, and `echo` were short enough to match unrelated content
  (lockfile hashes, the word "logging", shell `echo` commands). Go framework signals now use
  their fully-qualified module path; the task-queue signal no longer matches bare `rq`.
- `registry.yaml`: the `node` signal cannot distinguish a Node.js backend from front-end
  tooling in a full-stack repo. Documented in the signal's `notes` rather than removed.

### Evaluation

Behavioral evaluation (`docs/evaluation.md` §3) now covers 5 of 6 required cases, across four
real public repositories:

- A real, merged pull request reviewed in change-scoped mode against its actual diff (§3.6).
- Real Go benchmarks executed and cited as `Confirmed`-grade evidence (§3.5).
- No repository reviewed so far has produced zero findings — reframed as a result worth keeping,
  not a failed search (§3.7).
- The independent blind pass extended from one repository to all four (§3.8–3.11), summarized in
  §3.12: an agent with no memory of this project's own findings, given only the shipped skill,
  reproduced or exceeded the manual review's primary finding on every repository, and on three of
  the four found real evidence the manual review had missed — including a hard `SyntaxError` in
  `fastapi/full-stack-fastapi-template` that a careful reading alone had not caught, confirmed by
  actually running a parser against every file rather than only reading them.

### Removed

- `CODE_OF_CONDUCT.md` — an unmodified Contributor Covenant for a project with no contributors
  yet. Its one project-specific line moved into `CONTRIBUTING.md`.

### Planned

- Examples for Node.js + MongoDB + Redis, and Go + Neo4j.

---

## [0.1.0] — 2026-08-25

Initial release. Depth-first scope: a complete methodology with narrow but honest technology
coverage, rather than broad shallow coverage.

### Added

**Methodology core**

- `SKILL.md` — doctrine, both review modes, seven-phase workflow, rubrics, finding format,
  safety rules, and reference routing.
- `rubrics.md` — expanded scoring guidance with five worked examples, including one that
  correctly produces no findings.
- `registry.yaml` — 37 detection signals mapping to reference files and support tiers; the
  single source of truth for both routing and the published support table.
- `methodology/` — discovery, workload, critical paths, bottleneck analysis, validation.
- `templates/review-report.md` — report structure with layer gating.

**Reference content**

- `principles/` — latency, throughput, concurrency and contention, resources, work and
  algorithms.
- `application/` — API surface, data access, async and blocking, serialization, connection
  pools.
- `databases/` — universal, relational, document, key-value.
- `runtimes/universal.md` — execution, parallelism, concurrency, memory, and startup taxonomy.
- `distributed/` — timeouts and deadlines, retries and backpressure, caching.
- `infrastructure/resources.md` — containers, limits, autoscaling, probes, serverless.
- `technology/` — PostgreSQL, MongoDB, Redis at `deep` tier.

**Tooling**

- `scripts/detect_stack.py` — read-only stack detection. Python 3.8+, standard library only,
  no network access. Never reads files matching secret patterns; reports their presence only.

**Documentation**

- README with an honest support-tier table generated from the registry.
- `docs/installation.md`, `docs/architecture.md`, `docs/extending.md`,
  `docs/supported-technologies.md`, `docs/evaluation.md`.
- `docs/examples/fastapi-postgres.md` — a worked review, deliberately placed outside the
  loadable reference path.
- CONTRIBUTING with binding authoring rules and review gates.

### Design decisions worth recording

- **Two scored axes, derived priority.** Severity and an evidence-graded confidence are
  scored; priority comes from a published matrix. Earlier drafts of the specification carried
  four overlapping taxonomies with no rubric, which makes rankings irreproducible and drifts
  every finding toward "High".
- **Zero findings is a valid result.** Stated explicitly in `SKILL.md`, because completion
  pressure otherwise manufactures findings — which violates the project's own anti-cargo-cult
  rule.
- **Workload interview is bounded and non-blocking.** At most seven questions, asked once. An
  unanswered interview caps workload-dependent confidence at `Medium` rather than stalling the
  review.
- **Observability is inventoried in Phase 1, not last.** Available evidence sets the ceiling on
  achievable confidence for the entire review.
- **Examples live outside `skills/`.** Few-shot content leaks; an example containing a
  fabricated latency figure risks that figure appearing in a real report.
- **Technology files carry only non-derivable content.** Without this rule, N engine files
  become N checklists.
- **Support tiers are published.** `deep` / `conceptual` / `generic`, generated from the
  registry, so documented support cannot drift from actual content.

### Known limitations

- Only three engines at `deep` tier; graph, wide-column, search, time-series, and vector
  categories have no category file yet and degrade to universal reasoning.
- Behavioral evaluation against public repositories is specified but not yet executed.
- `detect_stack.py` reads a deliberately small YAML subset; extending `registry.yaml` beyond
  that subset requires extending the reader.

[Unreleased]: https://github.com/Sanoy24/backend-performance-review/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Sanoy24/backend-performance-review/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Sanoy24/backend-performance-review/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Sanoy24/backend-performance-review/releases/tag/v0.1.0
