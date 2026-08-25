# DynamoDB

Load after `databases/universal.md` and `databases/key-value.md`.

This file contains **only what those files do not give you**. Key design, round-trip
reasoning, and hot-key concepts are covered there and are not repeated here. DynamoDB is a
fully managed service with no user-selectable engine version, so this file's "version
differences" (§6) are about capability changes over time and account/table-level settings,
not a version string to detect.

---

## 1. Detection signals

Dependencies: `boto3`'s DynamoDB client/resource, `@aws-sdk/client-dynamodb`,
`aws-sdk-go-v2/service/dynamodb`, `com.amazonaws.services.dynamodbv2`, DynamoDB Local in a
`docker-compose` file (used for local development against a real DynamoDB API surface).

Other signals: `aws_dynamodb_table` in Terraform or CloudFormation, DAX (DynamoDB Accelerator)
client libraries or cluster resources, DynamoDB Streams event-source mappings on a Lambda
function.

**Record the capacity mode** (Provisioned with or without auto-scaling, versus On-Demand) and,
for each table, **whether a Global Secondary Index (GSI) exists and what it's provisioned to
relative to the base table** — several of the failure modes below hinge on these two facts and
neither is visible from the application code alone.

---

## 2. What differs from the key-value baseline

### Partition key choice determines physical throughput distribution, not just logical access

`databases/key-value.md` §6 covers hot keys generically; DynamoDB's mechanism for this is
concrete and worth naming directly. The partition key's hash determines which physical partition
an item lives on, and each partition has its own throughput ceiling — a partition key with low
cardinality or a skewed value distribution (a `status` field with three possible values used as
the partition key, for example) concentrates traffic on one physical partition regardless of how
much capacity the *table* has. **Adaptive capacity** mitigates this somewhat by shifting
throughput toward hot partitions automatically, but it is not instantaneous and not unlimited —
confirm the actual key design rather than assuming adaptive capacity has made this finding
obsolete.

### Query is cheap; Scan charges for every item examined, not just returned

`Query` (partition key, optionally narrowed by a sort key condition) is the efficient access
path `databases/key-value.md` describes generically. `Scan` reads the entire table or index and
is billed for every item **examined**, whether or not it matched a filter — filtering happens
after the read cost is already incurred. This makes a `Scan` on a request path a severe finding
specifically in DynamoDB's cost model, more so than "full scan" findings in engines where reading
unindexed data is merely slow rather than separately metered. A `Scan` used for a periodic
maintenance or export job is a legitimate, different use — the distinction to check is whether it
runs on a request path.

### A Global Secondary Index has its own capacity, and its throttling propagates back to the base table

A GSI is not a lightweight secondary index in the relational sense — it is closer to a second
table, with its own partition (and optional sort) key schema and, in Provisioned mode, **its own
provisioned throughput separate from the base table's**. A write to the base table that also
updates an under-provisioned GSI can be throttled because of the GSI's capacity even though the
base table has plenty of room — a specific, non-obvious failure mode worth checking explicitly:
GSI capacity is not automatically kept proportional to the base table's.

A **Local Secondary Index (LSI)**, by contrast, shares the base table's partition key and
capacity, but must be created at table-creation time (never added later) and imposes a
**10 GB size limit per partition-key value** across the base table and all of its LSIs combined —
a hard, easy-to-miss ceiling distinct from anything a GSI or the base table alone is subject to.

### Consistency is a per-read, per-cost choice — and GSIs don't offer the strong option at all

Reads default to **eventually consistent**, at half the capacity-unit cost of a **strongly
consistent** read. This is a real, checkable trade a review should surface explicitly rather than
assume: requesting strong consistency by habit on a read path that would tolerate eventual
consistency doubles its read cost for no benefit. **GSIs support eventually consistent reads
only** — there is no strongly-consistent option for a GSI query at all, regardless of table
settings — a hard constraint worth flagging when a design assumes otherwise.

### The 400 KB item size limit is a hard cap, not a graduated cost

Unlike the soft, graduated "large values cost more" reasoning in `databases/key-value.md` §2, a
DynamoDB item cannot exceed 400 KB — full stop. A design that stores a growing blob (an
accumulating log, a large computed structure, embedded binary data) directly in an item will hit
a hard write failure at this ceiling, not a gradual slowdown. The standard mitigation — storing
the large payload in S3 and keeping DynamoDB for metadata and a pointer — is worth naming
directly as the fix rather than a vague "reduce item size" recommendation.

### TTL deletion is best-effort, not a precision expiry mechanism

Time-to-live deletion runs as a background process and is not guaranteed to remove an expired
item immediately — AWS documents a delay of up to 48 hours in typical operation. TTL is a real,
useful tool for storage-cost and table-size management, but a design that depends on it for
timing-precise behavior (an item that must be gone from queries at a specific instant) is a
design mismatch, not a configuration tuning question — application-level filtering on the expiry
timestamp is still required if precise timing matters.

### DAX is a write-through, API-compatible cache with its own consistency window

DynamoDB Accelerator (DAX) sits in front of DynamoDB as a managed, API-compatible caching layer.
Being write-through and API-compatible distinguishes it from a generic Redis-in-front-of-a-
datastore pattern, but it introduces its own eventual-consistency window on reads and does
nothing for write-heavy workloads, which still go straight to DynamoDB — worth naming as the
reason DAX helps a read-heavy access pattern specifically, not a general-purpose fix.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| CloudWatch: `ConsumedReadCapacityUnits`/`ConsumedWriteCapacityUnits` vs. provisioned or on-demand limits | Whether the table (or a specific GSI) is near or exceeding its effective capacity | **safe-on-production** — a managed metric, no query-path impact |
| CloudWatch: `ThrottledRequests` / `ReadThrottleEvents` / `WriteThrottleEvents`, per table and per GSI | Direct evidence of throttling, and specifically whether it's isolated to a GSI | **safe-on-production** |
| `ReturnConsumedCapacity` on an individual API call | The actual RCU/WCU consumed by that specific request — directly attributable, `Confirmed`-grade evidence for a suspected expensive-operation finding | **safe-on-production** — adds a small amount to the response payload, negligible cost |
| Contributor Insights (a managed DynamoDB feature) | The most-accessed and most-throttled partition key values, over a time window | **safe-on-production** — a managed, opt-in analytics feature with no write-path impact once enabled |
| DynamoDB Streams | Change events for downstream processing; not a profiling tool, but relevant when reviewing latency in a Streams-driven pipeline | **safe-on-production** to read from; adds its own capacity/cost considerations to enable |

`ReturnConsumedCapacity` and Contributor Insights together are usually the fastest path from a
suspected hot-partition or GSI-throttling finding to `Confirmed`-grade evidence — the first gives
exact per-request cost, the second gives the skewed key distribution directly.

---

## 4. Common failure modes and their symptoms

| Symptom | DynamoDB-specific cause to check first |
|:--|:--|
| Throttling well below the table's aggregate provisioned or on-demand capacity | A hot partition from a low-cardinality or skewed partition key — check Contributor Insights before assuming adaptive capacity has already handled it |
| Base-table writes throttle despite the base table having ample headroom | An under-provisioned GSI on that table — GSI throttling propagates back to the base-table write path |
| A request-path operation is unexpectedly slow and expensive | A `Scan` where a `Query` was intended, or where the access pattern needs a different key/index design entirely |
| A write fails outright rather than degrading | An item approaching or exceeding the 400 KB size limit — check whether a large payload belongs in S3 with only a pointer in DynamoDB |
| Read capacity consumption is double what the read volume suggests | Strongly consistent reads requested where eventual consistency would have sufficed |
| An LSI-backed access pattern fails unexpectedly on a specific, heavily-used partition key | The 10 GB per-partition-key limit across the base table and its LSIs |
| Expired items still appear in query results shortly after their TTL | TTL's best-effort, background deletion (up to ~48 hours) — expected behavior, not a bug; add application-level filtering if precise timing matters |
| Traffic spikes cause throttling on Provisioned capacity mode | No auto-scaling policy configured, or a spike rate exceeding auto-scaling's reaction time — consider On-Demand mode for genuinely unpredictable traffic |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Capacity mode (Provisioned + auto-scaling vs. On-Demand) | Provisioned gives predictable cost at the risk of throttling during unanticipated spikes; On-Demand handles unpredictable traffic automatically but has its own scaling-rate ceiling and a higher per-request cost at steady, predictable volume |
| GSI provisioned capacity relative to base table | Matching or exceeding the base table's write capacity avoids GSI-caused base-table throttling; under-provisioning it is a direct, checkable risk |
| Read consistency (eventual vs. strong) per read path | Eventual consistency halves read-capacity cost; strong consistency is only available on the base table and primary region, never on a GSI |
| DAX in front of the table | Lower read latency and reduced read-capacity consumption for a read-heavy, cache-tolerant access pattern, versus an added eventual-consistency window and no benefit for writes |
| TTL | Reduced storage cost and table size over time, versus non-guaranteed deletion timing — not a substitute for application-level expiry filtering where precision matters |

---

## 6. Version differences worth knowing

DynamoDB has no user-selectable engine version, but real capability differences exist across
time and account/table configuration:

- **On-Demand capacity mode** was introduced in 2018 — a table's infrastructure-as-code
  predating this and never revisited may still be on Provisioned mode with static capacity for
  no reason other than that it was the only option when created.
- **Adaptive capacity** became automatic and near-instantaneous for all tables around 2019;
  earlier documentation describing a manual opt-in or a slower reaction time no longer reflects
  current behavior — but "automatic" still is not "unlimited" or "instant," per §2.
- **Global Tables** has two versions (the original 2017 version and the 2019.11.21 version) with
  materially different replication and conflict-resolution behavior; confirm which version a
  multi-region deployment actually uses before applying either's specifics.
- Account-level and table-level default throughput ceilings have increased over time — confirm
  the account's actual current limits rather than assuming figures from older documentation or
  a colleague's memory of a past limit.

**Confirm current AWS documentation and the account's actual configured limits** rather than
asserting a specific numeric ceiling generically.

---

## 7. What this file does NOT cover

- Single-table design as a modeling philosophy — a real and common approach to DynamoDB data
  modeling, but a design-opinion question rather than a performance fact; this file covers the
  mechanical consequences (partition/GSI/consistency behavior) that any modeling approach must
  account for.
- DynamoDB Streams-driven event architecture in depth — see `distributed/` and the relevant
  `application/` files for the consumer-side reasoning.
- Global Tables' conflict-resolution semantics and cross-region replication lag in depth beyond
  the version note in §6.
- DAX cluster sizing and node-type selection in depth beyond the consistency-window trade-off
  noted in §2.
- IAM, encryption, and other security-configuration topics — out of scope for a performance
  review; see `SKILL.md` rule 8 for how an adjacent security finding would be reported if noticed.
- Specific numeric capacity or auto-scaling target recommendations; recommend the measurement
  (`ReturnConsumedCapacity`, CloudWatch, Contributor Insights) that determines the right value.
