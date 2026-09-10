# GraphQL

Load after `application/api.md` and `application/data-access.md`. This file contains only
what those do not give you: the resolver execution model that makes GraphQL's version of
N+1 structurally different from a REST handler's, and the query-shape risks that follow
from the caller controlling the response shape rather than the server.

---

## 1. Detection signals

A GraphQL server dependency (`graphql`, `apollo-server`/`@apollo/server`, `graphql-yoga`,
`strawberry-graphql`, `graphene`, `ariadne`, `gqlgen`, `graphql-java`, `dgs`, `nexus`,
`type-graphql`), a `.graphql`/`.gql` schema file, or a `schema { query: ... }`/`type Query`
declaration. A federated/gateway setup additionally shows an Apollo Gateway, GraphQL Mesh,
or a `_service`/`_entities` field in the schema (Federation's introspection extension).

---

## 2. What differs from the API baseline

### The resolver tree is why GraphQL's N+1 is structural, not incidental

`application/api.md` §3 names serializer-level N+1 as one shape among several. In GraphQL it
is the default behavior of the execution model, not an occasional mistake: each field in a
selection set is resolved independently, so a list field's child resolver runs once per item
in the list by construction. A resolver that queries its own data source therefore issues
one query per item unless something explicitly collapses that per-item work — there is no
"forgot to join" version of this, because there is no join step to forget; the naive
implementation *is* the per-item loop.

**The batching mechanism (DataLoader and its equivalents) works by request-scoped
deduplication and windowing, not by rewriting the query.** A DataLoader collects the keys
requested by every resolver call within one tick (or one request), then issues a single
batched fetch keyed by the union of those ids, and fans the results back out to each
caller. This means the batching happens *across sibling resolvers in the same request*, not
within a single resolver — check whether a `DataLoader` instance (or the language's
equivalent: `graphql-java`'s `DataLoaderRegistry`, `strawberry`'s or `ariadne`'s dataloader
integration, a hand-rolled per-request memoizing batch function) is actually constructed
per-request and actually wired into the resolvers issuing the query, not merely present as a
dependency. A `DataLoader` imported but never passed into the resolver context does nothing.

### The caller controls the response shape, which inverts where boundedness has to be enforced

`application/api.md` §1 asks whether a response has an enforced maximum. In REST that
question is about the server's own endpoint design. In GraphQL, the *client* composes the
query, so the same field can be requested shallow or arbitrarily deep depending on what the
caller sends — boundedness has to be enforced against the query itself, not against a fixed
endpoint shape. Two structural risks follow directly:

- **Unbounded nesting.** A query that requests `author { posts { comments { author { posts
  { ... } } } } }` forces exponentially more resolver executions with each additional level,
  and the server has no way to know the caller intended this until it is already executing.
- **Unbounded breadth via aliases.** A single query can request the same expensive field
  many times under different aliases in one request, multiplying cost without multiplying
  the apparent "number of requests" anywhere a naive rate limiter would see it.

Neither risk exists in the same shape for a REST endpoint with a fixed response contract.

### Federation and schema stitching add a network hop per delegated field, not per request

A federated gateway (Apollo Federation, GraphQL Mesh) resolves a query by planning which
subgraph owns which field, then issuing one or more sub-requests per subgraph the query
touches. A single client query spanning three subgraphs is at minimum three network round
trips the gateway makes on the caller's behalf, and a poorly designed federation boundary —
an entity resolved by fields split across services that reference each other — can turn one
client-visible query into a fan-out the client-facing latency number never explains on its
own. This is `application/api.md`'s protocol-level cost reasoning, applied to a topology the
client never sees.

### Subscriptions hold a resolver open per subscriber, not per request

A subscription resolver runs once per event per active subscriber, for the lifetime of the
connection — closer to `distributed/`'s connection-and-backpressure reasoning than to a
request/response cost model. A subscription resolver that queries a datastore on every
event, multiplied by subscriber count, is a different growth shape than a query resolver's
per-request cost and should be reasoned about as recurring background work, not amortized
into a single request's latency budget.

---

## 3. Diagnostics

- **Per-resolver execution tracing** — Apollo's tracing extension format (`apollo-tracing`,
  or its Studio equivalent), `graphql-java`'s instrumentation hooks, or any resolver-level
  timing middleware. Shows resolver call counts and per-call duration, which is the direct
  way to confirm whether a suspected N+1 is actually collapsing through a DataLoader.
  `safe-on-production` when instrumentation overhead is bounded and already deployed;
  enabling new tracing instrumentation for the first time on a production server is
  `not-safe-on-production` until its overhead is measured on a non-production environment.
- **DataLoader batch-size and cache-hit logging** — most DataLoader implementations expose
  hooks or wrap-able methods for this. Confirms whether keys are actually deduplicating
  within a request, or whether each resolver call is producing a batch of one.
  `safe-on-production` (read-only instrumentation).
- **Query complexity/depth scoring** (`graphql-cost-analysis`-style libraries, or a custom
  cost function keyed to the schema) run against representative real queries. Identifies
  which fields dominate worst-case cost before a limit is set. `safe-on-production`.
- **Federation query-plan inspection** (Apollo Gateway's query-plan output, when enabled) —
  shows how many subgraph requests one client query actually generates.
  `safe-on-production` when query-plan logging is already available; generating a query plan
  for an arbitrary production query for the first time is `safe-on-production` since it does
  not execute the query, only plans it.

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| Latency grows with the size of a list field in the response, not with overall load | A per-item resolver issuing its own query with no DataLoader wired in |
| A small change in a client query causes a disproportionate latency jump | Nested field expansion multiplying resolver executions; check the query's actual depth and breadth, not just its line count |
| One endpoint's latency depends on which fields a specific client requests | Expected in GraphQL by design — this is the caller-controlled-shape property, not necessarily a bug, but it means "P95 for this endpoint" is a less meaningful number than it would be for a fixed REST response |
| A federated query is slower than the sum of its subgraphs' individual latencies | Sequential rather than parallel sub-request dispatch in the gateway's query plan, or an entity resolution boundary forcing a round trip that a differently-drawn schema boundary would not need |
| Subscription server memory or CPU grows with subscriber count, not event rate | Per-subscriber resolver state, or a query issued per event per subscriber with no shared caching across subscribers watching the same data |

---

## 5. Configuration worth checking, and what it trades

- **Query depth limit and query complexity limit.** Trade caller flexibility for a bound on
  worst-case cost. Confirm both are actually enforced (not merely available as a library
  option) and that the limit reflects a cost function tied to real resolver cost, not just
  syntactic depth — two queries at the same depth can have very different real cost if one
  touches a cheap scalar field and the other touches a resolver that hits a datastore.
- **DataLoader batch window / max batch size.** A larger window collects more keys into
  fewer round trips at the cost of added per-request latency waiting for the window to
  close; a smaller window reduces that latency at the cost of more, smaller batches. The
  right value depends on request shape and is a measurement question, not a default to copy.
- **Introspection availability in production.** Beyond the security question, a live schema
  introspection query is itself a request the server must execute; whether it is gated has
  a (usually small, but real) resource-consumption dimension distinct from the access-control
  one.
- **Automatic persisted queries (APQ), if supported by the client/server pair.** Trades a
  registration round trip (once, cacheable) for a much smaller payload on every subsequent
  call of the same query — a payload-size optimization, not a resolver-cost one; it does not
  bound query complexity or depth on its own.

---

## 6. Version differences worth knowing

Execution-engine behavior has changed across major versions of the reference JavaScript
implementation (`graphql-js`) and of the primary server frameworks built on it — including
changes to how independent resolvers within one selection set are scheduled. Confirm the
deployed version's release notes rather than assuming parallel resolver execution is
guaranteed; older versions and simpler runtimes may execute siblings sequentially, which
changes how much a slow resolver in one branch of the selection set affects the rest of the
response. Federation has also changed its query-planning model across major versions (v1 to
v2), which changes how a given schema is planned into subgraph requests — confirm which
major version is deployed before reasoning about planning behavior from general knowledge.

---

## 7. What this file does NOT cover

- Schema design as a correctness or API-ergonomics question (naming, nullability, union
  design) — only its performance-relevant consequences (nesting depth, field cost) are in
  scope here.
- GraphQL-over-WebSocket transport specifics beyond the subscription resolver-lifetime point
  above; transport-level connection reasoning belongs to `distributed/`.
- Client-side query caching and normalization (Apollo Client's cache, Relay) — a client-side
  concern, out of scope for a backend performance review.
- Authorization-per-field performance cost, beyond noting it as the same middleware-cost
  question `application/api.md` §4 already covers generically.
- Specific vendor products (Apollo Studio, Hasura, PostGraphile, WunderGraph) beyond what is
  named above for detection or diagnostics — their engine-specific behavior is not modeled.
