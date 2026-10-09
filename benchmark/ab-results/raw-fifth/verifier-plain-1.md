# Performance Review: github-signature-verifier

## Scope and context

The repository is small: one AWS Lambda handler (`src/index.js`, 90 lines), an example consumer (`src/example.js`), a dev-only test-event generator (`src/create-test-event.js`), and Terraform (`terraform/*.tf`) that deploys the verifier as a 128 MB, `nodejs4.3`, 30 s-timeout Lambda.

The work per request is tiny. The function computes one HMAC-SHA1 over the webhook body and compares it to the header. Computing the HMAC takes microseconds. Almost all of the latency, cost and failure risk comes from what surrounds it: a network round trip with KMS decryption on every call, a synchronous Lambda-to-Lambda hop, and logging the whole payload. The findings below are ordered by impact.

| # | Finding | Where | Severity |
|---|---------|-------|----------|
| 1 | Secret fetched and KMS-decrypted from SSM on every invocation, with no caching | `src/index.js:24-39`, `75` | High |
| 2 | Verification is a separate Lambda called synchronously by every consumer | `src/example.js:12-36`, README "Usage" | High |
| 3 | Full event (webhook body) and context logged on every invocation | `src/index.js:66-67` | Medium |
| 4 | SDK clients created per call, no HTTP keep-alive, no SDK timeout or retry tuning | `src/index.js:26`, `src/example.js:15`, `terraform/lambda.tf:14-15` | Medium |
| 5 | Runtime and sizing: `nodejs4.3`, 128 MB, full `aws-sdk` v2 loaded at cold start | `terraform/lambda.tf:13-15`, `src/index.js:3` | Medium-Low |
| 6 | Minor CPU and allocation waste on the hot path | `src/index.js:15`, `45`; `src/example.js:19`, `33` | Low |

---

## 1. Secret fetched and decrypted from SSM on every invocation (High)

**Where:** `src/index.js:24-39` (`getSecret`), called unconditionally at `src/index.js:75` for every valid request.

**Problem.** Every request calls `ssm.getParameters({ Names: ['GitHubWebhookSecret'], WithDecryption: true })`. That means:

- **Latency:** each request makes an HTTPS call to SSM, which in turn makes a KMS Decrypt call. This usually adds about 20-100 ms on a warm container. On a cold container it adds much more, because the TLS handshake and credential resolution happen inside the request (see #4). The HMAC itself takes microseconds, so this one call is by far the largest part of the function's run time.
- **Throughput ceiling and throttling:** SSM `GetParameter(s)` has a fairly low default per-account, per-region TPS limit with standard throughput, and KMS Decrypt has its own shared quota. Each webhook delivery uses one call from both quotas. The quotas are shared with everything else in the account. A burst of webhooks, such as a busy org, a mass re-delivery, or many repos pushing at once, can therefore cause `ThrottlingException`. The SDK then retries with backoff, which adds latency. If the retries run out, the handler returns **500** (`src/index.js:84-87`), so GitHub sees failed deliveries even though nothing is wrong with the signatures.
- **Cost:** you pay for the extra Lambda duration on every request (twice, see #2), and for SSM higher-throughput or KMS requests if those are enabled.
- **Availability coupling:** an SSM or KMS slowdown makes every webhook fail, even though the secret almost never changes.

**Fix.**
1. Cache the secret in module scope so warm invocations reuse it. Keep the promise itself in the cache so that concurrent cold-start callers share a single fetch:
   ```js
   const ssm = new AWS.SSM();             // created once, see #4
   const TTL_MS = 5 * 60 * 1000;
   let cached = null;                      // { promise, expires }

   function getSecret() {
     const now = Date.now();
     if (cached && cached.expires > now) return cached.promise;
     const promise = ssm.getParameter({ Name: 'GitHubWebhookSecret', WithDecryption: true })
       .promise()
       .then(d => d.Parameter.Value);
     cached = { promise, expires: now + TTL_MS };
     promise.catch(() => { cached = null; }); // don't cache failures
     return promise;
   }
   ```
2. Pick a TTL (for example 5-15 minutes) that matches how fast a secret rotation has to take effect. To support rotation without a gap, accept either the current or the previous secret during a rotation window, or on a signature mismatch refetch once and re-check, but only if the cached secret is older than about 60 s, so attackers can't use mismatches to force refetches.
3. An alternative is the AWS Parameters and Secrets Lambda Extension, which provides a local cache with a TTL. Another option is to inject the secret at deploy time as a KMS-encrypted environment variable and decrypt it once per container.
4. Use `getParameter` (singular) instead of `getParameters`, since only one name is fetched. This is mostly for clarity; the request cost is the same.

**Expected impact:** warm-path latency drops from tens of milliseconds to sub-millisecond for the verification logic. SSM/KMS calls drop from one per request to roughly one per container per TTL. The throttling-driven 500s go away.

---

## 2. Verification runs as a separate, synchronously invoked Lambda (High, architectural)

**Where:** `src/example.js:12-36` and the README's "Usage" section. Consumers are told to call `lambda.invoke({ InvocationType: 'RequestResponse', Payload: JSON.stringify(event) })` and wait for the result.

**Problem.** Every webhook goes through two Lambdas in series:
- **Double billing and double concurrency:** the consumer Lambda sits idle and keeps billing while the verifier runs. Each webhook therefore takes two concurrent executions out of the account's concurrency pool. The verifier's run time includes the SSM call from #1, so the consumer is billed for that wait as well.
- **Added latency:** the Lambda Invoke API adds a network round trip and invoke overhead (typically 10-50 ms warm). If the verifier container is cold, its cold start comes on top of the consumer's own cold start. GitHub times out webhook deliveries after 10 s, and a stack of two cold starts plus SSM/KMS plus retries brings this path much closer to that limit than it needs to be.
- **Payload amplification:** the whole API Gateway event is serialized (`JSON.stringify(event)`, `example.js:19`), sent over the network, parsed by Lambda, and then logged again by the verifier (#3). That includes the webhook body (GitHub payloads can be hundreds of KB, up to 25 MB) and all request context. Invoke payloads are limited to 6 MB, so the largest webhooks fail outright.
- **Single shared bottleneck:** every consumer in the account funnels through one function. If that function hits reserved concurrency, throttling, or a cold-start storm, every webhook consumer fails at once.

The stated reason for the hop is that consumers shouldn't need access to the secret. That is a valid design goal, but it doesn't require a runtime Lambda-to-Lambda call on the hot path.

**Fix, in order of preference:**
1. **Move verification to API Gateway with a Lambda authorizer**, or a REQUEST authorizer with result caching disabled because the body differs per request. Note that REST API authorizers can't see the request body. The usual pattern for body-based HMAC is therefore option 2 or 3 below.
2. **Ship verification as a shared library or Lambda Layer** (an `npm` module or layer exposing `verify(event)`). Each consumer verifies in-process using the module-scope-cached secret from #1. Scope each consumer's IAM to read only `GitHubWebhookSecret`. If the goal is to keep the secret away from consumers, use one dedicated "ingest" Lambda that verifies the request and then publishes to SNS, SQS or EventBridge, so the consumers never see raw webhooks.
3. **Make the verifier the front door** (API Gateway -> verifier -> async fan-out to consumers via EventBridge/SNS/SQS, or async `InvocationType: 'Event'`). This keeps the secret in one place, removes the synchronous hop, and lets the verifier return 202 to GitHub quickly.

If the current design has to stay for now, at minimum:
- Send only the fields the verifier needs, `{ headers: { 'X-Hub-Signature': ... }, body }`, rather than the whole event.
- Create the `AWS.Lambda` client once at module scope and enable keep-alive (#4).
- Consider provisioned concurrency for the verifier if cold-start stacking shows up in p99 latency.

**Correctness note found while reviewing:** `example.js:17` invokes `FunctionName: 'githubSignatureVerifier'`, but Terraform deploys `function_name = "github-signature-verifier"` (`terraform/lambda.tf:9`). As written, the example call will fail with `ResourceNotFoundException`, which surfaces as a 500. Also, the success branch in `example.js:53-55` never calls `callback`. The consumer then runs until the event loop drains, or until it times out, and returns `null`.

---

## 3. Entire event and context logged on every invocation (Medium)

**Where:** `src/index.js:66-67` (`console.log(event); console.log(context);`).

**Problem.**
- The event includes the full webhook body. GitHub push and PR payloads are often 10-200 KB and can be several MB. `console.log` of a large object runs `util.inspect`, which is synchronous and CPU-bound. On a 128 MB Lambda, which gets a small CPU share (#5), this adds noticeable milliseconds. It can also print truncated `[Object]` output, which makes the log less useful for debugging.
- CloudWatch Logs ingestion is billed per GB. If every webhook is logged once by the verifier and possibly again by consumers, log ingestion can easily become the biggest cost line for this function.
- It also writes the request signature, and in many setups other sensitive headers, to logs. This is a security concern rather than a performance one, but it's another reason to remove the logging.

**Fix.** Log a compact structured line instead: delivery ID (`X-GitHub-Delivery`), event type (`X-GitHub-Event`), body length, the outcome (200/400/401/500), and the elapsed time. Drop the `context` dump completely. If full payload logging is ever needed for debugging, put it behind a `LOG_LEVEL=debug` environment variable.

---

## 4. SDK clients created per call; no keep-alive; no client timeouts or retry tuning (Medium)

**Where:**
- `src/index.js:26` creates `new AWS.SSM()` inside `getSecret()` on every invocation.
- `src/example.js:15` creates `new AWS.Lambda()` inside `validateSignature()` on every invocation.
- No `httpOptions.agent` with `keepAlive`, and `AWS_NODEJS_CONNECTION_REUSE_ENABLED` is not set (it isn't in `terraform/lambda.tf`, which defines no `environment` block).
- No `httpOptions.timeout` or `connectTimeout` and no `maxRetries`, while the Lambda timeout is 30 s (`terraform/lambda.tf:15`).

**Problem.**
- With aws-sdk v2 defaults, Node's HTTP agent doesn't keep connections alive. Every SSM call, and every Lambda invoke in the consumer, therefore opens a new TCP and TLS connection, which costs about 1-3 extra round trips, often 10-40 ms. Creating the client per call also repeats config resolution and some setup work.
- No client-side timeouts are set, and the SDK retries throttled or failed calls with exponential backoff. When SSM is slow, one request can hang for a long time, up to the 30 s Lambda timeout. Meanwhile it holds concurrency in both the verifier and the waiting consumer (#2), so the whole chain is billed for the wait. GitHub gives up after 10 s anyway, so any time spent past about 10 s is wasted.

**Fix.**
- Create clients once at module scope:
  ```js
  const https = require('https');
  const agent = new https.Agent({ keepAlive: true });
  const ssm = new AWS.SSM({ httpOptions: { agent, connectTimeout: 1000, timeout: 2000 }, maxRetries: 2 });
  ```
  Or set the environment variable `AWS_NODEJS_CONNECTION_REUSE_ENABLED=1` in Terraform (supported by aws-sdk >= 2.463).
- Do the same for `AWS.Lambda` in consumers.
- Lower the Lambda `timeout` to something close to the realistic worst case, about 5-10 s, so stuck invocations fail fast instead of burning 30 s of concurrency.

---

## 5. Runtime and sizing: nodejs4.3, 128 MB, full aws-sdk v2 (Medium-Low)

**Where:** `terraform/lambda.tf:13-15`, `src/index.js:3`, `src/package.json:20`.

**Problem.**
- **`runtime = "nodejs4.3"`** has been end-of-life for years, and AWS no longer allows creating or updating functions on it. In practice the next deploy will fail. Performance-wise, Node 4 has a much slower V8, no native async/await, and older OpenSSL. Modern runtimes (Node 18/20/22) are clearly faster for both cold start and steady state.
- **`memory_size = 128`**: Lambda allocates CPU in proportion to memory, so 128 MB gets a small fraction of a vCPU. Cold start (loading the SDK), TLS handshakes, and `util.inspect` of large events (#3) are all CPU-bound and slow at this size. For a short-running function, 256-512 MB often finishes fast enough that the cost per request stays the same or drops. Measure it, for example with AWS Lambda Power Tuning, rather than guessing.
- **`require('aws-sdk')`** loads the whole v2 SDK, all service models, at cold start. That typically adds hundreds of milliseconds at 128 MB. Also note that the Terraform `archive_file` only zips `src/index.js` (`lambda.tf:3`), so the code relies on the runtime-provided SDK. Node 18+ runtimes ship only SDK v3, so a runtime upgrade requires migrating to v3 anyway.

**Fix.**
- Move to `nodejs20.x` or `nodejs22.x`.
- Replace `aws-sdk` v2 with the modular `@aws-sdk/client-ssm` (v3). That loads only the SSM client and reuses connections by default. Or use the Parameters and Secrets extension (#1), and then no SDK is needed on the hot path at all.
- Benchmark memory at 128, 256 and 512 MB and choose the lowest p95 per unit cost. Consider arm64 (`architectures = ["arm64"]`) for lower cost per ms.
- If p99 latency matters and traffic is steady, consider provisioned concurrency. With #1 and #2 fixed, it's usually unnecessary.

---

## 6. Minor hot-path inefficiencies (Low)

These are small but cheap to fix, and most can be folded into the changes above:

- **`src/index.js:15`**: the regex literal is re-evaluated on each call. V8 caches regex literals, so the cost is negligible. However, the pattern isn't anchored at the end (`/^sha1=[0-9a-f]{40}/`), so arbitrarily long header values are accepted and later string-compared. Anchor it with `$`. This is a robustness fix more than a speed fix.
- **`src/index.js:45-46`**: `split('=')` allocates an array, and the comparison uses `===`. Use `header.slice(5)` and `crypto.timingSafeEqual(Buffer.from(a, 'hex'), Buffer.from(b, 'hex'))` after a length check. `===` isn't a performance problem, but it allows timing attacks, and the fix costs the same.
- **`src/index.js:49-61`**: `respond()` calls `JSON.stringify` on constant messages for every request. Precompute the four possible response bodies as constants. This is trivial, but free.
- **`src/example.js:19` and `33`**: the full event is stringified and the response re-parsed for each call (see #2). Send a minimal payload instead.
- **`src/create-test-event.js:60` and `66`**: the body is stringified twice. This is a dev-only script, so it doesn't matter for production; it's noted only for completeness. Reusing `body` also guarantees the signed bytes and the emitted bytes are identical.

---

## Recommended order of work

1. **Cache the secret in module scope with a TTL** (#1), and **create SDK clients once with keep-alive and tight timeouts** (#4). Together this is about 20 lines in `src/index.js` and removes most per-request latency and all throttling risk.
2. **Replace payload logging with a compact structured log line** (#3).
3. **Upgrade the runtime to Node 20/22 with SDK v3, re-tune memory, and lower the timeout** (#5). This is required anyway, because `nodejs4.3` can't be deployed.
4. **Remove the synchronous Lambda-to-Lambda hop** (#2), using a shared verify library/layer or a verifier-as-front-door with async fan-out. This is the biggest structural improvement: it halves the Lambda executions per webhook and removes stacked cold starts.
5. Do the small cleanups from #6 alongside the changes above.

**How to measure:** before and after each change, record p50/p95/p99 `Duration` and `Init Duration` from the Lambda REPORT lines, plus the `Throttles` and `Errors` metrics, SSM `ThrottlingException` counts, and CloudWatch Logs `IncomingBytes` for the log group. Use a burst load test that replays signed events from `create-test-event.js`, for example 50-200 concurrent requests, so the SSM throttling behavior in #1 actually shows up.
