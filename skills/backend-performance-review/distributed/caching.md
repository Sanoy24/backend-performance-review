# Caching

Caching is the most over-recommended optimization in backend engineering. It is also
frequently correct. The difference is analysis.

This file exists to make cache recommendations *harder to make carelessly*. A cache trades
correctness for speed and adds a new system to operate. That trade must be argued, not
assumed.

---

## 1. Before recommending a cache

Caching is step 5 of 6 in the intervention order (`principles/work-and-algorithms.md` §7).
Confirm the earlier steps do not apply first:

1. Can the work be **removed**? A cache in front of an N+1 preserves the N+1 and adds
   invalidation. Batching removes it.
2. Can the work be **bounded**? An unbounded query cached is an unbounded query with
   occasional relief and a large memory cost.
3. Can the work be **moved** — precomputed at write time, or done in the background?
4. Can the work be made **cheaper** — a better index or query shape?

Only then: cache.

A cache recommendation that skips this sequence is the defining example of cargo-cult
optimization, and it is the single most common failure of automated performance review.

---

## 2. The five questions a cache recommendation must answer

Any cache recommendation missing one of these is incomplete and should not ship.

### Hit rate — why will this be requested again?

The benefit is proportional to the hit rate, and the hit rate is a property of the access
pattern, not of the cache.

- Data requested repeatedly within its lifetime caches well.
- Data requested once per user per session does not.
- A key space far larger than the cache holds thrashes: eviction happens before reuse, and
  the cache adds latency and cost while delivering nothing.

If you cannot argue why the key will be requested again before it expires, do not recommend
the cache. Where the argument is plausible but unproven, say so and recommend measuring hit
rate before committing.

### Invalidation — how does it learn the data changed?

| Strategy | Cost |
|:--|:--|
| TTL only | Staleness up to the TTL; simplest and most robust |
| Explicit invalidation on write | Every write path must know every affected key — easy to miss one |
| Write-through | Consistency at the cost of write latency and coupling |
| Versioned keys | No invalidation needed; old entries linger until evicted |
| Event-driven | Decoupled, but eventually consistent and dependent on delivery |

"TTL only" is a legitimate answer, but it requires stating **how stale is acceptable, and to
whom**. That is a product question, and the report should say so rather than deciding it
silently.

The failure mode to look for in existing caches: a write path that updates the datastore and
forgets one of the keys derived from it. Enumerate the derived keys and check each write
path against them.

### Granularity — what is the key?

- Too coarse: any change invalidates a large entry that was mostly still valid.
- Too fine: many entries, lower hit rate per entry, more round trips to assemble a response.
- Keyed on the wrong dimension: a per-user key for data that is identical across users
  multiplies storage and destroys hit rate.

Including personalization in a key that could otherwise be shared is a common and expensive
mistake. Splitting a response into shared and personalized parts is often the real fix.

### Stampede — what happens when it expires?

When a popular key expires, every concurrent request misses simultaneously and all of them
recompute. The recomputation load can exceed the uncached steady-state load, so a cache can
make the worst moment worse than having no cache at all.

Mitigations, each with a cost: a lock or single-flight so one request recomputes while others
wait; serving stale while refreshing in the background; jittered TTLs so keys do not expire
together; proactive refresh before expiry.

**Uniform TTLs set at the same moment expire at the same moment.** If a cache is populated by
a deploy or a warmup job, all entries share an expiry — a synchronized stampede. This is
worth checking specifically in existing caches.

### Consistency — who can tolerate stale data?

Some readers can, some cannot. Read-after-write is the sharp case: a user who just changed
something and immediately sees the old value experiences a bug, regardless of what the cache
documentation says. Identify the paths where staleness is user-visible and treat them
separately.

---

## 3. Cache layers, and where problems come from

| Layer | Scope | Characteristic problem |
|:--|:--|:--|
| In-process memory | One instance | Inconsistent across instances; grows unbounded without a size cap; lost on restart |
| Shared remote cache | All instances | Network round trip per access; a shared dependency and a shared failure mode |
| Client / HTTP cache | Per client | Invalidation is impossible once served; validator design matters |
| CDN / edge | Global | Cheapest hits, hardest invalidation |
| Datastore internal cache | The datastore | Not under application control; competes for the same memory as data |

An in-process cache with no size limit is a memory leak with a friendly name. Check for a
bound and an eviction policy on every in-process cache — this is a frequent, concrete
finding.

---

## 4. Reviewing a cache that already exists

Existing caches deserve as much scrutiny as proposed ones, and are often where the real
findings are:

- **Is the hit rate measured?** An unmeasured cache may be delivering nothing while costing a
  round trip on every request. This is common and invisible without instrumentation.
- **Is the TTL justified**, or was it typed once and never revisited?
- **Does every write path invalidate every derived key?** Enumerate and check.
- **Is there a size bound and an eviction policy?**
- **What happens when the cache is unavailable?** A cache that is a hard dependency has
  converted an optimization into a new outage source. It should degrade to the slow path,
  and that path must still be able to carry the load — check whether it can.
- **Is anything cached that should not be**: per-user data under a shared key, data whose
  staleness is user-visible, or data cheap enough that the round trip costs more than the
  computation?

That last case is real: caching something the datastore answers from memory in microseconds,
behind a network hop, is a net loss.

---

## 5. Negative caching and cache-as-truth

- **Negative caching** — caching "not found" — prevents repeated lookups for missing keys and
  is a genuine defense against enumeration-shaped load. It needs a shorter TTL than positive
  entries, or newly created items appear missing.
- **A cache treated as a system of record** is a data-loss risk. Eviction and restart both
  discard entries. If application logic assumes an entry is present, the cache is not a cache.
  Report it as a correctness finding.

---

## 6. What to look for in a review

- Whether removal, bounding, or moving the work was considered before caching.
- For each existing cache: hit rate measurement, TTL justification, invalidation completeness,
  size bound, eviction policy, and behavior when unavailable.
- Stampede exposure on popular keys and synchronized TTLs.
- Key granularity, and personalization defeating shared caching.
- In-process caches without size limits.
- Paths where staleness is user-visible.
- Whether the uncached path can still carry full load.

## 7. What not to conclude

- Do not recommend a cache without answering all five questions.
- Do not recommend a cache to hide repeated work that could be removed.
- Do not claim a hit rate or a speedup you did not measure.
- Do not treat an existing cache as evidence that caching is working — measure it.
- Do not recommend a distributed cache where a small bounded in-process cache would do; the
  network hop is frequently the entire cost you were trying to avoid.
