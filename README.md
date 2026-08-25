# backend-performance-review

An open-source **Agent Skill** that teaches an AI coding agent how to perform an
evidence-based backend performance review — on any language, framework, runtime, or
datastore.

It is not a checklist. It is a methodology, plus a set of reference files the agent loads
only when the detected stack calls for them.

```
Performance principle → observed implementation → technology manifestation
  → evidence → bottleneck → impact under stated workload → recommendation → validation
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-informational.svg)](CHANGELOG.md)
[![checks](https://github.com/Sanoy24/backend-performance-review/actions/workflows/checks.yml/badge.svg)](.github/workflows/checks.yml)

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

### Any other coding agent

The methodology is vendor-neutral Markdown. Point your agent at
`skills/backend-performance-review/SKILL.md` and let it follow the reference paths from
there. Only the YAML frontmatter is Claude-specific, and it is ignored elsewhere.

Full details, including how skill discovery works: **[docs/installation.md](docs/installation.md)**.

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

Support is tiered honestly. Most engines are at `conceptual` or `generic` in v0.1.0 — the
methodology still applies, and the report says so in its scope section.

| Tier | Meaning |
|:--|:--|
| **Deep** | Dedicated reference: engine-specific failure modes, diagnostics, and config trade-offs |
| **Conceptual** | Category principles apply in full; no engine-specific file yet |
| **Generic** | Universal methodology only; the skill degrades gracefully and marks specifics as unknown |

### Datastores

| Technology | Category | Tier |
|:--|:--|:--|
| PostgreSQL | relational | **deep** |
| MongoDB | document | **deep** |
| MySQL / MariaDB | relational | conceptual |
| SQL Server | relational | conceptual |
| Oracle | relational | conceptual |
| SQLite | relational | conceptual |
| CockroachDB | relational | conceptual |
| Couchbase | document | conceptual |
| Firestore | document | conceptual |
| DynamoDB | key-value | conceptual |
| Neo4j | graph | conceptual |
| Amazon Neptune | graph | conceptual |
| Cassandra / ScyllaDB | wide-column | conceptual |
| ClickHouse | wide-column | conceptual |
| Elasticsearch / OpenSearch / Solr | search | generic |
| InfluxDB | time-series | generic |
| Pinecone / Weaviate / Qdrant / Milvus / pgvector | vector | generic |

### Caches, brokers, runtimes, infrastructure

| Technology | Tier |
|:--|:--|
| Redis / Valkey | **deep** |
| Memcached | conceptual |
| Kafka · RabbitMQ · SQS · Celery/Sidekiq/BullMQ-style queues | generic |
| Node.js · Python · JVM · Go · .NET · Rust · PHP · Ruby | conceptual |
| REST · GraphQL · gRPC | conceptual |
| Docker · Kubernetes · Serverless · Terraform | conceptual |

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

**Example review:** [docs/examples/fastapi-postgres.md](docs/examples/fastapi-postgres.md)

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

## Honest limitations

- Static analysis cannot measure. Without runtime evidence, most findings cap at `High` or
  `Medium` confidence by design, and the report says so.
- Coverage is deliberately narrow in v0.1.0. Three engines are `deep`; everything else relies
  on category-level reasoning.
- The skill can be wrong. It is a starting point for a senior engineer, not a replacement for
  one — and its validation plans exist precisely so its claims can be checked.
- Behavioral evaluation has run against four real public repositories and found three real bugs
  in the detection tooling (now fixed), verified a real merged pull request's fix in
  change-scoped mode, and ran committed benchmarks for real evidence. It has not yet found a
  repository with literally zero findings — worth reading as a result in itself, not a gap; see
  [docs/evaluation.md](docs/evaluation.md) §3.7. A first independent pass — an agent with no
  memory of this project's own findings, reviewing one of the four repositories from scratch —
  reproduced the main finding, caught a real one the author's own review had missed, and used
  evidence the author had overlooked to correct that review's severity score (§3.8). It covers
  one of the four repositories so far, not all of them.

## License

MIT — see [LICENSE](LICENSE). Prose and reference content are original; where technical facts
derive from vendor documentation they are paraphrased and attributed by link, never copied.
