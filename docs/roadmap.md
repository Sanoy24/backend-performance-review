# Roadmap

What's open, ranked roughly by value, plus what this project deliberately will not do. If
you're looking for somewhere to start, this is the list — see
[CONTRIBUTING.md](../CONTRIBUTING.md) for how a contribution here gets reviewed.

---

## Evaluation gaps

These are worth more than additional reference content — see
[docs/evaluation.md §5](evaluation.md#5-known-evaluation-gaps) for full context on each.

| Gap | Why it matters | Difficulty |
|:--|:--|:--|
| No independent blind pass against a .NET repository | The last untested `deep`-tier runtime; JVM (§3.13) and Rust (§3.14) passes each found a real bug this way | Medium — needs a real public .NET repo and a fresh agent with no memory of this project |
| No independent blind pass against a Node.js repository | `deep`-tier since v0.2.0, never blind-passed despite five other stacks covered | Medium |
| No inter-run consistency measurement | The rubrics claim priority is "derived... not vibes" (README). Never measured: does the same repository reviewed twice by two independent agents produce the same ranking? | Medium — pick one already-reviewed repository, run a second independent pass, diff the findings |
| No context-cost measurement per review | Tests whether the reference-routing architecture (loading only what the detected stack calls for) actually pays off over loading everything | Medium — instrument token/context usage across a review and compare against a naive "load every reference" baseline |
| No human-expert baseline comparison | Would tell us how the skill's findings compare to a senior engineer's manual review of the same repository | Hard — needs a willing reviewer and a repository neither has seen |

## Technology promotion candidates

`conceptual`/`generic` signals in [`registry.yaml`](../skills/backend-performance-review/registry.yaml)
that would most benefit from a `deep`-tier reference file. See
[docs/extending.md](extending.md) for the seven-section structure a technology file needs,
and [docs/supported-technologies.md](supported-technologies.md) for the full current tier
table.

Roughly ordered by how commonly they show up as the primary datastore/cache in a backend
(rather than as one dependency among many):

- **Elasticsearch / OpenSearch** (`search`, conceptual) — shard sizing, refresh interval,
  query-vs-filter context, aggregation cost.
- **Cassandra / ScyllaDB** (`wide-column`, conceptual) — partition-key design, tombstone
  accumulation, read-repair cost, consistency-level trade-offs.
- **ClickHouse** (`wide-column`, conceptual) — MergeTree engine choice, insert batching,
  the cost of point lookups on a column store built for scans.
- **SQL Server** (`relational`, conceptual) — its own execution-plan and locking model
  differs enough from PostgreSQL/MySQL to be worth dedicated coverage.
- **Memcached** (conceptual) — sits alongside `deep`-tier Redis but has never gotten its
  own file; slab allocation and eviction behavior differ meaningfully from Redis's.
- **SQLite** (`relational`, conceptual) — the §3.13 blind pass (evaluation.md) found a real
  detection bug here (`sqlite-jdbc` false negative); the fix closed the bug but SQLite
  itself is still conceptual-tier.

The full conceptual/generic list — Oracle, CockroachDB, Couchbase, Firestore, Neo4j,
Neptune, InfluxDB, the vector stores, object storage, PHP, Ruby, GraphQL, gRPC, REST,
Kubernetes, Docker, Serverless, Terraform — is in `registry.yaml`; any of them is a valid
contribution, the list above is just where the highest leverage looks to be right now.

## Project infrastructure

- **Markdown link checking in CI.** Nothing currently verifies that a relative link between
  these ~14,000 lines of cross-referenced Markdown still resolves. A stdlib checker fits
  the project's no-dependencies posture for tooling.
- **Cross-platform CI matrix for `detect_stack.py`.** CI currently runs the stdlib-only
  jobs on `ubuntu-latest` only; the script's path handling is claimed portable but never
  verified on Windows or macOS runners.

---

## Out of scope

Stated plainly, so a PR proposing one of these can be declined quickly rather than
debated at length:

- **Modifying code.** The skill reads, reasons, and reports. It will not open a PR, apply
  a fix, or run a formatter — see README's "What it does not do".
- **Running anything against a live or production system.** Every diagnostic command in a
  report carries a safety label; the skill itself never executes one.
- **Security or correctness review.** Out of scope by design — a performance review that
  also tries to be a security audit does neither well. (Bugs *in this project's own*
  detection or tooling code are still fair game for SECURITY.md.)
- **Frontend or client-side performance.** "Backend" in the name is load-bearing — Core Web
  Vitals, bundle size, and rendering performance are a different methodology.
- **A general-purpose static analysis linter.** Findings that don't tie to a stated or
  inferable workload are explicitly against the doctrine (see `Conditions` in the finding
  format) — a generic "this could theoretically be slow" rule will be declined.
- **Invented benchmarks or performance guarantees.** No PR may add a specific number
  ("this is 3x faster") that isn't attributed to a citable source or a stated derivation —
  see CONTRIBUTING.md §5.

---

Found a gap not listed here? Open an issue — the
[technology request](../.github/ISSUE_TEMPLATE/technology-request.yml) template if it's a
missing engine, otherwise a regular bug report.
