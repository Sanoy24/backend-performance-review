# Serverless: AWS Lambda, Google Cloud Functions, Azure Functions, Vercel, and Netlify compared

Load after `infrastructure/resources.md` and `application/connection-pools.md`.
`infrastructure/resources.md` §5 covers the serverless model generically — cold starts,
per-invocation connections, environment reuse, concurrency limits, and duration-based
billing. This file contains only what that section does not give you: the concrete
divergences between five platforms in exactly the mechanisms §5 describes generically.

**This is a deliberately comparative file, not a single-platform one** (see
`docs/roadmap.md`) — these platforms differ in cold-start mitigation, concurrency-scaling
model, and execution environment in ways that directly change what a review should check.
**Confirm which platform is actually deployed before applying a row from any table below.**

---

## 1. Detection signals

| Platform | Manifest / signal |
|:--|:--|
| **AWS Lambda** | `serverless.yml` (Serverless Framework), `template.yaml`/`AWS::Serverless::Function` (SAM), a `lambda_handler`-shaped export, `aws-lambda`/`@aws-lambda` in dependencies |
| **Google Cloud Functions** | `functions-framework` dependency, a `main()`/`exports.X = (req, res) => ...` shaped export matching the platform's calling convention, `gcloud functions deploy` in CI |
| **Azure Functions** | `host.json`, `function.json` per function, a `[FunctionName(...)]`/`func.HttpTrigger`-shaped binding declaration |
| **Vercel** | `vercel.json`, a file under `api/` matching Vercel's file-based function routing, `@vercel/node`-family dependencies |
| **Netlify** | `netlify.toml`, a file under `netlify/functions/` |

---

## 2. What differs from the serverless baseline, platform by platform

### Warm-instance reuse windows and pre-emptive mitigation differ, and this changes how much cold-start advice actually applies

`infrastructure/resources.md` §5 states that state initialized outside the handler persists
between invocations on the same environment — true on all five, but *how long an environment
stays warm*, and what levers exist to keep it warm deliberately, differ:

- **AWS Lambda** offers **provisioned concurrency** — pre-initialized environments kept warm
  continuously, at a direct cost even when idle — and, separately, **reserved concurrency**,
  which caps (not just guarantees) how many concurrent environments a function can use. These
  are two different knobs solving two different problems (guaranteed warm capacity versus a
  concurrency ceiling) and are easy to conflate when reading a configuration.
- **Google Cloud Functions** (2nd gen, built on Cloud Run) similarly supports a minimum-
  instance setting that keeps a configured number of instances warm.
- **Azure Functions'** cold-start exposure depends heavily on which **hosting plan** is
  configured: the Consumption plan scales from zero and is fully exposed to cold starts; the
  Premium plan keeps pre-warmed instances; the Dedicated (App Service) plan runs on
  always-on infrastructure with materially different cold-start characteristics from the
  other two. Reading which plan a Function App actually uses is a prerequisite for reasoning
  about cold starts on Azure at all — the same code deployed under different plans has a
  different cold-start profile entirely.
- **Vercel and Netlify** functions are typically deployed behind each platform's own routing
  and CDN layer, and — for functions declared as Edge Functions rather than standard
  serverless functions — run in a lighter-weight isolate-based runtime (not a full container),
  which changes the cold-start cost profile substantially (closer to "milliseconds" than
  "seconds") but also changes what is available at runtime (a restricted API surface, no
  arbitrary native dependencies) — confirm which execution mode a given function actually
  uses before applying either cold-start expectation.

### VPC networking changes Lambda's cold-start profile in a way the other platforms' base case does not

`infrastructure/resources.md` §5 names dependency loading and connection establishment as
cold-start drivers generically. **AWS Lambda functions attached to a VPC** (commonly to
reach a private RDS instance or other VPC-only resource) historically carried an additional,
distinct cold-start cost from ENI (Elastic Network Interface) provisioning — a mechanism
specific to how Lambda attaches network interfaces for VPC access, not present for a
non-VPC-attached function or, in the same shape, for the other platforms in this comparison.
Whether a given Lambda function is VPC-attached, and why (usually: private-subnet-only
downstream resources), is worth confirming explicitly rather than assuming every Lambda
pays this cost equally.

### Maximum invocation duration varies by an order of magnitude or more across platforms

`infrastructure/resources.md` §5 does not name a duration ceiling, because it varies enough
across platforms to be technology-specific rather than a general serverless fact:

| Platform | Typical execution-time ceiling shape |
|:--|:--|
| AWS Lambda | Minutes-scale maximum, configurable up to that ceiling |
| Google Cloud Functions | Minutes-scale for HTTP-triggered functions; can differ for other trigger types |
| Azure Functions | Plan-dependent — the Consumption plan has a materially shorter default/maximum than the Premium or Dedicated plans |
| Vercel | Seconds-scale by default for standard serverless functions on most plans; Edge Functions carry their own, typically shorter, limit |
| Netlify | Seconds-scale by default, similarly plan-dependent |

Confirm the actual configured or plan-determined ceiling for the deployed platform and tier
rather than assuming a specific number — these change with pricing-tier and platform updates
and are exactly the kind of detail that goes stale fastest in a static reference. What is
stable across all five: a long-running operation that fits comfortably within one platform's
ceiling can fail outright on another, so a workload being migrated between these platforms
needs this checked explicitly, not assumed to carry over.

### The module-scope/handler split is expressed differently per platform, which changes what "outside the handler" means to check

`infrastructure/resources.md` §5's "reuse across invocations" point depends on correctly
identifying what actually runs once per environment versus once per invocation on each
platform:

- **Lambda**: code outside the exported handler function, at module/file scope, runs once
  per environment. Code inside the handler runs every invocation.
- **Google Cloud Functions**: the same shape — module-scope code outside the exported
  function runs once per instance.
- **Azure Functions**: static/module-scope initialization in the function's class or file
  runs once per instance; the pattern is the same, but static-field initialization semantics
  differ by language runtime (C# static fields versus a Node.js module-scope `const`) and are
  worth confirming for the specific language in use.
- **Vercel and Netlify**: the same module-scope-versus-handler-body split applies, with the
  caveat that Edge Functions' lighter-weight isolate model may have different lifetime
  semantics for module-scope state than a standard Node.js serverless function on the same
  platform — confirm per execution mode, not just per platform.

The mechanism is the same everywhere: module-scope code is the place to put anything meant to
survive across invocations (client connections, SDK initialization). What differs is only the
per-platform and per-language shape of "module scope," which is worth confirming rather than
assuming from one platform's convention.

---

## 3. Diagnostics

- **Cold-start-specific traces or logs** — AWS X-Ray's `Init Duration` field (present
  specifically on cold starts, absent on warm invocations), Google Cloud's Cloud Trace,
  Azure Application Insights' cold-start-adjacent duration metrics, and Vercel's/Netlify's
  own function-invocation logs (which typically flag a cold start explicitly). The direct
  way to confirm cold-start frequency and cost in practice rather than reasoning about it
  from deployment package size alone. `safe-on-production` (read-only).
- **Concurrent-execution metrics** (Lambda's `ConcurrentExecutions`/`Throttles` CloudWatch
  metrics, Cloud Functions' equivalent, Azure Functions' scale-controller metrics) — confirms
  whether a concurrency limit is actually being reached and causing throttling, rather than
  assumed from error reports alone. `safe-on-production`.
- **Duration and memory-utilization metrics per invocation** — the direct input to the
  cost/duration/memory trade-off `infrastructure/resources.md` §5 names; confirms whether
  increasing memory allocation would actually reduce cost by reducing duration for a specific
  function, rather than assuming the relationship holds without checking. `safe-on-production`.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| Latency is bimodal — most invocations fast, a minority much slower, correlating with low traffic periods | Cold starts on a platform/plan with no warm-instance mitigation configured |
| A Lambda function attached to a private VPC resource is disproportionately slower to cold-start than an equivalent non-VPC function | ENI provisioning cost specific to VPC-attached Lambda |
| An Azure Function's cold-start behavior does not match expectations set by documentation for a different plan | Reasoning applied from Consumption-plan behavior to a Premium- or Dedicated-plan deployment, or vice versa |
| A function fails partway through what should be a routine operation, with no application-level error | The operation's actual duration exceeding the platform's or plan's invocation-duration ceiling |
| A connection or client is re-established on every single invocation despite being "initialized once" in the code | The initialization code is actually inside the handler body rather than at true module/file scope for that platform and language |

---

## 5. Configuration worth checking, and what it trades

- **Provisioned/minimum-instance concurrency** (Lambda, Cloud Functions 2nd gen). Trades a
  direct, continuous cost for eliminating cold starts on the provisioned portion of traffic;
  traffic beyond the provisioned level still cold-starts.
- **Reserved concurrency** (Lambda) as a ceiling, distinct from provisioned concurrency as a
  floor — confirm which one a given configuration actually sets before reasoning about its
  effect.
- **Hosting plan choice** (Azure Functions: Consumption vs. Premium vs. Dedicated). Trades
  cost-at-idle for cold-start exposure and duration ceiling — a fundamentally different
  trade than a simple "more memory" or "more instances" knob.
- **Execution mode** (Vercel/Netlify standard function vs. Edge Function). Trades the
  restricted runtime API surface and dependency limitations of an isolate-based edge runtime
  for substantially lower cold-start cost and often lower latency to the caller via broader
  geographic distribution.
- **VPC attachment** (Lambda). Trades the ENI-provisioning cold-start cost for access to
  VPC-only resources; not attaching to a VPC when the function does not need VPC-only
  resources avoids the cost entirely rather than requiring it to be optimized around.

---

## 6. Version differences worth knowing

Cold-start mitigation has been an area of continuous platform-level change on all five of
these: AWS has iterated on Lambda's underlying execution environment (including SnapStart
for specific language runtimes, which changes cold-start cost characteristics for functions
that opt into it) and on VPC networking's cold-start cost specifically; Google Cloud
Functions' 2nd generation (built on Cloud Run) has materially different scaling and
concurrency behavior from 1st generation; Azure's Premium plan's pre-warming behavior has
evolved across platform updates. Confirm the deployed platform generation/tier and any
opted-in mitigation features (which are often not the default) before assuming a specific
cold-start cost profile from general platform knowledge.

---

## 7. What this file does NOT cover

- Edge-network/CDN caching behavior in front of these functions (Vercel's and Netlify's own
  CDN layers, CloudFront in front of Lambda) — a distinct caching topic from the function
  execution model covered here.
- Cost optimization as its own goal, beyond the memory-versus-duration point
  `infrastructure/resources.md` §5 already names generically.
- Event-source-specific behavior (S3 event notifications, EventBridge, Pub/Sub, Azure Event
  Grid) triggering these functions — the trigger's own delivery semantics (at-least-once,
  ordering, batching) are a distinct topic from the function's execution-time behavior.
- Local emulation/testing tooling (SAM CLI local, the Functions Framework's local runner,
  `vercel dev`, `netlify dev`) and any performance differences between local emulation and
  the real deployed platform.
- Container-image-packaged functions (Lambda container images) versus zip-packaged
  functions — a real, checkable divergence in cold-start behavior, but not modeled here in
  the depth the rest of this file gives the platform-level comparison.
