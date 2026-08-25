# Supported technologies

Support is tiered, and the tiers are honest. Most technologies in v0.1.0 are at `conceptual`
or `generic` — the methodology still applies, and a review says so in its scope section rather
than implying depth it does not have.

This page is derived from `skills/backend-performance-review/registry.yaml`, which is the
single source of truth. If the two disagree, the registry is right and this page is stale.

---

## The tiers

| Tier | What you get | What you do not get |
|:--|:--|:--|
| **Deep** | Dedicated technology reference: engine-specific failure modes, diagnostic commands with production-safety labels, configuration trade-offs, version caveats | Nothing withheld — this is the intended depth |
| **Conceptual** | Full category-level analysis: the data-modeling questions, access-pattern reasoning, and universal principles for that category | Engine-specific failure modes, diagnostics, config knobs. Findings that would depend on them are marked as unknowns |
| **Generic** | Universal methodology only: workload, critical paths, round trips, boundedness, growth, shared resources | Category-level reasoning as well as engine specifics. The skill infers the category, applies universal principles, and states plainly what it cannot determine |

Regardless of tier, the skill never fabricates engine behavior. `Generic` means a shorter,
more careful section — not a guessed one.

**Current coverage:** 3 deep · 28 conceptual · 7 generic (38 detection signals).

---

## Datastores

| Technology | Category | Tier | Notes |
|:--|:--|:--|:--|
| **PostgreSQL** | relational | **deep** | Includes TimescaleDB and pgvector detection; those extensions are themselves out of scope |
| **MongoDB** | document | **deep** | Atlas-specific features (Search, Federation, Online Archive) out of scope |
| MySQL / MariaDB | relational | conceptual | InnoDB specifics — clustered primary key layout, gap locking, optimizer differences — not yet written |
| SQL Server | relational | conceptual | |
| Oracle | relational | conceptual | |
| SQLite | relational | conceptual | Single-writer and WAL semantics dominate and are not covered; concurrency questions are flagged as unknowns |
| CockroachDB | relational | conceptual | Range distribution, transaction retries, and locality-aware placement not covered |
| Couchbase | document | conceptual | |
| Firestore | document | conceptual | Billing is per document read/write, so cost is often the binding constraint; the skill treats cost as a first-class axis here |
| DynamoDB | key-value | conceptual | Partition key design, hot partitions, and capacity modes dominate and are not yet written |
| Neo4j | graph | conceptual | Traversal-depth, index-free-adjacency, and supernode reasoning apply in full; engine-specific diagnostics not yet written |
| Amazon Neptune | graph | conceptual | Same graph category reasoning; managed-service specifics (instance sizing, Gremlin vs openCypher) not yet written |
| Cassandra / ScyllaDB | wide-column | conceptual | Partition-oriented wide-column reasoning applies in full; compaction/tombstone tuning and driver defaults not yet written |
| ClickHouse | wide-column | conceptual | Column-oriented analytical reasoning applies (a different sub-model within the same category file); merge-tree tuning specifics not yet written |
| Elasticsearch / OpenSearch / Solr | search | generic | No search category file in v0.1.0 |
| InfluxDB | time-series | generic | No time-series category file in v0.1.0 |
| Pinecone / Weaviate / Qdrant / Milvus / Chroma / pgvector / FAISS / LanceDB | vector | generic | Index type, dimensionality, search parameters, and the recall/latency trade are reported as explicit unknowns rather than inferred |

## Caches

| Technology | Tier | Notes |
|:--|:--|:--|
| **Redis / Valkey** | **deep** | Redis Stack modules (Search, JSON, TimeSeries, vector) out of scope |
| Memcached | conceptual | Key-value category reasoning plus cache reasoning |

## Message brokers and queues

| Technology | Tier | Notes |
|:--|:--|:--|
| Kafka / Redpanda | generic | Partition count, consumer group rebalancing, and batch/linger settings dominate; not yet written |
| RabbitMQ | generic | |
| Amazon SQS | generic | |
| Celery / Sidekiq / BullMQ / RQ / Dramatiq / Hangfire / Temporal / Asynq | generic | Detected as task queues; retry, backpressure, and pool-sharing reasoning applies |

All brokers get the full `distributed/retries-and-backpressure.md` and
`distributed/timeouts-and-deadlines.md` analysis, which covers most of what matters at the
application layer.

## Runtimes

| Runtime | Tier |
|:--|:--|
| Node.js | conceptual |
| Python | conceptual |
| JVM (Java / Kotlin) | conceptual |
| Go | conceptual |
| .NET | conceptual |
| Rust | conceptual |
| PHP | conceptual |
| Ruby | conceptual |

All runtimes get `runtimes/universal.md`, which is a taxonomy rather than a survey: the skill
places the runtime on five dimensions — execution model, parallelism, concurrency model,
memory management, startup cost — and the relevant failure modes follow. Per-runtime files are
the main planned addition for v0.2.0.

For Python specifically, the skill is directed to establish the concurrency model first —
threads/processes versus event loop — because the failure modes differ completely.

## API surfaces

| Surface | Tier |
|:--|:--|
| REST (FastAPI, Flask, Django, Express, NestJS, Koa, Gin, Echo, Fiber, Spring Boot, Actix, Axum, Laravel, Rails, ASP.NET Core) | conceptual |
| GraphQL | conceptual |
| gRPC | conceptual |

Resolver-level N+1 and unbounded query depth — the dominant GraphQL performance risks — are
covered generically in `application/api.md`.

## Infrastructure

| Technology | Tier |
|:--|:--|
| Docker | conceptual |
| Kubernetes / Helm | conceptual |
| Serverless (Lambda, Cloud Functions, Azure Functions, Vercel, Netlify) | conceptual |
| Terraform | conceptual |

Serverless gets specific treatment in `infrastructure/resources.md`, because standard
connection-pooling advice inverts under a per-invocation model.

---

## Technologies not listed

Not a failure case. The skill will:

1. Infer the category from the query interface, connection scheme, and usage patterns.
2. Apply universal datastore or runtime principles for that category.
3. Inspect its configuration and how the code actually uses it.
4. State what can be safely inferred and what cannot.
5. Recommend the validation that would determine the rest.

It will not invent engine-specific facts. A review of an unfamiliar engine produces a shorter,
more hedged section — which is the correct output, not a degraded one.

---

## Roadmap

Ordered by expected value, not by ease:

1. Category files for search, time-series, and vector — these move the remaining seven
   `generic` signals to `conceptual`. Graph (`databases/graph.md`, covering Neo4j and Amazon
   Neptune) and wide-column (`databases/wide-column.md`, covering Cassandra/ScyllaDB and
   ClickHouse) are done.
2. Per-runtime references: Node.js, Python, JVM, Go.
3. Deep references for MySQL and DynamoDB — the two most-requested `conceptual` engines.
4. Kafka and RabbitMQ references.

Contributions in any of these areas are welcome; see [extending.md](extending.md).

**Promoting a technology is a bounded, well-defined contribution:** one reference file, one
registry line, one README row.
