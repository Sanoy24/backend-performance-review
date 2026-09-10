# Docker

Load after `infrastructure/resources.md`. This file contains only what that file does not
give you: container-runtime-level mechanisms — signal handling, filesystem behavior, and
default resource enforcement — that determine whether the category file's assumptions
(graceful shutdown, enforced limits) actually hold for a given container.

---

## 1. Detection signals

A `Dockerfile`, `docker-compose.yml`/`compose.yaml`, `.dockerignore`, or CI/CD steps
invoking `docker build`/`docker run`/`docker compose`.

---

## 2. What differs from the general resources baseline

### PID 1 signal handling determines whether graceful shutdown actually happens

`infrastructure/resources.md` §2 assumes graceful shutdown is implemented and the
termination grace period exceeds the longest request. Whether a shutdown signal
(`SIGTERM`) is actually *received* by the application in a form it can act on depends on a
container-runtime-specific detail: **the process running as PID 1 inside a container has
different default signal handling than PID 1 on a normal host** — many runtimes and
languages do not install default signal handlers for PID 1 the way they would for any other
process, so `SIGTERM` can be silently ignored, or a shell wrapping the actual application
(a common pattern: `CMD ["sh", "-c", "node app.js"]`) can absorb the signal instead of
forwarding it to the real process. A container without an explicit init process (Docker's
`--init` flag, or an embedded init such as `tini`) and without the application itself
handling PID-1 signal semantics correctly is a concrete, checkable reason graceful shutdown
silently does not happen despite being implemented in application code — the grace period
`infrastructure/resources.md` describes elapses uselessly if the signal never reaches
anything that acts on it.

### Zombie process accumulation is a related, distinct consequence of the same PID 1 gap

A process running as PID 1 is also responsible for reaping (waiting on) any child processes
it spawns; without that reaping, exited child processes accumulate as zombies. This matters
specifically for applications that spawn subprocesses (a worker forking children, a shell
invoking helper commands) inside a container with no init process — zombie accumulation is
usually a slow leak of process-table entries rather than an immediate failure, which is why
it is easy to miss in a short-lived load test but real under sustained production uptime.

### Layer caching and image size affect scheduling-time latency, not steady-state runtime cost

Image layers are cached and reused across builds and, separately, an already-pulled image
layer does not need to be re-pulled on a node that already has it. This is a **startup-time**
cost, not a request-time one: it feeds directly into `infrastructure/resources.md` §2's point
that scale-up is not instant — a large image, or one with poor layer-caching hygiene
(invalidating cached layers on every build with a change early in the `Dockerfile`), adds to
image-pull time on a node that does not already have it cached, which lengthens the
scale-up and deploy path specifically, with no effect on requests once the container is
running.

### The storage driver's copy-on-write behavior costs first-write latency inside the writable layer

A container's own filesystem (outside any mounted volume) is a copy-on-write layer over the
read-only image layers, managed by a storage driver (commonly `overlay2`). **The first write
to a file that exists in a lower (image) layer requires copying that file into the writable
layer before the write can proceed** — a real, one-time cost per file, not an ongoing
per-write overhead once a file has already been copied up. This matters specifically for
workloads that write large files into locations that exist in the base image (rather than
into a mounted volume or a path created at runtime), and is a distinct concern from
`infrastructure/resources.md` §6's point about ephemeral storage exhaustion — this is a
latency question, not a capacity one.

### `HEALTHCHECK` is a distinct mechanism from an orchestrator's own probes

A Dockerfile-level `HEALTHCHECK` instruction is evaluated by the Docker engine itself (or
Swarm, where used) and is independent of any orchestrator-level probes (Kubernetes'
`livenessProbe`/`readinessProbe`, covered generically in `infrastructure/resources.md` §3 and
Kubernetes-specifically in `technology/kubernetes.md`). Running both a `HEALTHCHECK` and
orchestrator probes means the same underlying endpoint or check can be hit by two independent
mechanisms on two independent schedules — worth accounting for in the "probe cost × replicas"
reasoning the category file already names, since a `HEALTHCHECK` is easy to overlook when
counting probe-driven load.

---

## 3. Diagnostics

- **`docker stats`** — live CPU, memory, and I/O usage per container against configured
  limits. `safe-on-production` (read-only).
- **`docker inspect`** — confirms whether `--init` is set, what the actual `ENTRYPOINT`/`CMD`
  resolves to (to check for a shell wrapping the real process), and configured resource
  limits as actually applied, not merely as declared in a compose file that may not have been
  the one used to start the running container. `safe-on-production`.
- **`docker top` / a process listing inside the container** — confirms actual PID 1 and
  whether zombie processes (state `Z`) are accumulating. `safe-on-production`.
- **Sending a test `SIGTERM` and observing the container's actual shutdown behavior and
  timing**, in a non-production environment — the direct way to confirm graceful shutdown
  works as intended rather than inferring it from code alone. `not-safe-on-production` as
  stated; the equivalent check against a running production container (without deliberately
  terminating it) is inspecting logs from the *last* real deploy's shutdown sequence instead,
  which is `safe-on-production`.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| Deploys or restarts are slow to actually stop old containers, or requests are dropped mid-shutdown despite a configured grace period | No init process handling `SIGTERM`; a shell-wrapped `CMD` absorbing the signal instead of forwarding it |
| Process-table exhaustion or slow resource leak under long-running containers that spawn subprocesses | No init process reaping zombie children |
| Scale-up or deploy latency is dominated by image pull time on new nodes | Large image size or poor layer-caching hygiene, not a request-time cost |
| A specific write-heavy operation is slow the first time but fast on repeat | Copy-on-write cost for a first write to a file inherited from an image layer, resolved once the file exists in the writable layer |
| A health check appears to run twice as often as configured, or at an unexpected cadence | Both a Dockerfile `HEALTHCHECK` and an orchestrator-level probe independently hitting the same endpoint |

---

## 5. Configuration worth checking, and what it trades

- **`--init` (or an embedded init such as `tini`).** Trades a small amount of added process
  overhead for correct signal forwarding and zombie reaping — close to free, and its absence
  is a common, easily-missed gap rather than a deliberate choice in most cases.
- **`ENTRYPOINT`/`CMD` shape** (exec form `["node", "app.js"]` versus shell form
  `"node app.js"`). Exec form runs the application directly as PID 1; shell form runs a shell
  as PID 1 with the application as its child, which reintroduces the signal-forwarding
  question even with `--init` unless the shell itself forwards signals correctly.
- **Layer ordering in the `Dockerfile`.** Trades build-cache effectiveness (ordering
  rarely-changing layers first) against how quickly a change is reflected — no correctness
  trade, purely a build- and deploy-latency one.
- **Volume mounts for write-heavy paths**, versus writing into the container's own writable
  layer. Trades the operational overhead of managing a volume for avoiding both the
  copy-on-write cost above and the ephemeral-storage-exhaustion risk
  `infrastructure/resources.md` §6 names.

---

## 6. Version differences worth knowing

Default signal-handling and init behavior have changed across Docker Engine versions —
`--init` itself was added in a specific Docker release and is not available in materially
older engine versions, meaning a container relying on its absence to justify a
shell-form `CMD` may simply predate the flag rather than reflecting a considered choice.
Storage-driver defaults have also changed (`overlay2` superseding older drivers as the
default), which changes some of the copy-on-write specifics above depending on the deployed
engine version. Confirm the deployed Docker Engine version before assuming current defaults
apply.

---

## 7. What this file does NOT cover

- Multi-stage build design and final image size optimization as a build-time concern —
  relevant to the layer-caching/pull-time point above but not covered in its own right here.
- Docker Compose's own orchestration behavior (`depends_on`, restart policies) beyond what is
  implied by the resource and signal-handling points above.
- Docker Swarm as an orchestrator — see `technology/kubernetes.md` for the equivalent
  orchestrator-level reasoning under Kubernetes specifically; Swarm's own mechanisms are not
  modeled here.
- Registry pull performance and caching infrastructure (a registry mirror, pull-through
  cache) as a deployment-pipeline concern distinct from a running container's behavior.
- Rootless Docker and container security/isolation mechanisms (seccomp, AppArmor, user
  namespaces) — a security topic, out of this skill's stated scope.
