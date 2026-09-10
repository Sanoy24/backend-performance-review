# Change-scoped review

Load this when reviewing a diff, branch, pull request, or commit range.

This is the mode worth running continuously. A full review is a one-off event; a
change-scoped review runs on every pull request, which means it is the mode where a
false positive is most expensive — it will be seen by the same team every day, and a
check that cries wolf gets muted and then deleted. Precision matters more here than
anywhere else in this methodology.

---

## 1. What to look at

Start at the diff and expand outward only along paths the change actually reaches:

```
changed lines
  ↓  what calls this
callers, and the request paths they sit on
  ↓  what this reaches
data access, external calls, queue operations it issues
  ↓  what bounds it
configuration, limits, pagination, timeouts that apply
```

Stop expanding when the expansion stops touching the change. A change-scoped review that
ends up reading the whole repository has become a full review with extra steps, and it
will report findings the author cannot act on in this pull request.

**Report only what the change introduces, worsens, or sits directly adjacent to.** A
pre-existing problem the diff happens to touch is worth one line under "already present,
not introduced here" — not a finding against this author. Getting this wrong is how a
review becomes something people route around.

---

## 2. Credit fixes, do not re-flag them

A diff that *removes* a performance problem is a result, and saying so is part of the
review. When the change improves something:

- Say what was removed, in the same evidence-first terms a finding would use.
- Do not then re-report the pattern as a finding because it still appears in the diff's
  removed lines. Read the direction of the change before scoring it.
- If the fix is partial — one of two N+1s resolved, a bound added on one path but not its
  sibling — say exactly that. "Partially resolved" is a real and useful verdict.

---

## 3. The verdict

Every change-scoped review ends with exactly one verdict.

| Verdict | Means | Requires |
|:--|:--|:--|
| `PASS` | No performance regression found, and the review was able to look properly | Coverage of the changed paths, and no finding at `High` or `Critical` severity |
| `WARN` | Something worth a human's attention, but not a blocking claim | One or more findings, none of which are both high-severity and high-confidence |
| `FAIL` | A regression this change introduces, asserted with confidence | A finding at `High` or `Critical` severity **and** `Confirmed` or `High` confidence |
| `UNKNOWN` | The review could not establish whether this change regresses performance | Changed paths could not be analyzed — an unsupported technology, generated code, a diff too large to cover, or a change whose impact depends entirely on workload nobody stated |

**`UNKNOWN` must never collapse into `PASS`.** They are different claims: `PASS` says "I
looked and found nothing"; `UNKNOWN` says "I could not look properly." Reporting the
second as the first is the single most damaging thing this mode can do, because it
converts a gap in the review into a false assurance — and the reader has no way to tell.

If part of a change is analyzable and part is not, the verdict covers the whole change:
report `UNKNOWN` and say which part was covered. A verdict of `PASS` with a footnote
saying half the diff was skipped is a `PASS` nobody should trust.

**The verdict follows from the findings; it is not chosen.** Same discipline as priority:
score the findings first, then read the verdict off the table above.

---

## 4. Performance policy, where a repository declares one

A repository may declare its own constraints, conventionally in
`.performance-policy.yml` at the root:

```yaml
budget:
  p95_latency: "+10%"        # tolerated regression versus baseline
  query_count: "+5%"
  memory: "+10%"

invariants:
  max_db_queries_per_request: 20
  max_page_size: 100
  outbound_timeout_required: true
  retries:
    max: 3

criticality:
  tier: 1                    # business context, for sequencing only
```

Read it if present. Evaluate the change against it. Then observe the rule that makes this
useful rather than noisy:

> **A policy violation is not a measured performance problem, and the two are never
> reported as the same thing.**

They are different claims with different evidence behind them. "This endpoint issues 24
queries, and your policy caps it at 20" is a fact about a rule the team chose. "This
endpoint is slow" is a claim about production behavior that needs evidence this review
usually does not have. A policy violation may well be fine — the team may raise the limit.
Report policy violations in their own section, with their own language, and never let one
inflate a finding's severity.

**Budgets need a baseline to mean anything.** `p95_latency: "+10%"` is uncheckable without
a measured before and after. Where no baseline exists, say the budget could not be
evaluated — do not silently treat an unevaluable budget as met.

**Business criticality and SLOs affect sequencing, never severity.** A tier-1 service and
a tier-3 service with the same defect have the same technical severity; what differs is how
soon someone should act. Letting business context into the score is how a rubric stops
being reproducible.

---

## 5. Output

The report is shorter than a full review's. It needs:

- The verdict, first, with one sentence of justification.
- What was covered, and what was not. This is what makes `PASS` trustworthy.
- Findings this change introduces or worsens, in the standard format.
- Improvements this change makes, credited.
- Policy violations, in their own section, if a policy exists.
- Anything pre-existing and untouched, as one line each — or omitted entirely.

In the machine-readable output, set `mode: "change-scoped"` and `verdict`. The schema
requires the verdict in this mode precisely so it cannot be quietly omitted when the
answer is inconvenient.
