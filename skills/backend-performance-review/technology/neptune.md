# Amazon Neptune

Load after `databases/universal.md` and `databases/graph.md`.

This file contains **only what those files do not give you**. Index-free adjacency,
traversal depth as the dominant cost factor, supernodes, graph data modeling, and the
general shape of a graph query are covered there and are not repeated here.

---

## 1. Detection signals

Dependencies: `gremlinpython`, `gremlin-driver`/`gremlin-core` (JVM), `gremlin` (Node.js),
`neptune-python-utils`, `SPARQLWrapper` or an RDF library (`rdflib`, Apache Jena) when the
RDF surface is in use, `@aws-sdk/client-neptune` / `boto3` Neptune clients (control plane
only — these manage the cluster, they do not query the graph).

Other signals: a `*.neptune.amazonaws.com` endpoint (the strongest signal — note whether it
is the **cluster**, **reader**, or an **instance** endpoint, see §2), `AWS::Neptune::`
resources in CloudFormation/CDK, `aws_neptune_cluster` in Terraform, IAM-signed request
helpers around a graph client, an `wss://...:8182/gremlin` or `https://...:8182/sparql`
URL.

**Caution on generic query-language tokens.** `gremlin`, `sparql`, and `opencypher` name
query languages, not this engine: JanusGraph, Amazon's own Neptune Analytics, Blazegraph,
Apache Jena, and others speak them too. Treat a match on those tokens alone as "a graph
query language is in use," and confirm the engine from an endpoint or infrastructure
declaration before applying anything below.

---

## 2. What differs from the graph baseline

### One engine, three query languages, two data models — and they are not interchangeable

Neptune accepts **Gremlin** and **openCypher** over a property-graph model, and **SPARQL**
over an RDF model. The property-graph and RDF surfaces are distinct data models over the
same cluster: data loaded as RDF is not queryable via Gremlin, and vice versa. The
practical review consequences:

- A codebase using two languages should be checked for whether they address the *same* data
  (Gremlin and openCypher can) or two separate datasets (property graph and RDF do not
  share).
- Feature and optimizer coverage differ per language. A query pattern that plans well in one
  may not in another, and porting between them is not a mechanical translation.

This is a Neptune-specific structural fact with no counterpart in the category file, which
assumes one query surface.

### It is a managed service, so the tuning surface is the instance class, not a config file

There is no page cache to size, no heap to split, and no configuration file to read. The
buffer pool is a fixed proportion of the instance's memory, so **"does the working graph fit
in RAM" becomes an instance-class question** — the same underlying concern this skill's
other graph guidance raises, but resolved by resizing rather than by configuration.

The corollary for review: recommendations here take the form "measure `BufferCacheHitRatio`
and size the instance from it," not "tune this parameter." Cluster parameter groups exist
but expose a much narrower surface than a self-hosted engine.

### One writer, up to fifteen readers — and the endpoint the client uses decides which it gets

A Neptune cluster has exactly one writable instance. The endpoints are distinct and
checkable in application configuration:

- The **cluster endpoint** always points at the writer.
- The **reader endpoint** load-balances across read replicas — but only across connections,
  since it resolves by DNS. A long-lived connection pool that resolves the reader endpoint
  once can pin the entire application to a single replica while the others sit idle.
- An **instance endpoint** pins to one specific instance permanently.

**An application sending read-only traffic to the cluster endpoint is loading the writer
for no reason**, and it is one of the most common and most directly checkable Neptune
findings, visible in configuration without touching the database. The DNS-pinning caveat on
the reader endpoint is the follow-up question, and matters most for exactly the long-lived
connections a WebSocket-based Gremlin client maintains.

Replicas serve reads with replication lag, so read-your-own-write patterns routed to the
reader are a correctness-adjacent risk (`SKILL.md` rule 8) rather than only a staleness
preference.

### Bulk ingest has a dedicated loader, and per-row insertion is the anti-pattern

Neptune's **bulk loader** ingests from S3 in a format-specific bulk operation. Loading a
large dataset by issuing individual Gremlin `addV`/`addE` traversals — or individual SPARQL
`INSERT`s — is dramatically more expensive and is a checkable pattern in migration or
seeding code. This is the graph-engine instance of the category-independent "per-row
operation in a loop" finding, but with a specific, named alternative to point at.

### Query timeouts are enforced, and long analytical traversals hit them

Neptune enforces a server-side query timeout (configurable via the cluster parameter group,
with a default measured in minutes). An unbounded traversal — the category file's central
warning about depth — surfaces here as a *timeout error*, not merely as slowness. Code that
retries such a query on timeout will re-run the same expensive traversal, compounding load
rather than recovering.

### The DFE query engine is an opt-in alternative planner

Neptune's **DFE** (Differentiated Feature Engine) is an alternative query engine, enabled
via the `neptune_dfe_query_engine` parameter, with different plan characteristics for some
query shapes. Whether it is enabled is a checkable cluster-parameter fact that can change a
query's plan materially — worth recording during discovery rather than assuming the default
applies.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| Gremlin `.explain()` (via the `/gremlin/explain` endpoint) | The query plan and how the traversal is compiled | **safe-on-production** |
| Gremlin `.profile()` (via `/gremlin/profile`) | Executed plan with per-step timings and element counts | **not safe-on-production for an expensive query** — it executes |
| openCypher `EXPLAIN` / `PROFILE` (via the `/opencypher` endpoint headers) | Plan, and with `PROFILE`, actual execution detail | `EXPLAIN` **safe-on-production**; `PROFILE` executes |
| SPARQL `explain` (via `/sparql/explain`) | Query plan for the RDF surface | **safe-on-production** |
| `GET /gremlin/status` (and `/sparql/status`, `/opencypher/status`) | Currently running queries, with elapsed time — and the ability to cancel one | **safe-on-production** to read; cancelling is a deliberate intervention |
| CloudWatch `BufferCacheHitRatio` | Whether the working graph is being served from memory — the primary instance-sizing signal | **safe-on-production** |
| CloudWatch `MainRequestQueuePendingRequests` | Queued requests, indicating the instance is saturated rather than any one query being slow | **safe-on-production** |
| CloudWatch `ClusterReplicaLag` / `ReplicaLag` | Replica staleness, for read-routing correctness questions | **safe-on-production** |
| Slow-query logging (`neptune_enable_audit_log`, query logs to CloudWatch) | Historical query text and timings | **safe-on-production** — audit logging has its own throughput cost, so confirm the setting rather than enabling it casually |
| `GET /loader/<loadId>` | Bulk-loader job status and per-record errors | **safe-on-production** |

`BufferCacheHitRatio` and `MainRequestQueuePendingRequests` distinguish the two failure
shapes that look identical from the application: an under-sized instance versus a saturated
one.

---

## 4. Common failure modes and their symptoms

| Symptom | Neptune-specific cause to check first |
|:--|:--|
| The writer instance is loaded while replicas are idle | Read traffic sent to the cluster endpoint instead of the reader endpoint |
| Only one replica shows load despite several existing | Reader-endpoint DNS resolved once and pinned by a long-lived connection pool |
| Traversals slow broadly with no query change | `BufferCacheHitRatio` falling — working graph no longer fits the instance class |
| Queries fail with a timeout rather than returning slowly | Server-side query timeout reached by an unbounded traversal; retrying re-runs the cost |
| Requests queue while individual queries look fast | Instance saturation — check `MainRequestQueuePendingRequests`, not query plans |
| A data migration takes hours | Per-row `addV`/`addE` or `INSERT` instead of the S3 bulk loader |
| Stale results immediately after a write | Read routed to a replica with replication lag |
| A query plans differently than the same query did previously | DFE engine enabled/disabled, or an engine version upgrade changing the planner |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Instance class (writer and readers) | Buffer-cache capacity and CPU versus cost — the primary tuning lever, since there is no page-cache setting |
| Number of read replicas | Read throughput and failover readiness versus per-instance cost |
| Endpoint used per code path (cluster / reader / instance) | Correct write routing and read scaling versus the simplicity of one endpoint everywhere |
| `neptune_query_timeout` | Bounding runaway traversals versus killing legitimate long analytical queries |
| `neptune_dfe_query_engine` | Alternative planning that helps some query shapes versus a plan change across the board |
| Audit/slow-query logging | Diagnosability versus logging throughput cost |
| Bulk loader versus incremental writes | Ingest speed for large datasets versus the loader's S3 staging and format requirements |
| Neptune Serverless capacity range (NCU min/max) | Automatic scaling to load versus a capacity floor that still bills, and scaling lag on sharp bursts |

---

## 6. Version differences worth knowing

Record the engine version and the deployment shape during discovery; Neptune's surface has
grown substantially and material written for early versions omits entire query languages.

- **openCypher support was added well after Gremlin and SPARQL.** A codebase using only
  Gremlin may predate it rather than having chosen it, which matters when the review's
  recommendation would be easier to express in openCypher.
- **Neptune Serverless** replaces fixed instance classes with a scaling capacity range
  (NCUs). The instance-sizing findings above become capacity-range findings, and scaling is
  not instantaneous — a sharp burst can be served at the old capacity for a period.
- **Neptune Analytics is a separate service**, not a Neptune Database feature: it is an
  in-memory graph-analytics engine for algorithm workloads. A repository referencing it is
  not describing the transactional cluster, and the guidance above does not transfer.
- **Engine version upgrades change query planning.** Neptune's engine version is visible in
  the console and API; where a plan regression is suspected, the engine version at the time
  of the change is a checkable fact rather than a guess.

**Confirm version- and deployment-specific claims against the current AWS documentation for
the deployed engine version.**

---

## 7. What this file does NOT cover

- Index-free adjacency, traversal-depth reasoning, supernodes, and graph data modeling —
  see `databases/graph.md`.
- Gremlin, openCypher, and SPARQL as languages — their idioms, and how to express a given
  traversal well in each.
- Neptune Analytics (the separate in-memory analytics engine) and its algorithm library.
- Neptune ML (graph neural network inference integrated with SageMaker).
- Backup, snapshot, restore, and point-in-time recovery mechanics.
- VPC networking, IAM database authentication, and the request-signing requirement beyond
  noting that clients need it.
- AWS pricing and instance-class selection economics; this file names the metric to size
  from, never a rate or an instance recommendation.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
