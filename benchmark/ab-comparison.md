# Does the methodology actually help?

The question this project has to answer before any other: **does a strong coding agent
following this methodology produce materially better performance reviews than the same model
asked plainly?** Everything else here — the schema, the harness, the corpus, the reference
tree — is machinery in service of an answer that had never been measured.

This file is the protocol. It was written and committed **before** the control runs were
read, so the metric definitions could not be fitted to the result. That ordering is the
whole point; a comparison designed after seeing the data proves nothing.

---

## 1. Design

Paired, same-code comparison. For each case: one repository, pinned to one commit, reviewed
twice by the same model — once following the skill, once not.

| Arm | What the model is given |
|:--|:--|
| **Treatment** | The repository, plus `SKILL.md` and the reference tree, following the full methodology |
| **Control** | The repository, plus a plain request for a performance review. No methodology, no reference files, no schema |

The control prompt must be **what a competent engineer would actually type**, not a strawman
that hobbles the model. The exact prompt used:

> You are an experienced backend engineer. Review the backend codebase at this path for
> performance issues. Write a performance review report: what the performance problems are,
> where they are, how serious each one is, and what you would do about each. Prioritize what
> matters most. Go into enough detail that an engineer could act on your findings.

Both arms are barred from reading this project's own evaluation material
(`docs/evaluation.md`, `docs/review-response.md`, `benchmark/ground-truth/`). The control arm
is additionally barred from reading any skill file — that is what makes it a control.

### One piece of accidental hygiene worth recording

The treatment runs were **not produced for this comparison**. They are the blind passes from
the first real validation run (§"The first real validation run" in [README.md](README.md)) —
run earlier, blind, to measure run-to-run stability and to attempt a zero-findings case. They
were already committed to the corpus before an A/B was designed. A treatment arm that cannot
have been tuned to win the comparison it is later entered into is worth more than one
generated alongside its own control.

---

## 2. Threats to validity, stated before the result

A comparison run by the author of the thing being compared is worth exactly as much as its
stated threats. These are the ones that apply, and what is done about each.

**The ground truth is biased toward the treatment.** The `expected` items in
`ground-truth/*.json` were derived partly from skill-guided runs and then spot-verified
against source. Scoring *recall* against that corpus therefore favours the treatment by
construction: it is partly being scored against its own output. This is not fixable by
argument, so recall is demoted to a secondary metric and reported with this caveat attached.
The primary metrics (§3) do not touch `expected` at all.

**Control output is prose; scoring wants structure.** The control arm emits a Markdown report,
not schema-valid JSON — itself a real difference, but it means mechanical scoring requires
transcription. Transcription rule: **transcribe literally, never repair.** A field the control
did not state is recorded as absent, not inferred, not charitably filled in. If the control
named no file, `location.file` is empty — it is not resolved by going and looking. The
temptation runs the other way too: a transcriber who wants the treatment to win can flatten a
good control finding. Both directions are failures.

**A single model says nothing about model-independence.** This measures whether the
methodology helps *this* model. Whether it helps others is a separate open question — see
`docs/roadmap.md`, and item 75 in [review-response.md](../docs/review-response.md).

**n is small.** Three paired runs across two repositories and two stacks. Whatever comes out
is directional evidence, not a general result, and must be described as such.

**The control has no budget discipline imposed.** The treatment caps full-format output at
10–15 findings; the control is uncapped. If the control reports more findings, that is not
automatically worse — it is only worse if the extra findings are wrong, unsupported, or
forbidden. The metrics below are rate-based for exactly this reason.

---

## 3. Primary metrics — ground-truth-independent

These are the point. Each measures a discipline the methodology explicitly claims to impose,
and none requires the corpus to have guessed the right answers, so none inherits the bias
named above. Each is a **rate**, so an arm is not penalised for reporting more.

| Metric | Definition | Which rule it tests |
|:--|:--|:--|
| **Unsourced number rate** | Share of findings containing a performance number (latency, throughput, CPU, memory, hit rate, row count, traffic) that is not traceable to a repo file, a supplied measurement, or a shown derivation | Hard Rule 1 — "never invent numbers" |
| **Citation rate** | Share of findings naming a specific file the claim rests on | Evidence-first: a finding that cites nothing cannot be checked |
| **Falsifiability rate** | Share of findings stating what would disprove them, or the conditions under which they would not matter | `Counter-evidence` and `Why this might not matter` |
| **Conditioned-recommendation rate** | Share of recommendations naming the workload under which the change pays off | Hard Rule 5 — the `Conditions` field may never be empty |
| **Cargo-cult rate** | Share of recommendations proposing caching, Redis, added indexes, async, parallelism, sharding, denormalization, or microservices **without** a stated condition | Hard Rule 5, named list |
| **Confidence-ceiling violations** | Claims of measured fact where no runtime artifact exists in the repository | The `Confirmed` grade requires a citation |

A number that looks precise and is invented is the single most damaging output a performance
review can produce, because it is the one a reader cannot check and will act on. If the
methodology earns its context cost anywhere, it should be here.

## 4. Secondary metrics — ground-truth-dependent, declared biased

Reported for completeness, never as the headline.

| Metric | Caveat |
|:--|:--|
| Recall against `expected` | **Biased toward the treatment.** The corpus was partly derived from treatment output |
| Precision against `expected` + `acceptable` | Less biased — `acceptable` exists precisely so a real finding the annotator missed is not punished — but a control finding falling outside both buckets needs adjudication (§5) before it counts as a false positive |
| `forbidden` trap hits | **The least biased ground-truth metric.** Every `forbidden` entry carries a `why_not` that is a fact about the code, not a fact about what some run found. A trap hit is a review asserting something the source refutes |

## 5. Adjudication

A control finding matching nothing in the corpus is **not** automatically a false positive.
The corpus was annotated against treatment output and will have gaps. Procedure, in this
order, so the outcome cannot be decided by where a reviewer chose to stop:

1. Check the claim directly against the repository source.
2. If real and material → add an `acceptable` entry. It scores neither way, and the corpus
   improves.
3. If real but the source refutes its significance → add a `forbidden` entry **with the
   `why_not` fact that refutes it.** Never add a trap without the counter-evidence.
4. If not real → it is a false positive, and the finding is recorded verbatim in the results
   so the judgement can be argued with.

Findings adjudicated this way are marked in the corpus with their origin, so a future reader
can tell which entries came from treatment output and which from control output. An entry
added because the control found it is evidence *against* the corpus's original completeness,
and should be visible as such.

---

## 6. Results

Recorded in [`ab-results/`](ab-results/) once the control runs are adjudicated, one file per
case, plus a summary. Every claim there must cite the run it came from, and both arms' raw
output is committed so the numbers can be recomputed rather than trusted.

**Reporting rule, fixed in advance:** if the primary metrics do not separate the two arms,
that is the finding and it gets reported exactly as prominently as a positive result would
have. A methodology that cannot be shown to beat asking plainly is a methodology with a
problem worth knowing about, and this project's entire claim is that it would rather know.
