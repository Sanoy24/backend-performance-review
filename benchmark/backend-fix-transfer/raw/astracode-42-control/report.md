# Performance review: official judging is slow on problems with many test cases

Scope: the judge worker path `Kafka job -> ProcessJudgeJobUseCase -> OfficialLoader -> GoJudgeClient -> go-judge`. This is a static read only; nothing was run or measured, so the estimates below are reasoning, not profiling.

## Flow
1. `workers/judge/internal/adapter/inbound/kafka/judge_job_consumer.go` picks up the job.
2. `workers/judge/internal/application/usecase/judge/process_judge_job.go:43-58` fetches test case metadata over gRPC, loads the bundle, then calls `executor.Execute` with `StopOnFirstFailure: true`.
3. `workers/judge/internal/adapter/outbound/execute/go_judge_client.go` compiles once, then runs the test cases.

## P1 (highest): official judging runs test cases strictly one at a time, one HTTP round trip each
- Where: `workers/judge/internal/adapter/outbound/execute/go_judge_client.go:74-79`, with the call site at `workers/judge/internal/application/usecase/judge/process_judge_job.go:57`.
- What: `StopOnFirstFailure` is always true for official jobs, and that forces `currentBatchSize = 1`. The loop at line 79 then makes one blocking `POST /run` (`runBatch`, lines 194-251) per test case, in sequence. The `batchSize = 50` constant at line 18 is therefore never used for official submissions; `docs/codebase/CONCURRENCY.md` confirms only interactive runs batch.
- Cost: with N test cases, wall time is the sum of every case's run time. Each case also pays a full request cycle: JSON marshalling of the request, the HTTP call to go-judge, sandbox setup for one process, and response decoding. The sandbox is configured with `ES_PARALLELISM=8` in `docker-compose.yml`, but a batch size of 1 means at most one sandbox slot is busy per job. Time grows linearly with the test count, and the passing (common, accepted) case is the worst one, because it has to run every test.
- Fix, in order of preference:
  1. Decouple "stop on first failure" from "batch size 1". Run windows of K cases (for example K = 4 to 8, bounded by `ES_PARALLELISM` divided by the number of concurrent jobs) in one request, so go-judge runs them in parallel. After each window, check for a failure and stop. In the worst case this wastes at most K-1 extra runs on a failing submission, in exchange for roughly K times faster accepted runs.
  2. Optionally grow the window adaptively: start at 1 to 2 so obvious failures on the first case are cheap, then widen.
  3. If the first-failure result needs to stay deterministic, pick the lowest-index failing case within a window after it returns. The existing loop at lines 94-117 already walks results in order and returns on the first non-ACCEPTED result, so this needs only a small change.
- Severity: High. This is the direct cause of the reported symptom and scales with test count.

## P2: job concurrency is small and the sandbox is under-used
- Where: `judge_job_consumer.go:49-54` (pool size defaults to 4, overridable with `WORKER_POOL_SIZE`). `jobSlots` at lines 63 and 220-237 caps total in-flight jobs. `docker-compose.yml` runs go-judge with `ES_PARALLELISM=8` and a single worker container.
- What: because each job uses one sandbox slot at a time (P1), four jobs use about four of the eight slots. Under load, submissions queue behind jobs that are themselves running serially, so queueing delay adds to the per-job time. The topic has 6 partitions (`infra/kafka/kafka-init.sh:32`) but only one worker instance consumes.
- Fix: after P1 the parallelism budget should be allocated deliberately. Either raise `WORKER_POOL_SIZE` and set the per-job window so that `pool * window` is at or just below `ES_PARALLELISM`, or raise `ES_PARALLELISM` to match the cores available. Consider running more than one worker replica, noting the cache race below.
- Severity: Medium.

## P3: the compiled executable and test data are re-sent per case
- Where: `go_judge_client.go:208-234` (`runBatch`).
- What: each test case's stdin is embedded in the request body as `Content`, so it is serialised to JSON and sent over HTTP again for every case. For large inputs this adds marshal, transfer and parse cost on top of P1. go-judge is already configured with `ES_SRC_PREFIX=/cache/testcases` and mounts the testcase cache volume read-only, which suggests files could be referenced by path instead of shipped inline, but the client never uses it.
- Fix: reference the cached `.in` files with go-judge's local-file input option (`Src` under the `ES_SRC_PREFIX` path) instead of inline content. Use the same approach for stdin that exceeds a size threshold only, if you want to limit the change.
- Severity: Medium-low, larger for problems with big inputs.

## P4: the whole test set is read into memory on every job, and the cache is re-hashed each time
- Where: `workers/judge/internal/adapter/outbound/testcase/official_loader.go:209-216` calls `verifiedCachedChecksum` (lines 291-309), which runs `sha256File` over `bundle.zip` (up to 64 MiB) on every cache hit. Then `bundleFromDir` -> `loadFromDir` (lines 415-430, 483-551) re-lists the directory, applies a regex to each name, and reads every `.in` and `.out` file into strings with `os.ReadFile` (lines 533-548), up to the 128 MiB extracted limit, per job.
- What: this is paid on every submission even when the cache is warm, before any code runs. It is also repeated for each of the concurrent jobs, so memory use is pool size times bundle size. `ensureOutputLimitForExpected` (`go_judge_client.go:356-372`) additionally scans all cases again.
- Fix: keep a small in-process cache keyed by `(problem_id, version, checksum)` holding the parsed `[]ExecutionTestCase` (or lazily loaded files). Validate it with the metadata version only, and verify the zip hash once on cache fill rather than on every hit. Consider loading each case's data lazily so a first-case failure does not read the rest.
- Severity: Medium for large bundles; negligible for small ones.

## P5: per-job metadata gRPC call with no caching
- Where: `process_judge_job.go:43` -> `workers/judge/internal/adapter/outbound/problem/grpc_metadata_reader.go:285-340`.
- What: every job makes a gRPC call to the problem service (5 s default timeout) and the response includes a fresh presigned URL that the warm-cache path never uses. It adds a round trip of latency per submission and puts load on the problem service.
- Fix: cache metadata for a short TTL (seconds) keyed by problem id, or have the problem service publish version changes. Keep it short so test data updates still take effect.
- Severity: Low.

## P6: smaller items
- `go_judge_client.go:57-65`: compiled artifacts are kept as cached file IDs but there is no visible cleanup after the run. Check that `DELETE /file/{id}` is issued, otherwise go-judge memory/disk grows over time and degrades throughput. I did not find a delete call in this file. Severity: Low to Medium, needs verification against the go-judge setup.
- `go_judge_client.go:30`: the 120 s client timeout is per request, so with batch size 1 it never trips; once windows are introduced (P1), size it against window length times time limit.
- `go_judge_client.go:53`: `ExecutionRequest` is built without `Limits`, so `normalizeLimits` (lines 343-354) applies the 2 s / 256 MiB defaults to every problem. This is a correctness and latency question rather than a pure performance one: a problem-specific time limit is never used, and slow-failing cases always wait the full default. Confirm intent.
- `official_loader.go:274-277`: cache refresh does `RemoveAll` then `Rename` with no inter-process lock, so with more than one worker replica sharing the volume, a reader can see a missing directory and re-download. `CONCURRENCY.md` already flags this. It becomes relevant when P2 scales out replicas. Fix with a per-problem lock or by renaming into a versioned directory instead of deleting.
- `judge_job_consumer.go:173-207`: retries use sleeps of 500 ms, 1 s and 2 s inside a pool slot, which holds a slot idle during backoff. Minor.
- `domain/output.go:3-17` (`NormalizeOutput`) splits and joins the entire output for every comparison. It is linear and fine at typical sizes, but with 1 MiB-scale outputs a streaming comparison would avoid several large allocations per case. Low.

## Suggested order of work
1. P1: windowed parallel execution with early stop (biggest win, small change in one function).
2. P2: tune pool size and sandbox parallelism together after P1.
3. P4: in-memory parsed testcase cache and hash once per fill.
4. P3: pass test inputs by file reference.
5. P5/P6: metadata cache, file cleanup check, lock for the cache refresh.

## Caveats
- I did not measure anything. P1 is certain from the code; the size of the gain depends on per-case sandbox startup cost, which can be measured by timing one judge job at batch size 1 versus 8 on a problem with many cases.
- I did not find where go-judge file IDs are deleted; this should be checked in the sandbox configuration as well.
