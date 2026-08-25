# Serialization

Converting between in-memory representations and wire or storage formats. Usually a modest
cost, occasionally a dominant one — and the difference is decided by payload size and
frequency, which means it is decided by measurement.

This file is short by design. Serialization attracts more speculative optimization advice
than its typical share of runtime warrants.

---

## 1. When serialization actually matters

It becomes significant when:

- Payloads are large (megabytes, or many thousands of items).
- The path is high-frequency and the rest of it is cheap.
- The same data is serialized or parsed more than once per request.
- Serialization happens per item rather than per response.
- The work happens on a single-threaded event loop, where its cost is a stall rather than
  just a cost.

It is usually insignificant when payloads are small and the path makes remote calls — the
I/O dominates. **On an I/O-bound path, recommending a faster serializer is a false
positive** unless a profile says otherwise.

Before making any serialization recommendation, establish payload size. If you cannot,
say so and recommend the measurement instead of the change.

---

## 2. Costs worth looking for

- **Serializing data that is discarded.** Building a full response object and then filtering
  it, or serializing fields the caller never reads. Over-fetching at the API layer becomes
  serialization cost here.
- **Double work.** Parsing a payload, converting it into a model, then serializing it again
  unchanged. Common in proxy and gateway code, and in middleware that inspects bodies.
- **Per-item serializer instantiation** inside a loop, where one instance would serve.
- **Validation walking the payload separately** from parsing, when the parser could validate.
- **Deep copies before serialization**, often defensive and often unnecessary.
- **Reflection-based serialization in a hot loop**, where a compiled or code-generated path
  exists in the same library.
- **Logging serialized payloads.** Serializing a body purely to log it can cost as much as
  serializing it to return it — and it also costs I/O and storage.

The double-work and discarded-work items are the most productive to look for, because they
are removable rather than merely optimizable.

---

## 3. Format trade-offs

Stated neutrally; the right choice depends on constraints the review must identify, not
assume.

| Concern | Text formats (JSON and similar) | Binary/schema formats |
|:--|:--|:--|
| Human debuggability | High | Low without tooling |
| Size on the wire | Larger | Smaller |
| Encode/decode cost | Higher, especially parsing | Lower |
| Schema evolution | Convention-based, loose | Explicit, enforced |
| Ecosystem/tooling cost | Minimal | Build step, code generation, versioning discipline |
| Cross-language support | Universal | Good, but requires shared schemas |

Switching formats is a large change with real operational cost. Recommend it only with
evidence that serialization is a meaningful share of the path, and state the migration cost
in `Trade-offs`. A format change on an internal high-volume link is a very different
proposition from one on a public API.

---

## 4. Streaming versus buffering

Buffering an entire response in memory:

- Makes memory usage proportional to response size — and unbounded if the response is
  unbounded.
- Delays the first byte until the last one is ready.
- Can hold that memory for the duration of a slow client's download.

Streaming avoids all three, at the cost of harder error handling — once bytes are sent, the
status code cannot be changed — and, in some stacks, losing content-length and complicating
compression.

For large exports, reports, and file downloads, buffering is a genuine finding and streaming
is a genuine fix. For ordinary API responses it is usually not worth the complexity.

---

## 5. Compression

Trades CPU for bytes. Worth it for large, compressible payloads over constrained links.
Wasteful for small payloads, for already-compressed data (images, video, archives), and on
fast internal links where CPU is scarcer than bandwidth.

Check for: compression applied unconditionally regardless of size or content type;
compression applied twice (application and proxy); compression level set to a maximum
without any evidence the extra CPU buys anything at that size.

---

## 6. What to look for in a review

- Payload sizes on hot paths — and whether they can be determined at all.
- Data serialized and then discarded, or parsed and re-serialized unchanged.
- Whole-response buffering for large or unbounded responses.
- Serialization of large payloads on a single-threaded event loop.
- Body logging on high-volume endpoints.
- Compression applied where it cannot help.

## 7. What not to conclude

- Do not recommend a faster serializer without evidence it is a meaningful share of the path.
- Do not recommend a format migration on general principle.
- Do not claim a size or a speedup ratio you did not measure.
- Do not recommend streaming for small responses; the error-handling cost is real.
