# Performance Review: AstraCode judge pipeline (official submission judging)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the official-judging path (submission create -> outbox -> Kafka -> judge worker -> go-judge -> result consumer)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The slow-judging report is explained by the code. An official submission's test cases run strictly one at a time: one HTTP call to the sandbox per test case, in a serial loop. The worker is configured to run one job at a time by default. Accepted submissions never benefit from the early-exit. Cost therefore grows linearly with test-case count, while the sandbox is configured for 8-way parallelism that this path never uses. A second, smaller per-job cost re-hashes and re-reads the whole cached test bundle on every job. All findings are static; no measurements exist, so confidence tops out at Medium. Review confidence: Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Run official test cases in small concurrent windows (stop-after-window on failure) instead of batch size 1 | Dominant per-job term, linear in test count, matches the reported symptom |
| 2 | **PERF-003 (P2)** | Decide the worker job-slot count against measured sandbox and worker CPU; compose defaults to 1 job and 0.5 CPU | Queueing multiplies PERF-001's per-job time across concurrent submissions |
| 3 | **PERF-002 (P2)** | Stop re-hashing the cached zip and re-reading all files on every job | Fixed per-job cost proportional to bundle size, on the worker's 0.5 CPU (dev compose) |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Per-test-case wall time inside go-judge (sandbox spawn vs program runtime) | Whether PERF-001 is High or Medium in practice, and how large a window to use | Time `runBatch` per call (add a log or metric), or inspect go-judge logs in staging |
| Typical test count and bundle size per problem | PERF-001 and PERF-002 ranking | Query problem metadata (`test_count`), check size of cached `bundle.zip` files |
| Prod values of `WORKER_POOL_SIZE` and worker/sandbox CPU limits | PERF-003 severity | Read the deployed env file and compose override |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-002 / bundle size | `safe-on-production` | `du -sh /cache/testcases/problem_*/bundle.zip` (on the worker host) | If bundles are small (a few MiB), drop PERF-002 |
| PERF-001 / per-case time | `safe-on-production` | Procedure: compare worker log timestamps of "processing judge job" and "judge job completed" against `test_count` for several accepted submissions | If time scales with test count at a roughly constant slope, PERF-001 is supported |
| PERF-003 | `safe-on-production` | `docker stats judge_worker judge_sandbox` while submitting several jobs | Shows whether CPU is idle (raise concurrency) or saturated (leave it) |

---

## 2. Scope and method

**Reviewed:** `workers/judge` (Kafka consumer, use case, go-judge client, official testcase loader, problem metadata reader), `services/submission` (outbox relay, result application and persistence), `pkg/kafka`, `docker-compose.yml`, `docker-compose.prod.yml`, `infra/kafka/kafka-init.sh`, `docs/codebase/CONCURRENCY.md`.
**Not reviewed:** the problem service's testcase upload and metadata path, auth, website, gateway and envoy, `tools/benchmark`, go-judge's own internals, and the interactive run-code path except where it shares code.

**Evidence available:** Uninstrumented for this purpose (no metrics, traces, load-test results or dashboards in the repository that bear on judging time).
**Ranking method:** Structural signals only (static reading).
**Reference depth:** Go, Kafka, Postgres, gRPC, REST, Docker: deep tier. Detection evidence is `indirect` (declared dependencies); usage was confirmed by reading the code. The repository is multi-service, so findings are scoped to `workers/judge` and `services/submission`.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | The full judging path was read end to end; unrelated services were not |
| **Critical paths** | 1 / 1 (official judge) |
| **Shared resources** | 4 / 4 (worker job slots, go-judge sandbox, testcase cache volume, Kafka partitions) |
| **Technology support** | Go, Kafka, Postgres: Deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Per-call latency of go-judge `/run` | No evidence exists | Timing instrumentation around `runBatch` |
| Whether go-judge runs multiple `cmd` entries of one `/run` request concurrently | go-judge source not examined | Its documentation or a staging test with a batch of several cases |
| Production values of pool size, CPU limits, bundle sizes | No evidence exists | Deployed env and host inspection |

---

## 3. Architecture overview

| Component | Technology | Role |
|:--|:--|:--|
| submission-service | Go, Postgres, outbox | Creates submission, writes outbox row; relay publishes to Kafka; consumes results |
| Kafka | `judge.submission.jobs` (6 partitions), results topic (6) | Transport (`infra/kafka/kafka-init.sh`) |
| judge-worker | Go, Sarama consumer, resty | Loads testcases, drives sandbox, publishes result |
| go-judge | Sandbox, REST | Compiles and runs code |

**Shared resources:** worker job-slot semaphore, go-judge sandbox (2 CPU, `ES_PARALLELISM=8` in dev compose), shared `testcase_cache` volume, worker container (0.5 CPU in dev compose).

---

## 4. Workload model

**Known**
- Official jobs run with `StopOnFirstFailure: true`, which forces batch size 1 (`workers/judge/internal/application/usecase/judge/process_judge_job.go:53-58`, `workers/judge/internal/adapter/outbound/execute/go_judge_client.go:74-77`).
- Default job concurrency is 4 in code; compose sets `WORKER_POOL_SIZE` to 1 (`docker-compose.yml:337`; `docker-compose.prod.yml:97` defaults to `1`).
- Bundle limits: 64 MiB zip, 128 MiB extracted (`official_loader.go:29-30`).

**Assumed**
- Problems have "many" test cases, per the operator report — affects PERF-001, PERF-002.
- Production uses the compose defaults — affects PERF-003.

**Unknown**
- Test-case count and bundle size distribution; sandbox per-run overhead; submission arrival rate.

**Derived**
- An accepted submission with N test cases makes N sequential `/run` calls, plus one compile call for compiled languages — from `go_judge_client.go:56-120`.

**Measured:** none.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | What is the typical wall time of one test-case run in go-judge, and how many test cases do slow problems have? | severity, recommendation | PERF-001 window size and severity |
| 2 | high | What are production `WORKER_POOL_SIZE` and CPU limits, and what is peak submission concurrency? | severity | PERF-003 |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | Official judge: POST submission -> outbox (poll) -> Kafka -> worker -> go-judge -> result topic -> result apply | Yes, user waits for the verdict | Outbox read and update; one tx with row lock and one batch insert of results | N test cases; bundle up to 64 MiB | Debug logs only | Serial per test case |

**Amplification points:** per-test-case HTTP round trip and sandbox process launch (N times); per-job zip hash and file reads (bundle size, every job).

**Not analyzed in depth:** result persistence. It uses one batch `Create` for all rows inside one transaction (`services/submission/internal/adapter/outbound/persistence/postgres/submission_result.go:104-125`), so no per-test-case query was found.

---

## 6. Layer analysis

### 6.1 Application
Serial test-case loop in `GoJudgeClient.Execute`; full bundle re-verification in `OfficialLoader.Load`.

### 6.5 Distributed communication
The outbox relay polls every 2 s and publishes synchronously one message at a time (`services/submission/internal/container/app.go:82`, `relay.go:30-74`). Consumer dispatch is bounded by a shared semaphore, which is sound.

### 6.6 Infrastructure
Worker at 0.5 CPU and one job slot versus a 2-CPU sandbox with parallelism 8 (`docker-compose.yml:218,227-231,337,357-361`).

### 6.7 Observability
No per-stage timing (metadata fetch, load, compile, each run, publish). Only Info and Debug logs. This blocks `Confirmed` for every finding here.

---

## 7. Findings

### PERF-001 — Official test cases are judged serially, one sandbox request each

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `workers/judge/internal/adapter/outbound/execute/go_judge_client.go:74` (`GoJudgeClient.Execute`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
For official submissions, `StopOnFirstFailure` sets the batch size to 1 and the loop issues one blocking `/run` request per test case, strictly in sequence. Judging time is the sum of all per-case times, and a fully accepted submission (the common success case) always pays for every case.

**Performance principle**
Independent units of work should be overlapped when spare capacity exists; an early-exit optimization must not serialize the whole pipeline.

**Evidence**
- `go_judge_client.go:74-77`: batch size is 1 when `StopOnFirstFailure`.
- `go_judge_client.go:79-117`: sequential loop; each iteration calls `runBatch`, a blocking `Post("/run")` (lines 237-241).
- `process_judge_job.go:57` always sets `StopOnFirstFailure: true`.
- `docker-compose.yml:218`: sandbox `ES_PARALLELISM=8`; the sequential loop keeps at most one run from this job in flight.
- `docs/codebase/CONCURRENCY.md` states the one-case-per-request design is deliberate so execution can stop at the first failure.
- No runtime evidence exists.

**Impact**
Position: critical path (user-visible verdict). Frequency: per test case. Growth: O(n) in test count. Blast radius: every official submission and, via PERF-003, the whole worker. Amplification: Nx requests (derivation: N test cases = N calls).

**Conditions**
Matters when problems have many test cases (as the operators report) and per-run sandbox overhead is non-trivial. If per-run time and N are both small, the effect is small. Assumes the sandbox has idle capacity during judging.

**Counter-evidence**
Looked for concurrency in the loop, a pool of test cases, or request pipelining: none found. Early exit does cut work for failing submissions, which bounds the impact for wrong answers; it does not help accepted ones. Compiled languages compile once (lines 56-65), so compile is not repeated per case.

**Why this might not matter**
If the sandbox is CPU-bound by one job, or the host is small, parallelizing one job only shifts load. Serial runs also give timing fairness: concurrent runs can inflate measured CPU time and cause spurious time-limit verdicts.

**Recommendation**
Run test cases in windows of K (start at or below sandbox parallelism, tune by measurement): send K cases per `/run` request or K concurrent requests, stop issuing windows after the first non-ACCEPTED result, and keep results ordered by index. This overlaps independent work and keeps early exit at window granularity.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — windowed concurrency with stop-after-window | **preferred** | Keeps early exit, uses idle sandbox capacity |
| B — keep serial, reduce per-call overhead (for example the sandbox gRPC endpoint that dev compose enables but this path does not use) | | Does not remove the N-fold sandbox launch cost |
| C — add more worker slots only | | Raises throughput across jobs but does not shorten a single job (see PERF-003) |

**Trade-offs**
Wasted work after a failure within a window (at most K-1 extra runs); possible timing noise from concurrent execution on shared cores; slightly more complex result ordering.

**Validation**
Baseline: log per-call duration and total judge time versus `test_count` for accepted submissions (`safe-on-production`). Change: window K. Measure end-to-end job time and reported max `ExecutionTime` for a known problem before and after. Expectation: judge time falls for accepted submissions with many cases, bounded by sandbox parallelism and CPU; no magnitude is claimed. Falsifier: per-job time unchanged because the sandbox is CPU-saturated, or per-case time is dominated by program runtime rather than overhead. Guard: a benchmark replaying one problem with N cases, plus a test that time-limit verdicts do not flip under the window.

---

### PERF-002 — Cached test bundle is fully re-hashed and re-read on every job

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `workers/judge/internal/adapter/outbound/testcase/official_loader.go:61` (`OfficialLoader.Load`) |
| **Tags** | needs-measurement |

**Problem**
On every job, even with a warm cache, the loader reads the version file, SHA-256-hashes the entire `bundle.zip`, then reads every `.in` and `.out` file into memory. Problem metadata is also fetched over gRPC on each job.

**Performance principle**
Work whose result is invariant between requests (a cached bundle at an unchanged version) should be validated once, not per request.

**Evidence**
- `official_loader.go:72-79` calls `verifiedCachedChecksum` on each cache hit; it runs `sha256File` over the zip (`154-171`, `224-236`).
- `official_loader.go:278-293` and `346-414`: `bundleFromDir` re-reads all files with `os.ReadFile` each time.
- `process_judge_job.go:43` fetches metadata per job; no cache found.
- Limits allow a 64 MiB zip and 128 MiB extracted (`official_loader.go:29-30`); actual sizes are unknown.
- Dev compose gives the worker 0.5 CPU (`docker-compose.yml:357-361`).
- No runtime evidence exists.

**Impact**
Position: critical path, before any code runs. Frequency: per job. Growth: O(bundle size). Blast radius: every official submission on the worker, plus memory per in-flight job. No numbers are claimed; time depends on bundle size and disk.

**Conditions**
Matters when bundles are large (tens of MiB) or CPU is constrained. For small bundles the cost is negligible.

**Counter-evidence**
Looked for an in-memory cache keyed by problem and version: none found. Hashing is a deliberate integrity check against a corrupted shared volume, a reasonable reason to keep some check.

**Why this might not matter**
If typical bundles are a few MiB, the page cache makes the re-read and hash cheap compared with running N sandbox processes; PERF-001 would dominate.

**Recommendation**
Verify the checksum once per (problem, version) per process, or compare only file sizes and modification times on hits, and keep the parsed test cases in a bounded in-memory cache keyed by problem and version. Invalidate when the metadata version changes.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — bounded in-memory cache keyed by (problem, version, checksum) | **preferred** | Removes disk and hash work on the hot path |
| B — verify the checksum on first load and on error only | | Simpler; still re-reads files |
| C — leave as is | | Acceptable if bundles are small |

**Trade-offs**
Memory held by the cache (must be bounded; the dev worker limit is 512 MiB); weaker detection of volume corruption between verifications.

**Validation**
Baseline: `du -sh /cache/testcases/problem_*/bundle.zip` (`safe-on-production`) and a debug timer around `Load`. Expectation: the load stage drops on cache hits. Falsifier: bundles are small and load time is a negligible share of job time. Guard: a metric on loader duration by hit or miss.

---

### PERF-003 — Worker runs one job at a time on a small CPU budget

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | infrastructure |
| **Location** | `workers/judge/internal/adapter/inbound/kafka/judge_job_consumer.go:50` (`NewJudgeJobConsumer`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
A single semaphore bounds total in-flight official jobs per worker. Both compose files default it to 1, the dev worker has 0.5 CPU, while the sandbox has 2 CPU and parallelism 8. Concurrent submissions queue behind each other, each waiting for the whole serial run of PERF-001.

**Performance principle**
A concurrency limit on a shared resource should match the capacity of the bottleneck resource, not sit far below it.

**Evidence**
- `judge_job_consumer.go:50-56,63,220-231`: pool size from `WORKER_POOL_SIZE` (code default 4), enforced by `jobSlots`.
- `docker-compose.yml:337` sets `"1"`; `docker-compose.prod.yml:97` defaults to `1`.
- `docker-compose.yml:218,227-231,357-361`: sandbox 8-way and 2 CPU, worker 0.5 CPU.
- 6 partitions on the jobs topic (`infra/kafka/kafka-init.sh`); one worker container (`docs/codebase/CONCURRENCY.md`).
- Failed jobs sleep 0.5 s, 1 s, 2 s between retries while holding the slot (`judge_job_consumer.go:173-206`).
- No runtime evidence exists.

**Impact**
Position: critical path (queue wait). Frequency: per concurrent submission. Growth: wait grows with arrival rate times per-job time. Blast radius: all users of the worker.

**Conditions**
Matters when more than one submission is in flight (contests, classes) and the sandbox has idle CPU. Production overrides are unknown; compose defaults are assumed.

**Counter-evidence**
The limit may be deliberate to keep timing stable on a small host; the code lets it be raised by env var. No production values seen.

**Why this might not matter**
At low submission rates the queue is empty and only per-job time (PERF-001) matters. A low value may protect timing accuracy.

**Recommendation**
After PERF-001, measure sandbox and worker CPU under a few concurrent jobs and set slots to what the sandbox can run without timing distortion; revisit the 0.5 CPU worker limit, since hashing, file reads and JSON handling share it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — tune `WORKER_POOL_SIZE` and CPU from measurements | **preferred** | Configuration only |
| B — add worker replicas | | Needs a lock for the shared testcase cache (see COR-001) |

**Trade-offs**
More concurrent runs can perturb CPU-time measurements and raise memory use; replicas need cache coordination.

**Validation**
`docker stats judge_worker judge_sandbox` during several parallel submissions (`safe-on-production`). Expectation: if CPU is mostly idle, queue wait falls with more slots. Falsifier: CPU is already saturated at one job.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-004 | Low | High | P3 | `services/submission/internal/adapter/outbound/outbox/relay.go:30` | Outbox relay polls every 2 s (`container/app.go:82`), so each submission waits up to that interval before reaching Kafka; messages are published one at a time with a synchronous producer. Constant cost, not tied to test count. |

### Considered and not reported

| Path | Evidence checked | Discard reason | Revisit when |
|:--|:--|:--|:--|
| Result persistence | `submission_result.go:104-125`, `apply_judge_result.go:83-101` | Single batch insert in one tx; no N+1 | Result rows carry large outputs and transaction time shows in Postgres stats |
| Default per-case limits | `go_judge_client.go:343-354` | Official runs use 2 s CPU and 256 MiB defaults per case; bounded | Per-problem limits are introduced |
| Kafka retry backoff | `judge_job_consumer.go:173-206` | Only on failures; bounded sleeps | Failure rate is high |

### Adjacent findings — outside performance scope

### COR-001 — Testcase cache refresh has no inter-process lock

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Low |
| **Location** | `workers/judge/internal/adapter/outbound/testcase/official_loader.go:137` |

**Problem** The refresh does `RemoveAll(cacheDir)` then `Rename(tmpDir, cacheDir)`; a concurrent worker or an in-flight read on the shared volume could see a missing directory.
**Evidence** `official_loader.go:137-141`; `docs/codebase/CONCURRENCY.md` also flags it for multi-replica deployments.
**Impact** Low today: compose runs one worker container, so a concurrent refresh needs more replicas or overlapping jobs for one problem.
**Recommendation** Use a per-problem lock, or swap through a versioned directory name.
**Trade-offs** Slightly more cache layout complexity.
**Validation** A concurrent-load test of the loader.
**Would need** A correctness-focused test pass if replicas are planned.

---

## 8. Prioritized action plan

### P1 — High priority
PERF-001.
### P2 — Medium priority
PERF-003, PERF-002.
### P3 — Optimization opportunity
PERF-004.

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P1 | Medium | Dominant term, matches the symptom |
| 2 | PERF-003 | P2 | Low (config) | Sequenced after PERF-001 so tuning uses the new profile |
| 3 | PERF-002 | P2 | Low to Medium | Fixed per-job cost; check bundle sizes first |
| 4 | PERF-004 | P3 | Low | Cheap but small |

**If only one thing is done:** add per-stage timing, then run official test cases in small concurrent windows (PERF-001).

---

## 9. Validation plan

### PERF-001
- **Baseline:** per-job judge time against `test_count` from logs — `safe-on-production`.
- **Change:** window K.
- **Measurement:** end-to-end job time for one fixed problem and submission.
- **Expectation:** decreases for accepted submissions with many cases; magnitude not claimed.
- **Falsifier:** no change, or time-limit verdicts flip.
- **Guard:** benchmark and a verdict-stability test.

### PERF-002
- **Baseline:** bundle sizes and `Load` timing.
- **Commands:**
  - `safe-on-production` `du -sh /cache/testcases/problem_*/bundle.zip` — find bundle sizes.

### PERF-003
- **Commands:**
  - `safe-on-production` `docker stats judge_worker judge_sandbox` — see CPU headroom under concurrent submissions.

### Instrumentation gaps to close first
No timing exists for metadata fetch, bundle load, compile, each `/run` call, or result publish. Add per-stage duration metrics before changing anything.

---

## 10. Machine-readable output

See `review.json` alongside this report.

## 11. Notes on this review
- No runtime metric was estimated; the only derivation (N cases = N calls) is labelled where used.
- Nothing from the repository was executed.
