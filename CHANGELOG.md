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
- A first independent pass: an agent with no memory of this project reproduced the core finding
  on its target repository, caught one the author's review had missed, and corrected that
  review's severity score using evidence it had overlooked (§3.8).
- No repository reviewed so far has produced zero findings — reframed as a result worth keeping,
  not a failed search (§3.7).

### Removed

- `CODE_OF_CONDUCT.md` — an unmodified Contributor Covenant for a project with no contributors
  yet. Its one project-specific line moved into `CONTRIBUTING.md`.

### Planned

- A category file for object storage (S3-compatible/blob stores) — listed as a valid category in
  `registry.yaml`'s schema comment since v0.1.0 but never given a category file or any registry
  entries. Found while closing out the other five category files; not otherwise tracked
  anywhere. This category's performance model differs enough from the other eight (no rich
  query/index layer; the concerns are request-count minimization, multipart/batch operations,
  key/prefix design for listing performance, and egress cost) that it deserves its own file
  rather than a placeholder.
- A message-broker reference for RabbitMQ.
- Examples for Node.js + MongoDB + Redis, and Go + Neo4j.
- Extend the independent blind pass (`docs/evaluation.md` §3.8) to the other three evaluated
  repositories; currently covers one of four.
- Automated regression tests for the fixture cases identified in `docs/evaluation.md` §4.

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

[Unreleased]: https://github.com/Sanoy24/backend-performance-review/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Sanoy24/backend-performance-review/releases/tag/v0.1.0
