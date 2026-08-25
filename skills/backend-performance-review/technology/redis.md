# Redis

Load after `databases/universal.md`, `databases/key-value.md`, and — where it is used as a
cache — `distributed/caching.md`.

This file contains **only what those files do not give you**. Key design, hot keys, round
trips, and cache invalidation reasoning live there.

Most of what follows applies equally to API-compatible forks; where behavior may differ,
confirm rather than assume.

---

## 1. Detection signals

Dependencies: `redis` (Python), `aioredis`, `redis-py-cluster`, `ioredis`, `node-redis`,
`go-redis`, `rueidis`, `Jedis`, `Lettuce`, `Redisson`, `StackExchange.Redis`, `redis-rs`.

Higher-level uses that imply Redis without naming it: Celery, RQ, Sidekiq, BullMQ, Bee-Queue,
Resque, `django-redis`, Rails cache store, `connect-redis` and other session stores, Socket.IO
adapters, and rate-limiter libraries.

Other signals: `redis://` or `rediss://` schemes, `redis`/`valkey` container images,
`redis.conf`, ElastiCache or MemoryDB resources in infrastructure code, `sentinel` or
`cluster` configuration.

**Note what Redis is being used *for*** — cache, session store, queue, lock, rate limiter,
pub/sub, or primary data. The failure modes differ completely, and several deployments use it
for many of these simultaneously through a single instance, which is itself worth noting.

---

## 2. What differs from the key-value baseline

### Command execution is effectively single-threaded

Data commands are processed one at a time. Networking and some background work are threaded
in recent versions, but command execution is not parallel.

This is the most important fact about Redis performance, and it converts several "minor"
findings into severe ones:

- **One slow command blocks every other client**, on every code path, for its duration.
- Latency is therefore coupled across all users of the instance. Blast radius for any
  expensive command is `system-wide` by construction.
- Adding application concurrency does not help; the instance is the serialization point.

Commands whose cost scales with data size are the ones to find: full keyspace iteration,
operations returning or manipulating entire large collections, set operations across large
sets, sorting large collections, deleting a very large structure, and scripts that loop.

**A single call operating on a large collection can stall the instance long enough to cause
application-wide timeouts.** Treat any unbounded-size command on a request path as a
significant finding, and prefer the incremental or bounded variants where they exist.

### Data structures are the point

Using Redis purely as an opaque string store forgoes most of its advantage and usually
implies read-modify-write cycles that the native structures would perform atomically in one
round trip. Hashes, sorted sets, sets, lists, streams, and bitmaps each turn a pattern that
would otherwise be several round trips plus a race into a single atomic operation.

Conversely, a structure that grows without bound — a list used as a queue nobody drains, a
sorted set accumulating every event, a hash keyed by something unbounded — is both a memory
risk and a source of slow commands whenever it is read whole.

### Persistence has periodic costs

Snapshotting forks the process; log rewriting does similar work in the background. Both
consume I/O and CPU, and the fork can cause a memory spike and a latency pause proportional
to dataset size. These are periodic tail-latency events invisible in application code, and
they are a common explanation for "occasional inexplicable spikes".

For a pure cache, persistence may be unnecessary — and disabling it removes this whole class
of problem. For anything treated as durable, the persistence mode is a correctness question
first.

### Eviction policy determines whether it is a cache at all

The behavior when memory fills is configurable and includes both eviction and **refusing
writes**. A Redis used as a cache but configured to reject writes turns a cache miss — which
should degrade gracefully — into an application error. This is a specific, checkable, and
frequently wrong setting.

Equally: keys written without expiry accumulate. A cache with no TTLs and an eviction policy
that only considers keys with TTLs will fill and then start failing.

### Clustering constrains multi-key operations

In cluster mode, multi-key commands generally require all keys to live in the same slot. Hash
tags are the mechanism for forcing that, and they reintroduce hot-slot risk if over-used.
Pipelining across slots is more complex, and some libraries silently fall back to sequential
round trips — which removes the benefit that motivated pipelining in the first place.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `INFO` (and sections: `memory`, `stats`, `clients`, `persistence`, `replication`) | Memory use vs limit, hit/miss counters, evictions, blocked clients, connection counts | **safe-on-production** |
| `INFO commandstats` | Per-command call count and total/average time — the fastest route to the expensive command | **safe-on-production** |
| `INFO latencystats` | Latency percentiles per command | **safe-on-production** (recent versions) |
| `SLOWLOG GET` | Commands exceeding the slow threshold, with arguments | **safe-on-production** |
| `LATENCY LATEST` / `LATENCY DOCTOR` | Latency spike events and their causes | **safe-on-production** |
| `CLIENT LIST` | Connections, idle time, last command | **safe-on-production** |
| `MEMORY USAGE <key>` | Size of one key | **safe-on-production** |
| `MEMORY DOCTOR` | Memory-related warnings | **safe-on-production** |
| `--bigkeys` / `--memkeys` (via the CLI) | Largest keys per type, using incremental scanning | **safe-on-production** — samples rather than blocking |
| `SCAN` with a modest `COUNT` | Incremental keyspace iteration | **safe-on-production** |
| `KEYS <pattern>` | Full keyspace scan | **not-safe-on-production** — blocks the instance for the whole scan |
| `DEBUG SLEEP`, `DEBUG SEGFAULT` | Diagnostics | **not-safe-on-production** |
| `FLUSHALL` / `FLUSHDB` | Deletes everything | **never** — including on staging without confirmation |
| `MONITOR` | Every command in real time | **not-safe-on-production** — significant throughput cost |

The two highest-value first checks are `INFO commandstats` (which command is consuming the
instance) and the hit/miss and eviction counters in `INFO stats` (whether the cache is
working at all). The latter frequently reveals that a cache assumed to be effective has a
poor hit rate and is pure overhead.

---

## 4. Common failure modes and their symptoms

| Symptom | Redis-specific cause to check first |
|:--|:--|
| Application-wide latency spikes with no application change | A slow command blocking the instance; or a fork for persistence |
| Errors writing to the cache | `maxmemory` reached with a non-evicting policy |
| Memory climbing steadily | Keys written without TTL; an unbounded collection |
| Cache appears not to help | Low hit rate — check `keyspace_hits` vs `keyspace_misses` before anything else |
| High eviction counts | Working set larger than `maxmemory`; the cache is thrashing |
| Intermittent stalls correlated with a schedule | Snapshotting or log rewriting |
| Multi-key operation fails in production but not locally | Cluster mode slot constraint |
| Throughput plateau despite adding application instances | Single-threaded command execution is the constraint |
| A queue backing up | Consumers slower than producers; or a blocking operation with a long timeout holding a connection |
| Lock-based code behaving incorrectly under load | Lock TTL shorter than the protected work; a distributed-lock correctness issue with performance symptoms |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `maxmemory` | Must be set, and must leave room for replication buffers, client buffers, and fork overhead — the process needs more than `maxmemory` |
| `maxmemory-policy` | Eviction versus write rejection. `noeviction` is correct for durable data and wrong for a cache |
| Persistence mode (snapshot / append-only / none) | Durability versus periodic fork cost and latency spikes |
| `appendfsync` | Durability versus per-write latency |
| Slow-log threshold | Evidence versus log volume |
| `timeout` (idle client) | Reclaims abandoned connections versus disconnecting legitimately idle ones |
| `tcp-keepalive` | Detects dead peers versus a small amount of traffic |
| Client output buffer limits | Protects the instance from slow consumers versus disconnecting them |
| Replica read routing | Read capacity versus staleness — replication is asynchronous |
| Cluster vs single instance | Horizontal capacity versus multi-key constraints and operational complexity |

`maxmemory` set equal to the container memory limit is a specific and common misconfiguration:
the process needs headroom above `maxmemory` for buffers and for the fork, so it will be
killed before the eviction policy ever engages.

---

## 6. Version differences worth knowing

- Threaded I/O (introduced in 6.0, off by default initially) affects networking throughput but
  **not** command execution parallelism — the single-threaded execution model still applies.
- `SCAN` and its family have long been the safe alternative to `KEYS`; advice recommending
  `KEYS` for production predates their availability and should be rejected.
- Asynchronous deletion (`UNLINK`, and lazy-free settings, from 4.0) reclaims large structures
  in the background, avoiding the stall of a synchronous large delete.
- Streams (5.0) provide a consumer-group model that is a better fit for queue workloads than
  list-based patterns; queue reasoning based on lists may be dated.
- Functions (7.0) supersede some scripting patterns; scripts still execute atomically and
  therefore still block the instance for their duration.
- Licensing changes from 7.4 onward led to API-compatible forks. Behavior is largely shared
  but should be confirmed rather than assumed for anything version-sensitive.

---

## 7. What this file does NOT cover

- Key design, hot keys, value shape, round-trip reasoning — see `databases/key-value.md`.
- Cache hit rate, invalidation, stampede, and staleness reasoning — see
  `distributed/caching.md`.
- Distributed lock correctness. Lock TTLs, fencing tokens, and the safety limits of
  lock-by-cache are a correctness topic; performance review should flag the pattern and defer
  the correctness analysis.
- Pub/Sub delivery semantics and their capacity implications.
- Redis Stack modules — search, JSON, time series, vector similarity — each of which has its
  own performance model. Treat conclusions about them as unknowns.
- Sentinel and cluster failover behavior and its latency impact.
- Specific numeric recommendations for any setting; recommend the measurement that determines
  it.
