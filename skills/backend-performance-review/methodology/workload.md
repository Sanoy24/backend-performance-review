# Workload

Performance is meaningless without workload. The same code is excellent at 10 requests
per day and catastrophic at 10,000 per second. A review that skips this phase produces
findings that are technically true and practically unrankable.

This phase has three parts, in order: mine the repository, ask the user, then record what
remains unknown.

---

## 1. Mine the repository first

Do not ask the user for what the repo already tells you. Most repositories contain more
workload evidence than people expect.

### Load and capacity signals

| Signal | What it tells you | Where |
|:--|:--|:--|
| Load-test scripts | Someone's belief about expected rate | k6, Locust, Gatling, JMeter, Artillery, `wrk` |
| Rate limits / quotas | The ceiling the system is designed to accept | Middleware, gateway config, API definitions |
| Autoscaling config | Expected range of instances and the metric that drives it | HPA, ASG, Lambda concurrency |
| Replica counts | Baseline capacity | Deployment manifests, compose files |
| Worker/thread counts | Concurrency per instance | Entrypoint flags: `--workers`, `-c`, `GOMAXPROCS`, thread pool config |
| Connection pool size | An implicit claim about concurrent in-flight queries | ORM/driver config |
| Timeouts | An implicit claim about acceptable latency | Client config, server config, ingress |

**Pool size and worker count together are one of the most informative pairs in a
repository.** A pool of 5 behind 32 workers says the author expected most requests not to
touch the database concurrently — a claim worth testing.

### Data volume and growth signals

| Signal | What it tells you | Where |
|:--|:--|:--|
| Migration history | Which tables exist, which are written on every user action | Migration directory |
| Retention/archival jobs | Whether a table is bounded | Cron jobs, scheduled tasks, TTL indexes |
| TTL / expiry config | Bounded collections | Cache config, TTL indexes, S3 lifecycle |
| Pagination defaults and maxima | The largest result set the design anticipates | Handlers, serializers, query builders |
| Seed and fixture data | Rough shape, occasionally rough scale | `seeds/`, `fixtures/`, factories |
| Batch sizes | Expected item counts per operation | Bulk endpoints, job definitions |

A table written on every user action, with no retention job and no archival, grows without
bound. That is a documented fact about the repository, not an assumption — cite it.

### Latency and reliability expectations

- SLO/SLA documents, error budgets, runbooks.
- Alert thresholds — an alert on p99 > 500 ms is a stated latency target.
- Dashboards-as-code — the panels someone built reveal what they worry about.
- Circuit breaker and retry configuration — implicit statements about upstream latency.

### Read/write shape

Count endpoint and query kinds. A service with 30 read endpoints and 3 write endpoints has
a read-heavy shape. This is coarse but it is evidence, and it is often enough to rank
findings.

### Scheduled and background workload

Cron expressions, scheduler definitions, queue consumer counts, prefetch settings, and
batch windows. A job that runs every minute and scans a growing table is a scalability
risk with a visible clock on it.

---

## 2. The workload interview

Ask **at most seven questions, once, in a single message.** Do not interrogate
iteratively; do not ask what you already mined. Frame them so approximate answers are
useful, because approximate answers are what you will get.

1. Roughly what request rate do the busiest endpoints see at peak — order of magnitude is
   enough (per second, per minute, per day)?
2. What is the largest table or collection today, and roughly how fast is it growing?
3. Approximately what is the read/write ratio on the primary datastore?
4. Is there a latency target or SLO, and is it currently being met?
5. How many application instances or workers run in production, and with what CPU/memory
   limits?
6. Which operations are user-blocking, and which are background?
7. Is there a specific performance problem that prompted this review — a slow endpoint, an
   incident, a cost increase, a scaling deadline?

Question 7 is the highest-value one. If the user has a specific complaint, the review
should be organized around confirming or refuting it, and everything else becomes
secondary.

### If the user does not answer

**Proceed.** Do not block, do not re-ask, do not stall the review. Instead:

- Record each unanswered item in the unknowns list.
- Write the assumption you are using into every affected finding's `Conditions` field.
- Cap workload-dependent findings at `Medium` confidence.
- In the executive summary, state plainly that the ranking would change if workload data
  became available, and name which findings would move.

An honest review under stated assumptions is far more useful than a delayed one.

---

## 3. Build the workload model

Write down what you now believe, separating the three kinds of statement. The separation
is the point — it is what makes the review auditable.

```
KNOWN (from repo or user, cited)
  - Pool size 10, 4 workers per instance, 3 replicas (k8s/deployment.yaml, gunicorn.conf)
  - Read-heavy surface: 27 GET routes, 4 mutating routes
  - `events` table has no retention job (migrations/, cron/)

ASSUMED (stated, unverified — drives confidence caps)
  - `events` is the largest table and grows monotonically
  - Peak traffic is concentrated, not uniform across the day

UNKNOWN (would change conclusions)
  - Actual request rate and its distribution
  - Current p95/p99 latency
  - Row counts for `events` and `orders`
  - Whether the SLO is currently met
```

Every finding must be traceable to this model. If a finding does not depend on any line of
it, ask whether it is a real performance finding or a style preference.

---

## 4. Current bottleneck versus future risk

Keep these separate throughout the review. Conflating them is the most common way
performance advice becomes noise.

| | Current bottleneck | Future scalability risk |
|:--|:--|:--|
| Evidence | Something is measurably slow or saturated now | Growth curve implies a future limit |
| Severity | Scored on today's conditions | Scored on today's conditions, tagged `scalability-risk` |
| Recommendation | Fix | Usually: measure, set an alert, and define the threshold that triggers work |
| Failure mode of getting it wrong | Missed outage | Wasted effort on a problem that never arrives |

A future risk does **not** get inflated severity because it might get worse. It gets
today's severity plus the `scalability-risk` tag, and the report says at what scale it
becomes urgent. Where possible, name the trigger concretely: "at roughly 10× current row
count this scan moves off the index-only path."

---

## 5. The 10× question

For each critical-path finding, ask: **what happens at 10× traffic and 10× data?**

- Costs that stay flat are non-issues regardless of size.
- Costs that grow linearly are budget questions.
- Costs that grow superlinearly are cliffs — and cliffs are what turn into incidents.

The answer to this question is usually what determines whether a finding is `Medium` or
`Critical`. Compute it explicitly rather than intuiting it, and show the reasoning in
`Impact`.

---

## 6. Anti-patterns

- Declaring workload "unknown" without mining the repository first.
- Asking the user questions the repo already answers.
- Asking more than seven questions, or asking them one at a time.
- Blocking the review on an unanswered interview.
- Assuming the system is under heavy load because it exists.
- Assuming it is under light load because the code looks small.
- Letting an assumption sit implicitly inside `Impact` instead of explicitly in
  `Conditions`.
