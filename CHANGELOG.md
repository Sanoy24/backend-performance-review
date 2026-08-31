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

---

## [Unreleased]

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
