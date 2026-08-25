# Extending the skill

How to add a database, a runtime, a whole datastore category, or a new principle — without
touching the core methodology.

This single document replaces what would otherwise be three near-identical files, because the
process differs only in which directory the file lands in.

---

## The general shape

Every extension is the same two steps:

1. **Write one reference file** in the right directory.
2. **Add one entry to `registry.yaml`** pointing at it.

Nothing else changes. Not `SKILL.md`, not the methodology, not the rubrics. If your addition
seems to require changing those, the routing layer has a gap — raise an issue rather than
special-casing.

---

## 1. Adding a database engine

Say you want to add CockroachDB, currently at `conceptual` tier.

### Step 1 — decide what actually differs

Before writing anything, read `databases/universal.md` and `databases/relational.md` and ask:
**what would a competent engineer get wrong if they applied only those two files to this
engine?**

For a distributed SQL engine the answers might include: transactions can be retried by the
engine and application code must handle that; data is distributed in ranges so key choice
affects placement; a query touching many ranges pays cross-node latency the single-node
relational model does not predict.

That list is your file. Everything else is already covered.

If your list is empty or nearly empty, the honest outcome is **leave it at `conceptual`** and
improve the registry note instead. Not every engine needs a file, and a file that restates its
category is worse than no file.

### Step 2 — write the file

`skills/backend-performance-review/technology/cockroachdb.md`, using the mandatory structure:

```markdown
# CockroachDB

Load after `databases/universal.md` and `databases/relational.md`.

This file contains only what those files do not give you.

## 1. Detection signals
## 2. What differs from the relational baseline
## 3. Diagnostics          ← every command labelled safe-on-production or not
## 4. Common failure modes and their symptoms
## 5. Configuration worth checking, and what it trades
## 6. Version differences worth knowing
## 7. What this file does NOT cover
```

Section 7 is mandatory. It is what lets the skill tell a user where its knowledge stops
instead of implying completeness.

### Step 3 — register it

```yaml
- signal: cockroachdb
  kind: datastore
  category: relational
  match: [cockroach, cockroachdb, "cockroachdb://", "postgresql://...cockroachlabs"]
  load: [databases/universal.md, databases/relational.md, technology/cockroachdb.md]
  tier: deep
```

Promote `tier` from `conceptual` to `deep`, and drop the `notes` field that explained the
limitation — it is no longer true.

### Step 4 — verify

```bash
python skills/backend-performance-review/scripts/detect_stack.py /a/repo/using/it --pretty
```

Confirm the signal fires, that `references_to_load` lists the category files before yours, and
that the tier is reported correctly. Then run the consistency checks in `CONTRIBUTING.md` §10.

### Step 5 — update the README table

The support table is generated from the registry. Regenerate or edit it so the two agree.

---

## 2. Adding a datastore category

Larger than adding a technology reference. Every planned category (graph, wide-column, search,
time-series, vector, and object storage) now has a file — a new category file is only needed if
a genuinely new kind of datastore emerges that doesn't fit the existing nine.

A category file answers: **what is true of every engine in this category, and false or
different in the others?**

Use the existing category files as the shape. A good category file covers:

- What the category is optimized for, and what is expensive in it — the trade at its core.
- The data-modeling decision that dominates its performance. (For document stores this is
  embedding versus referencing; for wide-column it is partition and clustering key design; for
  vector it is index type and the recall/latency trade.)
- Access patterns that scale badly, and why — mechanism, not a list.
- What "unbounded" looks like in this category.
- What evidence the category's engines typically expose.
- What to look for, and what not to conclude.

**The hard rule: name no products.** If you cannot express something without naming an engine,
it belongs in a technology file. This is what keeps the category file useful for engines
nobody has written a file for yet.

Then update every registry entry in that category to load the new file, and promote those
entries from `generic` to `conceptual`.

---

## 3. Adding a runtime

Runtimes work slightly differently. `runtimes/universal.md` is a **taxonomy**, not a survey:
it defines the dimensions — execution model, parallelism, concurrency model, memory
management, startup cost — and the failure modes follow from where a runtime sits on them.

A runtime technology file therefore answers: **where does this runtime sit on each dimension,
and what does that make it uniquely vulnerable to?**

`skills/backend-performance-review/technology/node.md` might cover the single-threaded event
loop and what blocking it costs, the bounded auxiliary threadpool and what silently uses it,
worker threads and their memory cost, GC flags and their interaction with container memory
limits, and how to detect event-loop lag.

Same seven-section structure. Same rule: if it is true of every event-loop runtime, it belongs
in `runtimes/universal.md`.

```yaml
- signal: node
  kind: runtime
  match: ["package.json", "node_modules", nodejs]
  load: [runtimes/universal.md, application/async-and-blocking.md, technology/node.md]
  tier: deep
```

---

## 4. Adding or changing a principle

Highest bar in the project. `principles/` files change what the agent looks for in **every**
review, so a weak addition costs context on every run forever.

Before proposing one, check it is not already covered — the five existing files are broad by
design, and `principles/resources.md` in particular absorbs what would otherwise be four
separate files.

A new principle file must:

- Describe a **mechanism**, not a pattern. "Queueing grows non-linearly near saturation" is a
  principle. "Watch out for N+1" is a pattern, and belongs in a layer file.
- Be independent of language, framework, and engine.
- Change what the agent looks for, not just how it phrases things.
- End with "what to look for" and "what not to conclude". The second section is what makes a
  principle file reduce false positives rather than generate them.

Open an issue before writing. Adding a principle is closer to a methodology change than to a
content addition.

---

## 5. Adding a layer

`application/`, `distributed/`, and `infrastructure/` hold layer files — the manifestation of
principles in a specific part of a system.

Add one when there is a whole class of performance concern with no home. Serverless cold
starts, for instance, currently live inside `infrastructure/resources.md`; if that treatment
grows past the point where it is a section, it becomes its own file.

Do not add a layer file for a single pattern. Add it to the closest existing file instead.

---

## 6. Improving an existing file

The most valuable edits are usually **discriminations**, not additions:

> "This matters when the collection is unbounded. When a hard page cap exists, the same
> pattern is at most a `Medium` finding."

That kind of sentence turns a rule into judgment, and judgment is the entire product. Compare
with adding a fourth example of the same pattern, which costs context and adds nothing.

If you are removing content because it was generic filler, say so in the PR description. That
is a real improvement and reviewers should treat it as one.

---

## 7. What not to do

- **Do not add a checklist.** If your contribution is a list of things to check with no
  mechanism behind them, it is the failure mode the whole project is built to avoid.
- **Do not add specific configuration values.** They depend on hardware and workload, and a
  value copied from a reference file will be wrong somewhere. Explain the trade and name the
  measurement.
- **Do not add performance guarantees.** No "this will make it 10× faster".
- **Do not copy vendor documentation.** Paraphrase, link, attribute.
- **Do not put examples under `skills/`.** They must not be loadable as references.
- **Do not restate a category file in a technology file.** The most common rejection reason.

---

## 8. Checklist before opening a pull request

- [ ] File is in the right directory for its layer.
- [ ] Technology file has all seven sections, including "does NOT cover".
- [ ] Nothing in it is derivable from its category file.
- [ ] Category files still name no products.
- [ ] Every diagnostic command carries a production-safety label.
- [ ] No specific config values, no performance guarantees, no invented metrics.
- [ ] Registry entry added, category file ordered first, tier declared.
- [ ] `detect_stack.py` fires the signal on a real repository.
- [ ] Consistency checks in `CONTRIBUTING.md` §10 pass.
- [ ] README support table matches the registry.
- [ ] Under the ~400 line soft cap, or a stated reason.
- [ ] `CHANGELOG.md` updated under Unreleased.
