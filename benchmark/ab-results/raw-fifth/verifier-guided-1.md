# Performance Review: github-signature-verifier

**Date:** 2026-10-09
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec backend-performance-review/2.0)
**Commit:** `1239b2c335773e00897999c90e50c2b16160e4f1`

---

## 1. Decision summary

### Overall assessment

This is a very small AWS Lambda function (Node.js, one handler, about 90 lines) that verifies GitHub webhook HMAC signatures. Static analysis found **no Critical or High-severity bottleneck**. The material performance issue is fixed work repeated on every invocation: each verification makes a remote SSM Parameter Store call with KMS decryption (PERF-001). That call has no deadline shorter than the 30 s function timeout (PERF-002). Callers are told to wait on it through a synchronous Lambda-to-Lambda invoke (PERF-003). Each of these is bounded alone. Together they mean a degraded or throttled SSM can hold two Lambda concurrency slots per webhook for up to 30 s. **Nobody answered the workload questions, and the repository is uninstrumented**, so this ranking depends on webhook rate. Review confidence is **Medium**: every line of code was read, but no runtime evidence exists. The deployed runtime (`nodejs4.3`) is end-of-life (MAINT-001), and a correctness defect means the documented caller cannot reach the deployed function at all (COR-001). Both need attention before any performance change ships.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P2)** | Hoist the SSM client to module scope and memoize the secret per execution environment with a bounded TTL | Removes one remote call plus one KMS decrypt from almost every warm invocation. Cheap change (`quick-win`) |
| 2 | **PERF-002 (P2)** | Set explicit per-attempt timeouts and retry caps on the SSM client and the caller's Lambda client, sized under the caller's own budget | Bounds the worst case when SSM or Lambda is degraded, which is the case where the chain fails |
| 3 | **PERF-005 (P3)**, sequenced early | Enable tracing and alarm on `Duration`/`Throttles` before tuning further | No later change can be validated without it. Sequenced early for that reason only; its priority stays P3 |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Peak webhook deliveries per minute, and how bursty they are | Whether PERF-001/002 escalate from Medium to High severity (SSM or account-concurrency saturation) | CloudWatch `Invocations` for the function, at 1-minute resolution |
| Whether production callers actually use the synchronous `example.js` pattern, and how many there are | PERF-003 confidence (Medium → High) and whether to redesign the call topology | Inventory of functions with `lambda:InvokeFunction` on the verifier |
| Whether SSM `ThrottlingException`s or Lambda `Throttles` have occurred | PERF-002 severity | CloudTrail / CloudWatch read-only queries below |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 / webhook rate | `safe-on-production` | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Sum --period 60 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)` | Peak per-minute rate. Shows whether SSM call volume or concurrency is close to any limit |
| PERF-001 | `safe-on-production` | `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=GetParameters --max-results 50` | Confirms one GetParameters per invocation and whether any were throttled (`errorCode`) |
| PERF-002 | `safe-on-production` | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Maximum Average --period 300 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)` | Whether invocations ever approach the 30 s ceiling |

---

## 2. Scope and method

**Reviewed:** All source and configuration in the repository: `src/index.js` (the deployed handler), `src/example.js` (the documented caller pattern), `src/create-test-event.js` (developer CLI), `src/package.json`, `src/.eslintrc.json`, all six `terraform/*.tf` files, `README.md`, `.gitignore`.
**Not reviewed:** `GitHubSignatureVerificationFlow.png` (binary diagram; not read). The API Gateway that the README says fronts the webhook is not defined in this repository, so its timeouts, throttling, and integration type were not examined. Live AWS configuration and Terraform state were not read. Drift between the `.tf` files and the deployed function is unknown.

**Evidence available:** Uninstrumented. The repository has no metrics, no tracing (`terraform/lambda.tf` declares no tracing configuration), no benchmarks, no load tests, no alarms, and no SLOs. The only signal is `console.log` to CloudWatch Logs (enabled by `AWSLambdaBasicExecutionRole`, `terraform/iam.tf:44`). Platform metrics such as Lambda `Duration` and `Throttles` exist in AWS but were not supplied.

**Ranking method:** Structural signals only. No runtime data was available.

**Reference depth:** Node.js, Terraform, and serverless (AWS Lambda) have deep references. The serverless reference was loaded manually: the detector did not flag it, but `aws_lambda_function` in `terraform/lambda.tf:7` establishes it directly. AWS SSM Parameter Store and KMS have no engine reference and were analyzed as a remote key-value-style configuration dependency using category principles only.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every text file in the repository was read in full |
| **Critical paths** | 2 / 2 analyzed (verifier handler; documented caller → verifier invoke) |
| **Shared resources** | 4 / 4 analyzed (SSM Parameter Store API, KMS decrypt via SSM, account Lambda concurrency, CloudWatch Logs ingestion) |
| **Technology support** | Node.js: deep. Terraform: deep. Serverless/Lambda: deep. SSM/KMS: category principles only |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is Medium, not High. The code is fully covered, but the deployed runtime cannot match the repository as written (`nodejs4.3`, see MAINT-001), and the API Gateway layer in front of it is outside the repository.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Webhook arrival rate and burstiness | No evidence exists | CloudWatch `Invocations`, 1-minute resolution |
| SSM/KMS latency and throttling history | No evidence exists | CloudTrail `GetParameters` events with `errorCode`; X-Ray subsegments once tracing is on |
| Whether the deployed function matches `terraform/` | No evidence exists | `terraform plan` in `terraform/` (read-only) |
| API Gateway integration timeout and throttling | Out of scope (not in repo) | The gateway's own definition |
| SSM Parameter Store / KMS engine behavior | Technology unsupported | No dedicated reference. AWS service quotas for the account and region are the authoritative source |

---

## 3. Architecture overview

Webhook flow, as documented in `README.md:5-22`: GitHub → API Gateway (not in repo) → a consumer Lambda → **synchronous invoke** of this verifier Lambda → SSM `GetParameters` (with decryption) → HMAC-SHA1 compare → status code returned to the consumer, which forwards non-200 responses.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Verifier handler | Node.js on AWS Lambda | runtime `nodejs4.3` (`terraform/lambda.tf:14`) | deep | Signature verification, `index.handler` |
| AWS SDK | `aws-sdk` v2 | `^2.22.0` declared (`src/package.json:20`), no lockfile. Only `index.js` is zipped (`terraform/lambda.tf:3`), so the runtime-bundled SDK is what runs | — | SSM client; Lambda client in `example.js` |
| Secret store | SSM Parameter Store, `WithDecryption: true` (KMS) | — | category only | Holds the webhook secret |
| Provisioning | Terraform | `required_version = "~> 0.9"` (`terraform/config.tf:2`) | deep | Function, IAM role. Remote state backend present (identifiers not reproduced) |

**Shared resources:** the account/region SSM Parameter Store API throughput; the KMS decrypt that SSM performs on the caller's behalf; the account's Lambda concurrency pool, shared by the verifier, every caller, and every other function in the account, since no `reserved_concurrent_executions` is set (`terraform/lambda.tf:7-17`); CloudWatch Logs ingestion.

---

## 4. Workload model

**Known**

- Function timeout 30 s — `terraform/lambda.tf:15`
- Memory 128 MB — `terraform/lambda.tf:13`
- Runtime `nodejs4.3` — `terraform/lambda.tf:14`
- No reserved or provisioned concurrency declared — `terraform/lambda.tf:7-17` (no such argument, no `aws_lambda_provisioned_concurrency_config`)
- No tracing configuration — `terraform/lambda.tf:7-17`
- One SSM `GetParameters` call with decryption per invocation that passes input validation — `src/index.js:24-39, 75`
- Callers invoke the verifier with `InvocationType: 'RequestResponse'` and forward the entire event — `src/example.js:16-19`, `README.md:22`
- No timeout or retry configuration on any AWS SDK client — `src/index.js:6-9, 26`, `src/example.js:5-9, 15`

**Assumed** (unverified; these drive the confidence caps)

- Production callers follow the `example.js` pattern, as the README directs. Affects: PERF-003, PERF-002
- Webhook traffic arrives in bursts tied to repository activity (pushes, releases), not uniformly. Affects: PERF-001, PERF-002
- Webhook bodies vary in size with event type and can be much larger than the test fixture in `create-test-event.js`. Affects: PERF-004

**Unknown**

- Webhook arrival rate, peak, and burstiness
- SSM/KMS latency distribution and throttling history
- Number of distinct caller functions and their own timeouts
- Cold-start frequency and init duration
- Secret rotation frequency and acceptable staleness
- Whether the live function matches the Terraform

**Derived**

- Remote secret-store calls per webhook = 1 per verifier invocation, no reuse across warm invocations. From: `src/index.js:75`, the handler body runs per invocation, and nothing is held at module scope beyond `AWS.config`
- Concurrency slots held per in-flight webhook = 2 (caller blocked + verifier running). From: `RequestResponse` invocation (`src/example.js:18`)
- Upper bound on how long both slots are held when SSM hangs = verifier timeout 30 s (`terraform/lambda.tf:15`), unless the caller's own timeout, which is not in the repo, is shorter. Concurrency consumed in that state ≈ 2 × arrival rate × time-to-fail. The arrival rate is unknown, so no number is given

**Measured**

- None. No profile, trace, metric export, or load test was supplied or exists in the repository. This is why no finding is `Confirmed`.

### Questions that would change the ranking

Asked once. No answers were available, so the review proceeded under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Roughly how many webhook deliveries does the function see per minute at peak, and how bursty are they (e.g. mass pushes, release automation)? | severity, confidence | At a rate approaching SSM throughput or account concurrency, PERF-001 and PERF-002 move to High (P1). At a rate of a few per hour they stay P2–P3 |
| 2 | high | Do production consumer functions call the verifier synchronously as in `src/example.js`, and how many of them are there? | confidence, recommendation | Yes → PERF-003 confidence High. No → PERF-003 drops to `considered_not_reported` |
| 3 | high | Is the account's Lambda concurrency or SSM throughput shared with other critical workloads, and have `Throttles` or SSM `ThrottlingException`s been seen? | severity | Observed throttling raises PERF-002's blast radius to system-wide and its severity to High |
| 4 | medium | How often is `GitHubWebhookSecret` rotated, and how long may a rotated secret remain stale in a warm environment? | recommendation | Sets the TTL in PERF-001's memoization, or argues for refetch-on-mismatch |
| 5 | medium | What webhook event types are subscribed, and what is the typical and largest payload size? | severity | Large payloads raise PERF-004 (log volume and cost) from Low |
| 6 | low | Does the deployed function still run `nodejs4.3` at 128 MB, and do `Init Duration` values show frequent cold starts? | severity, recommendation | Decides whether PERF-006 matters and whether memory sizing is worth testing |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | Consumer Lambda → `lambda.invoke` (RequestResponse) → verifier `handler` (`src/example.js:12-36` → `src/index.js:63-90`) | yes. GitHub's delivery and the consumer both wait | 1 SSM `GetParameters` + KMS decrypt (inside the verifier) | Only by the verifier's 30 s timeout and the consumer's own (not in repo) | no | Two concurrency slots per webhook; double cold-start exposure |
| 2 | Verifier `handler` alone (`src/index.js:63-90`) | yes | 1 SSM call per invocation; HMAC over the body | 30 s function timeout; SDK defaults otherwise | no | Logs full event and context before validating |

**Amplification points:** none that grow with data. All per-request work is O(1) in call count. HMAC and logging are O(body size). The amplification here is in *time held*, not in work. While SSM is slow, every in-flight webhook holds two concurrency slots for as long as SSM takes (at most 30 s). SDK-default retries, not configured in the repo, can extend that wait toward the same ceiling.

**Paths deliberately not analyzed in depth, and why:** `src/create-test-event.js` is a developer CLI run by hand (`README.md:24-28`). It is offline, makes one SSM call per run, and shares nothing with the request path.

---

## 6. Layer analysis

### 6.1 Application

The handler does almost no CPU work. It runs one regex validation (`src/index.js:15`), one HMAC-SHA1 over the body (`src/index.js:42-44`), and a small `JSON.stringify` in `respond` (`src/index.js:56`). None of this is a credible bottleneck without a profile, and none is reported. The cost sits in the fixed per-invocation work around it: the remote secret fetch (PERF-001), unconditional full-event logging (PERF-004), and per-call SDK client construction (folded into PERF-001 and PERF-003).

### 6.2 Data access and datastore

The only datastore-like dependency is SSM Parameter Store. It is read once per invocation, with decryption, by a client constructed inside the call (`src/index.js:26`). No client timeout or retry configuration exists (PERF-001, PERF-002). The value read is a single, rarely changing secret, so per-request reads are repeated work.

### 6.3 Distributed communication

Two synchronous remote hops sit in series on the webhook path: consumer → verifier (Lambda invoke) and verifier → SSM. Neither has an explicit per-call timeout, retry cap, or deadline that accounts for the caller's remaining budget (PERF-002). The consumer → verifier hop exists for secret isolation (`README.md:22`). It costs a second function's duration and concurrency per webhook (PERF-003).

### 6.4 Infrastructure

`terraform/lambda.tf` sets 128 MB, a 30 s timeout, `nodejs4.3`, no reserved concurrency, no provisioned concurrency, and no tracing. The 128 MB allocation also sets the CPU share. Whether a larger allocation would cut duration enough to pay for itself is a question (Q6), not a finding, because no duration data exists. Cold-start initialization loads the whole v2 SDK at module scope (PERF-006). `nodejs4.3` is end-of-life for creates and updates (MAINT-001), so any redeploy of a performance fix also forces a runtime change.

### 6.5 Observability

The repository is uninstrumented (PERF-005). No finding can be confirmed and no fix can be validated until at least platform metrics are read and tracing is enabled. The one form of observability present, logging the full event and context on every call, is itself a cost and data-exposure concern (PERF-004, SEC-002).

---

## 7. Findings

### PERF-001 — Secret fetched from SSM with KMS decryption on every invocation

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `src/index.js:24` (`getSecret`, called at `src/index.js:75`) |
| **Tags** | quick-win |

**Problem**
Every verification that passes input validation makes a remote `ssm.getParameters` call with `WithDecryption: true`, through a newly constructed SSM client, to read a value that almost never changes. Nothing is reused across warm invocations of the same execution environment.

**Performance principle**
Do not repeat remote work whose result does not change between requests. Fixed per-request I/O on a blocking path adds latency to every request and load to a shared dependency.

**Evidence**
- `src/index.js:24-39`: `getSecret()` constructs `new AWS.SSM()` (line 26) and calls `getParameters` with `WithDecryption: true` (line 29) every time it runs.
- `src/index.js:75`: `getSecret()` is called inside `exports.handler`, so it runs per invocation. Only `AWS.config` is set at module scope (lines 6-9).
- `terraform/iam.tf:31`: the role grants `ssm:GetParameters` only, which fits a runtime fetch with nothing pre-provisioned.
- No runtime evidence. No trace, metric, or CloudTrail export was supplied.

**Impact**
- Position: critical path. The consumer, and through it GitHub's delivery, waits on it.
- Frequency: per request.
- Growth: O(1) per request. SSM call volume grows linearly with webhook rate.
- Blast radius: service under normal conditions. SSM throughput and the KMS decrypt behind it are account/region-shared, so under heavy bursts it can reach other consumers of Parameter Store in the account.

**Conditions**
Applies at any traffic level as per-request latency. It matters as a shared-resource problem only if peak webhook rate is a significant fraction of the account's SSM `GetParameters` throughput, together with all other SSM users in the account. The rate is unknown (Q1, Q3). The assumption here is bursty, low-to-moderate traffic, which keeps severity at Medium.

**Counter-evidence**
- Looked for module-scope memoization, a Lambda extension or layer for parameter caching, or the secret passed via environment variable. None found (`src/index.js`, `terraform/lambda.tf`). **no-effect**.
- Looked for a cap on webhook rate (API Gateway throttling, reserved concurrency). None in the repo, so the SSM call rate is bounded only by arrival rate. **no-effect**.
- The validation early-return (`src/index.js:70-72`) skips SSM for malformed requests, so invalid traffic does not hit SSM. This bounds the impact somewhat. **bounds-impact**: it is why blast radius is scored `service`, not `system-wide`.

**Why this might not matter**
Webhook volume for a single GitHub organization may be a handful per minute. At that rate one extra SSM round trip per webhook adds latency nobody notices and is nowhere near any quota. Lambda warm-environment reuse is also not guaranteed, so some fraction of invocations would fetch anyway.

**Recommendation**
Remove the repeated work. Construct the SSM client once at module scope and memoize the secret promise per execution environment with a bounded TTL. Optionally refetch once on a signature mismatch so a rotation is picked up immediately. This addresses the principle (stop repeating unchanging remote reads) rather than the latency symptom.
Cache analysis, per the skill's caching rules:
- **Hit rate:** every warm invocation in an environment reuses the one key, so after the first call per environment, every call is a hit.
- **Invalidation:** TTL plus refetch-on-mismatch.
- **Granularity:** a single key.
- **Stampede:** at most one fetch per concurrently cold environment, bounded by concurrency.
- **Consistency:** a stale secret during rotation is already inherent, because GitHub and SSM are updated separately. Acceptable staleness is a product decision (Q4).

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Module-scope client and memoized secret with TTL and refetch-on-mismatch | **preferred** | Removes the call from warm invocations with a few lines of code, needs no new infrastructure, and keeps the secret in SSM |
| B — AWS-provided parameter-caching Lambda extension | | Same effect without custom code, but adds a layer dependency and a local HTTP hop; worth it only if several functions need the same pattern |
| C — Inject the secret as an encrypted environment variable | | Removes the SSM call entirely, but moves rotation into a redeploy and changes the secret-handling model |
| D — Leave as is | | Defensible if Q1 shows very low traffic; PERF-001 then drops to Low |

**Trade-offs**
A secret held in memory for the lifetime of the environment. A staleness window after rotation, bounded by the TTL. The refetch-on-mismatch path lets an attacker trigger an SSM read per forged request, so rate-limit it (for example, at most one refetch per TTL window).

**Validation**
- Baseline: count `GetParameters` CloudTrail events against `Invocations` over the same window. Expect a ratio of about 1:1 for valid requests. `safe-on-production`.
- After the change, the ratio should fall to roughly cold starts plus TTL expiries. Verifier `Duration` p50 on warm invocations should fall by the SSM round trip; the size of that drop is unknown until measured.
- Falsifier: if `GetParameters` volume does not drop, environments are not being reused (traffic too sparse) and the finding's impact was overstated.
- Guard: a unit test asserting `getParameters` is called once across two sequential handler invocations in one module instance.
- Commands:
  - `safe-on-production` `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=GetParameters --max-results 50`: GetParameters volume and throttling errors.
  - `safe-on-production` `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Sum --period 60 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)`: invocation count to compare against.

---

### PERF-002 — No per-call timeout or deadline on SSM or the Lambda invoke; the only bound is the 30 s function timeout, held across two functions

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `src/index.js:24` (`getSecret`); also `src/example.js:15-22` |
| **Tags** | quick-win |

**Problem**
Neither outbound call on the webhook path has an explicit per-attempt timeout, a retry cap, or a total budget. The SSM client is created with defaults (`src/index.js:26`). So is the consumer's Lambda client (`src/example.js:15`). The only bound visible in the repository is the verifier's 30 s function timeout. While the verifier waits, the synchronous consumer is also blocked.

**Performance principle**
Every remote call on a blocking path needs a bound smaller than its caller's remaining budget. Without one, a slow dependency becomes resource exhaustion in every caller that waits on it.

**Evidence**
- `src/index.js:6-9, 26`: SDK configuration sets only `apiVersions` and `region`. No `httpOptions` timeouts and no `maxRetries`.
- `src/example.js:5-9, 15, 22`: same for the Lambda client. `lambda.invoke` is awaited with no timeout.
- `terraform/lambda.tf:15`: `timeout = "30"` is the only time bound.
- The consumer's own timeout is not in the repository.
- No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1).
- Blast radius: service, scored for normal conditions. When SSM is degraded or throttling, each in-flight webhook holds two concurrency slots from the shared, unreserved account pool for up to 30 s. Derived: slots held ≈ 2 × arrival rate × time-to-fail. With unknown arrival rate, this cannot be sized.

**Conditions**
Matters when SSM, KMS, or the Lambda control plane is slow or throttling. The cost then scales with webhook arrival rate. If peak rate × 30 s × 2 approaches the account's unreserved concurrency, which is unknown, the blast radius becomes system-wide and the severity High (Q1, Q3).

**Counter-evidence**
- Looked for a client timeout, an SDK retry configuration, or `context.getRemainingTimeInMillis()` use. None found. **no-effect**.
- The 30 s Lambda timeout does bound the worst case, so this is not an unbounded hang. **bounds-impact**: it is why this is Medium rather than the Critical that an unbounded missing timeout would score.
- Reserved concurrency would isolate the blast radius. It is not set (`terraform/lambda.tf`). **no-effect**.

**Why this might not matter**
SSM is a managed, regional service that is normally fast. At low webhook rates even a 30 s stall during an SSM incident holds few slots. GitHub also redelivers failed webhooks on request, so occasional failures are recoverable.

**Recommendation**
Bound the work: set explicit connect and read timeouts and a retry cap on both SDK clients. Size the verifier's total under the consumer's remaining time, and the consumer's under the gateway's integration timeout. The values should come from measured SSM and invoke latency, which nobody has measured yet (`Duration` metrics, then X-Ray subsegments). Set the presence of a bound now and its value from the measurement. If PERF-001 lands, most invocations no longer call SSM, which shrinks this exposure to cold starts and TTL refreshes.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Explicit per-attempt timeout and retry cap on both clients, within the caller's budget | **preferred** | Directly bounds the time slots are held; small configuration change |
| B — Lower the Lambda function timeout | | Blunt: also kills slow-but-healthy invocations and does nothing for the consumer's own wait |
| C — Reserved concurrency on the verifier | | Isolates the blast radius but turns overload into throttling errors; complements A, does not replace it |

**Trade-offs**
Timeouts set too short turn healthy slow calls into 500s, and with retries can add load to SSM during a brownout. Values need measurement and revisiting.

**Validation**
- Baseline: verifier `Duration` maximum and the count of invocations ending in `Task timed out` in CloudWatch Logs. `safe-on-production`.
- Expectation: after the change, the maximum duration is bounded by the configured client budget rather than 30 s during SSM slowness.
- Falsifier: if `Duration` maximum never exceeds a small fraction of 30 s over a long window that includes SSM incidents, the exposure is theoretical at this traffic level.
- Guard: a CloudWatch alarm on `Duration` maximum and on `Throttles`.
- Commands:
  - `safe-on-production` `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Maximum Average --period 300 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)`: how close invocations come to the 30 s ceiling.
  - `safe-on-production` `aws logs filter-log-events --log-group-name /aws/lambda/github-signature-verifier --filter-pattern "\"Task timed out\""`: count of timeout-terminated invocations. Read access only, but the log group holds full webhook payloads (see SEC-002), so restrict who runs it.

---

### PERF-003 — Synchronous Lambda-to-Lambda invocation doubles billed duration, concurrency, and cold-start exposure per webhook

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `src/example.js:12` (`validateSignature`) |
| **Tags** | needs-measurement |

**Problem**
The documented usage pattern (`README.md:22`) has every consumer call the verifier with `InvocationType: 'RequestResponse'`. The consumer sits idle and billed while a second function runs, cold-starts independently, and performs the SSM fetch. The whole event is serialized into the invoke payload and parsed back.

**Performance principle**
Do not add a synchronous network hop and a second compute unit on a blocking path for work the caller could do locally or hand off. Each serial hop adds its own latency, tail, and resource hold time.

**Evidence**
- `src/example.js:15-19`: `new AWS.Lambda()` is constructed per call. `RequestResponse`, `Payload: JSON.stringify(event)`.
- `src/example.js:22-34`: the consumer awaits the result, then runs `JSON.parse(data.Payload)`.
- `README.md:22`: this is the prescribed integration.
- The verifier's actual work is a few lines of HMAC (`src/index.js:41-47`).
- No runtime evidence. Whether production consumers use this exact pattern is unknown.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1) in call count. Payload serialization is O(body size).
- Blast radius: service (the consumer and verifier pair). This compounds PERF-002: it doubles the concurrency held during an SSM stall.

**Conditions**
Assumes production consumers follow `example.js` (Q2). Duration and concurrency cost scale with webhook rate (Q1). Cold-start compounding matters when traffic is sparse enough that environments go cold between deliveries.

**Counter-evidence**
- `example.js` is an example, not deployed by this repository's Terraform (`terraform/lambda.tf:3` zips only `index.js`). **lowers-confidence**: this is why confidence is Medium, not High.
- The design reason is explicit (`README.md:22`): keep the secret out of every consumer's IAM scope. That is a legitimate security trade, not an accident. **no-effect** on the mechanism, but it constrains the recommendation.

**Why this might not matter**
At low webhook volume the extra billed duration and the second concurrency slot are negligible, and the isolation benefit is worth more than the cost. Removing the hop would widen secret access to every consumer.

**Recommendation**
Keep secret isolation but stop blocking the consumer on it. Make the verifier the API Gateway target: it verifies, then dispatches the webhook to the downstream function asynchronously (`InvocationType: 'Event'`, or via a queue). This removes one synchronous hop and one blocked concurrency slot per webhook without granting consumers access to the secret. If the topology stays, at least construct the Lambda client at module scope in consumers.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Verifier as front door, async dispatch to consumers | **preferred** | Removes the blocked caller and the double cold start, and preserves secret isolation |
| B — Shared verification module inside each consumer | | Removes the hop entirely but gives every consumer read access to the secret, which is what the README design avoids |
| C — API Gateway Lambda authorizer | | Not viable as-is: request authorizers do not receive the request body, which HMAC verification needs |
| D — Keep the topology, hoist the client to module scope | | Minimal improvement; leaves the double hold in place |

**Trade-offs**
Option A changes routing (one gateway target that dispatches by event type) and makes downstream processing asynchronous. Consumers can no longer return a synchronous result to GitHub, and failed async deliveries need a dead-letter path.

**Validation**
- Baseline: consumer `Duration` against verifier `Duration` for the same webhooks. Expect the consumer's duration to include the verifier's. `safe-on-production`.
- Expectation: under option A, the gateway-to-response path has one function, and the consumer's billed duration no longer includes verification.
- Falsifier: if no production consumer uses `RequestResponse` invocation of the verifier (Q2), this finding does not apply.
- Guard: keep a single synchronous function on the webhook path in the architecture docs and IaC.

---

### PERF-004 — Full event and context logged on every invocation

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | cost |
| **Location** | `src/index.js:66-67` (`handler`) |
| **Tags** | quick-win |

**Problem**
`console.log(event)` and `console.log(context)` run unconditionally before validation. Every webhook body, with headers and signature, plus the context object, is shipped to CloudWatch Logs.

**Performance principle**
Instrumentation on the hot path should be proportional to its diagnostic value. Logging whole payloads makes ingestion cost and I/O scale with payload size on every request.

**Evidence**
`src/index.js:66-67`. The comment at line 65 says "for debugging", but there is no log-level gate. `terraform/iam.tf:44` attaches basic execution, which enables Logs. No retention setting for the log group exists in `terraform/` (no `aws_cloudwatch_log_group`), so log storage is unbounded by default. No runtime evidence.

**Impact**
- Position: critical path (synchronous write to stdout).
- Frequency: per request.
- Growth: O(body size), and retained log volume grows with no retention limit.
- Blast radius: endpoint (mainly cost).

**Conditions**
Cost matters in proportion to webhook rate × payload size. Both are unknown (Q1, Q5). It also matters at any rate for data exposure (SEC-002).

**Counter-evidence**
Looked for a log-level switch, an environment flag, or a log-group retention resource. None found. **no-effect**.

**Recommendation**
Log a bounded summary: delivery ID, event type, outcome, duration. Keep full-payload logging behind an explicit debug flag. Declare the log group with a retention period in Terraform.

**Trade-offs**
Less raw data for after-the-fact debugging of individual deliveries. GitHub's own delivery log partly covers this.

**Validation**
CloudWatch Logs `IncomingBytes` for the function's log group, before and after. Expect a drop proportional to average payload size. `safe-on-production`. Falsifier: no measurable change, meaning payloads are tiny and this is negligible.

---

### PERF-005 — Verification path is uninstrumented

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `terraform/lambda.tf:7` (`aws_lambda_function.github_signature_verifier`) |
| **Tags** | needs-measurement |

**Problem**
There is no tracing, no custom metric, no alarm, and no timing around the SSM call. None of PERF-001 to PERF-003 can be confirmed or their fixes validated from data the system produces about itself.

**Performance principle**
A critical path that is not measured cannot be optimized defensibly. Instrumentation precedes optimization.

**Evidence**
`terraform/lambda.tf:7-17` has no tracing configuration and there is no alarm resource anywhere in `terraform/`. `src/index.js` has no timing or metrics. No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1).
- Blast radius: endpoint.
- No direct runtime cost; the cost is the inability to validate any change.

**Conditions**
Applies regardless of traffic. Its value grows with how much of this report the team decides to act on.

**Counter-evidence**
Looked for X-Ray SDK usage, a tracing configuration, metric or alarm resources, and an embedded-metric-format log. None found. Lambda's platform metrics exist outside the repo but are not used or alarmed here. **no-effect**.

**Recommendation**
Enable active tracing on the function, which shows the SSM subsegment and init duration. Add alarms on `Duration` maximum, `Errors`, and `Throttles`. Sequence this before PERF-001/002 so their effect is measurable.

**Trade-offs**
Tracing has a small per-invocation overhead and cost, and IAM needs an extra permission for trace export.

**Validation**
After enabling, confirm traces show an SSM `GetParameters` subsegment per invocation and an `Init Duration` on cold starts. `safe-on-production`.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-006 | Low | Medium | P3 | `src/index.js:3` (module scope) | The whole `aws-sdk` v2 package is required at module scope (rather than only the SSM client) on a 128 MB function (`terraform/lambda.tf:13`), which adds to init time on every cold start. Startup-only and bounded. Its magnitude depends on cold-start frequency and the runtime's SDK loading behavior, neither of which is measured. Fix: require only `aws-sdk/clients/ssm`, or move to the modular v3 client during the runtime upgrade (MAINT-001). Validate via X-Ray `Init Duration` before and after (`safe-on-production`). Falsifier: no change in `Init Duration`. |

### Considered and not reported

- **Memory sizing at 128 MB (`terraform/lambda.tf:13`).** Lambda allocates CPU with memory, so more memory could reduce duration and possibly cost. Evidence checked: `terraform/lambda.tf`, the handler's workload (one I/O call, one small HMAC). Discarded as a finding because the outcome depends entirely on measured duration, which does not exist. It is carried as question Q6. Revisit when `Duration` and `Init Duration` data are available.
- **HMAC and regex CPU cost (`src/index.js:15, 41-47`).** Evidence checked: one SHA-1 HMAC over the body, and a regex literal compiled once by the engine. Discarded: CPU on this I/O-dominated path cannot be shown to matter without a profile. Revisit only if a profile attributes material time to the handler's CPU.

### Adjacent findings — outside performance scope

These were noticed while reading for performance. They are **not** scored on the performance rubric, and this skill has no security or correctness methodology, so each one points to the review that should assess it properly.

### SEC-001 — Signature compared with a non-constant-time `===`, using the SHA-1 header

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/index.js:46` |

**Problem** `validSignature` compares the expected and received hex digests with `===`, which can return early on the first differing character. Verification also relies only on the SHA-1 `X-Hub-Signature` header. The format regex (`src/index.js:15`) is not anchored at the end.

**Evidence** `src/index.js:41-47` (`requestSignature === expectedSignature`); `src/index.js:12-15`; `README.md:12` describes HMAC-SHA1.

**Impact** A timing side channel on HMAC comparison is a well-known class of weakness that can, in principle, help forge signatures. SHA-1 HMAC is weaker than the SHA-256 signature GitHub also offers. Risk is Medium rather than High because exploiting this remotely through API Gateway and Lambda is noisy and hard. It is still a verifier's core job.

**Recommendation** Use `crypto.timingSafeEqual` on equal-length buffers. Verify the SHA-256 signature header. Anchor the regex.

**Trade-offs** None material. The runtime upgrade (MAINT-001) is needed for `timingSafeEqual`, since it is not available on Node 4.

**Validation** Unit tests for equal, unequal, and wrong-length signatures. Confirm the SHA-256 header is required.

**Would need** A dedicated security review of the webhook authentication flow.

### SEC-002 — Full webhook payloads, signatures, and invocation context written to logs

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Low |
| **Location** | `src/index.js:66-67` |

**Problem** Every request, including invalid or forged ones, is logged in full: headers with the signature, the body (private repository metadata per the README's use case), and the context object.

**Evidence** `src/index.js:66-67`. No log-group retention is declared in `terraform/`.

**Impact** Low: the logs are inside the same AWS account, but they widen who can read webhook content and keep it indefinitely.

**Recommendation** Log a minimal summary (see PERF-004) and set a log retention period.

**Trade-offs** Less debugging detail.

**Validation** Inspect a new log stream and confirm no body or signature is present.

**Would need** A data-handling / logging-policy review.

### COR-001 — Documented caller invokes a function name that the Terraform does not deploy

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/example.js:17` |

**Problem** The example and README call `FunctionName: 'githubSignatureVerifier'`. Terraform deploys `function_name = "github-signature-verifier"`.

**Evidence** `src/example.js:17`; `README.md:19, 22`; `terraform/lambda.tf:9`.

**Impact** A consumer copied from the example will fail every invoke with a not-found error and return 500 for every webhook, unless a separately named function exists outside this repo. Risk is Medium because it fails loudly, not silently.

**Recommendation** Align the example and README with the deployed name, or reference the Terraform output `lambda_function_name` (`terraform/outputs.tf:5`).

**Trade-offs** None.

**Validation** Invoke via the documented name in a non-production account. Expect success after the fix.

**Would need** An integration test of the consumer → verifier contract.

### MAINT-001 — End-of-life runtime and toolchain: `nodejs4.3`, Terraform `~> 0.9`, AWS SDK v2

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `terraform/lambda.tf:14` |

**Problem** The function declares runtime `nodejs4.3`, which AWS Lambda has long deprecated for creation and updates. Terraform is pinned to `~> 0.9` with pre-0.12 syntax (`terraform/config.tf:1-2`). The code depends on AWS SDK for JavaScript v2 (`src/package.json:20`), which AWS has moved out of active support. There is no lockfile.

**Evidence** `terraform/lambda.tf:14`; `terraform/config.tf:1-2`; `src/package.json:20`.

**Impact** High: the function cannot be updated as declared, so any fix in this report, performance or security, is blocked until the runtime and toolchain are upgraded.

**Recommendation** Upgrade to a currently supported Node.js Lambda runtime. Migrate to modular AWS SDK v3 clients. Upgrade Terraform syntax and pin providers. Fold PERF-001/002/006 and SEC-001 into that change.

**Trade-offs** Upgrade effort and regression risk. Behavior differences across Node and SDK majors need testing.

**Validation** `terraform plan` succeeds against current providers. Tests pass on the new runtime.

**Would need** A dependency and runtime upgrade pass.

---

## 8. Prioritized action plan

No P0 or P1 findings.

### P2 — Medium priority (with prerequisites and one P3 item sequenced early, as noted)

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 0 | MAINT-001 / COR-001 / SEC-001 | n/a (adjacent) | Medium | Prerequisite: the function cannot be redeployed on `nodejs4.3`. Fix the caller name and the timing-safe compare in the same change |
| 1 | PERF-005 | P3 | Low | Sequenced early because it is cheap and every later change needs it to be validated. Priority unchanged |
| 2 | PERF-001 | P2 | Low | Removes the SSM call from warm invocations and shrinks PERF-002's exposure |
| 3 | PERF-002 | P2 | Low | Bounds the degraded-dependency worst case on both hops |
| 4 | PERF-003 | P2 | High | Topology change; do it only once Q2 confirms consumers use the synchronous pattern |

### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 5 | PERF-004 | P3 | Low | Small cost and data-exposure cleanup; pairs with SEC-002 |
| 6 | PERF-006 | P3 | Low | Do it as part of the SDK v3 migration in MAINT-001 |

**If only one thing is done:** memoize the secret and the SSM client at module scope (PERF-001), as part of the runtime upgrade the function needs anyway.

---

## 9. Validation plan

### PERF-001

- **Baseline:** CloudTrail `GetParameters` event count vs. `Invocations` over the same 7-day window. Safe on production: yes.
- **Change:** module-scope SSM client and memoized secret with TTL and rate-limited refetch-on-mismatch.
- **Measurement:** the same ratio; warm-invocation `Duration` p50 (X-Ray once PERF-005 is in place).
- **Expectation:** `GetParameters` calls fall from about one per valid invocation to about one per cold start or TTL expiry. Warm duration falls by the SSM round trip; the size is unmeasured.
- **Falsifier:** call volume unchanged, meaning environments are not being reused at this traffic level.
- **Guard:** unit test asserting a single `getParameters` call across two handler invocations.
- **Commands:**
  - `safe-on-production` `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=GetParameters --max-results 50`: GetParameters volume and throttling errors.
  - `safe-on-production` `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Sum --period 60 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)`: invocation baseline and peak per-minute rate (also answers Q1).

### PERF-002

- **Baseline:** `Duration` maximum and the `Task timed out` count. Safe on production: yes.
- **Change:** explicit per-attempt timeouts and retry caps on the SSM and Lambda clients, inside the caller's budget.
- **Measurement:** `Duration` maximum during an SSM slowdown, or under fault injection in staging.
- **Expectation:** worst-case duration is bounded by the configured client budget, not 30 s.
- **Falsifier:** maximum duration is already far below 30 s over a window that includes SSM incidents.
- **Guard:** alarms on `Duration` maximum and `Throttles`.
- **Commands:**
  - `safe-on-production` `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=github-signature-verifier --statistics Maximum Average --period 300 --start-time $(date -u -d '-7 days' +%FT%TZ) --end-time $(date -u +%FT%TZ)`: proximity to the 30 s ceiling.
  - `safe-on-production` `aws logs filter-log-events --log-group-name /aws/lambda/github-signature-verifier --filter-pattern "\"Task timed out\""`: count of timeout-terminated invocations (log group holds payloads; restrict access).

### PERF-003

- **Baseline:** consumer `Duration` vs. verifier `Duration` per webhook. Safe on production: yes.
- **Change:** verifier as front door with async dispatch.
- **Measurement:** functions on the synchronous path per webhook; consumer billed duration.
- **Expectation:** one synchronous function instead of two; consumer duration excludes verification.
- **Falsifier:** no production consumer invokes the verifier synchronously.
- **Guard:** architecture rule in the IaC: a single synchronous function behind the webhook route.

### PERF-004

- **Baseline:** log-group `IncomingBytes`. Safe on production: yes.
- **Change / Expectation:** summary logging. Bytes fall in proportion to the average payload. **Falsifier:** no change.

### PERF-005

- **Change:** active tracing plus alarms. **Expectation:** an SSM subsegment and `Init Duration` become visible. **Falsifier:** n/a (enabling capability). Safe on production: yes.

### PERF-006

- **Baseline / Measurement:** X-Ray `Init Duration` before and after the narrower require. Safe on production: yes. **Falsifier:** no change in init duration.

### Instrumentation gaps to close first

1. Read existing Lambda platform metrics (`Invocations`, `Duration`, `Errors`, `Throttles`). This is free and answers Q1 and Q3.
2. Enable tracing on the verifier, and on consumers if they keep the synchronous pattern.
3. Run `terraform plan` (read-only, `safe-on-production`) in `terraform/` to confirm the repository matches what is deployed before acting on any value cited here.

---

## 10. Machine-readable output

Emitted alongside this report at `verifier-guided-1.json` (conforms to `schemas/review.schema.json`; validated with the skill's `scripts/validate_review.py`).

---

## 11. Notes on this review

- Findings are classified by evidence grade. None is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated or assumed. The only numbers are read from files: 30 s timeout, 128 MB, runtime and SDK versions. The concurrency expression in §4 is a labelled derivation with no invented inputs.
- Account identifiers, the state bucket, and the KMS key ARN present in `terraform/` were deliberately not reproduced.
- The workload questions in §4 could not be put to anyone. The review proceeded under the stated assumptions, and workload-dependent confidence is capped at Medium where the finding depends on rate.
