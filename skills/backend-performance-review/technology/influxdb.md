# InfluxDB

Load after `databases/universal.md` and `databases/time-series.md`.

This file contains **only what those files do not give you**. Time as the partition key,
series cardinality as the category's defining failure mode, the tag-versus-field schema
decision, retention and downsampling, and write-ordering effects are covered there and are
not repeated here.

---

## 1. Detection signals

Dependencies: `influxdb-client` (the 2.x client, published under this name for Python,
JavaScript, and others), `influxdb` (the 1.x-era client package name),
`influxdb-client-go`/`influxdb1-client` (Go), `com.influxdb:influxdb-client-java` or
`org.influxdb:influxdb-java` (JVM — note which, they correspond to different generations),
`InfluxDB.Client` (.NET), `influxdb3-python` and similar 3.x clients.

Other signals: `influxd`/`influx` CLI invocations, `influxdb.conf` or `config.yml` for the
server, `influxdb`/`influxdb:2`/`influxdb:3` container images, a line-protocol payload in
application code (`measurement,tag=value field=1 timestamp`), Telegraf configuration
(`telegraf.conf`) writing to it, `prometheus_remote_write` configuration pointing at an
InfluxDB endpoint, an `INFLUX_TOKEN`/`INFLUX_ORG`/`INFLUX_BUCKET` environment triple (2.x)
versus `INFLUXDB_DB`/username-password (1.x).

**Record the major version first — before anything else in this file applies.** See §2.

---

## 2. What differs from the time-series baseline

### The three generations are different databases wearing one name

This is the most important InfluxDB-specific fact, and it precedes every other claim here:

| Generation | Query language | Storage engine | How cardinality behaves |
|:--|:--|:--|:--|
| **1.x** | InfluxQL | TSM + series index (`inmem` or TSI) | The category file's cardinality wall applies in full; high cardinality is a hard operational limit |
| **2.x** | Flux (InfluxQL still available) | TSM + TSI | Same cardinality model as 1.x; adds buckets/orgs/tokens and tasks |
| **3.x** | SQL (and InfluxQL) | Columnar, Parquet-based object storage | The series-index cardinality wall is substantially removed; different concerns replace it |

Guidance for one generation can be actively wrong for another. "Watch your tag cardinality"
is the defining advice for 1.x/2.x and much less central in 3.x; "use Flux" is 2.x-specific
and a dead end in 3.x; the operational surface (`influxd` self-hosted versus Cloud
Dedicated/Serverless) differs again. **Establish the version before scoring any finding on
this engine**, and say so explicitly in the report's scope section when it cannot be
determined.

### In 1.x/2.x, the series index is a memory structure with a configurable location

The category file explains *why* cardinality is the dominant risk. What is InfluxDB-specific
is that the index has two implementations, and which one is configured determines the shape
of the failure:

- **`inmem`** holds the entire series index in RAM. Cardinality growth ends in an
  out-of-memory condition — the process dies rather than getting slower.
- **TSI (`tsi1`)**, disk-based with memory caching, converts that cliff into degradation:
  queries and startup get slower, memory stays bounded.

The setting (`index-version` in 1.x configuration; TSI is standard in 2.x) is directly
checkable, and it changes the *symptom* an operator will have reported. A team describing
"the database keeps getting OOM-killed" and a team describing "queries got slow and startup
takes forever" may have the identical underlying cardinality problem on differently
configured servers.

1.x additionally exposes explicit guard rails — `max-series-per-database` and
`max-values-per-tag` — which are off (unlimited) or set at a default that a growing workload
silently blows past. Whether they are configured is a checkable fact and a legitimate
recommendation, since the failure they prevent is an outage rather than a slowdown.

### Retention is enforced by dropping whole shards, so shard duration is a real design parameter

Data is organized into **shard groups**, each covering a contiguous time span (the **shard
duration**), and retention enforcement drops entire shards rather than deleting points. Two
consequences that the category file's general retention guidance does not give you:

- **Shard duration must divide the retention period sensibly.** A retention period only
  slightly longer than the shard duration means data lives well past its nominal retention
  (a shard is only dropped once *all* of it has expired); a shard duration far too small
  produces a large number of shards, each with its own index and file handles, which slows
  startup and metadata operations.
- **Deleting individual points is expensive and not the intended mechanism.** Code issuing
  `DELETE` statements for data lifecycle management is working against the design; retention
  policies and shard dropping are the supported path.

Default shard durations are derived from the retention period, so a hand-set retention
policy with a default shard duration is worth checking against both effects above.

### Downsampling is a first-class, scheduled mechanism — and its absence is the finding

Long-horizon storage of raw-resolution points is the growth failure the category file
describes. InfluxDB's answer is generation-specific machinery: **continuous queries** in
1.x, **tasks** in 2.x, and scheduled SQL-based approaches in 3.x. Their presence or absence
is directly checkable, and a deployment retaining raw high-frequency data indefinitely with
no downsampling configured is a concrete, evidenced finding rather than a general concern.

### Writes are batched by the client, and unbatched line-protocol writes are the common defect

The write path is HTTP line protocol, and every client library provides batching (a write
API with batch size and flush interval, or an explicit batching wrapper). Application code
that writes one point per HTTP request — the natural shape if the client is used naively —
pays a full round trip per point. This is the InfluxDB-specific instance of the round-trip
finding the universal file names, and it is visible directly in application code, without
any server access.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `SHOW SERIES CARDINALITY` (and `SHOW SERIES EXACT CARDINALITY`) | The cardinality number that determines whether this deployment is near its limit | Estimated form **safe-on-production**; the `EXACT` form scans and is **not safe-on-production** on a large database |
| `SHOW TAG KEYS` / `SHOW TAG VALUES CARDINALITY WITH KEY = <tag>` | Which tag is responsible for cardinality growth — the actionable follow-up to the number above | **safe-on-production** for the estimating forms |
| `SHOW MEASUREMENTS` / `SHOW FIELD KEYS` | The schema as actually written, versus what the code believes it writes | **safe-on-production** |
| `EXPLAIN <influxql>` / `EXPLAIN ANALYZE <influxql>` | Query plan; `ANALYZE` adds real execution statistics | `EXPLAIN` **safe-on-production**; `EXPLAIN ANALYZE` executes the query |
| `SHOW RETENTION POLICIES` / `SHOW SHARDS` | Retention periods, shard durations, and the actual shard inventory | **safe-on-production** |
| `SHOW QUERIES` | Currently executing queries, with elapsed time; supports killing one | **safe-on-production** to read |
| `influx_inspect report-tsi` / `report` (1.x, on the server) | Per-measurement cardinality breakdown from the on-disk index | **not safe-on-production during peak** — it reads index files directly and is I/O-heavy |
| `/debug/vars` (1.x) and the `_monitoring` bucket (2.x) | Internal server metrics: write throughput, query duration, memory, shard counts | **safe-on-production** |
| `/health` and `/metrics` (Prometheus format, 2.x) | Liveness and instrumented server metrics for scraping | **safe-on-production** |

`SHOW SERIES CARDINALITY` followed by per-tag cardinality is the single highest-value
diagnostic sequence for 1.x/2.x: it turns the category file's general warning into a specific
number and names the tag responsible for it.

---

## 4. Common failure modes and their symptoms

| Symptom | InfluxDB-specific cause to check first |
|:--|:--|
| The server is repeatedly OOM-killed | 1.x/2.x cardinality growth with the `inmem` index; TSI converts this to degradation instead |
| Startup takes progressively longer | Large series index and/or a very large number of shards from a too-small shard duration |
| Queries slowed gradually over months with no code change | Cardinality growth, or raw retention with no downsampling task/continuous query configured |
| Write throughput is far below expectations | Unbatched line-protocol writes — one HTTP round trip per point |
| Disk usage grows without bound despite a retention policy | Retention period and shard duration misaligned, so shards are not yet fully expired; or writes going to a different retention policy than assumed |
| A tag that "shouldn't matter" dominates memory | A high-cardinality value (request ID, user ID, raw URL) written as a tag rather than a field — the category file's schema decision, with a specific tag to name |
| Query guidance from a blog post produces syntax errors | Generation mismatch — Flux examples on a 1.x server, or InfluxQL-era advice on 3.x |
| Deletes are slow and do not reclaim space as expected | Point-level `DELETE` used for lifecycle management instead of retention/shard dropping |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `index-version` (`inmem` versus `tsi1`, 1.x) | Bounded memory and survivable high cardinality versus slower queries and startup |
| `max-series-per-database` / `max-values-per-tag` (1.x) | Rejecting writes that would breach a known limit versus silently growing toward an outage |
| Shard duration versus retention period | Efficient whole-shard expiry versus shard count, index overhead, and startup time |
| Downsampling tasks / continuous queries | Bounded long-horizon storage and fast historical queries versus the compute they consume and the resolution permanently lost |
| Client batch size and flush interval | Write throughput versus how much buffered data is lost on a client crash, and added write latency |
| `cache-max-memory-size` / `cache-snapshot-memory-size` (1.x/2.x) | Write buffering before a forced snapshot versus memory headroom |
| WAL and data directory placement | Write durability and throughput versus storage cost and I/O contention with the data directory |

---

## 6. Version differences worth knowing

Version identification is not optional background for this engine — it determines which of
the sections above apply at all.

- **1.x → 2.x** replaced databases and retention policies with **buckets**, added
  organizations and token auth, and made **Flux** the primary language (with an InfluxQL
  compatibility layer). Code using `INFLUXDB_DB` with username/password is 1.x-shaped; code
  using `INFLUX_ORG`/`INFLUX_BUCKET`/`INFLUX_TOKEN` is 2.x-shaped.
- **3.x** replaced the storage engine entirely with a columnar, Parquet-and-object-storage
  design and made **SQL** a first-class query language. The series-cardinality wall that
  dominates 1.x/2.x reasoning is substantially removed; in exchange, the relevant concerns
  become file/compaction behavior and object-storage access patterns. **Do not carry a
  cardinality finding from 1.x/2.x experience onto a 3.x deployment without confirming it
  still applies.**
- **Flux is no longer the strategic direction.** A 2.x codebase heavily invested in Flux is
  not broken, but a recommendation to write *new* logic in Flux would be poor advice — note
  the migration question rather than deepening the investment.
- **Cloud offerings** (Serverless, Dedicated, Clustered) differ from self-hosted in what is
  configurable at all; the shard-duration and index-version settings above may simply not be
  exposed.

**Confirm version- and deployment-specific claims against the current documentation for the
deployed version — more so for this engine than for most.**

---

## 7. What this file does NOT cover

- Time as the partition key, series cardinality as the category's defining failure mode, the
  tag-versus-field decision, retention/downsampling reasoning, and write-ordering effects —
  see `databases/time-series.md`.
- Flux and InfluxQL as languages, and how to express a given query well in either.
- Telegraf's own configuration, plugin selection, and buffering behavior.
- 3.x's internal compaction and object-storage layout in depth — named above as the concern
  that replaces cardinality, not characterized here.
- Clustering, replication, and high availability across the commercial offerings.
- InfluxDB Cloud pricing and capacity tiers.
- Migration mechanics between generations.
- Security: token/permission models and TLS configuration.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
