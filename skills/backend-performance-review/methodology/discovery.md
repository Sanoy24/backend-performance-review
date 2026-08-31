# Discovery

Goal of this phase: build an accurate map of *what exists* before forming any opinion
about what is slow. Opinions formed before the map are opinions about a codebase you
imagined.

Discovery produces four things:

1. A stack inventory (languages, frameworks, datastores, caches, brokers, infrastructure).
2. An architecture sketch (services, entry points, shared resources, external calls).
3. An **observability inventory** — what evidence exists at all.
4. A list of layers that are *absent*, so later phases can skip them.

---

## 1. Stack inventory

Run the accelerator first if available:

```
python ${CLAUDE_SKILL_DIR}/scripts/detect_stack.py <repo-path>
```

It is a convenience, not a dependency. If it is missing, errors, or the repo has an
unusual layout, do it by hand.

Its output attributes every match to the specific file it came from. Treat a signal marked
`"weak_evidence": true` (every match for it landed only in a non-manifest YAML file — CI
config, k8s values, arbitrary docs) as an unverified lead, not a detected technology: check
the files it actually names in `matched_on[].files` before loading that technology's
reference or writing a finding that assumes it is in use. A signal with no `weak_evidence`
field matched inside a real manifest, lockfile, or its own filename — normal-strength
evidence, still a declared dependency rather than proof of runtime use.

### Manifests to read

| Ecosystem | Files |
|:--|:--|
| Node | `package.json`, lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`) |
| Python | `requirements*.txt`, `pyproject.toml`, `poetry.lock`, `Pipfile`, `setup.py`, `uv.lock` |
| Go | `go.mod`, `go.sum` |
| Rust | `Cargo.toml`, `Cargo.lock` |
| JVM | `pom.xml`, `build.gradle`, `build.gradle.kts`, `gradle.lockfile` |
| .NET | `*.csproj`, `*.fsproj`, `*.sln`, `packages.lock.json` |
| PHP | `composer.json`, `composer.lock` |
| Ruby | `Gemfile`, `Gemfile.lock` |

**Read the lockfile, not just the manifest**, when you need to know whether a dependency
is actually present and at what version. A manifest range tells you what is permitted; the
lockfile tells you what ships.

### Signals beyond manifests

Manifests miss things. Also check:

- `Dockerfile`, `docker-compose.yml`, `compose.yaml` — services the app talks to, base
  image, resource hints, entrypoint and worker counts.
- Kubernetes manifests, Helm charts — replicas, requests/limits, probes, HPA.
- Terraform / CloudFormation / CDK / Pulumi — managed datastores, instance classes,
  capacity modes.
- CI config — how the app is built and started; sometimes the only place worker counts
  appear.
- `Procfile`, `Makefile`, `justfile`, entrypoint scripts — the real production command
  line, including worker/thread flags.
- Connection-string schemes anywhere in config: `postgresql://`, `mongodb+srv://`,
  `redis://`, `amqp://`, `bolt://`. **Note the scheme, never the credentials.**
- Migration directories — the schema is the most reliable statement of the data model.
- ORM/ODM configuration — lazy vs eager loading defaults, pool settings, statement caching.

Resolve each datastore to a **category** before looking for engine specifics
(`databases/universal.md`). An unrecognized dependency with a `://` scheme and CRUD-shaped
usage is still classifiable.

### Version matters

Record versions for anything you will make a claim about. Performance-relevant behavior
changes across major versions — pool defaults, planner behavior, GC defaults, driver
async support. If you cannot determine a version, say so rather than assuming the latest.

---

## 2. Architecture sketch

You need enough structure to answer "where does a request go", not a full design doc.

**Find the entry points.** Search for route registration, HTTP handler decorators, gRPC
service implementations, GraphQL resolvers, message consumers, scheduled jobs, and CLI
commands. These are the roots of every path you will analyze.

**Trace one representative request end to end** before analyzing anything. Handler →
service/business layer → data access → datastore, plus any external calls. This single
trace usually reveals the architecture's real shape faster than reading the directory
structure.

**Identify shared resources.** These are where local problems become global ones:

- Connection pools (per process? per worker? global?)
- The event loop, in single-threaded runtimes
- Global locks, mutexes, semaphores, singletons with state
- The primary database, especially a single writer
- A shared cache instance
- Rate limiters and circuit breakers with shared state
- A `tenant_id`/`workspace_id`/`account_id`/`org_id` column, subdomain- or path-based
  tenant routing, or any other sign that one deployment serves multiple customers who do
  not trust each other — if present, load `distributed/multi-tenancy.md`

**Identify external dependencies** — third-party APIs, internal services, object storage,
auth providers. Each is a latency source you do not control and, without a timeout, a
failure mode you inherit.

**Note the deployment model**: single process, multi-process, containers with replicas,
serverless. It determines how concurrency and pooling behave and whether per-instance
reasoning is even valid.

---

## 3. Observability inventory — do this before analysis, not after

This step sets the ceiling on the confidence of every finding in the review. Doing it last
means discovering, after all the work, that nothing could have been `Confirmed`.

Search for:

| Evidence | Where it hides |
|:--|:--|
| Metrics | Prometheus client libraries, `/metrics` endpoints, StatsD, OpenTelemetry metrics, CloudWatch calls |
| Tracing | OpenTelemetry SDK, Jaeger, Zipkin, Datadog APM, `traceparent` propagation |
| Structured logs | Logging config, whether request duration is logged at all |
| Benchmarks | `bench*`, `*_bench.go`, `pytest-benchmark`, JMH, criterion |
| Load tests | k6, Locust, Gatling, JMeter, Artillery, `wrk` scripts |
| Query analysis | Committed `EXPLAIN` output, slow-query config, ORM query logging |
| Profiles | Checked-in flame graphs, `pprof` endpoints (`net/http/pprof`), profiling flags |
| SLOs | SLO/SLA docs, error budgets, alert rules, runbooks |
| Dashboards | Grafana JSON, Datadog monitor definitions as code |

Then classify the repo:

- **Well instrumented** — traces or profiles exist and cover the critical path. `Confirmed`
  findings are achievable; go read the data before speculating.
- **Partially instrumented** — metrics exist but not for the paths in question. Some
  findings can cite config-derived evidence; most cap at `High`.
- **Uninstrumented** — nothing. Every workload-dependent finding caps at `Medium`, and
  "add instrumentation to the critical path" is itself likely a top recommendation, since
  without it no future optimization can be validated.

State which of these three the repo is, in the report's scope section. It tells the reader
how much to trust everything that follows.

---

## 4. Layer gates

Record which layers are present. Absent layers are skipped **silently** — no empty
section, no "N/A: no message broker detected" paragraph.

| Layer | Present if |
|:--|:--|
| API | HTTP/gRPC/GraphQL entry points exist |
| Application | Any non-trivial business logic between entry point and data |
| Data access | Any datastore client, ORM, or raw query |
| Cache | A cache client, or in-process memoization of remote data |
| Distributed | Calls to other services, brokers, or queues |
| Infrastructure | Container, orchestration, or serverless configuration in the repo |
| Observability | Always analyzed — its absence is itself a finding |

For a change-scoped review, gate further: only layers the diff touches, plus the layers
its touched paths call into.

---

## 5. What discovery must not do

- Do not form conclusions. A dependency on Redis is not a finding.
- Do not assume a dependency is used because it is declared. Grep for actual use.
- Do not assume the code you are reading runs in production. Check for feature flags,
  dead routes, deprecated modules, and vendored copies.
- Do not read secret files for content. Note their existence and move on.
- Do not report the stack inventory as if it were analysis. It is input.

---

## Output of this phase

A short internal note you will reuse in the report's scope section:

```
Languages/runtimes:   ...
Frameworks:           ...
Datastores:           ... (category → engine → version if determinable)
Cache / broker:       ... or none
Deployment:           ...
Entry points:         N HTTP routes, N consumers, N scheduled jobs
Shared resources:     ...
Observability:        well instrumented | partially | uninstrumented — with what exists
Layers present:       ...
Layers absent:        ...
Unknowns from discovery: ...
```
