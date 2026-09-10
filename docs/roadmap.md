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
| Inter-run consistency measured on two repositories, two stacks (§3.18, §3.21) | Both point the same direction (location/mechanism reproduce exactly, matrix never diverges), and §3.21 additionally shows severity moving correctly when a tier is promoted between runs — but two data points is still not a general result across the other six blind-passed repositories/stacks | Medium — pick another already-reviewed repository, run a second independent pass, diff the findings |
| Context cost measured with a real tokenizer, but not from a live instrumented run (§3.20) | §3.20 replaced the byte proxy with real token counts across four stack breadths; a live, API-metered review's actual context consumption — which also includes the target repo's source and the growing conversation — is still unmeasured | Hard — needs an actual dispatched review with token usage recorded, not an offline reference-file count |
| No human-expert baseline comparison | Would tell us how the skill's findings compare to a senior engineer's manual review of the same repository | Hard — needs a willing reviewer and a repository neither has seen |

## Detection gaps

No open items. The one entry this section held — Sequelize and Knex naming no specific
datastore in `package.json` — is resolved: `detect_stack.py` now reads their config files
(`config/config.json`, `knexfile.js`) the same way it already reads Prisma's
`schema.prisma`; see [CHANGELOG.md](../CHANGELOG.md). A driver-coverage audit of every
`deep`-tier signal against a real dependency declaration per supported runtime, committed
as `DriverCoverageTests`/`OrmConfigFileTests` in `tests/test_detect_stack_regressions.py`,
found this and eleven other false negatives; a future promotion missing its dominant driver
in some runtime now fails the same regression suite rather than waiting to be found by
accident.

## Technology promotion candidates

`conceptual`/`generic` signals in [`registry.yaml`](../skills/backend-performance-review/registry.yaml)
that would most benefit from a `deep`-tier reference file. See
[docs/extending.md](extending.md) for the seven-section structure a technology file needs,
and [docs/supported-technologies.md](supported-technologies.md) for the full current tier
table.

**The 1.0.0 milestone is to leave nothing below `deep`.** Every datastore engine is now
there: the six originally on this list (Elasticsearch/OpenSearch, Cassandra/ScyllaDB,
ClickHouse, SQL Server, Memcached, SQLite), then Oracle, and now CockroachDB, Couchbase,
Firestore, Neo4j, Neptune, and InfluxDB. See [CHANGELOG.md](../CHANGELOG.md) for each
promotion.

Remaining for 1.0.0, in planned batch order:

| Batch | Signals | Note |
|:--|:--|:--|
| Datastores — comparative | `vector-store`, `object-storage` | Multi-vendor umbrellas; see below |
| Runtimes | `php`, `ruby` | The last two of eight runtimes still `conceptual` |
| Brokers | `sqs`, `task-queue` | The only two `generic`-tier signals left; `task-queue` is an umbrella |
| Frameworks | `graphql`, `grpc`, `rest` | `rest` spans 15 web frameworks |
| Infrastructure | `kubernetes`, `docker`, `serverless`, `terraform` | `serverless` is an umbrella |

### The umbrella signals, and how they get promoted

Five of the remaining signals are not one engine each. `object-storage` spans S3-compatible
services, GCS, and Azure Blob; `vector-store` spans eight engines; `task-queue` spans ten
queue libraries; `serverless` spans five platforms; `rest` spans fifteen web frameworks.

Unlike the `elasticsearch`/`redis` signals — which combine engines that really are
API-compatible forks — these span independently designed systems whose limits and defaults
genuinely differ. The decided approach is **one deliberately comparative technology file per
umbrella**, not splitting each signal into per-vendor signals (which would turn the remaining
work into roughly 35 promotions). Precedent exists: `technology/cassandra.md` covers
Cassandra *and* ScyllaDB's divergence, and `technology/elasticsearch.md` covers Elasticsearch
*and* OpenSearch while naming its Solr gaps.

The bar such a file has to clear is the same as any other: per
[CONTRIBUTING.md §4](../CONTRIBUTING.md#4-the-non-derivable-content-rule), it must carry what
the category file cannot — for these, that is the concrete divergences (parameter names,
documented limits, defaults) presented side by side. `docs/supported-technologies.md` already
names exactly this as the gap for both datastore umbrellas: *"engine-specific parameter names
and defaults not yet written."* A file that hedges every claim to the lowest common
denominator would fail that bar and should not be merged.

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
