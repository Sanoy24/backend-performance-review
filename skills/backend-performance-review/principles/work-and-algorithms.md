# Work and algorithms

The cheapest work is work not done. This file covers the largest category of backend
performance findings: work that is repeated, unbounded, or done in the wrong place.

Note that classical algorithmic complexity is rarely the dominant term in a backend
service. The dominant term is almost always **how many remote operations happen and how
much data they move**. Analyze the work, not just the code.

---

## 1. Count the work, not the lines

For any path, count:

- Remote operations (queries, cache calls, HTTP requests, message publishes).
- Items processed.
- Bytes moved and bytes parsed.
- Allocations on the hot path.

Then ask how each count scales with data and traffic. A tidy function issuing one query per
loop iteration is worse than an ugly one issuing a single batched query, and no amount of
code-quality judgment will reveal that. Counting will.

---

## 2. Repeated work

The most productive category in static review, because repetition is visible in code and
expensive at runtime.

**N+1 operations.** One query to fetch a collection, then one query per element. The
canonical form. Variants that are easy to miss:

- ORM lazy loading, where the second query is triggered by attribute access and is
  invisible at the call site. **This is the hardest and most valuable variant to catch:**
  the loop looks like pure in-memory iteration.
- One cache lookup per item where a multi-get exists.
- One HTTP call per item in a list.
- A GraphQL field resolver executing per parent object.
- A permission or ownership check re-issued per item.
- A nested serializer that fetches related objects during rendering.

**Duplicate work within a request.** The same configuration re-read, the same user re-fetched
by two layers, the same token re-validated, the same payload deserialized twice, the same
derived value recomputed in several places.

**Work repeated across requests** that is genuinely invariant — parsing a config file per
request, compiling a regex per call, rebuilding a client per call, re-reading a secret.

The right fix is usually to remove the repetition (batch, hoist, pass the value down), not
to cache it. Caching repetition preserves the repetition and adds invalidation.

---

## 3. Unbounded work

Unbounded work is what turns a slow endpoint into an outage, because the cost is set by data
you do not control.

- Queries without a limit; endpoints without a maximum page size.
- Loading a whole collection to count, filter, or aggregate in application code.
- Unbounded fan-out — one operation per element of a caller-supplied list.
- Unbounded concurrency — a task per item with no semaphore.
- Unbounded buffering — accumulating an entire response, file, or stream in memory.
- Recursion or graph traversal with no depth or breadth cap.
- Retry loops with no attempt cap.

**A default page size is not a bound.** If the caller can pass `limit=100000`, the endpoint
is unbounded. Look for the enforced maximum, not the default.

Bounding is usually a small change with a large effect on worst-case behavior, which makes
these findings frequent `quick-win` candidates.

---

## 4. Work in the wrong place

Moving work is often cheaper than optimizing it.

| Doing it here | Usually belongs here | Why |
|:--|:--|:--|
| Filtering in application code after fetching everything | In the datastore query | Avoids transferring and parsing rows that are discarded |
| Sorting a full result set in memory | In the datastore, with an index that supports the order | Avoids the transfer and the sort |
| Aggregating row by row | In the datastore | One pass, no transfer |
| Joining two collections in a loop | In the datastore, or with a single keyed lookup map | Turns O(n·m) into O(n+m) |
| Computing a derived value per read | Once at write, or in a materialized form | Read paths are usually hotter |
| Doing it during the request | In a background job | The user does not wait |
| Doing it per request | At startup, or once per process | Amortized to nothing |

The reverse also occurs: work pushed into the datastore that the datastore is bad at —
complex string manipulation, business rules in triggers, per-row functions defeating an
index. The datastore is usually the scarcer, harder-to-scale resource; do not move work
into it casually.

---

## 5. Data structures and access shape

- Repeated linear scans of a list that should be a hash lookup. Common in join-in-code paths
  and effectively invisible until data grows.
- Rebuilding a lookup structure inside a loop instead of once before it.
- Copying large structures where a reference or slice would do.
- String concatenation in a loop where a builder or join exists.
- Deep copies of large object graphs.
- Recursive algorithms on data with no depth bound.

These matter most in the per-item work amplified by list sizes. In an I/O-bound path they
are usually noise — and reporting them there is a false positive.

---

## 6. Growth is the question that decides severity

For each finding, answer: **as data grows 10×, does the cost grow 1×, 10×, or 100×?**

| Growth | Typical source | Review posture |
|:--|:--|:--|
| O(1) | Point lookup on a key, fixed work, bounded cache | Fine at any scale |
| O(log n) | Index traversal | Fine at realistic scales |
| O(n) | Scan, unbounded result set, per-row application work | Budget question — depends on n |
| O(n·m) | N+1, nested loop over two collections, per-item fan-out | Usually the finding |
| O(n²)+ | Nested scans, repeated re-sorting, in-memory dedupe of a scan | Cliff — treat as urgent on a hot path |

Superlinear growth on the critical path is the strongest single indicator of `Critical`
severity, because the system has a cliff rather than a slope. Compute the growth explicitly
and put it in `Impact`.

---

## 7. The cheapest intervention first

In order, prefer:

1. **Remove the work** — fix the N+1, drop the duplicate call, stop selecting unused fields.
2. **Bound the work** — pagination, limits, timeouts, batch caps, semaphores.
3. **Move the work** — into the datastore, into a background job, to startup, to write time.
4. **Make the work cheaper** — better index, better query shape, better data structure.
5. **Cache the work** — only with hit-rate reasoning, an invalidation strategy, and a stated
   acceptable staleness.
6. **Add capacity** — last, and only against saturation evidence.

The ordering is not stylistic. Each step down adds either complexity, a new failure mode, or
ongoing cost. Recommending step 5 for a problem solvable at step 1 is the defining shape of
cargo-cult optimization.

---

## 8. What to look for in a review

- Count remote operations per path, and how that count scales with result size.
- Look for lazy loading, which hides N+1 behind ordinary attribute access.
- Find every unbounded input: result sets, fan-out, concurrency, buffers, recursion.
- Check whether filtering, sorting, and aggregation happen where the data lives.
- Check for per-request work that is actually invariant.
- Compute growth for every critical-path finding.

## 9. What not to conclude

- Do not report micro-optimizations on I/O-bound paths.
- Do not recommend caching before removing or bounding the work.
- Do not treat a high complexity class as a problem when n is small and bounded — say so
  and move on.
- Do not assume the datastore is the right place for everything; it is usually the hardest
  resource to scale.
