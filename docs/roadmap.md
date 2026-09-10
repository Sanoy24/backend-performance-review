# Roadmap

What's open, ranked roughly by value, plus what this project deliberately will not do. If
you're looking for somewhere to start, this is the list — see
[CONTRIBUTING.md](../CONTRIBUTING.md) for how a contribution here gets reviewed.

---

## Measurement programme

An external review of this project produced 140 recommendations; every one has a recorded
disposition in [review-response.md](review-response.md), so a proposal already considered can
be answered with a link rather than re-argued. Its central point — that the next stage is
measuring this system rather than adding more references to it — is accepted, with one
correction to the ordering: the machine-readable finding schema is upstream of every metric
worth having, so it shipped first (see [architecture.md §11](architecture.md)).

What that unlocked, and what remains:

| Step | Status |
|:--|:--|
| Machine-readable finding and review schema | **Done** — `schemas/`, validated in CI |
| Counter-evidence as a recorded, scoring-relevant step | **Done** — `methodology/bottleneck-analysis.md` §4 |
| Review completeness and coverage confidence | **Done** — report template §2 |
| Structured detection evidence, service topology | **Done** — `detect_stack.py` |
| Ground-truth format and a scoring harness | **Done** — `schemas/ground-truth.schema.json`, `benchmark/scoring/score.py`, tested in CI |
| Ground truth seeded from the historically-recorded blind passes | **Done** — all eight independently blind-passed repositories annotated, plus one change-scoped case, in `benchmark/ground-truth/*.json` |
| A corpus repository whose correct answer is **zero findings** | **Attempted, not yet obtained.** A fresh blind pass against `rails-realworld` (a stack never previously blind-passed) was run specifically as an E4 attempt and produced four real findings instead — consistent with `docs/evaluation.md` §3.7's finding that no repository reviewed in this project's history has produced a literal zero-finding result. Still expressible (an empty `expected` with a populated `forbidden`) whenever a genuinely clean candidate is found |
| Run-to-run stability as a computed metric, run for real | **Done, for the first time on live data.** Two fresh, independent agents (no memory of this project's findings, forbidden from reading `docs/evaluation.md`/`benchmark/ground-truth/`) reviewed `gin-realworld` against the complete, updated skill. Both reproduced the dominant N+1 finding. This run found two real harness gaps and fixed both: **(1)** location-only matching was too brittle across a call-chain citation choice — both runs cited the identical bug at opposite ends of one call chain, neither more correct — fixed with `ground-truth.schema.json`'s new `also_locations`; **(2)** `stable_id_agreement` is not a reliable signal as specified — the two runs agreed on location/severity/recommendation and still had 0% `stable_id` agreement, because no canonical hashing algorithm is mandated. `score.py stability`'s output now says so explicitly (a `caveats` field) rather than silently reporting a number that looks meaningful and isn't. See `benchmark/README.md` "The first real validation run" for the full account, including a third real finding a `forbidden` trap incorrectly caught (a different, valid SQLite claim sharing a file with a ruled-out one) |
| Change-scoped verdict model, policy file, SARIF, GitHub Action | **Done** — `methodology/change-scoped.md`, `action.yml`, [github-action.md](github-action.md). Advisory by default; `UNKNOWN` never reads as `PASS`. Not yet exercised in a live GitHub Actions run — see the open item below |
| A canonical `stable_id` hashing algorithm | **Open, found necessary by the validation run above.** Needed before `stable_id_agreement`, fix verification, or historical comparison can mean anything across independently-run agents |
| A live GitHub Actions dry run of `action.yml` | **Open.** Its constituent scripts are tested directly and CI-verified end to end; the composite action itself has not executed on a real runner |

Deliberately deferred, with reasons in [review-response.md](review-response.md): runtime
evidence adapters, query-plan parsing, cross-model evaluation, synthetic bug generation, and
performance debt tracking. Declined outright: human-feedback telemetry (this skill has no
server and adding one would contradict its own privacy posture), and energy efficiency.

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

**None. The 1.0.0 milestone — leave nothing below `deep` — is complete.** All 39 detection
signals in [`registry.yaml`](../skills/backend-performance-review/registry.yaml) are `deep`
tier: every datastore, cache, broker, runtime, API-surface framework, and infrastructure
signal. No signal is `conceptual` or `generic` any more.

This section stays in place — empty of candidates — as where a future promotion candidate
belongs if a genuinely new engine, framework, or platform emerges and is added at
`conceptual` or `generic` tier first. See [docs/extending.md](extending.md) for the
seven-section structure a technology file needs, and
[docs/supported-technologies.md](supported-technologies.md) for the full current tier table.

The complete promotion history, batch by batch: the six datastores originally on this list
(Elasticsearch/OpenSearch, Cassandra/ScyllaDB, ClickHouse, SQL Server, Memcached, SQLite),
then Oracle, CockroachDB, Couchbase, Firestore, Neo4j, Neptune, and InfluxDB, then the
`vector-store` and `object-storage` umbrellas; all eight runtimes, `php` and `ruby` last;
both remaining brokers, `sqs` and `task-queue`; the frameworks batch (`graphql`, `grpc`,
`rest`); and finally the infrastructure batch (`kubernetes`, `docker`, `serverless`,
`terraform`). See [CHANGELOG.md](../CHANGELOG.md) for each promotion's detail.

### How an umbrella signal gets promoted, for the next one

Five of the promotions above were genuine multi-vendor umbrellas rather than one engine
each: `vector-store` (eight engines), `object-storage` (four providers),
`task-queue` (eight libraries), `rest` (fifteen frameworks), and `serverless` (five
platforms). The precedent, should a new umbrella signal ever need it:

Unlike the `elasticsearch`/`redis` signals — which combine engines that really are
API-compatible forks — an umbrella signal spans independently designed systems whose limits
and defaults genuinely differ. The decided approach is **one deliberately comparative
technology file per umbrella**, not splitting the signal into per-vendor signals (which would
have turned these five promotions into thirty-nine separate ones).
`technology/vector-stores.md`, `technology/object-storage.md`, `technology/task-queues.md`,
`technology/rest.md`, and `technology/serverless.md` are the precedent for this shape,
alongside `technology/cassandra.md` (Cassandra *and* ScyllaDB's divergence) and
`technology/elasticsearch.md` (Elasticsearch *and* OpenSearch, naming its Solr gaps).
`technology/rest.md` additionally had to decide what to do with genuinely different
concurrency models under one signal (Node's single event loop versus the JVM's
thread-per-request Servlet stack versus Go's goroutine-per-request) — naming which model each
framework actually uses, rather than writing generic "concurrency" advice true of none of
them specifically, is what kept the file from collapsing into a restatement of
`application/api.md`. `technology/terraform.md` had the opposite problem to solve: Terraform
is not a runtime at all, so its file is a map from provider resource types to the specific
arguments `infrastructure/resources.md`'s arithmetic needs, rather than a runtime-behavior
comparison — the same non-derivable-content bar, met by a different shape of content.

The bar such a file has to clear is the same as any other: per
[CONTRIBUTING.md §4](../CONTRIBUTING.md#4-the-non-derivable-content-rule), it must carry what
the category file cannot — for a comparative file, the concrete divergences (parameter
names, documented limits, defaults) presented side by side, the same gap
`docs/supported-technologies.md` named for the two datastore umbrellas before they were
promoted: *"engine-specific parameter names and defaults not yet written."* A file that
hedges every claim to the lowest common denominator would fail that bar and should not be
merged.

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
