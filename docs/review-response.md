# Response to the external performance review

An external review of this project produced 140 numbered recommendations. This page records
what happened to each one, so that a proposal already considered can be answered with a link
rather than re-argued from scratch — the same reason [roadmap.md](roadmap.md#out-of-scope)
keeps an explicit out-of-scope list.

## Dispositions

| Disposition | Meaning |
|:--|:--|
| **Adopted** | Accepted, with the implementation phase named |
| **Present** | Already implemented, under this name or another. Pointer given. Building it again would be waste |
| **Reframed** | The premise is right and the proposed mechanism is wrong for this architecture. What was actually adopted is stated |
| **Deferred** | Accepted in principle, blocked on something. The blocker is named |
| **Declined** | Not doing it. The reason is given |

Phase letters refer to the implementation plan: **A** decision record · **B** finding schema ·
**C** reasoning doctrine · **D** detector · **E** measurement harness · **F** change-scoped
flagship · **G** deferred.

## Three constraints that shaped these answers

1. **There is no runtime.** This project is Markdown read by an LLM plus one stdlib-only
   detector. Nothing executes between detection and report, so a recommendation phrased as a
   pipeline stage ("clustering layer", "hypothesis engine") has nowhere to live. Each such item
   was re-expressed as doctrine, schema, or detector code — or declined.
2. **Schema and report changes are breaking.** Per [CHANGELOG.md](../CHANGELOG.md), changing
   finding-schema field names or report structure is a major change. The review proposes roughly
   forty new fields; they land in one batched release (Phase B), not across a dozen minors.
3. **Every field is a permanent context tax.** [architecture.md §11](architecture.md) caps
   `SKILL.md` growth deliberately and states that precision, not recall, is the scarce resource.
   Adopted fields are therefore conditional on severity, or confined to the machine-readable
   output, wherever the human report does not need them.

---

## The 140 items

| # | Item | Disposition | Note |
|:--|:--|:--|:--|
| 1 | Build a real benchmark | Adopted — E4 | Harness first, corpus incrementally. First new repository targets the class never yet obtained: one whose correct answer is zero findings (see §3.7) |
| 2 | Measure precision and recall | Adopted — E2 | Per category, from schema-valid output |
| 3 | Severity calibration | Adopted — E2 | Exact / ±1 / large disagreement against expert severity |
| 4 | Confidence calibration | Adopted — E2 | Predicted-confidence vs actual-correctness buckets |
| 5 | Evidence graph | Reframed — B | Not a graph structure; no executor exists to walk one. The chain becomes linked schema fields plus `root_cause_id` |
| 6 | Root-cause clustering | Reframed — B | The *intent* is already mandatory: `bottleneck-analysis.md` §2, "Merging is mandatory, not stylistic." What is missing is an identifier, not the rule |
| 7 | Finding deduplication layer | Reframed — B | Same as #6. A `root_cause_id` field, not a pipeline stage |
| 8 | Structured detection over substring matching | Adopted — D1 | Partly present: `detect_stack.py` already parses `schema.prisma`, `knexfile.js`, and `config/config.json` for real datastore declarations |
| 9 | Detection confidence model | Adopted — D1 | Extends the existing `weak_evidence` grading rather than replacing it |
| 10 | Direct vs indirect evidence | Adopted — D1 | Becomes a four-level `evidence_strength` |
| 11 | Monorepo detection | Adopted — D2 | Largest genuine detector gap. Emits `services[]`, each with its own stack |
| 12 | Dependency graph analysis | Deferred — G | Needs real AST analysis across eight runtimes. Out of proportion to current value |
| 13 | Call-path analysis | Deferred — G | Same blocker as #12 |
| 14 | Model growth explicitly | Present | `SKILL.md` §Severity — growth is one of four required factors. Promoted to an explicit schema enum in B |
| 15 | Resource-saturation reasoning | Present | `principles/resources.md`, `principles/throughput.md`, `bottleneck-analysis.md` §1 "Where are resources saturated or misconfigured?" |
| 16 | Queueing theory, Little's Law | Present | `principles/latency.md`, `principles/resources.md`, `rubrics.md` |
| 17 | Runtime evidence first-class | Present (partial) | The `Confirmed` grade already *requires* cited runtime evidence. Adapters are #18 |
| 18 | Runtime evidence adapters | Deferred — G | Blocked on Phase B and on real users supplying evidence |
| 19 | Before/after analysis | Deferred — G | Blocked on #18 |
| 20 | Validation loop | Deferred — G | Blocked on #18/#19 |
| 21 | Machine-checkable falsifiers | Adopted — B | Falsifiers are already *mandatory prose* (`validation.md` §1); B makes them parseable |
| 22 | A "do not optimize" finding | Present | `templates/review-report.md` §"Considered and not reported"; `bottleneck-analysis.md` §3 and §6 |
| 23 | Opportunity cost in recommendations | Present (partial) | `Trade-offs` is already a required finding field. Effort is deliberately excluded from priority — `architecture.md` §5 |
| 24 | Recommendation alternatives | Adopted — B | `alternatives[]` with a stated `preferred` and why |
| 25 | Detect cargo-cult optimizations | Present | `SKILL.md` Hard Rule 5 names caching, Redis, indexes, async, parallelism, sharding, microservices by name |
| 26 | Assumption ledger | Adopted — C3 | Upgrades the existing "Major unknowns" section rather than duplicating it |
| 27 | "Why this might not matter" | Adopted — B/C1 | Required for Medium severity and above; conditional, to respect the context budget |
| 28 | Counter-evidence search | Adopted — C1 | **The highest-value item in the review.** Partly present but unnamed and unrecorded: `bottleneck-analysis.md` §3 already carries an alternative-explanation test and an intent test. C1 names the discipline, makes it a reported field, and requires it to move confidence |
| 29 | Bayesian-style evidence updating | Adopted — C1 | As doctrine, not arithmetic: counter-evidence found must lower confidence |
| 30 | Change-scoped review as flagship | Adopted — F | Already a supported mode; F gives it a verdict model and a CI surface |
| 31 | Performance regression budget | Adopted — F | Part of the policy file |
| 32 | GitHub PR integration | Adopted — F | Advisory by default; never blocks a merge unless configured |
| 33 | SARIF / machine-readable output | Adopted — F | Cheap once B exists |
| 34 | Stable finding schema | Adopted — B | The keystone. Every measurement item in this review is downstream of it |
| 35 | Methodology versioning | Adopted — B | Partly present: frontmatter already carries `version` and `spec` |
| 36 | Version technology knowledge | Present | `docs/extending.md` requires §6 "Version differences worth knowing" in every technology file |
| 37 | Version-aware recommendations | Present | Same required section |
| 38 | Technology reference tests | Adopted — E | Folded into reference governance |
| 39 | Negative test cases per technology | Adopted — E | The "technology detected → technology-specific problem invented" failure is the one worth testing |
| 40 | False-positive corpus | Present | `tests/test_detect_stack_regressions.py`, in CI. Every fixture traces to a real bug found in behavioral evaluation. **Extended in D3, not replaced** |
| 41 | False-negative corpus | Present | `DriverCoverageTests` is exactly this — it found twelve false negatives in one audit |
| 42 | Precision vs recall as a product decision | Adopted — C | Stated as policy in `rubrics.md`: high precision at P0/P1, broader recall acceptable at P3 |
| 43 | Finding stability testing | Present (partial) → E5 | `evaluation.md` §3.18, §3.21 measured it twice by hand. E5 makes it a script |
| 44 | Measure agent cost | Present | `evaluation.md` §3.19, §3.20 — real tokenizer, four stack breadths |
| 45 | Reference retrieval efficiency | Present | `evaluation.md` §3.19 |
| 46 | Context-window stress tests | Adopted — E | Low priority within E |
| 47 | Sampling strategies for huge repositories | Adopted — F | Change-scoped mode already does dependency-aware expansion; F formalizes it |
| 48 | Critical-path graph | Present | `methodology/critical-paths.md` is this phase |
| 49 | Background-job analysis | Present | `critical-paths.md`; `workload.md` §"Scheduled and background workload" |
| 50 | Distributed failure modes | Present | `distributed/retries-and-backpressure.md`, `distributed/timeouts-and-deadlines.md` |
| 51 | Fan-out/fan-in detection | Present | `principles/work-and-algorithms.md`, `critical-paths.md`, and six technology files |
| 52 | Retries as work multipliers | Present | `distributed/retries-and-backpressure.md` |
| 53 | Cache analysis beyond "use a cache" | Present | `distributed/caching.md` covers hit rate, TTL, invalidation, stampede, staleness |
| 54 | Query shape analysis | Present | `databases/relational.md` and per-engine files |
| 55 | ORM analysis as a layer | Present | `application/data-access.md` |
| 56 | Query-plan parsing | Deferred — G | Blocked on #18 |
| 57 | Evidence quality separate from confidence | Adopted — B | JSON only; the human report already conveys this through the `Confirmed` grade |
| 58 | Evidence provenance | Adopted — B | source, file, line, tool, timestamp, user-provided vs derived |
| 59 | Reproducibility metadata | Adopted — B | Envelope-level |
| 60 | "Unknown" as a first-class state | Adopted — C4 | Distinguishes *no evidence exists* from *technology unsupported* |
| 61 | Scope completeness | Adopted — C4 | Extends the existing "Evidence available" and "Reference depth" fields |
| 62 | Review coverage confidence | Adopted — C4 | Explicitly distinct from finding confidence |
| 63 | "Review completeness" section | Adopted — C4 | What stops "no findings" being read as "everything is healthy" |
| 64 | Human feedback collection | **Declined** | The skill is Markdown installed into someone else's agent, running against their private code. It has no server and no telemetry channel, and adding one contradicts its own privacy posture — Hard Rule 3, and a detector that refuses to *read* a `.env`. Reconsider only if a hosted product surface ever exists |
| 65 | Finding feedback dataset | **Declined** | Same reason as #64 |
| 66 | Fix verification | Adopted — B (enabler) / G (workflow) | `stable_id` + `state` make it possible; the re-review workflow is deferred |
| 67 | Finding states | Adopted — B | `OPEN`/`ACKNOWLEDGED`/`IN_PROGRESS`/`RESOLVED`/`WONT_FIX`/`FALSE_POSITIVE`/`RETEST_REQUIRED` |
| 68 | IDs that survive code changes | Adopted — B | `stable_id` hashes root cause + file + symbol + mechanism |
| 69 | Historical comparison | Adopted — E5 | Falls out of `stable_id` |
| 70 | Performance debt tracking | Deferred — G | Needs persistence the skill does not have |
| 71 | README positioning | Adopted — docs | Lead with the value, not the implementation |
| 72 | 60-second demo | Adopted — docs | Genuinely missing and cheap |
| 73 | Real-world case studies | Present (partial) | `docs/examples/` exists; measured outcomes need #19 |
| 74 | Compare against baselines | **Done** | Run 2026-09-11: `benchmark/ab-comparison.md` (protocol, pre-registered) and `benchmark/ab-results/first-comparison.md` (results). Mixed — the methodology wins decisively on evidence discipline and loses on breadth |
| 75 | Cross-model evaluation | Deferred — G | Each data point is a full manual review. Blocked on E2 |
| 76 | Measure model independence | Deferred — G | Same as #75 |
| 77 | Agent-performance benchmark | Deferred — G | The most ambitious item in the review; blocked on E in its entirety |
| 78 | Mutation testing for findings | Adopted — E | Partly present: `check_repo_invariants.py` was itself mutation-tested before being trusted |
| 79 | Synthetic performance bugs | Deferred — G | The review's own #80 concedes these are weak for real-world reasoning. Building a generator before any ground truth exists optimizes the measurable half of the problem |
| 80 | Do not rely only on synthetic tests | Adopted — E | Recorded as a governing principle of E |
| 81 | Interaction effects | Adopted — C | One addition to `bottleneck-analysis.md` §2: independently moderate issues can combine into a severe one |
| 82 | Amplification factor | Present (concept) → B (field) | Amplification is already reasoned about in six files; B makes it a recorded value, numeric only when derivable |
| 83 | Workload scenarios | Adopted — C3 | Extends `workload.md` §5's existing 10× question |
| 84 | Data-volume modeling | Present | `workload.md` §"Data volume and growth signals" |
| 85 | Multi-tenant analysis | Present | `distributed/multi-tenancy.md` |
| 86 | Hotspot detection | Present | `critical-paths.md` |
| 87 | Tail-latency reasoning | Present | `principles/latency.md` |
| 88 | Variance analysis | Present | `principles/latency.md` |
| 89 | Cold vs warm analysis | Present | `principles/latency.md`, `application/connection-pools.md`, `critical-paths.md` |
| 90 | Cost awareness | Present | `cost` is already a finding category; `principles/resources.md`. Never inventing cost numbers is Hard Rule 1 |
| 91 | Energy efficiency | **Declined** | Out of scope; the review marks it optional |
| 92 | Reference governance | Adopted — E | Promotion to `deep` should require benchmark evidence that the reference improves output. Enforceable the moment E exists |
| 93 | Reference quality scores | Adopted — E | Depends on #92 |
| 94 | Known blind spots per technology | Present | `docs/extending.md` requires §7 "What this file does NOT cover" in every technology file |
| 95 | Technology contribution template | Present | The seven-section structure in `docs/extending.md`, plus `.github/ISSUE_TEMPLATE/technology-request.yml` |
| 96 | Automated reference linting | Present | `scripts/check_repo_invariants.py`, in CI — checks product-name leakage, required sections, load order, tier counts, line caps |
| 97 | Link/source validation | Present | `scripts/check_links.py`, in CI |
| 98 | Source provenance for technical claims | Adopted — E | Partly present: `CONTRIBUTING.md` §5 already forbids unsourced numbers |
| 99 | Source strength classification | Adopted — E | Folded into #98 |
| 100 | Research references | Adopted — docs | Low priority |
| 101 | Two-layered report | Present | The template is already layered: §1 executive summary for humans, §6–§7 evidence and reasoning for engineers |
| 102 | Top 3 actions | Present | Template §1 "Most important findings" and §8 "Prioritized action plan" |
| 103 | "Don't do these things" | Present | `SKILL.md` §"Anti-patterns in your own output" |
| 104 | Explicit decision tree | Adopted — C | A compact decision model in `bottleneck-analysis.md`, supporting the discard discipline |
| 105 | Performance hypothesis object | Reframed — B | The finding schema *becomes* the hypothesis object once `counter_evidence` and structured `validation` are fields. No separate object needed |
| 106 | Separate detection from judgment | Reframed — C2 | Doctrine ordering, not a pipeline |
| 107 | Separate recommendation from detection | Adopted — C2 | Wanting to recommend Redis makes Redis-shaped problems easier to see. Cheap rule, real effect |
| 108 | Recommendation validation tests | Adopted — E | Test that the right *fix* is preferred, not just the right problem found |
| 109 | Track recommendation quality separately | Adopted — E2 | Finding accuracy and recommendation accuracy are scored separately |
| 110 | Trade-off correctness testing | Adopted — E | Depends on #108 |
| 111 | Security/correctness boundary tests | Adopted — D3 | The *test* is adopted; the boundary does not move. Security review is out of scope by design (`roadmap.md`), and Hard Rule 8 already governs adjacent findings |
| 112 | Continuous secret-safety tests | Present | `evaluation.md` §2 and the detector's `SECRET_PATTERNS`. Kept in the regression suite by D3 |
| 113 | Malicious repository tests | Adopted — D3 | Repository content is data, never instructions |
| 114 | Prompt-injection resistance | Adopted — D3 | First-class security test for an agent skill |
| 115 | Tool-safety classification | Present (2-level); expansion **declined** | `safe-on-production` / `not-safe-on-production` is enforced on every diagnostic. A five-level scale adds report surface without changing any decision the reader makes |
| 116 | Diagnostic command budgets | Adopted — C5 | Partly present through `not-safe-on-production`; C5 adds asking before expensive diagnostics |
| 117 | Environment awareness | Adopted — C5 + 1.0.0 infra batch | Needs `kubernetes`/`docker`/`serverless` at `deep` tier |
| 118 | Deployment-aware reasoning | Present (partial) → 1.0.0 | `infrastructure/resources.md` covers limits and autoscaling; deepened by the infrastructure batch |
| 119 | Configuration cross-validation | Present | `evaluation.md` required case 6 — pool size vs worker count, heap vs container limit — checked on every evaluated repository |
| 120 | Configuration contradiction detection | Present | Same as #119; `infrastructure/resources.md` |
| 121 | Performance invariants | Adopted — F | Part of the policy file |
| 122 | Repository-specific policies | Adopted — F | |
| 123 | Policy packs | Deferred — G | Needs #122 in real use first |
| 124 | Policies must be evidence-aware | Adopted — F | **Kept as a hard rule:** a policy violation is not a measured performance problem, and the two are never reported as the same thing |
| 125 | Performance contract | Adopted — F | |
| 126 | Service-level objectives | Adopted — F | Consumer input, not a derived finding property |
| 127 | Error-budget awareness | Adopted — F | Same |
| 128 | Business-criticality metadata | Adopted — F | Policy-side only |
| 129 | Two-dimensional priority | **Declined** as a finding field; adopted F-side | Letting business context into the finding schema would let it corrupt technical severity — the exact failure deriving priority exists to prevent (`architecture.md` §5). Sequencing may use it; scoring may not |
| 130 | Evidence budget | Adopted — C5 | Investigation depth proportional to candidate priority |
| 131 | Adaptive investigation | Adopted — C5 | |
| 132 | Stop conditions | Adopted — C5 | When to stop investigating — prevents unbounded exploration cost |
| 133 | Explicit uncertainty boundaries | Adopted — C4 | What static analysis can and cannot establish, stated per review |
| 134 | Adaptive workload questionnaire | Adopted — C3 | The seven-question cap stays; which seven becomes risk-driven |
| 135 | Prioritize questions by information gain | Adopted — C3 | Ask the question most likely to change the ranking |
| 136 | "Decision-changing questions" section | Adopted — C3/C4 | More useful than "more information is needed" |
| 137 | Interactive follow-up reviews | Adopted — C3 | Incremental evidence updates revise confidence and priority |
| 138 | More evidence ≠ higher severity | Present | The core of the two-axis rubric; `architecture.md` §5 explains why |
| 139 | Formal finding lifecycle | Adopted — B (states) / Deferred — G (workflow) | |
| 140 | Strategic: stop adding technologies | Reframed | Only **7 signals** remain below `deep`, in two small batches — not six months of work. Three of them (`kubernetes`, `docker`, `serverless`) are prerequisites for the deployment-aware reasoning the review itself asks for in #117/#118. Both tracks run in parallel; `architecture.md` §2 designed the tree so they do not collide. The point that survives: **after 1.0.0, promotion should require benchmark evidence** — which is #92 |

---

## Summary

| Disposition | Count |
|:--|--:|
| Present (already implemented) | 44 |
| Adopted | 74 |
| Reframed | 6 |
| Deferred | 12 |
| Declined | 4 |
| **Total** | **140** |

Counted by primary disposition, since several items are hybrids — #43 is present-but-informal and
adopted for formalization, #66 and #139 are part-adopted and part-deferred, and #115's *expansion*
is declined while the underlying capability is present. Outright declines are #64, #65, #91, and
#129.

The single largest category is work that already existed under a different name. That is worth
recording plainly: an external reviewer reading the repository fresh could not tell that
`bottleneck-analysis.md` §3's "alternative-explanation test" is counter-evidence search, or that
`extending.md` §7 "What this file does NOT cover" is a known-blind-spots register. Where a
capability is real but unfindable, the naming is the defect — and several items above are adopted
precisely to give an existing discipline a name and a recorded output.
