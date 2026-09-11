# backend-performance-review

**AI-assisted performance engineering with evidence, workload context, and validation** —
on any language, framework, runtime, or datastore.

It is not a checklist, and it is not a linter. It is a methodology an AI coding agent
follows, plus reference files it loads only when the detected stack calls for them. Its
most distinctive property is what it refuses to do: it will not invent a number, and it
will return zero findings rather than manufacture one.

Packaged as an open-source **Agent Skill**.

```
Performance principle → observed implementation → technology manifestation
  → evidence → bottleneck → impact under stated workload → recommendation → validation
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-informational.svg)](CHANGELOG.md)
[![checks](https://github.com/Sanoy24/backend-performance-review/actions/workflows/checks.yml/badge.svg)](.github/workflows/checks.yml)

---

## In sixty seconds

> **You:** Review this PR for performance.

> **Agent:** **WARN** — 1 finding: 1 P1.
>
> **PERF-001 — P1 / High confidence**
> Building the order list issues one datastore query per order rather than one query for
> all of them. `src/orders/service.py:84`
>
> **Matters when:** a single request returns more than a handful of orders. Request rate is
> unknown, so this is scored on growth, not on measured load.
>
> **May not matter if:** orders per user are bounded in the low tens by a product constraint
> not visible in this code.
>
> **Counter-evidence:** no memoization wraps the lookup; no pagination bounds the route; the
> ORM is not configured for eager loading here.
>
> **Suggested:** batch the related lookup into one query — preferred over caching, because it
> removes the work rather than hiding it.
>
> **Validation:** query count per request should fall from one-per-row to a constant.
> *Falsifier: if query count drops and latency does not, this path was not query-bound and
> the impact was overstated.*
>
> *No runtime evidence was supplied, so nothing here is measured.*

Fix the query, push, and the check re-runs:

> **Agent:** **PASS** — no performance regression found in the changed paths.
> 1 improvement: the per-row lookup is now a batched query.

Note what is *absent*: no invented p99, no "add Redis", no severity without a workload to
justify it, and an explicit statement of what would prove the finding wrong. Every one of
those omissions is a rule, not an accident — see [Honest limitations](#honest-limitations).

Run it on every pull request with the [GitHub Action](docs/github-action.md). Advisory by
default; it does not block merges.

---

## Why this exists

Ask a coding agent to "review this backend for performance" and you usually get one of two
failure modes:

1. **A generic checklist.** "Check your indexes. Consider caching. Watch out for N+1." All
   true, none actionable, no relationship to the code in front of it.
2. **Confident fabrication.** Invented p99 latencies, imagined query plans, made-up cache
   hit rates, and a recommendation to add Redis to a service with fourteen users.

Both come from the same root cause: no discipline about evidence, and no model of workload.
A finding that cannot say *what workload makes this matter* and *what evidence supports it*
is not a finding.

This skill enforces that discipline. Its most distinctive rule is that **returning zero
findings is a valid, successful result** — because the alternative is an agent that
manufactures problems to fill a report.

## What it does

- **Detects the stack** from manifests, lockfiles, container and infrastructure config —
  optionally via a bundled read-only Python script.
- **Builds a workload model** from repository evidence (load tests, autoscaling config, pool
  sizes, retention jobs, alert thresholds), then asks you at most seven questions once. If
  you do not answer, it proceeds and caps its own confidence accordingly.
- **Loads only relevant references.** A Postgres service never loads the document-store file.
- **Scores every finding on two axes** — severity and an evidence-graded confidence — and
  *derives* priority from a published matrix, so rankings are reproducible rather than vibes.
- **Refuses to invent numbers.** Every figure in a report traces to a file, to something you
  supplied, or to a labelled derivation.
- **Produces a validation plan** with every recommendation, including a falsifier and a
  production-safety label on every diagnostic command.

## What it does not do

It does not modify code, run anything against your production systems, replace a profiler
or APM, or perform security or correctness review. It reads, reasons, and reports.

---

## Installation

### Claude Code — as a plugin

```bash
/plugin marketplace add Sanoy24/backend-performance-review
/plugin install backend-performance-review
```

### Claude Code — as a project or personal skill

Copy the skill directory into either location:

```bash
# project-scoped (shared with your team via the repo)
mkdir -p .claude/skills
cp -r path/to/backend-performance-review/skills/backend-performance-review .claude/skills/

# personal (available in every project)
cp -r path/to/backend-performance-review/skills/backend-performance-review ~/.claude/skills/
```

### OpenCode, Antigravity, Codex CLI, and other coding agents

The methodology is vendor-neutral Markdown. Only the YAML frontmatter is Claude-specific, and
it is ignored elsewhere.

- **OpenCode** reads the same `.claude/skills/<name>/SKILL.md` path Claude Code uses — the
  copy step above already covers it.
- **Google Antigravity** and **OpenAI Codex CLI** both discover skills under
  `.agents/skills/<name>/SKILL.md` — copy or symlink the directory there.
- **Any other agent**: point it at `skills/backend-performance-review/SKILL.md` and let it
  follow the reference paths from there.

Full details, exact paths, and how skill discovery works: **[docs/installation.md](docs/installation.md)**.

---

## Usage

Once installed, ask naturally:

```
Review this service for performance problems.
Why is the /orders endpoint slow?
Will this scale to 10x traffic?
Does this PR introduce a performance regression?
```

Or invoke it directly: `/backend-performance-review`

**Two modes.** A *full review* covers the whole repository. A *change-scoped review* covers a
diff, branch, or PR — cheaper, and the mode most worth running continuously.

### What to expect

The skill will ask you up to seven workload questions before analyzing. Answering improves
the ranking substantially; declining is fine, and the report will say which conclusions
would change if you had answered.

Expect fewer findings than a generic linter produces, each with more behind it.

---

## How it works

```
skills/backend-performance-review/
├── SKILL.md         doctrine, workflow, rubrics, finding format, routing
├── registry.yaml    detection signal → references to load + support tier
├── rubrics.md       expanded scoring, with worked examples
├── methodology/     discovery · workload · critical paths · analysis · validation
├── principles/      latency · throughput · concurrency · resources · work
├── application/     api · data access · async · serialization · pools
├── databases/       universal + one file per category
├── runtimes/        execution/memory/concurrency model taxonomy
├── distributed/     timeouts · retries & backpressure · caching
├── infrastructure/  containers · limits · autoscaling · serverless
├── technology/      one file per engine — only non-derivable content
├── templates/       the report structure
└── scripts/         detect_stack.py — read-only, stdlib-only
```

Two rules keep this from becoming a pile of overlapping documents:

- **Category files never name a product.** If `databases/relational.md` mentions a specific
  engine, it is leaking.
- **Technology files contain only what their category file does not give you.** If
  `technology/postgres.md` explains what an index is, it is wrong.

More: **[docs/architecture.md](docs/architecture.md)**.

---

## Supported technologies

Support is tiered honestly. Every engine this skill detects is now `deep` tier — an
unrecognized one still gets a real review — the methodology still applies, and the report
says so in its scope section.

| Tier | Meaning |
|:--|:--|
| **Deep** | Dedicated reference: engine-specific failure modes, diagnostics, and config trade-offs |
| **Conceptual** | Category principles apply in full; no engine-specific file yet |
| **Generic** | Universal methodology only; the skill degrades gracefully and marks specifics as unknown |

### Datastores

| Technology | Category | Tier |
|:--|:--|:--|
| **PostgreSQL** | relational | **deep** |
| **MongoDB** | document | **deep** |
| **MySQL / MariaDB** | relational | **deep** |
| **SQL Server** | relational | **deep** |
| **Oracle Database** | relational | **deep** |
| **SQLite** | relational | **deep** |
| **CockroachDB** | relational | **deep** |
| **Couchbase** | document | **deep** |
| **Cloud Firestore** | document | **deep** |
| **DynamoDB** | key-value | **deep** |
| **Neo4j** | graph | **deep** |
| **Amazon Neptune** | graph | **deep** |
| **Cassandra / ScyllaDB** | wide-column | **deep** |
| **ClickHouse** | wide-column | **deep** |
| **Elasticsearch / OpenSearch / Solr** | search | **deep** |
| **InfluxDB** | time-series | **deep** |
| **Pinecone / Weaviate / Qdrant / Milvus / Chroma / pgvector / FAISS / LanceDB** | vector | **deep** |
| **S3-compatible / GCS / Azure Blob / MinIO** | object-store | **deep** |

### Caches, brokers, runtimes, infrastructure

| Technology | Tier |
|:--|:--|
| **Redis / Valkey** | **deep** |
| **Memcached** | **deep** |
| **Kafka / Redpanda** | **deep** |
| **RabbitMQ** | **deep** |
| **Amazon SQS** | **deep** |
| **Celery / Sidekiq / BullMQ / RQ / Dramatiq / Hangfire / Temporal / Asynq** | **deep** |
| **Node.js** | **deep** |
| **Python (CPython)** | **deep** |
| **JVM (Java / Kotlin)** | **deep** |
| **Go** | **deep** |
| **.NET** | **deep** |
| **Rust** | **deep** |
| **PHP** | **deep** |
| **Ruby** | **deep** |
| **REST (FastAPI, Flask, Django, Express, NestJS, Koa, Gin, Echo, Fiber, Spring Boot, Actix, Axum, Laravel, Rails, ASP.NET Core)** | **deep** |
| **GraphQL** | **deep** |
| **gRPC** | **deep** |
| **Docker · Kubernetes · Serverless (Lambda, Cloud Functions, Azure Functions, Vercel, Netlify) · Terraform** | **deep** |

An unrecognized technology is not a failure: the skill classifies it by category, applies
universal principles, and states plainly what it cannot determine.

Full list and what each tier includes: **[docs/supported-technologies.md](docs/supported-technologies.md)**.

---

## Finding format

Every finding carries the same fields. Two are unusual and deliberate: `Conditions` may never
be empty, and `Validation` must include something that would prove the finding *wrong*.

```
ID:            PERF-001
Severity:      Critical | High | Medium | Low | Informational
Confidence:    Confirmed | High | Medium | Low
Priority:      P0 | P1 | P2 | P3          (derived from the matrix, never chosen)
Category:      data-access
Location:      src/api/orders.py:84

Problem:               What is wrong.
Performance principle: What it violates, stated without reference to the technology.
Evidence:              Files and lines. If there is no runtime evidence, it says so.
Impact:                Position, frequency, growth, blast radius — all four, explicit.
Conditions:            The workload under which this matters. Never empty.
Recommendation:        What to change, addressing the cause not the symptom.
Trade-offs:            What the change costs.
Validation:            Baseline, measurement, expectation, falsifier, safety label.
```

### Priority is derived

| Severity ＼ Confidence | Confirmed | High | Medium | Low |
|:--|:--|:--|:--|:--|
| Critical | P0 | P0 | P1 | P2 |
| High | P0 | P1 | P1 | P2 |
| Medium | P1 | P2 | P2 | P3 |
| Low | P2 | P3 | P3 | P3 |
| Informational | P3 | P3 | P3 | P3 |

Effort never changes priority. A cheap fix is tagged `quick-win` and sequenced early; its
priority is unchanged, because priority measures impact.

**Example reviews:** [docs/examples/fastapi-postgres.md](docs/examples/fastapi-postgres.md),
[docs/examples/node-mongo-redis.md](docs/examples/node-mongo-redis.md)

---

## Extending

Adding a technology requires **one reference file and one registry entry**. No change to
`SKILL.md`, no change to the methodology.

```yaml
- signal: cockroachdb
  kind: datastore
  category: relational
  match: [cockroach, cockroachdb]
  load: [databases/universal.md, databases/relational.md, technology/cockroachdb.md]
  tier: deep
```

See **[docs/extending.md](docs/extending.md)** for the authoring rules, the mandatory
seven-section structure for technology files, and how to add a runtime or a whole datastore
category.

## Contributing

Contributions are welcome — especially technology references that move an engine from
`conceptual` to `deep`, and **reports of false positives**, which are the most valuable bug
reports this project can receive.

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. It sets out the review gates, including the
rules against cargo-cult recommendations, unsupported claims, product names in category
files, and fabricated performance guarantees.

Not sure where to start? **[docs/roadmap.md](docs/roadmap.md)** lists open gaps and
promotion candidates, several tagged `good first issue`.

By participating, you're expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To
report a vulnerability in the bundled script, see [SECURITY.md](SECURITY.md).

## Honest limitations

- Static analysis cannot measure. Without runtime evidence, most findings cap at `High` or
  `Medium` confidence by design, and the report says so.
- Coverage is deliberately narrow. Thirty-two engines are `deep`; everything else relies on
  category-level reasoning.
- The skill can be wrong. It is a starting point for a senior engineer, not a replacement for
  one — and its validation plans exist precisely so its claims can be checked.
- Behavioral evaluation has run against eight real public repositories and found seven real bugs
  in the detection tooling (now fixed), verified a real merged pull request's fix in
  change-scoped mode, and ran committed benchmarks for real evidence. It has not yet found a
  repository with literally zero findings — worth reading as a result in itself, not a gap; see
  [docs/evaluation.md](docs/evaluation.md) §3.7. An independent blind pass — agents with no
  memory of this project's own findings, each reviewing one repository from scratch — has now
  been run nine times against eight repositories across eight stacks (§3.8–3.11, §3.13–3.18),
  including three runs with no prior author review of the target repository at all, and one
  repository blind-passed a second time to measure inter-run consistency. On the four with a
  prior review to compare against, it reproduced or exceeded the original review's primary
  finding every time, and on three of those four it found real evidence — including, on one
  repository, a hard `SyntaxError` that made the application fail to import entirely — that the
  original review had missed (§3.12). The JVM, Rust, .NET, and Node.js runs each found a real,
  now-fixed bug in the skill's own detection or reference content rather than only in the target
  repository (§3.13–§3.16) — two structurally identical ORM/ODM-provider detection false
  negatives (`sqlite-jdbc` for JVM, `Microsoft.EntityFrameworkCore.SqlServer` for .NET), an
  undocumented runtime default, and a pair of Node.js bugs (a `package-lock.json` hash-collision
  false positive and an unread Prisma-schema false negative). The repeat pass (§3.18) found the
  primary finding's location, mechanism, and derived priority reproduced exactly across all
  three independent reviews of that repository.

## License

MIT — see [LICENSE](LICENSE). Prose and reference content are original; where technical facts
derive from vendor documentation they are paraphrased and attributed by link, never copied.
