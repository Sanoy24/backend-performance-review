# Multi-tenancy

A multi-tenant system shares infrastructure — a database, a cache, a connection pool, a
process, a node — across customers, workspaces, or accounts that do not trust or coordinate
with each other. Every principle elsewhere in this skill still applies; multi-tenancy adds
one new question on top of all of them: **whose workload is this, and can one tenant's
workload degrade another's?** A finding that is merely "slow" in a single-tenant system is
"slow for every other tenant sharing this resource" here, which is a different severity.

---

## 1. Identify the isolation model before analyzing anything else

The isolation model determines which of the sections below apply, and how severely.

| Model | What is shared | Isolation |
|:--|:--|:--|
| **Silo** | Nothing — separate database, separate deployment per tenant | Strong; noisy-neighbor findings in this file mostly do not apply |
| **Pool (shared database, tenant column)** | Database, connection pool, cache | Weak; almost everything in this file applies |
| **Bridge (shared database, tenant schema or database-per-tenant on shared hardware)** | Compute and storage hardware, not the schema | Partial; contention is at the infrastructure layer, not the query layer |
| **Shared everything, tenant discriminated by a foreign key** | All of it | Weakest; every shared resource is a noisy-neighbor vector |

Look for the discriminator: a `tenant_id`/`workspace_id`/`account_id`/`org_id` column or
partition key, subdomain- or path-based routing to a shared handler, or a shared connection
pool serving requests tagged by tenant in middleware. Its presence — and where it is enforced
— tells you the isolation model faster than any documentation will.

**A system with no discriminator anywhere is not multi-tenant; do not force this file's
findings onto it.** This is the layer-gate: if there is one tenant, or the isolation model is
silo, most of what follows is not applicable and should not appear in the report.

---

## 2. Noisy neighbors — one tenant's workload consuming shared capacity

This is the central failure mode. A shared resource — connection pool, cache, thread pool,
disk I/O, a single database instance's CPU — has finite capacity. One tenant's traffic spike,
inefficient query, or large data volume consumes a disproportionate share, degrading every
other tenant on the same resource, usually with no visible relationship between cause and
effect from the affected tenants' point of view.

What makes a noisy-neighbor finding distinct from an ordinary resource-contention finding
(see `principles/concurrency-and-contention.md`) is the blast radius: the finding is not
"this request is slow," it is "this tenant's request makes every other tenant's request slow,"
and the fix has to preserve the noisy tenant's own functionality, not just move the bottleneck.

Common vectors:

- **Connection pool exhaustion by one tenant's burst.** A shared pool with no per-tenant cap
  lets one tenant's traffic spike consume every connection, starving everyone else. See
  `application/connection-pools.md` for the pool mechanics; the multi-tenant question on top
  is whether the pool has *any* per-tenant fairness at all.
- **An unindexed or unbounded query issued by one tenant** saturates shared database CPU or
  I/O, degrading query latency for every tenant on that instance — not just the one whose
  query was slow.
- **A cache shared across tenants with no per-tenant sizing.** One tenant with a much larger
  working set evicts every other tenant's entries, dropping their hit rate. See
  `distributed/caching.md` for eviction mechanics generally.
- **Background jobs sharing a worker pool with request-serving code.** A large tenant's batch
  export or reindex job competes for the same workers as every other tenant's live traffic.
- **A single hot tenant on a partitioned or sharded resource** — this is the multi-tenant
  instance of a hot partition or hot key (see `databases/*.md`'s per-category coverage);
  worth naming as this file's failure mode when the partition key *is* the tenant.

---

## 3. Fairness and admission control

The presence or absence of any per-tenant fairness mechanism is the highest-value single
check in this file — it determines whether §2's failure modes are bounded or unbounded.

| Mechanism | What it bounds |
|:--|:--|
| Per-tenant rate limiting | Request rate into the system |
| Per-tenant connection or concurrency caps | Share of a pooled resource one tenant can hold |
| Per-tenant quotas (storage, job count, queue depth) | Growth of a tenant's footprint over time |
| Weighted fair-share or priority scheduling | Relative CPU/worker time across competing tenants |
| Per-tenant circuit breaking | Whether one tenant's failing dependency calls affect others |

**A system with none of these has no isolation regardless of its data model.** Report this as
the root cause when you find several §2 symptoms rather than writing each one up as an
independent finding — they usually share this one cause.

Rate limiting and quotas are the one place in this skill where "consider adding X" is not
automatically cargo-cult (`SKILL.md` rule 5) — but the finding still needs `Conditions`: state
which shared resource is actually at risk of exhaustion and under what traffic shape, not
"multi-tenant systems should have rate limits" as a generic claim.

---

## 4. Data-model consequences of the isolation choice

The isolation model chosen in §1 has direct performance consequences distinct from the
noisy-neighbor question:

- **Pool model, unindexed tenant discriminator.** If `tenant_id` is not the leading column of
  every index that filters by it, every tenant-scoped query scans across other tenants' rows
  to filter them out. This is a correctness-adjacent performance bug distinct from a normal
  missing index: it wastes work proportional to *total* data volume across all tenants, not
  just this tenant's own data.
- **Tenant size skew.** A schema designed and indexed against a typical tenant's data volume
  degrades non-linearly for outlier tenants (the one enterprise customer with 1000x the median
  data). Query plans, index selectivity, and pagination assumptions that hold at the median
  can fail entirely at the tail. Ask, or check for, the actual distribution of tenant size
  rather than assuming uniformity.
- **Schema-per-tenant or database-per-tenant at scale.** Connection pool sizing multiplies by
  tenant count (see `application/connection-pools.md`); migrations must run once per
  tenant-schema, and a migration that is fast on one schema is not necessarily fast at
  N-hundred schemas run sequentially.
- **Cross-tenant aggregation or reporting features** (admin dashboards, usage analytics) are
  often the one query path that legitimately needs to scan across the tenant boundary — check
  whether it is isolated from the tenant-serving path (separate replica, separate schedule) or
  competes with live traffic on the same resource.

---

## 5. Cost attribution

Multi-tenant systems are usually the case where **cost per tenant**, not just cost per
request, is the metric that matters (`principles/resources.md` §7 covers cost as a resource
generally). Two distinct questions:

- **Can the system attribute resource consumption to a tenant at all?** Without per-tenant
  metering, a runaway tenant is invisible until the shared resource is already saturated.
- **Does pricing or capacity planning assume uniform tenant cost?** A cost model built on
  average tenant resource use under-provisions for — or fails to price — the outlier tenants
  identified in §4.

---

## 6. What to look for in a review

- The isolation model (§1): silo, pool, bridge, or shared-everything — and whether it matches
  what the code and schema actually implement, not what documentation claims.
- Every shared resource a request touches: connection pool, cache, thread/worker pool, disk,
  a single database instance — and whether any of them has a per-tenant bound.
- Whether `tenant_id`/equivalent is the leading column of every index used to filter by it.
- Any per-tenant rate limiting, quota, or fair-share mechanism, and what happens in its
  absence — trace at least one concrete "large tenant exhausts X, small tenant's requests
  queue behind it" path if the code allows it.
- Background or batch work sharing a resource with request-serving work, with no isolation
  between them.
- Whether tenant-size distribution is known or discoverable, and whether the workload
  interview (`methodology/workload.md`) should ask about it.
- Per-tenant cost or resource-consumption visibility.

## 7. What not to conclude

- Do not apply this file's findings to a system with one tenant, or a genuine silo model —
  layer-gate it out per `SKILL.md` rule 6.
- Do not recommend per-tenant rate limiting, quotas, or dedicated infrastructure without
  identifying which specific shared resource is at risk and under what tenant-traffic shape;
  an ungrounded "add tenant isolation" recommendation is cargo-cult on the same terms as an
  ungrounded "add caching" recommendation.
- Do not assume uniform tenant size without evidence; the single most common mistake in
  multi-tenant capacity planning is designing and testing against the median tenant and
  discovering the failure mode only at the outlier.
- Do not conflate a silo model's per-tenant *operational* cost (N deployments to manage) with
  a performance finding — that is a maintenance/cost trade-off, not a contention risk.
