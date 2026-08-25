# Kafka

Load after `distributed/retries-and-backpressure.md` and `distributed/timeouts-and-deadlines.md`.

This file contains **only what those files do not give you**. Partition-bounded parallelism,
key skew, consumer lag as a health metric, poison messages, and batch/prefetch trade-offs are
covered generically there (`distributed/retries-and-backpressure.md` §6) and are not repeated
here — this file gives the concrete Kafka mechanisms behind them, plus what's specific to
Kafka's architecture. Covers Kafka and its wire-compatible forks (Redpanda) unless noted
otherwise.

---

## 1. Detection signals

Dependencies: `kafka-python`, `confluent-kafka`, `kafkajs`, `segmentio/kafka-go`, `Sarama`,
Spring Kafka, Kafka Streams client libraries. Container images (`confluentinc/`, `bitnami/
kafka`, `redpanda`), `server.properties`/broker configuration, Kafka Connect connector configs,
Schema Registry usage (Avro/Protobuf schemas — relevant context, not this file's concern), MSK
or Confluent Cloud resources in infrastructure code.

**Determine whether the deployment runs in KRaft mode or with ZooKeeper**, and **which
rebalancing protocol the consumer groups use** (see §2 and §6) — both materially change which
failure modes and diagnostics below apply.

---

## 2. What differs from the generic broker/queue baseline

### Consumer group rebalancing is the concrete mechanism behind partition-bounded parallelism, and its blast radius depends on the protocol

`distributed/retries-and-backpressure.md` states generically that partition count bounds
consumer parallelism and that adding consumers beyond it adds nothing. Kafka's specific
mechanism for enforcing this is **consumer group rebalancing**: when membership changes (a
consumer joins, leaves, crashes, or is judged dead), the group renegotiates partition ownership.
Under the older **eager** rebalancing protocol, every consumer in the group stops consuming for
the duration of the rebalance — a brief, group-wide throughput collapse on every membership
change, not just a redistribution among the partitions that actually moved. The **cooperative
sticky** protocol (available since Kafka 2.4) reassigns only the affected partitions, leaving
unaffected consumers running — a materially smaller blast radius for the same event. Confirm
which protocol is configured before characterizing a rebalance's severity.

### Exceeding `max.poll.interval.ms` triggers a rebalance even though the consumer is alive

A consumer that takes longer to process a batch than `max.poll.interval.ms` allows is presumed
dead by the group coordinator and evicted — triggering the rebalance described above — even
though it is still running and will eventually finish the batch. This is the concrete,
Kafka-specific version of "prefetching more than can be processed": unlike a queue where an
over-long lease simply causes redelivery of that one message, here it also disrupts every other
consumer in the group. A processing-time regression (a slow downstream call, a GC pause, a
sudden data-size increase) that pushes batch handling past this threshold is a specific,
checkable cause of a throughput collapse that looks unrelated to the actual slow operation.

### Retention is time/size-based and independent of consumption — Kafka is a log, not a queue

Unlike a traditional queue, a Kafka message is not removed once consumed — it remains on the
partition's log until `retention.ms`/`retention.bytes` expires it (or, for a compacted topic, until
a newer value for the same key supersedes it under log compaction). This means:

- **Disk usage is a function of retention configuration and write rate, not consumer speed.** A
  slow or stopped consumer does not cause the log to grow unboundedly the way an unbounded queue
  would — but a retention window configured without regard to actual disk capacity and message
  volume will fill disk regardless of how well consumers keep up.
- **Replay is a first-class capability** — a consumer can reset its offset and reprocess history
  within the retention window — which is a real operational tool, not just a side effect.
- **Log compaction** (retaining only the latest value per key, rather than a time/size window) is
  the appropriate retention policy for a changelog or latest-state topic; using time/size
  retention where compaction was intended is a specific, checkable misconfiguration that lets old
  keyed state disappear once retention expires rather than persisting indefinitely as intended.

### Producer `acks` is a direct, named durability/latency trade specific to Kafka's replication model

`acks=0` (fire-and-forget, no acknowledgment at all), `acks=1` (acknowledged once the partition
leader has written it, but not yet replicated), and `acks=all`/`-1` (acknowledged only once the
in-sync replica set has the write) are three distinct points on a durability/latency spectrum,
and the difference is not cosmetic: `acks=1` can silently lose an acknowledged message if the
leader fails before replication completes. Finding `acks=1` or `acks=0` on a producer whose data
is treated as durable (not a metrics/logging pipeline tolerant of loss) is a correctness-adjacent
finding (`SKILL.md` rule 8), not merely a performance one — surfaced during a performance review
because it interacts directly with `min.insync.replicas` and replication-factor configuration.

### Increasing partition count on an existing topic breaks existing key-to-partition assignment

Partition count is the parallelism ceiling `distributed/retries-and-backpressure.md` already
names generically, but the Kafka-specific trap is sharper than "you can't add more consumers
than partitions": a key's partition is determined by `hash(key) % partition_count`, so
**increasing partition count on an existing topic changes which partition future messages for an
existing key land on**, breaking per-key ordering continuity across the change. This is a real,
frequently-missed constraint — repartitioning is not a free scaling lever the way adding
consumers up to the existing partition count is.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| `kafka-consumer-groups.sh --describe` (or the equivalent admin API call) | Per-partition consumer lag, directly — the tool to confirm a key-skew or rebalance-driven lag finding | **safe-on-production** — read-only |
| Broker/client JMX metrics (`RecordsLagMax`, `UnderReplicatedPartitions`, request/response queue times, ISR shrink/expand rate) | Aggregate health and replication state | **safe-on-production** — metrics only |
| Kafka Connect REST API (`/connectors/{name}/status`) | Connector and task state, when Connect is in use | **safe-on-production** |
| `kcat`/`kafkacat` for ad-hoc topic inspection | Direct message inspection for debugging | **safe-on-production** when consuming without a group ID (or with a disposable one) — joining a real application's consumer group with an ad-hoc tool would itself trigger the rebalance described in §2 |

Per-partition lag from `kafka-consumer-groups.sh --describe` deserves the same emphasis given to
the always-on diagnostics in this skill's other technology references: no code change required,
and usually the fastest way to confirm whether an aggregate-lag or key-skew finding is real.

---

## 4. Common failure modes and their symptoms

| Symptom | Kafka-specific cause to check first |
|:--|:--|
| Sudden, group-wide throughput collapse with no code change | Frequent consumer group rebalances — check whether `max.poll.interval.ms` is being exceeded, and which rebalancing protocol (eager vs. cooperative sticky) is configured |
| Messages occasionally go missing on leader failure | `acks=1` (or `acks=0`) on a producer whose data is treated as durable — check `min.insync.replicas` and replication factor alongside it |
| Broker disk usage climbs steadily regardless of consumer health | Retention (`retention.ms`/`retention.bytes`) configured without regard to actual write rate and disk capacity — not a consumer-lag problem |
| A changelog/latest-state topic loses old keys unexpectedly | Time/size retention configured where log compaction was intended |
| Aggregate consumer lag looks healthy but downstream processing for specific keys is stale | Key skew concentrating load on one partition — check per-partition lag directly, not the aggregate |
| Per-key ordering breaks after a partition-count increase | The `hash(key) % partition_count` reassignment this causes — a partition-count change is not a transparent scaling operation for keyed data |
| Low-throughput topic has added latency with no batching benefit | `linger.ms` configured for a throughput profile the actual traffic doesn't have — a pure latency cost being paid for no gain |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `acks` | `0`/`1` favor latency and throughput at real durability risk; `all` waits for the in-sync replica set, trading latency for durability |
| `linger.ms` / `batch.size` (producer) | Higher values improve batching efficiency under real throughput, at a pure latency cost when traffic doesn't actually benefit from batching |
| `max.poll.records` / `max.poll.interval.ms` (consumer) | A larger processing budget per poll reduces spurious rebalances, at the cost of a longer window before a genuinely stuck consumer is detected |
| Rebalancing protocol (eager vs. cooperative sticky) | Cooperative sticky reduces rebalance blast radius substantially; requires client library support and explicit configuration to enable |
| `retention.ms`/`retention.bytes` vs. log compaction | A time/size window bounds disk usage for event-stream topics; compaction is the correct choice for changelog/latest-state topics — the two are not interchangeable |
| Replication factor / `min.insync.replicas` | Higher values improve durability and availability during broker loss, at the cost of more storage and slower acknowledgment under `acks=all` |
| Partition count | Sets the consumer-parallelism ceiling, but is expensive to change after the fact for keyed data (see §2) — worth sizing deliberately up front rather than treating as freely adjustable later |

---

## 6. Version differences worth knowing

- **Cooperative sticky rebalancing** (KIP-429) has been available since **Kafka 2.4** but requires
  explicit client configuration to use instead of the older eager protocol — confirm it is
  actually enabled rather than assuming a recent broker version implies it.
- **Static group membership** (KIP-345, 2.3+) lets a consumer keep a persistent
  `group.instance.id` across a brief restart, avoiding a rebalance entirely for planned restarts
  or deploys — a real, checkable mitigation for rebalance-sensitive workloads.
- **Idempotent producers became enabled by default in Kafka 3.0** (`enable.idempotence`
  defaulting to `true`), preventing duplicate messages from producer-side retries; confirm this
  wasn't explicitly disabled on an older or migrated configuration.
- **KRaft mode** (removing the ZooKeeper dependency) reached production readiness around
  Kafka 3.3–3.5 and became the default in Kafka 4.0. Confirm which metadata mode a deployment
  actually runs — ZooKeeper-specific operational and latency reasoning does not apply to a KRaft
  cluster, and vice versa.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Kafka Connect connector-specific configuration and tuning — a distinct topic per connector.
- Kafka Streams or ksqlDB stream-processing topology performance (operators, state stores,
  changelog topics) — a substantially larger, separate performance model, out of scope here.
- Schema Registry and schema evolution/compatibility — a correctness and API-contract concern,
  not this file's performance focus.
- Broker-side JVM tuning — Kafka brokers run on the JVM; draw on `technology/jvm.md` for GC and
  heap-sizing reasoning, which this file does not repeat.
- MirrorMaker and cross-cluster replication.
- Exactly-once semantics' internal implementation beyond noting its availability (idempotent
  producers, transactions) in §2.
- Specific numeric recommendations for any setting; recommend the measurement that determines it.
