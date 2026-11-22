# UDP relay performance review

Scope: the node's UDP forwarding path. I read `crates/node/src/forwarder/udp.rs`, `limiter.rs`, `outbound.rs`, `selector.rs` and `crates/node/src/reporter.rs`. I also grepped `manager.rs` for how the listener is spawned. Nothing was run.

## Summary

The node serves each UDP listen port with a single receive loop in one task, `serve_udp_listener` in `crates/node/src/forwarder/udp.rs`. Every datagram from every client goes through that loop, one at a time. Each datagram takes at least two async mutex acquisitions on the loop, plus a third on the same lock when `udp_touch` runs. The loop also awaits the rate limiter, which can sleep.

Each client session adds its own reader task, which takes the same locks on the reply path. As clients are added, the number of tasks and sockets grows and the contention on the shared locks grows with it. The serialized receive loop and the shared locks are the likely cause of the reported slowness and CPU use.

I found no quadratic scan on the per-packet path. A previous version apparently had one, and the code comments say it was removed.

## Findings, most serious first

### 1. High: a single receive loop per port, with the rate-limiter sleep inline

`crates/node/src/forwarder/udp.rs:131-291`, in particular lines 285-290.

- One task does `recv_from`, then session lookup, then `rate_limit.acquire_upload(n).await` (line 285), then `outbound_sock.send(...).await` (line 286), then `counter.add(...)` (line 289), and only then loops back to `recv_from`.
- When the rule has an upload cap, `acquire_upload` sleeps for the token-bucket debt (`limiter.rs:125-130`). That sleep happens inside the only receive loop, so it stalls reception for every client on the port, not just the heavy one.
- While the loop sleeps, datagrams queue in the kernel socket buffer. Once the 4 MiB buffer set in `outbound.rs:308` fills, the kernel drops them. The result is lower throughput and loss as more clients send at once.
- Even without a cap, all inbound packets for a port are handled on one task, so a port cannot use more than about one core. With many clients, CPU saturates on that task.
- Fix:
  - Do not sleep in the receive loop. Either drop or queue over-limit packets, or hand each datagram to a per-session task or channel so one throttled client does not block the others.
  - Run several receive loops per port with `SO_REUSEPORT`. Note that `bind_udp_socket` in `outbound.rs:362-381` sets only `SO_REUSEADDR`. This needs care so a client's datagrams stay on the same session.
  - Use batched receive (`recvmmsg`) if throughput must go up further.

### 2. High: a global async mutex taken several times per packet

`crates/node/src/forwarder/udp.rs:98`, `163`, `254`; `crates/node/src/reporter.rs:169`, `203-233`.

- `sessions: Arc<Mutex<HashMap<..>>>` is locked for every inbound datagram (line 163) and for every reply (line 254, in each per-session reader). The sweeper also holds it while scanning (lines 116-118).
- `ConnectionTracker::udp` is a second `tokio::sync::Mutex<HashMap>`. `udp_touch` takes it on every inbound datagram (line 153) and every reply (line 250).
- The two maps hold the same information: which clients are active, and when each was last seen. Each datagram therefore updates two maps under two locks.
- `Instant::now()` is called under each lock.
- With N clients sending at once, the receive loop and all N reader tasks contend on these two locks. tokio's async mutex is a fair queue, so contended acquisitions cost a wake-up and a task switch each. That shows up as CPU use and as latency that grows with client count.
- The periodic sweepers (every 15 s) run `retain` over the whole map while holding the lock, so they stall every packet for O(sessions) time. `current()` in `reporter.rs:261-271` does the same on every status report.
- Fix:
  - Use a sharded or concurrent map (`dashmap`, or a `Vec<Mutex<HashMap>>` sharded by hash of the client address).
  - Store `last_active` as an `AtomicU64` of a coarse timestamp inside the session entry, so refreshing it needs no write lock.
  - Merge the tracker's UDP table with the listener's session table, or have the tracker count sessions with an atomic instead of keeping its own map.
  - Make `udp_touch` on the reply path unnecessary, since the entry was just refreshed on the inbound path.

### 3. Medium-High: per-client resources, including a 64 KiB buffer per session

`crates/node/src/forwarder/udp.rs:130`, `174`, `237-238`; `crates/node/src/forwarder/outbound.rs:312-330`.

- Every new client allocates and binds a new UDP socket, with a socket2 setup call sequence (create, nonblocking, two buffer-size calls, bind) and a `connect`. This is repeated for every new source address.
- Each session spawns a task with `vec![0u8; UDP_BUF_SIZE]`, which is 65535 bytes (line 238). With many clients that is tens of MB of heap, plus the kernel's own per-socket buffers.
- The outbound socket also requests 4 MiB send and receive buffers (`outbound.rs:308`, `325-326`). The kernel clamps this to `rmem_max`/`wmem_max`, but where the limit is high, many sessions multiply kernel memory use.
- Setup cost is paid inside the receive loop (lines 174-204), so a burst of new clients, such as many clients starting at once, stalls reception for established clients while sockets are created. The comment at line 157 says the map lock is not held, but the loop itself is still blocked.
- Fix:
  - Use a smaller reply buffer, around 2 KiB or the real MTU, or a pooled buffer.
  - Move session setup into a spawned task so the loop keeps receiving.
  - Lower the outbound buffer request, since most per-client sockets do not need 4 MiB.

### 4. Medium: per-packet bookkeeping on every datagram

`crates/node/src/forwarder/udp.rs:246`, `289`; `crates/node/src/reporter.rs:36-53`.

- `counter.add` takes the `RwLock` read side on every packet and does two atomic adds. This is cheaper than the earlier design, but it is still an async lock acquisition per packet, on both directions.
- Fix: cache the `Arc<(AtomicU64, AtomicU64)>` for the rule once per listener or session and `fetch_add` on it directly, so there is no map lookup or lock per packet. The map is only needed at snapshot time. Note `commit()` removes zero entries (`reporter.rs:135-137`), so caching needs a different cleanup scheme.

### 5. Medium: shared rate-limiter buckets

`crates/node/src/forwarder/limiter.rs:92-146`.

- When a rule has a cap, all of that rule's sessions share two `tokio::sync::Mutex<Bucket>`. This is correct for an aggregate cap and it does not hold the lock while sleeping, but it is one more contended lock per packet in each direction.
- Each over-limit packet also creates a separate `tokio::time::sleep`, which is a timer allocation per packet at high packet rates.
- Fix: use a `std::sync::Mutex` or atomic-based bucket, since the critical section is tiny and synchronous. For the UDP path, prefer dropping or queueing over sleeping per packet.

### 6. Low: other items

- `crates/node/src/forwarder/selector.rs:112-134`: `order()` allocates two `Vec`s per new session. This only runs once per session, so the cost matters only during connection bursts. Fix: compute the first alive index without allocating.
- `crates/node/src/forwarder/udp.rs:185-189`: the target is chosen only on session creation. Fine for affinity, but a dead target is not detected for existing sessions, so they sit until the 60 s timeout.
- `crates/node/src/forwarder/udp.rs:136-149`: a transient `recv_from` error sleeps 100 ms in the only receive loop, so a burst of `ENOBUFS` errors would stall reception.
- `crates/node/src/forwarder/udp.rs:224`, `reporter.rs`: `tracing::debug!` calls on session open/close are cheap when the level is disabled, but they sit under the tracker lock at `reporter.rs:224-229`.

## Suggested priority

1. Take the rate-limit sleep and session setup out of the receive loop (findings 1 and 3). This most directly affects throughput under many clients.
2. Replace the two global session mutexes with sharded or atomic structures (finding 2).
3. Remove per-packet map lookups and big per-session buffers (findings 3 and 4).
4. Measure afterwards: run a load test with many source addresses, and profile lock contention (for example with `tokio-console`) to confirm where the time goes.

## Confidence

The findings come from reading the code only; I did not run or measure anything. Findings 1 and 2 are the ones I am most confident explain the reported symptoms. I could not tell from the code whether the operators' rules have rate caps set. If they do not, finding 1's sleep point does not apply and finding 2 plus the single-loop ceiling carry most of the weight.
