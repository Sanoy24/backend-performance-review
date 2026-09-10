# Contributing

Thank you for considering a contribution. This project has an unusual bar: the value of the
skill is entirely in whether an agent following it produces *fewer, better* findings than one
that does not. Content that adds words without adding discrimination makes the project worse.

Please read §2 for how branching and pull requests work here, and the review gates in §6 before
opening one — they are what a maintainer will check first.

---

## 1. The most valuable contributions

In rough order:

1. **A reported false positive.** A case where the skill produced a finding that was wrong,
   irrelevant, or unactionable. These are the highest-signal bug reports this project can
   receive. Include the code shape that triggered it and why it was wrong.
2. **Promoting a technology from `conceptual` to `deep`** by writing its reference file — the
   largest gap remaining now that every datastore category has a file.
3. **Sharpening a methodology or principles file** by making a discrimination clearer:
   when does this apply, when does it not.
4. **A worked example** demonstrating restraint as well as findings.
5. **Validation techniques** — better diagnostics, safer commands, cheaper regression guards.

---

## 2. Branching and pull requests

This project uses a two-branch model: **`develop` is where work lands, `main` is what has
been released.** Both are protected; every change reaches either one through a pull request.

| Branch | What it holds | What targets it |
|:--|:--|:--|
| `develop` | Current state of work. The default branch — always green, not necessarily released | Every `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/` branch |
| `main` | The last released state, and nothing else. Every tag is cut from here | A release PR from `develop`, or a `hotfix/` branch |

### The model

1. Branch from `develop`: `<type>/<short-description>`, matching the commit-prefix convention
   below — `feat/graph-category`, `fix/registry-collision`, `docs/branching-workflow`.
2. Commit there. Prefixes follow [Conventional Commits](https://www.conventionalcommits.org/):
   `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`. Keep the first line under about 70
   characters; put the "why," not just the "what," in the body.
3. Open a pull request into `develop` (the default target). Use the PR template — it mirrors
   §6's review gates as checkboxes, so filling it in honestly is most of the review.
4. Both `develop` and `main` are **protected branches**: no direct pushes, including from
   repository admins. All five CI jobs in `.github/workflows/checks.yml` must pass before a PR
   can merge, the branch must be up to date with its target first (GitHub will prompt to update
   it), and history stays linear — merges are squashed or rebased, not merge-commits.
5. Delete the branch after merging. A merged feature branch has no further purpose, and letting
   branches accumulate makes it harder to tell what's actually in flight.

### Releasing

A release is a deliberate milestone, not an automatic consequence of merging:

1. Open a `chore/release-vX.Y.Z` PR **from `develop` into `main`**, moving `CHANGELOG.md`'s
   `[Unreleased]` section under a dated version heading and bumping the version in all five
   places the version-coherence invariant checks (`.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json`, `skills/backend-performance-review/SKILL.md`, README's
   badge, `CITATION.cff`).
2. Merge it, then tag `main` — `.github/workflows/release.yml` publishes the GitHub release
   from that version's CHANGELOG section on any `v*.*.*` tag push.
3. Merge `main` back into `develop` (or rebase `develop` onto it) so the two do not diverge.

Between releases, version numbers do not move: files stay at the last released version and
`[Unreleased]` accumulates. **Patch and minor releases still happen when something genuinely
warrants shipping sooner** — a detection bug that silently drops a signal, say — but the default
is to let work accumulate toward a substantial milestone rather than tagging every batch of
merges.

### Hotfixes

When something on `main` needs fixing before `develop` is ready to release:

1. Branch `hotfix/<short-description>` **from `main`**, not `develop`.
2. PR it into `main`, tag the patch release from `main`.
3. **Merge it into `develop` as well**, so the next release does not silently revert the fix.
   This back-merge is the step that gets forgotten; treat it as part of the hotfix, not as
   follow-up work.

### Why this model, and why not trunk-based any more

The earlier trunk-based model said GitFlow's extra branch types "exist to solve problems this
project doesn't have yet," and named the condition for revisiting: shipping versioned releases
that need work to continue independently of them. That condition now holds. Six releases landed
in the project's first weeks, several of them cutting a version for what was really a single
afternoon's work — the cadence was driven by "`main` moved" rather than by "there is something
worth releasing." Separating the two branches makes that distinction structural instead of a
matter of restraint: merging to `develop` no longer implies a release, so a release can be a
decision about readiness rather than a bookkeeping step.

The cost is real and worth naming: two branches can diverge, and the hotfix back-merge above is
a genuine footgun. That trade is worth it here specifically because `main` now means something
checkable — "the code behind the latest tag" — which it did not before.

There is currently no required-approval count on `main` (`required_approving_review_count: 0`)
— every change still goes through a PR and both CI checks, but a solo maintainer isn't blocked
waiting on a second reviewer who doesn't exist yet. **Add a required-approval count as soon as
this project has more than one active maintainer** — a repository with real contributors and no
review requirement is a repository where the review gates in §6 are unenforced in practice, no
matter how well they're written down.

---

## 3. Adding a technology reference

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

## 4. The non-derivable content rule

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

## 5. Writing standards

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

## 6. Review gates

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
- [ ] `CHANGELOG.md` updated under `[Unreleased]`; `docs/roadmap.md` no longer lists the
      change as an open item.
- [ ] Under the ~400 line soft cap, or a reason given.
- [ ] A reported false positive or false negative in `detect_stack.py` ships with a new
      fixture in `tests/` reproducing it.

---

## 7. Contributing an example

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

## 8. Reporting a false positive

Open an issue with:

1. The code shape (minimal, anonymized) that triggered the finding.
2. What the skill reported.
3. Why it was wrong — irrelevant to the workload, factually incorrect, or unactionable.
4. Which reference file you believe led it there.

If the fix is a sharper discrimination in a reference file — "this matters when X, not when
Y" — that is exactly the contribution the project wants most.

---

## 9. Proposing a methodology change

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

## 10. Local checks

```bash
python scripts/check_repo_invariants.py
python -m unittest discover -s tests
```

Both run automatically on every push and pull request (`.github/workflows/checks.yml`); run
them locally before opening a PR to catch the same failures sooner.

`check_repo_invariants.py` verifies every gate in §6: the registry parses and every entry
resolves, category files stay product-neutral, technology files have all seven required
sections, the priority matrix is identical everywhere it is published, the published tier
counts match the registry, and `detect_stack.py` imports only the standard library.

`tests/` is the false-positive/false-negative fixture corpus described in `docs/evaluation.md`
§4 — each test reproduces the actual collision text from a real lockfile, CI workflow, or
compose file that once produced a wrong detection, and asserts it stays fixed. Adding a fixture
here is the expected response to any newly reported false positive or false negative in
`detect_stack.py`.

## 11. Conduct

Be respectful, and argue with the claim rather than the person. This is a project about
evidence, so challenging a finding or a design decision is expected and welcome — "this
assertion has no mechanism behind it" is a good review comment. Keep it there.

The full policy, including how to report a problem, is [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## 12. License

Contributions are accepted under the [MIT License](LICENSE). By submitting a pull request you
confirm you have the right to license your contribution under those terms, and that it
contains no copied proprietary documentation.
