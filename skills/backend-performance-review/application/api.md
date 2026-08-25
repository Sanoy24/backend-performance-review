# API surface

The API shape determines how much work each call implies. Many performance problems that
appear to be database problems are really API design problems expressed downstream.

Applies to REST, GraphQL, gRPC, and RPC-over-HTTP alike; protocol-specific notes are marked.

---

## 1. Boundedness of responses

The first question for any read endpoint: **what is the largest response this can produce?**

- Is there an enforced maximum page size, or only a default? A default the caller can
  override is not a bound.
- Can a filter be omitted, producing a full-collection response?
- Does a nested field expand to an unbounded collection?
- Is there a maximum depth on nested expansion? *(GraphQL, and REST APIs with `?include=`)*
- Can a batch endpoint accept an unbounded list of ids?

An unbounded response is simultaneously a latency problem, a memory problem, a datastore
problem, and a bandwidth problem. It is one of the highest-value findings available from
static review, and the fix — an enforced maximum — is usually small.

**Offset pagination degrades with depth.** Skipping many rows costs proportionally to the
offset in most datastores, so deep pages get slower even though page size is constant.
Keyset (cursor) pagination avoids this, at the cost of losing random page access. State
that trade-off rather than recommending cursors reflexively.

---

## 2. Payload shape

- **Over-fetching.** Returning fields the caller never uses costs at every stage: datastore
  read, transfer, serialization, and parsing on the client. Worst when the unused field is
  a large blob or triggers a join.
- **Under-fetching.** A response too thin forces the caller into N calls. This is how a
  clean API produces a chatty client, and it is the cause of many N+1 patterns *in the
  caller* rather than in the service.
- **Nested expansion.** Convenient for callers, expensive per level, and the levels
  multiply.

The reviewable question is not "is this RESTful" but "does the response shape match how it
is actually consumed". If callers always follow a list response with a per-item detail
call, the list is under-fetching, and the fix is at the API layer.

---

## 3. N+1 at the API layer

The database N+1 has an API-layer twin, and it appears in three places:

- **Serializer/resolver level.** A field that issues a query per parent object. In GraphQL
  this is the default behavior of a naive field resolver; batching by key (the dataloader
  pattern) exists specifically to collapse it.
- **Client level.** A list endpoint with insufficient data, forcing per-item follow-ups.
- **Middleware level.** A per-request lookup — permissions, tenant, feature flags,
  configuration — repeated per item or per nested object.

Look at serializers and resolvers, not only at repository code. A query issued during
response rendering is easy to miss because it does not appear in the handler.

---

## 4. Middleware and per-request overhead

Middleware runs on every request, so its cost is multiplied by everything.

Worth checking:

- Authentication and authorization that hit a datastore per request without any caching of
  a short-lived, safely cacheable result.
- Feature-flag evaluation that performs a remote call per request or per flag.
- Request/response body logging on high-volume endpoints — serialization plus I/O plus
  storage cost.
- Validation that walks a large payload more than once.
- Compression applied to responses too small to benefit, or applied twice.
- Tracing or metrics with per-request allocation and high-cardinality labels.

Middleware ordering matters: expensive work placed before cheap rejection (rate limiting,
auth, payload-size checks) means invalid requests pay full cost.

---

## 5. Protocol-level costs

- **Connection reuse.** Keep-alive on the server and in every client. Without reuse, every
  call pays handshake cost. See `application/connection-pools.md`.
- **HTTP/2 and gRPC multiplexing** removes head-of-line blocking at the connection level but
  introduces per-stream flow control and, in some deployments, load-balancing skew because a
  long-lived connection pins to one backend.
- **Compression** trades CPU for bytes. Worth it on large, compressible payloads over slow
  links; wasteful on small payloads and on already-compressed data.
- **TLS termination point** determines who pays handshake cost, and whether internal hops
  re-encrypt.
- **Server timeouts** — read, write, idle, and header timeouts — are the server's defense
  against slow clients holding worker slots. Their absence is a capacity risk, not just a
  robustness one.

---

## 6. Write-path shape

- Is the write idempotent? Non-idempotent writes cannot be safely retried, which forces
  callers into either data risk or long timeouts.
- Does the write do work the caller does not need synchronously — sending mail, generating
  thumbnails, updating search indexes, calling analytics? Each is a candidate for
  background processing, and each is latency the user pays for no benefit.
- Does the write hold a transaction open across an external call? This is a common and
  severe pattern: lock duration becomes dependent on a third party.
- Are bulk writes available, or must callers loop?

---

## 7. Cacheability

Not a recommendation to add caching — a question about what the API makes possible.

- Are responses cacheable at all (correct method semantics, no unnecessary `no-store`)?
- Are validators (`ETag`, `Last-Modified`) available so a caller can revalidate cheaply?
- Does personalization defeat shared caching that could otherwise apply to most of a
  response?
- Does the response mix stable and volatile data, forcing the whole thing to the shortest
  TTL? Splitting the endpoint is sometimes the real fix.

---

## 8. What to look for in a review

- The maximum possible response size for every read endpoint.
- Enforced maxima versus defaults on pagination and batch inputs.
- Queries issued during serialization or resolution.
- Per-request middleware cost and ordering.
- Synchronous work on write paths that the caller does not need.
- Transactions spanning external calls.
- Timeouts on the server side, not only the client side.
- Whether the response shape matches actual consumption.

## 9. What not to conclude

- Do not recommend GraphQL, REST, or gRPC over another for performance reasons; the shape
  of the work dominates the protocol.
- Do not recommend response caching without addressing invalidation and personalization.
- Do not treat over-fetching as significant without knowing payload sizes — say what would
  determine it.
- Do not recommend cursor pagination without acknowledging the loss of random page access.
