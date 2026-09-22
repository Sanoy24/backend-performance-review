# Workload

Performance is meaningless without workload. The same code is excellent at 10 requests
per day and catastrophic at 10,000 per second. A review that skips this phase produces
findings that are technically true and practically unrankable.

This phase has four parts, in order: mine the repository, resolve question candidates from
that evidence, select the smallest useful interview, then record what remains unknown.

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

### Resolve repository-answerable questions before asking

Turn the evidence above into answers, not merely observations. For each possible workload
question, search these sources before it can enter the interview:

1. deployment manifests and infrastructure definitions;
2. application, worker, client, pool, timeout, quota, and scheduler configuration;
3. load tests, benchmarks, dashboards-as-code, alerts, SLOs, and runbooks;
4. route, query, pagination, batch, retention, and background-job code.

Record an answer in `workload.inputs` with `asked: false`, its `repository` or `derived`
source, and the exact evidence checked. A derived answer shows its inputs. A configured
ceiling is evidence of a ceiling, not evidence that production reaches it; a load-test rate
is the scenario that test exercises, not automatically production traffic. If the repository
only narrows a range, retain that bound and ask only for the unresolved part.

Do not ask a human to transcribe a replica count, worker count, pool limit, timeout, batch
size, schedule, pagination maximum, retention rule, or declared SLO that is already visible.
Repository inference is complete only when every proposed question has an evidence-search
result: answered, partially answered, or absent.

---

## 2. Select the workload interview

Ask **zero to seven questions, once, in a single message.** Seven is a hard maximum, not a
target or default quota. Do not interrogate iteratively; do not ask what you already mined.
Frame selected questions so approximate answers are useful, because approximate answers are
what you will get.

### Build a private question-candidate ledger

Start from repository gaps and plausible mechanisms, not from a fixed questionnaire:

| Question candidate | Evidence checked | Possible answer range | Decision changed | Expected decision value | Disposition |
|:--|:--|:--|:--|:--|:--|
| `<question>` | `<files/artifacts searched>` | `<meaningfully different outcomes>` | `severity | confidence | recommendation` - `<how>` | `highest | high | medium | low` | `ask | inferred | discard | merge into Q-n` |

This ledger is private working state. It prevents duplicate or already-answered questions
without turning the report into an interview transcript.

### Apply the decision-change gate

A question may be asked only when at least two plausible answers would change one or more of:

- **Severity** - a factor such as frequency, growth, critical-path position, or blast radius;
- **Confidence** - whether a material assumption becomes evidenced or falsified;
- **Recommendation** - whether to change code/configuration, measure first, choose a different
  trade-off, or make no change.

Write the concrete branch before asking: "If A, PERF-001 remains P1; if B, its bounded growth
makes it P3," or "If the queue already drains within its SLO, recommend an alert rather than
more consumers." Discard questions that would add context but leave every decision unchanged.
Merge questions when one answer resolves the same uncertainty for several mechanisms.

### Rank by expected decision value

Order the survivors by the size and reach of the plausible decision change, then by the cost
to answer. Use the ordinal values below; do not invent a numeric score or probability:

- `highest` - can reverse a ship/no-ship decision, move a likely finding across multiple
  priority levels, or choose between materially different recommendations;
- `high` - can change one likely finding's severity, confidence, or recommendation;
- `medium` - can refine a decision within one finding without changing the top action;
- `low` - decision-changing but narrow; ask only if capacity remains after higher-value items.

Break ties by preferring one cheap answer that affects several candidates, then one that
resolves a stated user concern. Ask the first seven survivors at most. If none survive, ask
no questions and continue with the repository-derived workload model.

### Candidate bank, not a mandatory questionnaire

The following are seeds for a review with no stronger repository-specific candidates. Ask
only the ones that survive inference, the decision-change gate, and ranking:

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

The final seed is often the highest-value one. If the user has a specific complaint, the review
should be organized around confirming or refuting it, and everything else becomes
secondary.

Swap in the questions the detected risk calls for:

| If the dominant risk is | Ask instead about |
|:--|:--|
| Datastore access | Typical and maximum rows per request; row counts on the largest tables; current query latency if known |
| A queue or broker | Arrival rate, consumer count, current lag, and what happens to a message that fails |
| A cache | Hit rate, TTL, key cardinality, and behavior on a cold start |
| Connection pools | Pool size against the datastore's connection limit, and instance count |
| Background jobs | Schedule, runtime, and whether runs can overlap |
| A specific reported incident | What changed immediately before it, and what the symptom actually was |

Keep a generic candidate only when it survived the same gate. Missing general context does
not by itself justify asking for it.

### If the user does not answer

**Proceed.** Do not block, do not re-ask, do not stall the review. Instead:

- Record each unanswered item in the unknowns list.
- Record asked questions in `workload.inputs` with `asked: true`, `source: unanswered` until
  answered, their decision dimensions, expected decision value, and evidence already checked.
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

DERIVED (computed from the above — inputs shown)
  - Peak concurrent DB connections needed = 4 workers × 3 replicas = 12,
    against a pool of 10 per instance and the server's max_connections
  - Query count for the orders listing grows linearly with rows returned,
    because the lookup is issued once per row of the outer result

MEASURED (from a real runtime artifact, cited)
  - (none — no benchmark, trace, or query plan was supplied)
```

Five buckets, because they carry different weight and are easy to blur together. `DERIVED`
is where arithmetic goes, and it must show its inputs — a derived number with no visible
derivation is indistinguishable from an invented one, which is the failure Hard Rule 1
exists to prevent. `MEASURED` is usually empty for a static review, and saying so plainly is
more useful than leaving the distinction implicit: it is the line that tells a reader why
almost nothing here is `Confirmed`.

Every finding must be traceable to this model. If a finding does not depend on any line of
it, ask whether it is a real performance finding or a style preference.

### Carry the ledger into the report

The ledger is not scratch work. It is reported (report template §4), and it is what lets a
reader disagree with one specific step instead of with the conclusion as a whole. A reader
who thinks the `events` table is not the largest can say so, and you both know exactly which
findings that changes.

### Name the questions that would change the ranking

Close the review with the unanswered questions that would actually move something, most
valuable first, each with what it would change:

```
1. Production request rate for the orders listing route.
   → Would move PERF-001 between P1 and P3.
2. Maximum rows one request can return.
   → A bound in the low tens would cut PERF-001's severity.
```

This is more useful than "more information is needed", and it converts the interview the
user did not answer into a specific, cheap next step.

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
- Asking for context that cannot change severity, confidence, or recommendation.
- Asking more than seven questions, or asking them one at a time.
- Filling the seven-question allowance after all high-value uncertainties are covered.
- Blocking the review on an unanswered interview.
- Assuming the system is under heavy load because it exists.
- Assuming it is under light load because the code looks small.
- Letting an assumption sit implicitly inside `Impact` instead of explicitly in
  `Conditions`.
