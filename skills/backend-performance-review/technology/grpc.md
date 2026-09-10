# gRPC

Load after `application/api.md`, `application/serialization.md`, and
`distributed/timeouts-and-deadlines.md`. This file contains only what those do not give
you: what HTTP/2 multiplexing and Protocol Buffers change about connection- and
serialization-level reasoning, and gRPC's specific deadline-propagation and streaming
semantics.

---

## 1. Detection signals

A gRPC dependency (`grpc`, `@grpc/grpc-js`, `grpcio`, `grpc-go`/`google.golang.org/grpc`,
`grpc-java`, `Grpc.AspNetCore`, `tonic`), a `.proto` file with a `service { rpc ... }`
declaration, generated `_pb2_grpc.py`/`.pb.go`/`Grpc.cs`/`ServiceGrpc.java` stubs, or a
`protoc`/`buf generate` build step.

---

## 2. What differs from the API baseline

### One HTTP/2 connection carries many concurrent calls, which changes what "connection pooling" means

`application/connection-pools.md`'s pool-sizing reasoning assumes each concurrent call
needs its own connection (or one from a pool sized to concurrency). gRPC calls multiplex
over a single HTTP/2 connection as independent streams, so a gRPC client typically needs far
fewer connections than concurrent calls — often one connection per target host is enough,
with concurrency handled by HTTP/2 stream multiplexing rather than by connection count. The
consequence that does not follow from the generic pooling file: **a single misbehaving or
saturated connection now affects every call multiplexed onto it**, and load-balancing
schemes that assume one connection equals one unit of balanceable traffic (many L4 load
balancers) will pin all of a client's multiplexed traffic to one backend instance
regardless of how many logical calls that represents — an L4 balancer sees one flow, not N
concurrent requests. Check whether load balancing happens at L7 (aware of gRPC/HTTP/2
streams) or L4 (aware only of the underlying TCP connection) before reasoning about whether
load is actually being distributed across backend instances.

### Protobuf serialization cost is dominated by message shape, not by format choice alone

`application/serialization.md` covers payload-size and format-choice trade-offs generally.
What is specific to Protobuf: deeply nested or highly repeated (`repeated` field) message
shapes cost more to encode/decode than the wire size alone suggests, because each field
requires its own tag-and-length encoding step; a message with many small repeated fields can
cost more CPU per byte than a message with fewer, larger fields. This matters specifically
when reasoning about CPU cost on a hot path, since two messages of similar wire size can have
materially different (de)serialization cost depending on field cardinality and nesting.

### Streaming calls hold state for the life of the stream, not the life of one message

gRPC's client-streaming, server-streaming, and bidirectional-streaming call types keep a
call (and any per-call state — buffers, an open cursor, an accumulated result) alive for the
duration of the stream, not for a single request/response exchange. A server-streaming
handler that materializes its full result before starting to send defeats the purpose of
streaming and holds memory for as long as a unary call would while claiming to be a
streaming one; a bidirectional-streaming handler needs its own backpressure reasoning
(`distributed/retries-and-backpressure.md`) *within* the call, since a slow consumer stalls
the producer side of the same open stream rather than simply queuing a fresh request.

### Deadlines propagate through the call chain by default, in a way most REST clients do not

A gRPC deadline set on the top-level call is, in most language implementations, propagated
automatically to any gRPC calls the handler itself makes downstream — a fundamentally
different default from a REST client, where each hop's timeout must be configured
independently and a caller's deadline is not visible to a callee unless deliberately passed
along (a header, a context value). This is a genuine advantage for the exact problem
`distributed/timeouts-and-deadlines.md` describes (a caller waiting past a downstream
deadline that has already expired), *when it is actually used* — check whether deadlines are
being explicitly set on the top-level call at all, since automatic propagation of a deadline
that was never set propagates nothing.

---

## 3. Diagnostics

- **HTTP/2 connection and stream count** — most gRPC implementations expose channel state
  (`grpc-go`'s `Channelz`, `grpc-java`'s `Channelz` service, gRPC-core's channel tracing).
  Confirms actual connection count versus concurrent call count, which is the direct way to
  check whether multiplexing is behaving as expected or whether the client is unexpectedly
  opening many connections. `safe-on-production` (introspection only).
- **Per-call latency broken out by method** — gRPC interceptors (client- and server-side)
  are the standard extension point for this; most gRPC-aware tracing integrations
  (OpenTelemetry's gRPC instrumentation) attach here. `safe-on-production` when the
  interceptor is already deployed; adding a new interceptor to a production server for the
  first time should be validated on a non-production environment first given it wraps every
  call.
- **Deadline-exceeded and cancellation counts by method** — most gRPC server implementations
  surface these as status-code metrics. A method with a high `DEADLINE_EXCEEDED` rate is
  direct, runtime evidence of a timeout budget that does not match the method's actual cost —
  stronger evidence than static config inspection alone. `safe-on-production`.
- **Message size histograms**, where instrumented — confirms whether the message-shape
  reasoning above (repeated/nested fields inflating (de)serialization cost) actually applies
  to the messages this service sends, rather than reasoning from the `.proto` schema alone.
  `safe-on-production`.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| All traffic from one client lands on a single backend instance despite multiple replicas | L4 load balancing pinning a multiplexed HTTP/2 connection to one instance; needs L7/gRPC-aware balancing or client-side load balancing |
| High CPU on a service with unremarkable wire-level payload sizes | Deeply nested or highly repeated Protobuf message shapes costing more to (de)serialize per byte than a flatter equivalent |
| A server-streaming call's memory grows with result size before any data is sent to the client | The handler materializing the full result set before starting to stream, defeating the point of the streaming call type |
| Downstream calls keep running well after the top-level request's deadline has passed | No deadline set on the top-level call at all — there is nothing for automatic propagation to propagate |
| A bidirectional stream slows down or stalls under a slow consumer | Missing backpressure handling within the stream itself, distinct from ordinary request-level retry/backpressure reasoning |

---

## 5. Configuration worth checking, and what it trades

- **Keepalive settings** (ping interval, timeout, permit-without-calls). Trade connection
  liveness detection and faster failure detection for periodic keepalive traffic; overly
  aggressive keepalive settings from a client can trip a server's own keepalive-abuse
  protection and cause connections to be closed as misbehaving.
- **Max concurrent streams per connection.** Bounds how much multiplexed traffic one
  connection carries; too low forces more connections (undermining multiplexing's benefit),
  too high concentrates more blast radius onto a single connection.
- **Max message size** (send and receive). Trades flexibility for a bound on how much memory
  a single message can force the receiver to allocate — the gRPC-specific version of
  `application/api.md`'s boundedness question, since an unbounded max message size means an
  unbounded per-call memory cost regardless of how well-designed the rest of the API is.
- **Deadline propagation being explicitly enabled/disabled**, where the implementation makes
  it configurable rather than automatic. Confirm which behavior the deployed client library
  version actually has by default before assuming propagation is happening.

---

## 6. Version differences worth knowing

HTTP/2 flow-control and connection-management behavior has evolved across major versions of
the common gRPC implementations (`grpc-go`, `grpc-java`, `grpc` for C-based/Python
bindings), including changes to default keepalive behavior and to how aggressively a channel
attempts to reuse or recreate connections. Confirm the deployed library version's release
notes before asserting a specific default rather than treating gRPC's defaults as uniform
across versions or languages — the specification is shared, but default *values* for
tunables like keepalive intervals are implementation-specific and have changed over time.

---

## 7. What this file does NOT cover

- Protocol Buffers schema evolution and wire-compatibility rules (field numbering,
  reserved fields) — a correctness question, not a performance one.
- gRPC-Web and its browser-specific transport constraints, which differ materially from
  gRPC-over-HTTP/2 between backend services.
- Service mesh sidecar interactions (Envoy, Linkerd) with gRPC traffic — real, but a
  distinct infrastructure-layer topic this file does not model.
- Authentication/authorization interceptor cost beyond the general per-call middleware-cost
  reasoning `application/api.md` §4 already covers.
- Specific vendor gRPC-adjacent products (Buf, gRPC-Gateway) beyond what is named above for
  detection.
