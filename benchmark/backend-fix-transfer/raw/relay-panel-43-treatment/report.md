# Performance Review: relay-panel (node service, UDP forwarding path)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the UDP relay path in `crates/node`
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

The UDP path has already had a round of optimization (v1.0.9: no per-packet table scan, lock-free traffic counters, enlarged socket buffers, setup outside the map lock). The per-packet code is lean. The one material problem is a resource leak: idle sessions are removed from the map, but the task and socket behind each one are never released, so file descriptors, tasks and buffers grow with the number of distinct clients seen over time (PERF-001). Two smaller serialization risks (PERF-002, PERF-003) may explain slowdown at high concurrency but need measurement. No runtime evidence exists, so review confidence is Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Cancel a session's reader task when the sweeper evicts the session, and make the task's exit remove only its own map entry. | Follows from the code (High confidence); growth is unbounded by load and exhaustion hits the whole node. |
| 2 | **PERF-002 (P2)** | Find out whether affected rules have rate limits; if so, move the limiter wait and new-session setup off the single receive loop. | A capped rule stalls intake for all clients at once. |
| 3 | **PERF-003 (P3)** | Profile lock wait first; change nothing yet. | Cheap to measure, not justified by static reading alone. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Do descriptor and task counts stay high after traffic stops for more than 75 s? | Confirms PERF-001 as the main driver (raises to Confirmed). | Read the node process's open fd count and task count before, during and after a client burst. |
| Do the affected rules set upload or download caps? | Makes PERF-002 the primary explanation, or sets it aside. | Read the rules' limit fields in the panel. |
| Is one core saturated while others idle during the slow period? | Points to the single ingress task (PERF-002/003) over the leak. | Per-thread CPU view of the node process during a load run. |

### Validation commands

No repository-local command is justified; the checks are procedures.

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | safe-on-production | Sample the node process's open fd count at peak and again 90 s after the last packet. | Flat at peak confirms the leak; return to baseline refutes it. |
| PERF-002 | safe-on-production | Read per-rule caps from the panel config; compare against observed aggregate rate. | Whether the limiter stalls ingress. |
| PERF-003 | not-safe-on-production | Profile (or tokio-console) a multi-client UDP load test in staging; read lock wait share. | Whether lock work is worth changing. |

---

## 2. Scope and method

Reviewed read-only; no code, tests or service were run. `detect_stack.py` reported four services (frontend, node, panel, shared); only `crates/node` is relevant to the operators' report. Tokio async runtime on Rust (deep tier). Files read in full or in the relevant parts: `forwarder/udp.rs`, `forwarder/limiter.rs`, `forwarder/outbound.rs` (UDP socket creation), `forwarder/selector.rs` (`order`), the UDP spawn in `forwarder/manager.rs`, and `ConnectionTracker`/`TrafficCounter` in `reporter.rs`. Panel, frontend, TCP, WebSocket and TLS paths were not examined.

### Review completeness

| Item | Value |
|:--|:--|
| Review confidence | Medium |
| Evidence available | Uninstrumented (no metrics, benchmarks, load tests for UDP in the reviewed files) |
| Ranking method | Structural signals only |
| Critical paths analyzed | 1 of 1 identified (client to target to client UDP relay) |
| Shared resources analyzed | 4 of 4 (receive loop, session map, UDP tracker, per-rule limiter) |

### What this review could not determine

- Client counts, packet rates, packet sizes and client churn.
- Whether rules carry rate caps.
- CPU split between syscalls, locks and logging.
- The effective file-descriptor limit of the operators' deployment (the install script sets one; other deployments may differ).

## 3. Architecture overview

Per rule and address family, one `recv_from` loop on the bound inbound socket. Each new client source address gets its own connected outbound UDP socket, a spawned reader task that forwards target replies back through the inbound socket, and an entry in a per-listener session map. A separate node-wide tracker counts sessions for the panel. Both families of one rule run under one `tokio::select!` task (`manager.rs`). A per-rule token bucket limits upload and download if configured.

## 4. Workload model

Operator report only: many clients sending at once, throughput degrading as client count grows. No numbers supplied. Workload-dependent findings are capped at Medium confidence, except PERF-001, whose mechanism follows from the code.

### Questions that would change the ranking

See "Key unknowns" above.

## 5. Critical path analysis

Client datagram: `recv_from`, tracker touch (mutex), session lookup (mutex), optional new-session setup, limiter wait, `send`, traffic counter. Reply: outbound `recv`, limiter wait, counter, tracker touch (mutex), `send_to`, session timestamp (mutex). Shared resources are the receive loop (one task per rule), the session map, the tracker map and the limiter buckets.

## 6. Layer analysis

### 6.1 Application

Covered by findings below. Observed good: receive buffer reused for the life of the listener; the traffic counter uses a read lock plus atomics; the limiter releases its lock before sleeping; setup runs outside the session-map lock.

## 7. Findings

### PERF-001 — Expired UDP sessions leave their reader task and socket alive

- **Root cause:** ROOT-001
- **Severity:** High  **Confidence:** High  **Priority:** P1
- **Category:** memory
- **Location:** `crates/node/src/forwarder/udp.rs:118` (sweeper), `:237-268` (reader task), symbol `serve_udp_listener`
- **Tags:** scalability-risk

**Problem.** The sweeper removes idle entries from the session map, but each session's reader task holds its own `Arc` to the outbound socket and waits in `recv` indefinitely. Nothing tells it to stop, so the socket, the task and its 65535-byte buffer survive. A later packet from the same client finds no map entry and builds a second set.

**Performance principle.** Resources acquired per unit of demand must be released when that demand ends; otherwise use grows with history, not with load.

**Evidence.**
- Sweeper only calls `sessions.retain(...)` (udp.rs:118); no signal to the task.
- Reader loop exits only on a `recv` error or a failed `send_to` (udp.rs:240-262). An idle connected socket gives neither. No cancellation token, abort handle or timeout exists in the file.
- Each reader allocates `vec![0u8; UDP_BUF_SIZE]` with `UDP_BUF_SIZE = 65535` (udp.rs:31, 238). Each outbound socket requests 4 MiB buffers (outbound.rs:308, 325-326); the kernel may clamp that and its real size is unknown.
- On exit the task does `sessions_c.lock().await.remove(&src_c)` (udp.rs:266), keyed by client address only. A stale task ending late can delete a newer live session for the same client and orphan its reader.
- Install script sets `LimitNOFILE=65536` (scripts/relay-node-install.sh:391). No runtime evidence exists.

**Impact.** Critical path, per client, O(n) in cumulative expired sessions, service-wide blast radius: descriptor exhaustion also breaks TCP accepts and new outbound sockets.

**Conditions.** Distinct client addresses keep arriving over time (NAT port churn, short-lived clients) and each idles past 60 s. Stable small populations do not exhibit it. Client counts unknown.

**Counter-evidence.** No cancellation mechanism found. Release can still happen if the target answers late or an ICMP error surfaces as a `recv` error; that depends on target behaviour (bounds impact). The 65536 descriptor cap bounds memory growth but not the exhaustion failure.

**Why this might not matter.** With few, long-lived clients or active reply traffic, sessions rarely expire; frequent restarts also reset accumulation.

**Recommendation.** Store a cancellation handle in the session; the sweeper fires it on eviction; the reader selects on it and recv so it drops the socket. Make the exit-path removal conditional on the map still holding this task's own socket (pointer comparison).

**Alternatives.** (a) Timeout around `recv` checking `last_active` - works but needs a wake-up per session. (b) Cancellation handle - preferred, also fixes the stale-remove race. (c) Raise `LimitNOFILE` - delays failure, releases nothing.

**Trade-offs.** One handle per session and a select branch per reader; a returning client pays setup again, as today.

**Validation.** Metric: node open fd count and live task count after traffic stops (`safe-on-production`). Expect return to baseline within about 75 s (60 s timeout plus 15 s sweep) after the change; flat at peak before. Falsifier: counts already recover within 75 s on current code. Guard: a paused-clock test asserting the outbound socket's strong count reaches zero after expiry.

### PERF-002 — Single receive loop serializes limiter waits and new-session setup

- **Root cause:** ROOT-002
- **Severity:** Medium  **Confidence:** Medium  **Priority:** P2
- **Category:** concurrency
- **Location:** `crates/node/src/forwarder/udp.rs:285` (limiter), `:174-204` (setup), symbol `serve_udp_listener`
- **Tags:** scalability-risk, needs-measurement

**Problem.** All client-to-target work for a listener runs in one loop, including the awaited upload limiter sleep, per-client socket creation, bind, connect and spawn, and the send. A throttled rule or a burst of new clients delays every other client's datagrams.

**Performance principle.** A shared serial stage caps throughput at one worker and makes any slow step block everyone; slow work belongs off the dispatch path.

**Evidence.**
- Setup runs inline: `udp_outbound_socket` (socket creation, two buffer options, bind) then `connect` then `tokio::spawn` (udp.rs:174-237; outbound.rs:312-330).
- `rate_limit.acquire_upload(n).await` runs inline (udp.rs:285); the limiter returns a debt wait and sleeps (limiter.rs:125-130). The bucket lock is released first, so the lock is not the stall; the sleeper is the only receiver.
- Both address families of a rule run under one `select!` task (manager.rs:575-600).
- Inbound buffers of 4 MiB are requested (outbound.rs:376), absorbing short bursts; effective size unknown.

**Impact.** Critical path, per datagram and per new client, growth O(n) in traffic, bounded to the rule's endpoint. Amplification unknown.

**Conditions.** A rule with an upload cap whose aggregate traffic exceeds it; a burst of new clients; or a packet rate one task cannot sustain. None known. Stalling intake under a cap is partly by design (aggregate cap).

**Counter-evidence.** Unlimited rules skip the limiter. v1.0.9 already moved setup outside the map lock. Replies run in one task per session, so only the upload direction is serial. Per-packet work for existing sessions is small and not shown to be CPU-bound; no batched read exists.

**Why this might not matter.** If the cap is meant to throttle the whole rule, stalling is the mechanism; and below one task's packet capacity serial ingress is not the limit.

**Recommendation.** Measure first. If the limiter sleep dominates, hand datagrams to a bounded per-rule (or per-session, to preserve ordering) queue so the loop keeps draining, with an explicit counted drop on overflow. If setup dominates, spawn the setup and queue the first datagram behind it.

**Alternatives.** Raise the cap (no code risk, only helps if the cap is the cause); queue plus spawned setup (preferred); `SO_REUSEPORT` multiple loops (splits flows, harder to share the session map).

**Trade-offs.** New queue memory and a drop policy; spawning per datagram can reorder packets within a flow.

**Validation.** Metric: per-rule forwarded datagrams per second and kernel receive-queue drops at rising client counts, with and without a cap (`not-safe-on-production`). Expect intake not to stall during limiter waits. Falsifier: a profile showing the receive task mostly idle in `recv_from` during the slow period. Guard: a load test with N clients against a capped rule.

### PERF-003 — Per-packet shared mutexes and locked full scans

- **Root cause:** ROOT-003
- **Severity:** Low  **Confidence:** Medium  **Priority:** P3
- **Category:** concurrency
- **Location:** `crates/node/src/reporter.rs:203`, symbol `ConnectionTracker::udp_touch`
- **Tags:** needs-measurement

**Problem.** Each datagram, in both directions, takes the node-wide UDP tracker mutex and the listener's session-map mutex. The sweeper and `current()` hold those same locks across O(sessions) scans.

**Performance principle.** State written per operation should be sharded or lock-free; global scans should not hold the hot-path lock.

**Evidence.** `udp_touch` locks per call (reporter.rs:203); the reply path takes the tracker then the session lock per datagram (udp.rs:250-256); the sweeper scans under the session lock (udp.rs:116-118) and the tracker scan runs under its lock (reporter.rs:254-279, `current()` at ~268). Last-activity is written to two maps per packet.

**Impact.** Critical path, per datagram, O(n) scan cost in sessions, service-wide for the tracker lock (shared by all UDP rules on the node). Amplification unknown.

**Conditions.** Only when packet rate and session count are high enough for the short critical sections to contend. Unknown here.

**Counter-evidence.** v1.0.9 already removed the per-packet scan and made the traffic counter lock-free. Critical sections have no await. Scans run every 15 s per listener.

**Recommendation.** Do not change on static grounds. If a profile shows lock wait, keep one per-session atomic last-active timestamp shared by session and tracker entries, so the hot path takes no global lock.

**Trade-offs.** Extra consistency work between the two structures.

**Validation.** Lock wait share of per-packet time at rising client counts (`not-safe-on-production`). Falsifier: negligible share at the highest tested count.

### Remaining findings

None beyond PERF-001 to PERF-003.

### Considered and not reported

- Per-datagram buffer copies or allocations: receive buffer is allocated once per listener and reused.
- `TrafficCounter::add`: shared read lock plus relaxed atomics; write lock only on a rule's first bytes.
- `TargetSelector::order` allocations: per new session only, bounded by target count.

### Adjacent findings — outside performance scope

None noted.

## 8. Prioritized action plan

### P1 — High priority
- PERF-001: cancel reader tasks on session eviction.

### P2 — Medium priority
- PERF-002: after confirming rule caps and a CPU profile, move limiter waits and session setup off the receive loop.

### P3 — Optimization opportunity
- PERF-003: profile; change only if lock wait shows up.

## 9. Validation plan

Run in a staging environment with a multi-client UDP load generator where marked `not-safe-on-production`.

### PERF-001
Sample open fds and task count at peak and 90 s after traffic stops (`safe-on-production`).

### PERF-002
Compare per-rule caps with aggregate rate; measure datagrams forwarded per second and kernel receive drops at rising client counts (`not-safe-on-production`).

### PERF-003
Profile lock wait under N clients (`not-safe-on-production`).

### Instrumentation gaps to close first
No metrics exist for session count, live reader tasks, limiter wait time or inbound socket drops. Adding these as counters or gauges would let the unknowns above be read directly.

## 10. Machine-readable output

See `review.json` alongside this report (schema version 1.0).

## 11. Notes on this review

Static review only; no numbers other than constants read from the repository (60 s session timeout, 15 s sweep interval, 65535-byte buffer, 4 MiB requested socket buffers, 65536 descriptor limit in the install script) appear here. The observed slowdown with client count may come from PERF-001, PERF-002 or something outside the reviewed path; this review cannot say which without runtime data.
