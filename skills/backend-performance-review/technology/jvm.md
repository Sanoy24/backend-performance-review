# JVM (Java / Kotlin)

Load after `runtimes/universal.md` and `application/async-and-blocking.md`.

This file covers the HotSpot JVM, the implementation almost every backend Java or Kotlin
service runs on. It contains only what those two files do not give you — generic GC and
blocking/non-blocking reasoning are covered there. Where a different JVM implementation
(GraalVM native image, Android's ART) is in use, see §6 before applying anything here.

---

## 1. Detection signals

`pom.xml`, `build.gradle`/`build.gradle.kts`, a Maven/Gradle wrapper (`mvnw`, `gradlew`), or a
`Dockerfile` with a JDK/JRE base image. Kotlin is detected the same way plus `.kt` sources or a
`kotlin` Gradle plugin block. Record the JDK version (`Dockerfile` base image tag,
`pom.xml`'s `<java.version>`, `build.gradle`'s `sourceCompatibility`/toolchain block) — several
behaviors below are version-dependent, especially around virtual threads.

**Determine the concurrency model before anything else**, from the actual framework and thread
configuration rather than assuming: a classic servlet container (Tomcat/Jetty behind Spring MVC,
one thread per request), a reactive stack (WebFlux, Vert.x, Akka, a Netty event loop), or
virtual threads (Project Loom, JDK 21+, opt-in per executor or framework setting). This single
fact determines which half of §2 applies.

---

## 2. What differs from the universal runtime baseline

### The concurrency model changes the severity of the identical blocking call

| Model | Typical stack | A blocking call in a handler costs... |
|:--|:--|:--|
| **Platform threads, thread-per-request** | Spring MVC on Tomcat/Jetty, traditional servlets | One pooled thread for the duration — a capacity cost, bounded by pool size, the same shape as Python's WSGI model |
| **Reactive, event-loop-based** | WebFlux, Vert.x, Akka HTTP, Netty-direct | A shared event-loop thread — the same severe, request-wide-stall pattern `application/async-and-blocking.md` describes for any single-threaded event loop |
| **Virtual threads** | JDK 21+ with `Executors.newVirtualThreadPerTaskExecutor()`, or a framework's virtual-thread mode | Blocking is cheap **only when the blocking operation itself is virtual-thread-aware** — the virtual thread unmounts from its carrier thread while waiting. A blocking call into code that pins the carrier (a `synchronized` block, some native/JNI calls) defeats this and reintroduces the platform-thread cost model silently |

**Do not apply reactive-event-loop severity reasoning to a thread-per-request deployment, or vice
versa.** A blocking JDBC call is a minor, bounded cost on Tomcat's default thread pool and a
`Critical`, event-loop-stalling finding inside a WebFlux `Mono`/`Flux` chain.

Virtual threads are the newest of the three and the easiest to get wrong in review: confirm
whether the specific blocking call in question (a `synchronized` block, a legacy JDBC driver, a
native call) is actually documented as virtual-thread-friendly before assuming the "blocking is
now cheap" framing applies to it.

### Warm-up is a real, first-class performance dimension, not a footnote

The JVM starts by interpreting bytecode, then progressively JIT-compiles hot methods (tiered
compilation: C1 for fast startup, C2 for peak throughput). A JVM service is measurably slower in
its first seconds-to-minutes of traffic than at steady state, and this is structural, not a
symptom of misconfiguration.

- **Benchmarking or profiling a JVM service without accounting for warm-up produces misleading
  numbers.** A load test or profile taken immediately after startup measures the interpreter and
  C1-compiled code, not the C2-optimized steady state the service will actually run at.
- This is the direct reason JVM services are a poor fit for very-short-lived execution contexts
  (aggressive serverless scale-to-zero, per-request container spin-up) compared to a runtime with
  near-instant startup — the same "startup cost" dimension `runtimes/universal.md` asks about, but
  concretely large here.
- **GraalVM native image** compiles ahead-of-time to a native binary specifically to eliminate
  this cost, trading it for a different set of constraints (limited reflection/dynamic class
  loading support, a separate build step) — worth flagging as the relevant alternative when
  startup latency is the actual bottleneck, not something to recommend by default.

### Garbage collection is a chosen, tunable trade-off between multiple real algorithms

Unlike a runtime with one fixed collector, HotSpot ships several GC algorithms with materially
different pause/throughput trade-offs, and the choice is explicit configuration, not a given:

| Collector | Trade-off |
|:--|:--|
| **Parallel GC** | Highest throughput, stop-the-world pauses scale with heap size — a poor fit for latency-sensitive services |
| **G1 (default since JDK 9)** | Balances throughput and pause time via region-based incremental collection; the reasonable default for most backend services |
| **ZGC / Shenandoah** | Sub-millisecond target pause times, largely independent of heap size, at some throughput and memory-overhead cost — the right choice when tail latency matters more than raw throughput |

A pause-time complaint should be diagnosed against the collector actually configured (GC logs,
`-Xlog:gc`) before recommending a collector switch — the fix for a Parallel-GC pause problem is
usually "move to G1 or ZGC," not "tune Parallel GC harder."

### Off-heap and non-heap memory are invisible to heap-only monitoring

Heap usage is only part of a JVM process's memory footprint. Metaspace (class metadata, distinct
from the old permgen it replaced in JDK 8), direct `ByteBuffer`s, thread stacks, and JIT code
cache all live outside the heap and outside what a heap dump or `-Xmx` ceiling shows. A
container OOM-kill with heap usage reported as comfortably under the configured max is the
signature symptom — check non-heap consumption (native memory tracking, `jcmd
VM.native_memory`) before concluding the heap limit itself is wrong.

Dynamic class generation in particular (proxy-heavy DI frameworks, some ORM/ AOP libraries) is a
specific, checkable metaspace-growth source distinct from a heap leak.

---

## 3. Diagnostics

| Tool | What it shows | Production safety |
|:--|:--|:--|
| JFR (Java Flight Recorder) | Continuous low-overhead profiling: CPU, allocation, GC, lock contention, thread state — built into the JDK | **safe-on-production** — designed for always-on use, the first tool to reach for |
| `async-profiler` | Sampling CPU/allocation flame graphs, including native frames JFR can miss | **safe-on-production** at default settings — low overhead by design |
| GC logs (`-Xlog:gc*`) | Pause frequency, duration, and cause per collection | **safe-on-production** — logging only, negligible overhead |
| `jstack` / thread dump | Full thread states and stack traces at a point in time — the direct way to confirm thread-pool exhaustion or lock contention | **safe-on-production** for an occasional dump; frequent dumping adds overhead |
| `jcmd` | Multi-purpose: heap summary, native memory tracking, thread dumps, GC triggering | **safe-on-production** for inspection subcommands; GC-triggering subcommands are not |
| `jmap -dump` (heap dump) | Full object graph snapshot for memory-leak analysis | **not-safe-on-production** — pauses the JVM for the duration of the dump, which can be substantial on a large heap |
| VisualVM / a commercial APM agent | Aggregated, longer-window views of the above | Varies by agent overhead — confirm the vendor's own production-safety guidance |

JFR deserves the same emphasis given to `py-spy` and `perf_hooks` in the other runtime
references: it requires no restart, has negligible steady-state overhead, and is usually the
fastest path from a suspected finding to `Confirmed`-grade evidence.

---

## 4. Common failure modes and their symptoms

| Symptom | JVM-specific cause to check first |
|:--|:--|
| Latency is markedly worse in the first minutes after deploy/restart than at steady state | JIT warm-up — expected and structural; confirm before treating it as a regression |
| A reactive (WebFlux/Vert.x) endpoint's latency spikes and drags down unrelated concurrent requests | A blocking call (a sync JDBC driver, blocking file I/O) on the event-loop thread — the JVM analogue of the Node/Python event-loop-blocking finding |
| A virtual-thread deployment doesn't show the expected concurrency improvement under blocking I/O | Carrier-thread pinning — a `synchronized` block or a non-virtual-thread-aware blocking call defeating the unmount-on-block behavior |
| Periodic, multi-hundred-millisecond-or-longer pauses under load | GC collector mismatched to the latency requirement (Parallel GC under a latency-sensitive workload) — check GC logs before tuning flags |
| Container OOM-killed while heap metrics look fine | Off-heap growth — metaspace (often from dynamic proxy/class generation), direct buffers, or thread-stack count from an unbounded thread pool |
| Steadily growing metaspace over the process lifetime | Dynamic class generation without corresponding unloading — common with proxy-heavy DI/AOP frameworks under certain classloader configurations |
| Thread-pool exhaustion under a moderate request rate on a thread-per-request deployment | Pool sized without accounting for per-request blocking-call duration; the bounded-capacity-cost finding the concurrency-model table above describes |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Heap size (`-Xmx`/`-Xms`) vs container memory limit | Too high risks an OOM-kill that ignores in-process heap headroom entirely; too low forces more frequent GC. **Pre-JDK 8u191/JDK 10, the JVM did not read cgroup limits at all** and would size itself against host memory inside a constrained container — a real, dangerous default on older images that must be checked, not assumed fixed |
| GC collector choice (`-XX:+UseG1GC`, `-XX:+UseZGC`, etc.) | Throughput versus worst-case pause time, per the collector table in §2 |
| Thread pool size (Tomcat `maxThreads`, a custom `ExecutorService`) | More concurrent request capacity versus per-thread memory (platform thread stacks are not free) and context-switching overhead at high counts |
| Virtual-thread adoption scope | Removes the platform-thread capacity ceiling for I/O-bound work, but only for call paths confirmed not to pin the carrier thread — partial adoption without that check reintroduces the old cost model silently |
| Tiered compilation flags (`-XX:TieredStopAtLevel`, etc.) | Faster warm-up versus lower eventual peak throughput — rarely worth changing from defaults without a measured warm-up problem |

---

## 6. Version differences worth knowing

- **G1 became the default collector in JDK 9** (Parallel GC was default before). A service still
  pinned to an old default because nobody revisited it after an upgrade is a real, checkable
  finding.
- **JDK 8u191 / JDK 10** fixed container-awareness (cgroup memory/CPU limit detection) — confirm
  the actual running JDK build before assuming the JVM sees the container's real limits.
- **ZGC and Shenandoah** matured considerably between their initial (JDK 11-ish, experimental)
  releases and later LTS versions; do not assume production-readiness claims from early
  documentation still describe the current version.
- **Virtual threads (Project Loom)** were finalized in **JDK 21**. Before that, "lightweight
  thread" claims refer to preview APIs with different guarantees — confirm the JDK version before
  applying the virtual-thread reasoning in §2 at all.
- **Kotlin coroutines** are a language-level concurrency construct built on top of the JVM thread
  model (dispatchers ultimately schedule onto platform or virtual threads); they are not a
  separate runtime concurrency model, but a coroutine `Dispatcher` misconfigured to run blocking
  work on a dispatcher meant for non-blocking work reproduces the reactive event-loop-blocking
  finding from §2. See §7 for what this file does not cover on coroutines specifically.
- **GraalVM native image** trades JIT warm-up entirely for ahead-of-time compilation, with real
  constraints (reflection/dynamic proxies need explicit configuration) — confirm which build is
  actually deployed before applying HotSpot-specific JIT/warm-up reasoning to it.

**Confirm version-specific claims against the release notes for the version actually deployed**
rather than asserting them generically.

---

## 7. What this file does NOT cover

- Framework-specific behavior (Spring internals, Hibernate/JPA query generation, Micronaut/Quarkus
  build-time processing) — see `application/api.md`, `application/data-access.md`, and the
  relevant `databases/`/`technology/` files.
- Kotlin coroutine scheduling and structured concurrency in depth beyond the dispatcher-pinning
  note in §6 — coroutines are a language feature layered on the JVM thread model, not a distinct
  runtime.
- Android's ART runtime, which shares Java/Kotlin as source languages but has a materially
  different execution model, GC, and constraints — out of scope for a backend service review.
- GraalVM native image internals beyond the warm-up/reflection trade-off noted in §2 and §6.
- Exhaustive JVM flag tuning; recommend the measurement (GC logs, JFR) that determines the right
  value rather than a specific number.
