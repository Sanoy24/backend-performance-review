# Amazon SQS

Load after `distributed/retries-and-backpressure.md` and `distributed/timeouts-and-deadlines.md`.

This file contains **only what those files do not give you**. Retry amplification,
backpressure, the general queue/consumer reasoning (throughput versus lag, partition-bound
parallelism, poison messages, batch/prefetch trade-offs, ordering constraints), and
deadline/timeout propagation are covered there and are not repeated here.

---

## 1. Detection signals

Dependencies: `@aws-sdk/client-sqs` (Node.js), `boto3` with an `sqs` client (Python),
`aws-sdk-go-v2/service/sqs` (Go), `software.amazon.awssdk:sqs` (JVM), `AWSSDK.SQS` (.NET).

Other signals: `sqs.<region>.amazonaws.com` or `sqs.<region>.amazonaws.com.cn` in a
connection URL, `arn:aws:sqs:` in configuration or IAM policy, `AWS::SQS::Queue` in
CloudFormation/SAM, `aws_sqs_queue` in Terraform.

**Record whether the queue is Standard or FIFO** during discovery — this is fixed at queue
creation and cannot be changed afterward, and it determines which of §2's throughput and
ordering facts apply.

---

## 2. What differs from the generic queue baseline

### Visibility timeout is SQS's specific lease mechanism, and mis-sizing it is the most common SQS-specific finding

A received message is not deleted — it becomes invisible to other consumers for the queue's
configured **visibility timeout**, then reappears if the consumer has not deleted it (or
called `ChangeMessageVisibility` to extend the lease) within that window. This is the
concrete SQS mechanism behind the generic file's "prefetching more than can be processed
within the lease window guarantees redelivery" principle:

- A visibility timeout shorter than realistic processing time causes **duplicate processing
  of in-flight, not-yet-failed messages** — the message reappears and a second consumer picks
  it up while the first is still legitimately working on it.
- Long-running message processing needs an explicit `ChangeMessageVisibility` call to extend
  the lease mid-flight; code that assumes the original timeout covers worst-case processing
  time, with no extension logic, is a checkable, common defect.

### Standard queues can deliver duplicates and out-of-order messages even without any visibility-timeout expiry

This is stronger than the generic file's poison-message framing: AWS explicitly documents
Standard SQS as **at-least-once delivery with occasional duplicates as normal operation**,
a consequence of the service's own distributed architecture, independent of any consumer
misbehavior. Consumer logic that assumes exactly-once or strictly-ordered delivery on a
Standard queue is working against a documented guarantee the queue type does not provide —
this is a correctness question (`SKILL.md` rule 8) as much as a performance one.

**FIFO queues** trade this away for strict per-message-group ordering and
deduplication (content-based or an explicit `MessageDeduplicationId`, within a 5-minute
window) — but pay for it with a real throughput ceiling: 300 messages/second without
batching or 3,000/second with batching, per API action, unless **high-throughput mode**
(which scales with the number of distinct message-group IDs rather than a flat per-queue
cap) is enabled. A FIFO queue approaching this ceiling is a hard architectural constraint,
not a tuning problem — the fix is high-throughput mode, more message groups, or
reconsidering whether FIFO's ordering guarantee is actually needed for this workload.

### Short polling wastes requests and can miss messages that exist

`ReceiveMessageWaitTimeSeconds = 0` (short polling, the default if unset) returns
immediately, even when no message is available, and — because SQS distributes a queue's
messages across many backend servers — a short-poll request that queries only a subset of
those servers can return an empty result **while messages genuinely exist on the queue**.
**Long polling** (`ReceiveMessageWaitTimeSeconds` set to 1–20 seconds) waits for a message to
become available (or the timeout to elapse) and samples more completely. This is
simultaneously a cost finding (fewer, more meaningful API calls) and a correctness-adjacent
one (short polling can under-report what is actually on the queue) — worth flagging as both,
not filing only under one.

### Batching is the direct, checkable cost lever, with an exact ceiling

`SendMessageBatch` and `ReceiveMessage`'s `MaxNumberOfMessages` (up to 10 messages per call)
reduce API request count directly, and SQS's request-based pricing makes this a direct cost
finding, not merely a latency one — the SQS-specific, numerically exact form of the generic
"batch API calls" principle. Code issuing single-message `SendMessage`/`ReceiveMessage`
calls in a loop where the messages are independent and available together is the checkable
anti-pattern.

### The message size limit is hard, and the documented workaround is a specific, named pattern

Messages are capped at 256 KB. The documented workaround for larger payloads is the
**SQS Extended Client Library**, which stores the payload in S3 and sends a pointer through
SQS — not a custom-rolled equivalent. A repository handling this constraint some other way
(truncating, compressing without a fallback, or simply failing on oversized messages) is
worth comparing against the standard pattern.

### Dead-letter queue redrive requires an explicit, separate action

A **redrive policy** (`maxReceiveCount` pointing at a separate DLQ queue) is the SQS-specific
form of the generic file's poison-message/DLQ guidance. What is specific to SQS: messages
that land in the DLQ **do not automatically return to the source queue** — reprocessing them
requires an explicit redrive action (`StartMessageMoveTask` in current SDKs, or an equivalent
manual re-send). A DLQ with no redrive or inspection process attached is a common,
checkable gap — messages accumulate there and are never looked at again.

---

## 3. Diagnostics

| Command / source | What it shows | Production safety |
|:--|:--|:--|
| CloudWatch `ApproximateNumberOfMessagesVisible` | Queue depth — the primary lag/health metric the generic file names as essential | **safe-on-production** |
| CloudWatch `ApproximateAgeOfOldestMessage` | How long the oldest unprocessed message has waited — often a sharper signal than raw depth | **safe-on-production** |
| CloudWatch `NumberOfMessagesSent` / `NumberOfMessagesReceived` / `NumberOfMessagesDeleted` | Producer versus consumer throughput, directly comparable | **safe-on-production** |
| CloudWatch `ApproximateNumberOfMessagesNotVisible` | Messages currently leased (in flight) — a sustained high value suggests visibility-timeout or processing-time mismatch | **safe-on-production** |
| DLQ's own depth metrics | Poison-message rate | **safe-on-production** |
| `aws sqs get-queue-attributes` | Current configuration: visibility timeout, retention period, redrive policy | **safe-on-production** |

`ApproximateAgeOfOldestMessage` rising while `ApproximateNumberOfMessagesVisible` stays flat
is the specific signature of a stuck or slow consumer on an otherwise steady queue — worth
checking before assuming a capacity problem.

---

## 4. Common failure modes and their symptoms

| Symptom | SQS-specific cause to check first |
|:--|:--|
| The same message is processed more than once, close together | Visibility timeout shorter than actual processing time; no `ChangeMessageVisibility` extension for long-running work |
| Occasional duplicates or out-of-order delivery with no other explanation | Standard queue's documented at-least-once, best-effort-ordering behavior — not a bug |
| A FIFO queue's throughput plateaus under load | The per-message-group-ID throughput ceiling; check whether high-throughput mode or more message groups would help |
| `ReceiveMessage` sometimes returns empty despite a visibly nonzero queue depth | Short polling sampling only a subset of backend servers |
| API costs are far higher than message volume would suggest | Single-message `Send`/`Receive` calls where batching (up to 10) was available |
| A large payload fails to send, or is silently truncated | The 256 KB message-size limit, with no Extended Client Library in use |
| Messages pile up in the DLQ and are never resolved | No redrive process or alerting attached to the DLQ |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Visibility timeout | Enough lease time to avoid duplicate in-flight processing versus how long a genuinely failed consumer's message stays invisible before retry |
| `ReceiveMessageWaitTimeSeconds` (long polling) | Fewer, more complete API calls versus a small added latency per receive when the queue is empty |
| `MaxNumberOfMessages` (batch receive, up to 10) | Fewer API calls and lower cost versus larger per-call processing batches to manage |
| Standard versus FIFO | Higher, effectively unbounded throughput versus strict ordering and deduplication at a real throughput ceiling |
| FIFO high-throughput mode | Scaling with message-group-ID count versus the flat per-queue throughput cap |
| `maxReceiveCount` (redrive policy) | Genuine transient-failure retries versus how long a poison message circulates before quarantine |
| Message retention period | Replay/recovery window versus messages an unresolved outage eventually discards |

---

## 6. Version differences worth knowing

SQS is a managed service with no version to record in the usual sense, but its feature
surface has grown:

- **FIFO high-throughput mode** is a comparatively recent addition; a FIFO queue built
  before it existed (or simply not configured to use it) is subject to the older flat
  per-queue throughput ceiling even though the newer mode is available.
- **`StartMessageMoveTask`** (bulk DLQ-to-source redrive via the API/console) replaced
  earlier custom-scripted redrive approaches; a repository with a hand-rolled redrive Lambda
  or script may predate it.

**Confirm current limits and available features against current AWS documentation** — SQS's
numeric limits in particular are the kind of fact that changes without a version number
attached to the change.

---

## 7. What this file does NOT cover

- Retry amplification, backpressure, general queue/consumer reasoning, and fan-out — see
  `distributed/retries-and-backpressure.md`.
- Timeout configuration and deadline propagation for the surrounding request path — see
  `distributed/timeouts-and-deadlines.md`.
- SNS-to-SQS fan-out patterns and SNS's own delivery semantics.
- IAM policy design and cross-account queue access.
- Server-side encryption (SSE-SQS versus SSE-KMS) and its latency/cost trade-off.
- Lambda-triggered consumption (event-source mapping) specifics, beyond noting it exists as
  an alternative to a polling consumer.
- Exact current pricing; this file names what is billed (requests, not strictly messages, and
  data transfer), never a rate.
- Specific numeric threshold recommendations beyond the documented limits and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
