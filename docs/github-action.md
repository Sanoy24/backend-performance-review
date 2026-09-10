# GitHub Action

Run a change-scoped performance review on every pull request, and publish it as a comment
and a SARIF upload.

---

## What the action does, and what it does not

The action does the **deterministic half**: validate the machine-readable review, convert it
to SARIF, comment on the pull request, and surface the verdict.

It does **not run the review**. That needs a coding agent with model access, and the
credentials, cost, and non-determinism that come with it. Those are the caller's decisions,
and an action that quietly spent a model budget on every push would be the wrong default. So
the workflow below has two parts: a step you configure that produces `review.json`, and this
action, which publishes it.

The split has a second benefit. Everything the action does is testable and reproducible —
the same `review.json` always yields the same comment and the same SARIF — so the
non-deterministic part is isolated to one step you can inspect, cache, or replace.

---

## Minimal workflow

```yaml
name: performance

on: pull_request

permissions:
  contents: read
  pull-requests: write      # to comment
  security-events: write    # to upload SARIF

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0    # the review needs the diff, so it needs history

      # ── The half you configure ────────────────────────────────────────────
      # Run your coding agent with the backend-performance-review skill in
      # change-scoped mode against ${{ github.event.pull_request.base.sha }}..HEAD,
      # and have it write the machine-readable output to review.json.
      #
      # The skill emits that JSON alongside its Markdown report — see the report
      # template §10 and schemas/review.schema.json.
      - name: Run the performance review
        run: |
          # your agent invocation here, writing review.json
          ...

      # ── The half this action does ─────────────────────────────────────────
      - uses: Sanoy24/backend-performance-review@v1
        with:
          review: review.json
```

---

## Inputs

| Input | Default | Notes |
|:--|:--|:--|
| `review` | — | **Required.** Path to the review JSON |
| `sarif-file` | `backend-performance-review.sarif` | Where to write SARIF |
| `upload-sarif` | `true` | Needs `security-events: write` |
| `comment` | `true` | Needs `pull-requests: write` |
| `include-adjacent` | `false` | See "Adjacent findings" below |
| `fail-on` | `never` | `never`, `fail`, or `warn` |
| `strict-verdict` | `true` | Fail if the declared verdict contradicts the findings |

Outputs: `verdict`, `findings`, `sarif-file`.

---

## Why `fail-on` defaults to `never`

A static review with no runtime evidence is advice, not a measurement. Making it a merge
gate on day one has a predictable outcome: the first false positive blocks someone's release,
the check gets marked non-required, and then it gets deleted.

The suggested path is to earn it:

1. **Start advisory.** `fail-on: never`. Watch the comments for a few weeks.
2. **Once the findings are consistently right, gate on the confident ones.** `fail-on: fail`
   trips only on a `FAIL` verdict, which requires a `High`/`Critical`-severity finding at
   `High` or `Confirmed` confidence.
3. `fail-on: warn` is available and is rarely the right choice. It blocks on hypotheses.

`UNKNOWN` never fails the job under any setting. "The review could not analyze this change"
is not the author's fault, and failing on it teaches people to write code the tool
understands rather than code that is fast.

---

## Reading the verdict

| Verdict | Means |
|:--|:--|
| `PASS` | Looked at the changed paths, found no regression |
| `WARN` | Something worth attention, not asserted confidently enough to block |
| `FAIL` | A regression this change introduces, asserted with confidence |
| `UNKNOWN` | **Could not analyze the change.** Not a pass |

`UNKNOWN` and `PASS` are different claims, and the comment says so explicitly, because a
reader skimming a green check will otherwise treat them the same. If `UNKNOWN` appears
often, that is a signal about coverage — an unsupported technology, generated code, or diffs
too large to cover — not a reason to ignore it.

---

## Adjacent findings

A performance review sometimes turns up a real security, correctness, or maintenance issue.
Those are reported in the Markdown under `SEC-`/`COR-`/`MAINT-` ids, and they are **excluded
from SARIF by default**.

This is deliberate. This project has no security or correctness methodology (`SKILL.md` Hard
Rule 8), and pushing those items into GitHub code scanning would present them as the output
of a security scanner. A team would reasonably read a clean security dashboard as evidence
of a security review that never happened.

`include-adjacent: true` is there for someone who understands that trade. Even then, they are
emitted at `note` level regardless of stated risk, for the same reason.

---

## Comment behavior

The comment is updated in place rather than appended on each push — a check that accumulates
a comment per commit gets muted, then deleted. Identification is by a hidden marker
(`<!-- backend-performance-review -->`) on the first line.

The comment deliberately carries:

- the verdict and what it means;
- each finding's **conditions** — the workload that makes it matter;
- **why it might not matter**, for findings above `Low` severity;
- a coverage table, so `PASS` can be read for what it is;
- policy violations in their own section, visually separated from findings.

That last separation matters: a policy violation is a fact about a rule the team chose, and
a finding is a claim about performance. Presenting them together lets an arbitrary threshold
read as a measurement.

---

## Performance policy

If the repository has a `.performance-policy.yml`, the review reads it and evaluates the
change against it. See `methodology/change-scoped.md` §4 for the shape and the rules —
particularly that a budget expressed against a baseline nobody measured is reported as
**not evaluable**, never as met.

---

## Troubleshooting

**"verdict mismatch"** — the review declared one verdict while its own findings derive
another, e.g. `PASS` alongside a confirmed high-severity regression. With
`strict-verdict: true` this fails the job on purpose: the review is internally inconsistent
and should not be published.

**Schema validation failures** — run the validator locally against the same file:

```
python scripts/validate_review.py --review review.json
```

It checks the schema, the priority matrix, root-cause references, duplicate ids, and the
rule that `Confirmed` confidence requires cited runtime evidence.

**No comment appears** — the action only comments on `pull_request` events, and needs
`pull-requests: write`.
