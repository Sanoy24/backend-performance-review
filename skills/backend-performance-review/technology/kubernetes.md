# Kubernetes

Load after `infrastructure/resources.md`. This file contains only what that file does not
give you: the specific mechanisms Kubernetes uses to enforce the limits/requests, probes,
and autoscaling reasoning the category file already covers generically.

---

## 1. Detection signals

A manifest with `kind: Deployment`, `kind: StatefulSet`, `kind: DaemonSet`, or
`apiVersion: apps/v1`; a Helm chart (`Chart.yaml`, a `templates/` directory of manifest
templates); a Kustomize overlay (`kustomization.yaml`); `kubectl` invocations in CI/CD
scripts.

---

## 2. What differs from the general resources baseline

### CPU throttling is a specific cgroup mechanism, not just a general "limits cause throttling" fact

`infrastructure/resources.md` §1 states that exceeding a CPU limit causes throttling.
Kubernetes implements this via the Linux CFS (Completely Fair Scheduler) quota mechanism:
a CPU limit is translated into a quota of CPU time allowed per fixed accounting period
(commonly 100ms). **A container can be throttled within a single period even while the
node has spare CPU capacity available**, because the quota is enforced per-period
regardless of what else is running — a multi-threaded process that briefly needs more than
its quota allows in one 100ms window is throttled in that window even if idle capacity
exists elsewhere on the node. This is the specific, checkable mechanism behind the "p99
latency spikes with unremarkable average CPU" symptom the category file names; the
accounting-period granularity is why bursty, multi-threaded workloads are disproportionately
affected relative to their average CPU usage.

### QoS class, derived from requests and limits, determines eviction order under node memory pressure

Kubernetes assigns every pod one of three QoS classes purely from its requests/limits
configuration: **Guaranteed** (every container's requests equal its limits, for both CPU and
memory), **Burstable** (requests are set but differ from limits, or only some resources have
limits), or **BestEffort** (no requests or limits set at all). Under node memory pressure,
the kubelet evicts BestEffort pods first, then Burstable pods (ordered by how far usage
exceeds requests), and Guaranteed pods last. `infrastructure/resources.md`'s "request equals
limit" row names the isolation trade-off generically; the QoS-class mechanism is *why* that
configuration also buys eviction priority, which is a real, checkable reason to choose it for
workloads where restart is disproportionately costly.

### The Horizontal Pod Autoscaler reacts on a lag, and has its own stabilization behavior

`infrastructure/resources.md` §2 notes that scale-up is not instant. HPA specifically polls
metrics (from `metrics-server` for CPU/memory, or a custom/external metrics adapter for
other signals) on an interval, so there is a real, non-zero delay between a load change and
the HPA deciding to act — beyond the further delay of the new pod actually becoming ready.
HPA also applies a **stabilization window** to scale-down decisions by default, specifically
to avoid thrashing (scaling down, then immediately back up) — this means scale-down is
deliberately slower than scale-up by design, not a misconfiguration, and reading a
deployment's actual HPA object (not just assuming default behavior) is necessary before
concluding the scaling cadence is wrong.

### `startupProbe` exists specifically so slow-starting containers do not need a loosened liveness probe

`infrastructure/resources.md` §3 covers readiness and liveness probes generically. The
Kubernetes-specific mechanism worth checking: `startupProbe` (a distinct probe type) is
designed to hold off liveness checks until a container reports as started, so a slow-starting
application does not need its liveness probe's own timeout loosened to accommodate startup
time — a common workaround that then leaves liveness too permissive for genuine hangs once
the app is running. A Deployment with a slow-starting container but no `startupProbe`
configured, only a loosened `livenessProbe`, is a checkable configuration smell with a
concrete, better-named fix available.

### Service load-balancing mode changes how evenly long-lived connections are actually distributed

`infrastructure/resources.md` §4 names connection pinning with long-lived connections as a
load-concentration risk. Kubernetes Services implement load balancing via `kube-proxy`,
which historically defaults to `iptables` mode (effectively random selection per new
connection, with no awareness of existing connection distribution) or can be configured for
`IPVS` mode (supports several algorithms, including least-connection). Neither mode
rebalances *existing* long-lived connections after they are established — new connections are
distributed according to the configured mode, but a connection made before a scale-up event
stays where it is regardless of mode.

### Namespace-level ResourceQuota and LimitRange are a ceiling above the individual pod's own limits

Beyond a single pod's requests/limits, a namespace can carry a `ResourceQuota` (a hard cap on
total resource consumption across all pods in the namespace) and a `LimitRange` (default or
maximum per-container requests/limits, applied automatically to pods that do not specify
them). A pod's individually-declared limits can be entirely reasonable while the namespace's
aggregate quota is what actually prevents a scale-up from succeeding — worth checking
specifically when a deployment's `maxReplicas` in its HPA is never actually reached in
practice.

---

## 3. Diagnostics

- **`kubectl top pods` / `kubectl top nodes`** — current CPU/memory usage against configured
  requests and limits. `safe-on-production` (read-only).
- **CPU throttling metrics**, where a metrics pipeline exposes them (`container_cpu_cfs_
  throttled_periods_total` / `container_cpu_cfs_periods_total` from cAdvisor/kube-state-metrics,
  commonly graphed as a throttling ratio). The direct way to confirm the CFS-quota mechanism
  above is actually occurring, rather than inferring it from latency alone.
  `safe-on-production` (metrics query only).
- **`kubectl describe pod`** for a pod's actual QoS class, recent events (OOMKilled,
  evictions, probe failures), and resource requests/limits as scheduled. `safe-on-production`.
- **`kubectl get hpa`** for the HPA's current/target metric values and recent scaling events —
  confirms whether the autoscaler is actually reacting to the metric a review assumes it is
  reacting to. `safe-on-production`.
- **`kubectl get events`**, filtered to a namespace or deployment, for eviction, scheduling
  failure, and probe-failure history over time — often the fastest way to confirm a suspected
  resource-pressure pattern actually happened rather than being theoretical.
  `safe-on-production`.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| p99 latency spikes with CPU usage graphs that look unremarkable | CFS quota throttling within individual 100ms accounting periods, invisible to average-utilization metrics |
| Pods are evicted under node pressure well before the node appears fully utilized | BestEffort or Burstable QoS class on workloads that should be Guaranteed, or a `LimitRange` default that does not match actual usage |
| The HPA's `maxReplicas` is configured generously but the deployment never scales that far | A namespace `ResourceQuota` capping aggregate consumption below what `maxReplicas` would require |
| Traffic stays concentrated on pre-scale-up pods after a scale event | Long-lived connections (HTTP/2, gRPC, database connections through a Service) not rebalancing after new pods join; `kube-proxy` only affects new connection placement |
| A slow-starting container is repeatedly restarted before it finishes initializing | No `startupProbe` configured, so `livenessProbe` is (correctly, given its own timeout) killing the container before startup completes |

---

## 5. Configuration worth checking, and what it trades

- **CFS quota period awareness when sizing CPU limits for bursty/multi-threaded workloads.**
  A limit that looks adequate on average can still throttle within individual periods; there
  is no configuration knob that removes this trade-off, only awareness of it when reasoning
  about a limit's apparent adequacy from average CPU alone.
- **HPA stabilization window and scale-down policies.** Trades faster reaction to a real
  load drop for resistance to thrashing under noisy or oscillating load.
- **`kube-proxy` mode (`iptables` vs `IPVS`)**, where the cluster allows choosing it. IPVS
  supports additional load-balancing algorithms at the cost of additional operational
  complexity; the choice does not by itself solve the long-lived-connection-pinning problem
  above.
- **`ResourceQuota` and `LimitRange` at the namespace level.** Trade predictable aggregate
  consumption and safe defaults for individual pods against the possibility of capping a
  workload's legitimate scale-up below its own HPA's configured maximum.

---

## 6. Version differences worth knowing

`startupProbe` was added as a distinct probe type in a Kubernetes 1.x minor release and is
not available in materially older clusters — before its introduction, the workaround was a
loosened `livenessProbe` (or a generous `initialDelaySeconds`), and a manifest still using
that pattern may simply predate the feature rather than reflecting a deliberate choice. HPA's
default behavior around scale-down stabilization and its support for multiple simultaneous
metrics have both evolved across API versions (`autoscaling/v1` versus `autoscaling/v2`).
Confirm the deployed cluster's Kubernetes version and the manifest's declared API version
before assuming current documentation describes the behavior in use.

---

## 7. What this file does NOT cover

- Cluster-level capacity planning and node autoscaling (Cluster Autoscaler, Karpenter) —
  real, but an infrastructure-provisioning question distinct from the per-workload reasoning
  here.
- Network policies and their effect on cross-namespace traffic — a security/segmentation
  concern with a possible latency dimension not modeled here.
- Persistent Volume performance characteristics (storage class, IOPS provisioning) — see
  `infrastructure/resources.md` §6's general network-attached-storage point; volume-plugin
  specifics are not covered.
- Service mesh (Istio, Linkerd) internals beyond the general sidecar-cost point already made
  in `infrastructure/resources.md` §4.
- Specific managed-Kubernetes-provider behavior (EKS, GKE, AKS) beyond what is generic to
  Kubernetes itself.
