# Contributing

Thank you for considering a contribution. This project has an unusual bar: the value of the
skill is entirely in whether an agent following it produces *fewer, better* findings than one
that does not. Content that adds words without adding discrimination makes the project worse.

Please read the review gates in §5 before opening a pull request — they are what a maintainer
will check first.

---

## 1. The most valuable contributions

In rough order:

1. **A reported false positive.** A case where the skill produced a finding that was wrong,
   irrelevant, or unactionable. These are the highest-signal bug reports this project can
   receive. Include the code shape that triggered it and why it was wrong.
2. **Promoting a technology from `conceptual` to `deep`** by writing its reference file.
3. **A new datastore category file** (graph, wide-column, search, time-series, vector) — these
   are the largest gaps in v0.1.0.
4. **Sharpening a methodology or principles file** by making a discrimination clearer:
   when does this apply, when does it not.
5. **A worked example** demonstrating restraint as well as findings.
6. **Validation techniques** — better diagnostics, safer commands, cheaper regression guards.

---

## 2. Adding a technology reference

Two steps. Nothing else changes — not `SKILL.md`, not the methodology.

**Step 1 — write `skills/backend-performance-review/technology/<name>.md`** using the
mandatory seven-section structure:

```
1. Detection signals
2. What differs from the category baseline
3. Diagnostics — every command labelled safe-on-production or not
4. Common failure modes and their observable symptoms
5. Configuration knobs that matter, and what they trade off
6. Version differences worth knowing
7. What this file does NOT cover
```

Section 7 is not optional. Honest scope beats implied completeness, and it is what lets the
skill tell a user where its knowledge stops.

**Step 2 — add one entry to `registry.yaml`:**

```yaml
- signal: cockroachdb
  kind: datastore              # datastore | cache | broker | runtime | framework | infrastructure
  category: relational         # for datastores
  match: [cockroach, cockroachdb, "cockroachdb://"]
  load: [databases/universal.md, databases/relational.md, technology/cockroachdb.md]
  tier: deep
```

`load` must list the category file **before** the technology file. Update `tier`, and the
README support table is generated from the same source — keep them consistent.

Verify the entry parses and matches:

```bash
python skills/backend-performance-review/scripts/detect_stack.py /path/to/a/repo/using/it --pretty
```

Detailed walkthrough, including adding a whole category or a runtime:
[docs/extending.md](docs/extending.md).

---

## 3. The non-derivable content rule

**A technology file may contain only what is not derivable from its category file.**

This single rule is what keeps a growing set of engine references from collapsing into a pile
of near-identical checklists.

| Belongs in a technology file | Belongs in the category file |
|:--|:--|
| This engine's specific failure modes | What an index is and when it helps |
| Its diagnostic commands and their output | Selectivity, cardinality, compound ordering |
| Its config knobs and what they actually control | Why transaction scope affects contention |
| Behavior that surprises people coming from other engines | Working-set and cache reasoning |
| Version-specific differences | Round trips versus payload size |

If a paragraph in your technology file would be equally true of three other engines in the
same category, it belongs in the category file — or nowhere.

**Category files must not name products.** If `databases/document.md` mentions a specific
product, that content belongs in a technology file.

---

## 4. Writing standards

**Every claim must be a mechanism or a citable fact.** Explain *why* something is slow, not
that it is. Folklore ("everyone knows X is slow") is rejected regardless of whether it happens
to be true.

**No performance guarantees.** Never "this will make it 10× faster". Magnitudes depend on
workload, data, and hardware. Say what changes and what to measure.

**No invented numbers.** This applies to the reference content itself, not only to generated
reports. Do not include specific latencies, throughputs, or ratios unless they are structural
facts rather than measurements.

**No specific configuration values.** Do not write "set `work_mem` to 64MB". Explain what the
setting trades and recommend the measurement that determines it. Values depend on hardware and
workload, and a value copied from a reference file will be wrong somewhere.

**No copied documentation.** Paraphrase, and link. Do not reproduce vendor tables, prose, or
examples. Attribute where attribution is required.

**Version claims must be checkable.** Where behavior depends on a version, say so and point at
release notes rather than asserting it flatly. Prefer "confirm against the release notes for
the deployed version" to a confident statement that may be stale.

**Soft cap of ~400 lines per reference file.** Longer usually means it should be split, or
that it has drifted into restating a category file.

**Every diagnostic command needs a production-safety label.** `safe-on-production` or
`not-safe-on-production`, with the safe alternative where it exists. This is a hard requirement:
these commands end up in reports that people actually run.

---

## 5. Review gates

A pull request will be checked against these. Most rejections are one of the first four.

- [ ] No cargo-cult recommendation. Nothing suggests caching, Redis, indexes, async,
      parallelism, sharding, denormalization, microservices, more servers, or a different
      language as a default. Where any is discussed, the conditions and trade-offs are stated.
- [ ] No unsupported claim. Every assertion is a mechanism or a citable fact.
- [ ] No product name in a category file.
- [ ] No technology file restating its category file.
- [ ] All seven sections present in a technology file, including "does NOT cover".
- [ ] Every diagnostic command carries a production-safety label.
- [ ] No specific configuration values or performance guarantees.
- [ ] No invented metrics anywhere, including in examples.
- [ ] Registry entry added, category file ordered before technology file, tier declared.
- [ ] README support table consistent with `registry.yaml`.
- [ ] Under the ~400 line soft cap, or a reason given.

---

## 6. Contributing an example

Examples are in `docs/examples/` and **never** under `skills/` — they must not be loadable as
references, because example content leaks into real reviews as few-shot patterns.

Requirements:

- **No invented runtime metrics.** Where an illustrative figure is unavoidable, mark it inline
  as `[illustrative — not measured]`.
- **Demonstrate restraint.** Every example must include at least one explicit unknown, and at
  least one plausible optimization that is deliberately *not* recommended, with the reason.
  An example that only finds things teaches the wrong behavior.
- **Do not be artificially perfect.** Real reviews have gaps, uncertainty, and judgment calls.

---

## 7. Reporting a false positive

Open an issue with:

1. The code shape (minimal, anonymized) that triggered the finding.
2. What the skill reported.
3. Why it was wrong — irrelevant to the workload, factually incorrect, or unactionable.
4. Which reference file you believe led it there.

If the fix is a sharper discrimination in a reference file — "this matters when X, not when
Y" — that is exactly the contribution the project wants most.

---

## 8. Proposing a methodology change

Changes to `SKILL.md`, `rubrics.md`, or `methodology/` affect every review. Open an issue
first and explain:

- What failure mode the current methodology produces.
- Why it cannot be fixed in a reference file instead.
- What the change would cause the agent to do differently.
- Whether it changes the finding schema or the rubrics — if so, it is a **breaking change**
  under the versioning policy, because previously issued severities would no longer be
  reproducible.

Prefer changes that make the agent *discard* more candidates over changes that make it find
more. Precision is the scarce resource here.

---

## 9. Local checks

```bash
# registry parses, and every entry has load + tier
python - <<'PY'
import sys; sys.path.insert(0, "skills/backend-performance-review/scripts")
import detect_stack as d
entries, warnings = d.parse_registry("skills/backend-performance-review/registry.yaml")
assert not warnings, warnings
missing = [e["signal"] for e in entries if not e.get("load") or not e.get("tier")]
assert not missing, missing
print(f"{len(entries)} registry entries OK")
PY

# every referenced file exists
python - <<'PY'
import os, sys; sys.path.insert(0, "skills/backend-performance-review/scripts")
import detect_stack as d
root = "skills/backend-performance-review"
entries, _ = d.parse_registry(f"{root}/registry.yaml")
missing = {r for e in entries for r in e.get("load", []) if not os.path.exists(f"{root}/{r}")}
assert not missing, missing
print("all registry references resolve")
PY

# no product names in category files
! grep -rniE 'postgres|mysql|mongodb|redis|cassandra|dynamodb|neo4j' \
    skills/backend-performance-review/databases/ \
    skills/backend-performance-review/principles/ \
  && echo "category files are product-neutral"
```

## 10. Code of conduct

By participating you agree to abide by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## 11. License

Contributions are accepted under the [MIT License](LICENSE). By submitting a pull request you
confirm you have the right to license your contribution under those terms, and that it
contains no copied proprietary documentation.
