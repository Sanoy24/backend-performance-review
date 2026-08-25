# RabbitMQ

Load after `distributed/retries-and-backpressure.md` and `distributed/timeouts-and-deadlines.md`.

This file contains **only what those files do not give you**. Retry/backpressure controls,
generic queue reasoning (lag, poison messages, dead-letter paths, batch/prefetch trade-offs) are
covered there and are not repeated here — this file gives RabbitMQ's concrete mechanisms and
what makes its failure modes distinct from Kafka's or a generic queue's.

---

## 1. Detection signals

Dependencies: `pika`, `amqplib`, `streadway/amqp`, `kombu` (also Celery's broker abstraction —
note when RabbitMQ is being used purely as a Celery broker, since Celery-specific reasoning
belongs to the task-queue signal, not this file). Container images (`rabbitmq`), `rabbitmq.conf`
or `definitions.json`, `rabbitmqctl`/management-plugin usage, CloudAMQP or `rabbitmq_cluster`
resources in infrastructure code.

**Record the queue type in use** — classic, quorum, or stream — and **whether classic mirrored
queues are still configured** (deprecated, removed in RabbitMQ 4.0). This determines which
replication and memory-behavior reasoning in §2 applies.

---

## 2. What differs from the generic queue/broker baseline

### A backed-up queue can trigger a cluster-wide memory alarm that stalls every publisher on the broker

This is the single most severe, RabbitMQ-specific failure mode, and it has no equivalent in this
skill's other broker coverage. RabbitMQ enforces a **memory high watermark**
(`vm_memory_high_watermark`): when a node's memory usage crosses it, the broker raises a
**memory alarm** and blocks — not just the queue that's backed up, but **publishing on every
queue on that node**, cluster-wide if the alarm propagates. One slow consumer letting a single
classic queue accumulate unacknowledged or unconsumed messages in RAM can, through this
mechanism, stall unrelated publishers with no relationship to the backed-up queue at all — a
`system-wide` blast radius that is easy to miss because the triggering queue and the affected
publishers can look completely unconnected in application code. **Lazy queues** (or the paging
behavior newer versions apply more proactively) move messages to disk more aggressively
specifically to avoid this, trading some latency for memory safety — worth checking as a direct
mitigation where memory alarms have occurred or are a real risk.

### A single queue is not partitioned by default — its own throughput is the ceiling, not a partition count you control

Where Kafka's parallelism ceiling is an explicit, visible partition count, a classic or quorum
RabbitMQ queue is a single logical entity (backed by one Erlang process) that all its competing
consumers pull from — there is no partition count to raise. Adding consumers to a single queue
scales *distribution of work* among them, but the queue's own per-message processing overhead
becomes the ceiling once consumers are no longer the bottleneck — a different scaling shape from
Kafka's, where **partitioning must be done deliberately**, either via multiple queues or a
consistent-hash exchange, if genuine parallel throughput beyond one queue's own ceiling is
needed. Recommending "add more consumers" indefinitely to a single-queue design is the RabbitMQ
analogue of recommending consumers beyond Kafka's partition count.

### Exchange routing has its own, distinct publish-time cost

RabbitMQ's exchange/binding model (direct, topic, fanout, headers) decouples publishers from
queues, but that decoupling is not free: a publish to an exchange is evaluated against every
matching binding, and a **fanout exchange with many bound queues, or a topic exchange with many
overlapping binding patterns, multiplies the cost of a single publish** across all of them. This
is a distinct cost from the generic "fan-out to many downstream operations" finding
`distributed/retries-and-backpressure.md` §7 describes — that finding is about what happens
*after* delivery; this one is about the broker's own routing-evaluation cost *at* publish time,
before any consumer sees a message.

### Prefetch (QoS) is the concrete throughput/fairness knob behind competing-consumer delivery

`basic.qos` sets how many unacknowledged messages a consumer may hold at once. Set too low
(historically `1` in several client defaults), it limits throughput to roughly one network
round trip per message; set too high, one slow consumer can hoard a large share of outstanding
messages while other, faster consumers in the same competing-consumer group sit under-fed. This
is the concrete mechanism behind the generic file's "batch and prefetch sizes trade throughput
against redelivery risk" — worth naming the actual setting and its historical low-default
gotcha directly.

### Manual acknowledgment is the durability control, and a consumer that never acks is a specific, checkable bug shape

Auto-ack (no acknowledgment at all) treats a message as delivered the instant it's written to
the socket — lost if the consumer crashes before finishing. Manual acknowledgment is safer but
requires the consumer to actually ack in a timely fashion; a code path that processes a message
but exits (via an unhandled exception, for instance) without acking or nacking leaves that
message **unacknowledged indefinitely**, consuming memory and, at scale, contributing to the
memory-alarm condition in the first subsection above. A steadily growing unacked-message count
on an otherwise-healthy-looking queue is the direct symptom to check for this.

### `consumer_timeout` closes the channel outright, rather than silently rebalancing

RabbitMQ (from roughly version 3.9 onward) enforces `consumer_timeout`: if a consumer holds a
message unacknowledged for longer than this setting, the broker closes the channel with an
error, rather than the message simply being redelivered. This is the RabbitMQ analogue of
Kafka's `max.poll.interval.ms` eviction, but the consequence is different and worth naming
precisely — a hard channel closure and connection-level error, not a quieter group rebalance — so
a consumer legitimately needing long per-message processing time must either raise this setting
deliberately or restructure to ack faster and process asynchronously.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| Management UI / HTTP API | Per-queue depth, consumer count, message rates, and per-node memory/disk alarm status | **safe-on-production** for `GET` endpoints — read-only |
| `rabbitmqctl list_queues` / `list_consumers` | Queue depth, consumer counts and their prefetch/ack state | **safe-on-production** — read-only |
| `rabbitmq-diagnostics` (memory breakdown, cluster status) | Node-level memory usage by category, cluster health | **safe-on-production** |
| Prometheus plugin metrics (`rabbitmq_queue_messages`, `rabbitmq_consumer_utilisation`, alarm state) | The same data as the management UI, suited to continuous monitoring and alerting | **safe-on-production** |
| Firehose tracing plugin | Full message-level tracing across the broker | **not-safe-on-production** for continuous use — captures every message and adds real overhead; a targeted, time-boxed debugging tool only |

The management HTTP API's memory-alarm and per-queue depth endpoints deserve particular
emphasis: they are usually the fastest way to confirm whether an unrelated publisher's stall is
actually the memory-alarm mechanism from §2, rather than a problem with that publisher itself.

---

## 4. Common failure modes and their symptoms

| Symptom | RabbitMQ-specific cause to check first |
|:--|:--|
| Publishers on unrelated queues suddenly stall, with no obvious connection between them | The cluster-wide memory alarm, triggered by one backed-up classic queue on the same node — check per-node memory alarm state before assuming an unrelated cause |
| A single queue's throughput plateaus despite adding more consumers | The queue itself (one Erlang process) has become the ceiling — genuine additional parallelism requires multiple queues or a consistent-hash exchange, not more consumers on the one queue |
| Publish latency is high on an exchange with many bound queues or a broad topic pattern | Per-binding routing evaluation cost at publish time — a fanout or wide topic pattern multiplying the cost of a single publish |
| One consumer processes far more slowly than its peers in the same competing-consumer group | Prefetch (`basic.qos`) set too high, letting one consumer hoard unacknowledged messages |
| Overall throughput is far below expectation despite idle-looking consumers | Prefetch set too low (e.g. `1`), forcing a round trip per message |
| Unacked message count grows steadily on an otherwise normal-looking queue | A consumer code path that processes a message but never acks or nacks it — check exception handling around the ack call specifically |
| A long-running consumer's channel closes unexpectedly with an error | `consumer_timeout` exceeded — confirm whether the processing time is expected and the timeout needs raising, or whether it's genuinely stuck |
| Dead letters accumulate without bound | A dead-letter exchange configured with no consumer on the resulting queue — the same unbounded-growth risk as any unmonitored queue, easy to miss because it's off the primary path |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Prefetch / QoS (`basic.qos`) | Higher values improve per-consumer throughput at the risk of uneven load distribution among competing consumers; too low limits throughput to round-trip latency per message |
| Queue type (classic vs. quorum vs. stream) | Quorum queues (Raft-based) trade some per-message overhead for stronger replication guarantees than classic mirroring, which is deprecated; streams suit a log-like, replay-oriented access pattern closer to Kafka's model |
| Lazy queue / paging behavior | Reduced memory pressure and lower risk of the memory-alarm condition, at the cost of higher per-message latency from more aggressive disk paging |
| `vm_memory_high_watermark` | A higher watermark delays the memory alarm but leaves less headroom before an out-of-memory condition; a lower watermark triggers the (broker-wide) alarm earlier and more conservatively |
| Acknowledgment mode (auto vs. manual) | Auto-ack is simpler and marginally faster but loses messages on consumer crash; manual ack is durable but requires the consumer to ack reliably, including on error paths |
| `consumer_timeout` | A higher value tolerates genuinely long per-message processing; a lower value detects a stuck consumer faster, at the risk of closing the channel on legitimately slow (but healthy) processing |

---

## 6. Version differences worth knowing

- **Quorum queues** (Raft-based replication) were introduced in **RabbitMQ 3.8** as the modern
  replicated queue type. **Classic queue mirroring is deprecated and removed in RabbitMQ 4.0** —
  a deployment still relying on classic mirroring is carrying a real, checkable migration debt,
  not just a stylistic choice.
- **Streams** (an append-only, replay-oriented queue type, since RabbitMQ 3.9) behave closer to a
  Kafka-style log than a classic or quorum queue — confirm which type is actually in use before
  applying this file's competing-consumer reasoning, which assumes classic or quorum semantics.
- **`consumer_timeout`'s default and enforcement** have changed and tightened across versions —
  confirm the actual configured value and version rather than assuming a specific default.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Celery- or other task-queue-framework-specific behavior when RabbitMQ is used purely as their
  broker — that reasoning belongs to the task-queue detection signal, not this file.
- Federation and Shovel plugins for cross-cluster or cross-region replication.
- RabbitMQ Streams' consumption model and tooling in depth, beyond flagging it in §6 as distinct
  from classic/quorum queue reasoning.
- Cluster network-partition handling modes (`pause_minority`, `autoheal`, etc.) in depth.
- Specific numeric recommendations for any setting; recommend the measurement (management API,
  Prometheus metrics) that determines the right value.
