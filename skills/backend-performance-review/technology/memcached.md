# Memcached

Load after `databases/universal.md`, `databases/key-value.md`, and `distributed/caching.md`.

This file contains **only what those files do not give you**. Key design, value size and
shape, round trips, the general memory/eviction questions, and cache-invalidation
reasoning are covered there and are not repeated here.

Memcached and Redis are the two engines a review most often finds sitting side by side, and
several of the points below are stated as a direct contrast with Redis (`technology/redis.md`)
for that reason — not because Redis is a baseline this file assumes, but because the
trade-off only becomes concrete in comparison.

---

## 1. Detection signals

Dependencies: `pymemcache`, `python-memcached` (Python), `memjs` (Node.js), `gomemcache`
(Go), `spymemcached`/`XMemcached` (Java/Kotlin), `Enyim.Caching`/`EnyimMemcached` (.NET),
`Dalli` (Ruby).

Other signals: `memcached` container images, `memcached.conf`, ElastiCache for Memcached
resources in infrastructure code, connection configuration listing multiple host:port pairs
(a client-side sharding list — see §2), `-m`/`-I` flags in a Memcached startup command
(memory limit and max item size).

**Note whether a client-side consistent-hashing library (e.g. ketama) is in use**, versus a
plain modulo-based host list — this determines what happens to the whole keyspace when the
node list changes (§2).

---

## 2. What differs from the key-value baseline

### There is no persistence, no replication, and no clustering — by design, not by configuration

Where Redis makes persistence and replication configurable choices, Memcached has none of
either, period: a restart or an OOM-killed process loses the entire cache with no warm-up,
and there is no server-side mechanism to keep a second copy anywhere. High availability is
purely a **client-side** concern — typically consistent hashing across a set of independent,
uncoordinated nodes, not a coordinated cluster. A node failing does not fail over; it simply
stops serving the keys hashed to it, and (depending on the hashing scheme) either those keys
alone or the entire keyspace remaps. This is worth stating plainly during discovery — a
mental model imported from Redis's replication/cluster mode will be wrong here.

### Client-side sharding means the hashing scheme decides the blast radius of a topology change

Scaling out or in changes which node owns which key. With **consistent hashing**, only the
keys whose ownership actually moves are affected. With a naive **modulo-on-node-count**
scheme, changing the node count remaps nearly the entire keyspace at once — a checkable,
common cause of a cache stampede immediately following a routine scaling event, worth
tracing directly to the client library's hashing algorithm rather than treated as a mystery
traffic spike.

### Slab allocation trades some memory for freedom from fragmentation — until the value-size distribution shifts

Memory is divided into slabs, each split into fixed-size chunks that grow by a configured
growth factor (documented default 1.25×) across a fixed set of size classes; a value is
stored in the smallest chunk class that fits it, and the gap between value size and chunk
size is wasted (internal fragmentation) by design — this is the trade Memcached makes to
avoid a general-purpose allocator's own fragmentation. The consequence worth checking for is
**slab calcification**: once a slab has been assigned to a size class, older versions do not
return it to a general pool, so a workload whose value-size distribution shifts over time
(smaller values growing larger, or vice versa) can end up starved for chunks in the classes
it now needs, while chunks sit underused in classes it no longer does — degrading effective
cache capacity with no memory leak and no configuration change to point to. Newer versions'
slab reassignment/automove settings mitigate this at the cost of the rebalancing work itself;
confirm whether it is enabled (§6) before assuming calcification is impossible.

### Eviction is LRU, but segmented and approximate, not query-language-selective

Memcached's eviction is a segmented LRU (hot/warm/cold segments) scoped **per slab class**,
not a single global order — an item competes for eviction only against other items in its
own size class, so eviction pressure in one class does not protect or evict items in another.
There is also no way to query, filter, or selectively evict by anything other than key or
expiry; the general eviction-policy questions in the category file apply per slab class here,
not to the cache as a whole.

### There are no data structures, no scripting, and exactly one atomicity primitive

Memcached's command surface is deliberately minimal: `get`/`set`/`delete`, `incr`/`decr` on
integer values, `append`/`prepend`, and `cas` (compare-and-swap, using an opaque per-item
token returned with each read). There are no lists, sets, hashes, or sorted sets, and no
server-side scripting or transactions. The category file's general "storing a structure as a
blob forces read-modify-write" finding (§2) applies here with no alternative at all — where
Redis offers a structured type as an escape hatch, Memcached does not, so a read-modify-write
race on a Memcached-stored blob has exactly one fix available: `cas`, not a switch to a
richer command. Code performing read-modify-write on shared Memcached keys without using
`cas` is a direct, checkable finding.

### Multi-threading changes which single-command-blocks-everyone claim actually applies

The category file explicitly defers the "does one expensive command block every client"
question to the engine's own reference (§5). Memcached is the concrete counter-example to
Redis's single-threaded model: since the 1.4 line, it runs a configurable number of worker
threads (`-t`, commonly defaulting to 4), each handling a subset of connections, with the
item hash table protected by its own locking rather than a single serialized command queue.
A single large or slow operation therefore does not stall every client the way it would on a
single-threaded store — but worker-thread count relative to available CPU cores, and lock
contention on the shared hash table under very high concurrency, are Memcached's own
scaling questions in their place, and worth checking (`-t` versus core count) rather than
assuming either "it's single-threaded, one slow op blocks everyone" or "it's multi-threaded,
so it scales without limit."

### The item size limit is small, fixed, and fails the write outright

The default maximum item size is 1 MiB (`-I` to raise it), far smaller than Redis's
practical per-value ceiling. A value exceeding the configured limit fails to store —
visibly, as a client-side error — rather than degrading; an application storing values that
can grow past this limit (a serialized object whose size depends on user data, for example)
needs to either chunk the value itself or handle the failure explicitly. This is a hard
architectural ceiling to check for directly, not a tunable performance trade like most of
the rest of this file.

---

## 3. Diagnostics

| Command | What it shows | Production safety |
|:--|:--|:--|
| `stats` | Global counters: hit/miss rate, eviction count, current connections, bytes used | **safe-on-production** |
| `stats slabs` | Per-slab-class chunk size, utilization, and eviction count — the direct evidence for slab calcification | **safe-on-production** |
| `stats items` | Per-slab-class item count and age distribution | **safe-on-production** |
| `stats settings` | Effective configuration: memory limit, item size limit, thread count, growth factor | **safe-on-production** |
| `stats reset` | Resets the counters above to zero | **not-safe-on-production** — discards the history the other `stats` commands report, breaking any dashboard or alert reading them |

There is no query language and no plan to inspect, so "why is this slow" almost always
resolves through `stats`/`stats slabs` (eviction rate, hit rate, per-class utilization) and
ordinary network/connection-count investigation, not through per-operation profiling.

---

## 4. Common failure modes and their symptoms

| Symptom | Memcached-specific cause to check first |
|:--|:--|
| Hit rate drops with no traffic or data-volume change | Slab calcification after a value-size distribution shift — check `stats slabs` utilization per class |
| Cache stampede immediately after a scaling event | Modulo-based (not consistent) client-side hashing remapped most or all of the keyspace |
| A restart or crash loses the entire cache with no partial recovery | Expected — Memcached has no persistence at all, unlike Redis's optional RDB/AOF |
| Read-modify-write race corrupts a counter or blob value | Code not using `cas`, Memcached's only concurrency primitive for this |
| Write fails for a specific value | Value exceeds the configured item size limit (`-I`, default 1 MiB) |
| One node's failure silently drops a portion of the keyspace with no failover | Expected — no server-side replication or clustering exists |
| High CPU with many concurrent clients despite low per-operation cost | Worker thread count (`-t`) mismatched to available cores, or hash-table lock contention |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| `-m` (memory limit) | Cache capacity versus host memory available for everything else |
| `-I` (max item size) | Ability to store larger values versus the fixed ceiling every value must respect |
| Growth factor (chunk-size growth between slab classes) | Fewer, coarser size classes (more internal fragmentation) versus more classes (more slab-management overhead) |
| Slab reassignment / automove (where available) | Resilience to value-size-distribution shift versus the rebalancing work itself |
| `-t` (worker thread count) | Concurrency across connections versus hash-table lock contention at high thread counts |
| Client-side hashing scheme (consistent vs modulo) | Blast radius of a node-count change: partial remap versus near-total remap |
| Extstore (newer versions; SSD-backed overflow) | Larger effective cache capacity versus the latency of an SSD-backed item versus a pure-memory one |

---

## 6. Version differences worth knowing

Record the Memcached version, and whether the deployment is self-managed or a managed
service (ElastiCache for Memcached, Memorystore for Memcached), during discovery.

- **Multi-threading** (the `-t` worker-thread model in §2) has been the default since the
  1.4 line; a very old deployment may still be effectively single-threaded — confirm rather
  than assume the multi-threaded scaling questions above apply.
- **Slab reassignment / automove**, which mitigates calcification, was added after the
  original fixed-slab design and is not necessarily enabled by default on every version —
  confirm it is actually on before ruling calcification out.
- **Extstore**, an SSD-backed overflow for evicted items, is a newer, opt-in feature that
  changes the eviction-versus-capacity trade described above; confirm whether it is
  configured before assuming all data lives in RAM.
- **Managed-service versions** (ElastiCache, Memorystore) may lag or diverge from the
  open-source release cadence and add their own operational surface (snapshots for backup
  purposes only — not used for cache warm-up — auto-discovery of node lists); confirm
  managed-service-specific behavior against that provider's documentation rather than
  upstream Memcached's.

**Confirm version- and deployment-specific claims against the current documentation for
the deployed version or service.**

---

## 7. What this file does NOT cover

- Key design, value size and shape, round-trip-dominated cost, the general memory/eviction
  questions, and cache-invalidation reasoning as concepts — see `databases/key-value.md`
  and `distributed/caching.md`.
- Redis-specific behavior — see `technology/redis.md`; this file names Redis only for
  contrast, not as a baseline.
- Managed-service-specific autoscaling, pricing, snapshot/backup mechanics, and node
  auto-discovery protocols (ElastiCache, Memorystore).
- Authentication and encryption in transit (SASL support and TLS exist in recent versions
  but are not detailed here).
- The UDP protocol variant, which is deprecated/removed in current versions and not worth
  building review guidance around.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
