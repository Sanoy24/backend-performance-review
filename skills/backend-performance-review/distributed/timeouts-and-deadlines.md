# Timeouts and deadlines

The most commonly missing performance control in backend systems, and one of the leading
causes of tail latency and cascading failure.

Timeout findings are unusually strong: they are visible in configuration, they do not depend
on workload, and their blast radius is typically system-wide. A missing timeout is
`High` confidence by default, and the workload cap does not apply to it.

---

## 1. Why a missing timeout is a performance finding

Every outbound call without a timeout has an unbounded worst case. The consequences are
resource consequences, not merely latency ones:

- A hung dependency holds a worker slot, a connection, and memory for as long as it hangs.
- Those resources are finite, so a slow dependency converts into local resource exhaustion.
- The service then fails for *every* request, including those that never touch the slow
  dependency.

This is how one degraded dependency takes down an unrelated part of a system. The failure is
not "requests to X are slow" but "the service is down", which is why the diagnosis so often
goes in the wrong direction.

**Default timeouts are usually absent or very large.** Many clients default to no timeout,
to an operating-system-level timeout measured in minutes, or to a timeout on connection
establishment only — which does nothing for a connection that is established and then
silently stops responding. Check what is actually configured; do not assume a sensible
default.

---

## 2. Timeouts are plural

A single "timeout" setting rarely covers everything. Check each layer independently:

| Timeout | Covers | Consequence when absent |
|:--|:--|:--|
| Connection | Establishing the connection | Hangs on an unreachable host |
| TLS handshake | Negotiation | Hangs on a misbehaving endpoint |
| Read / socket | Waiting for data | The common gap: connected, then silent |
| Total request | The whole operation including retries | Retries multiply an otherwise-bounded wait |
| Idle | An unused pooled connection | Stale connections accumulate |
| Server read/write/header | Slow clients | Slow clients hold server worker slots |
| Datastore statement | A single query | One runaway query holds a connection indefinitely |
| Lock acquisition | Waiting for a lock | Unbounded queueing behind a hot lock |
| Pool acquisition | Waiting for a connection | Saturation becomes a hang instead of a fast failure |

**Connection timeout without read timeout is the most frequent gap**, because it looks like
a timeout is configured. Look for it specifically.

Note also that a total-request timeout and a per-attempt timeout are different things. If
retries are configured, a per-attempt timeout of 2 seconds with three attempts is a 6-second
worst case before backoff.

---

## 3. Deadline propagation

A deadline is the time by which the whole operation must complete. Propagating it means each
service in the chain knows how much time remains and passes the remainder onward.

Without propagation:

```
client waits 1s
  └─ service A timeout 5s
       └─ service B timeout 10s
            └─ datastore no timeout
```

Every timeout below the client is useless — the client has already given up. Meanwhile A and
B continue to hold resources doing work whose result nobody will read. Under load this is
pure waste, and it occurs exactly when capacity is scarcest.

Correct shape: each hop's timeout is strictly smaller than its caller's remaining budget,
leaving room for the caller's own processing and for a retry if one is planned. Where a
protocol supports deadline metadata, propagate it rather than hard-coding per-service values
that drift apart over time.

Check for **inverted budgets** — an inner timeout larger than an outer one. It is a
configuration inconsistency, checkable from files, and a good `High`-confidence finding.

---

## 4. Choosing timeout values

A timeout should be set from the latency distribution of the dependency, not from intuition.

- Too short: healthy slow requests fail, retries amplify load, and the system becomes less
  reliable than it would have been with no timeout at all.
- Too long: the timeout provides no protection, because resources are exhausted before it
  fires.
- A reasonable starting point is somewhere above the observed high percentile of the
  dependency's normal latency, adjusted by how much of the caller's budget remains.

**This requires measurement.** If the repository has no latency data for a dependency, say
so, recommend the measurement, and give the shape of the answer rather than inventing a
number. "Set a timeout derived from the p99 of this call, which is currently unmeasured" is
an honest recommendation. "Set it to 500 ms" is a fabrication unless something in the repo
supports it.

Where a value must be suggested before data exists, frame it explicitly as a provisional
bound to be corrected after measurement — and prefer suggesting the *presence* of a timeout,
which is unambiguously right, over a specific value, which may not be.

---

## 5. Cancellation

A timeout that fires without cancelling the underlying work saves the caller and not the
system. The work continues, holding the same resources it held before, and the result is
discarded.

Check that cancellation propagates end to end: from the entry point, through business logic,
into datastore calls and outbound requests. Many stacks provide a cancellation mechanism that
is only honored if explicitly threaded through — which means it usually stops at the first
layer that forgot.

Client disconnects deserve the same treatment. If a client hangs up and the server continues
processing, that is uncancelled work, and it accumulates precisely during the overload that
caused the disconnects.

---

## 6. Interaction with retries

Timeouts and retries must be designed together; each is dangerous without the other.

- Retrying a timed-out request adds load to a dependency that is already struggling.
- A per-attempt timeout with N attempts produces an N-fold worst case that may exceed the
  caller's budget entirely.
- Retrying a non-idempotent operation after a timeout risks duplicate side effects — and a
  timeout does not tell you whether the operation completed.

See `distributed/retries-and-backpressure.md`.

---

## 7. What to look for in a review

- Every outbound call: is there a timeout, and does it cover reading as well as connecting?
- Datastore statement timeouts and pool acquisition timeouts.
- Server-side read, write, header, and idle timeouts.
- Timeout ordering across hops — inner smaller than outer.
- Whether a deadline is propagated or each service invents its own.
- Whether cancellation actually reaches the datastore and outbound clients.
- Whether the per-attempt timeout multiplied by retry count fits inside the caller's budget.
- Whether any timeout value is justified by measured latency.

## 8. What not to conclude

- Do not invent a specific timeout value. Recommend the presence of a timeout and the
  measurement that would set it.
- Do not recommend shortening a timeout without considering the retry amplification it
  causes.
- Do not assume the client library has a sensible default; find the configured value.
- Do not treat a missing timeout as minor because nothing has failed yet — it is an
  unbounded worst case, and its severity comes from blast radius rather than frequency.
