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

### Fixed

Found by Round 1 of the behavioral evaluation (`docs/evaluation.md` §3), run against two real,
unmodified public repositories — not by the architecture self-check, which only verifies the
registry is internally consistent, not that it matches real files correctly.

- **`scripts/detect_stack.py`: `_strip_quotes` silently dropped escaped inner quotes.** A
  registry match token written as `"\"node\":"` — intended to match the literal `"node":` in a
  `package.json` — parsed to the four literal characters `\"node\":`, which never matches
  anything. This token had been dead since v0.1.0; nothing exercised it because the `node`
  signal's other, broader tokens always fired alongside it. This is the more serious of the two
  fixes: it is a silent false *negative* with no visible symptom, versus the false *positives*
  below, which at least produce noticeable wrong output.
- **`registry.yaml`: three match tokens were short or common enough to collide with unrelated
  content.** `rq` and `koa` matched inside base64-encoded hash fragments in `go.sum`; `gin` and
  `echo` matched inside the English word "logging" and ordinary shell `echo` commands in CI
  YAML. Fixed by requiring fully-qualified module paths for the Go frameworks
  (`gin-gonic/gin`, `labstack/echo`, `gofiber/fiber`), narrowing the task-queue signal to
  `django-rq`/`python-rq`, and quoting `koa` as `"koa":`.
- **`registry.yaml`: the `node` signal's `package.json`/`node_modules` tokens cannot distinguish
  a Node.js backend from front-end tooling in a full-stack repository.** Not removed — doing so
  loses real detection value — but now flagged in the signal's `notes`, which surfaces in a
  report's scope section.

### Planned

- Category files for graph, wide-column, search, time-series, and vector datastores.
- Per-runtime technology references: Node.js, Python, JVM, Go, .NET, Rust.
- Deep references for MySQL and DynamoDB.
- Message-broker references for Kafka and RabbitMQ.
- Examples for Node.js + MongoDB + Redis, and Go + Neo4j.
- Behavioral evaluation cases 2 (no-problem repo), 4 (committed runtime evidence), and 5
  (change-scoped PR review) — see `docs/evaluation.md` §3.4.
- An independent (non-author) or blind pass of the behavioral evaluation.
- Automated regression tests for the three fixture cases identified in
  `docs/evaluation.md` §4.

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
