## What this changes and why

<!-- One or two sentences. The "why" matters more than the "what" — the diff already shows what
     changed. -->

## Type of change

- [ ] New technology reference (promotes a `conceptual`/`generic` entry toward `deep`)
- [ ] New datastore category
- [ ] Methodology / principles / rubric change
- [ ] Bug fix (false positive, detection bug, broken reference)
- [ ] Documentation only
- [ ] Tooling / CI

## Review gates (CONTRIBUTING.md §6)

Check what applies. An unchecked box isn't a blocker by itself — it's a prompt to explain why in
a comment, so the reviewer isn't left guessing.

- [ ] No cargo-cult recommendation introduced or endorsed without stated conditions and trade-offs.
- [ ] No unsupported claim — every assertion is a mechanism or a citable fact.
- [ ] No product name in a category file (`databases/*.md`).
- [ ] No technology file restating its category file (only non-derivable content).
- [ ] All seven required sections present in any new/changed technology file, including
      "does NOT cover".
- [ ] Every diagnostic command carries a `safe-on-production` / `not-safe-on-production` label.
- [ ] No specific configuration values or performance guarantees invented.
- [ ] No invented metrics anywhere, including in examples.
- [ ] Registry entry added/updated: category file ordered before technology file, tier declared.
- [ ] README and `docs/supported-technologies.md` tier tables/counts match `registry.yaml`.

## Verification

<!-- What you actually ran, not just what you wrote. Paste output where it helps. -->

- [ ] `python scripts/check_repo_invariants.py` passes locally.
- [ ] For a new/changed registry signal: ran `detect_stack.py` against a real repository using
      it and confirmed the expected references load in the expected order.

## Anything you're unsure about

<!-- A judgment call you made, a section you're not fully confident is right, a place you'd want
     a second opinion. Flagging this is not a weakness in the PR — it's exactly the kind of
     honesty this project asks of the skill itself. -->
