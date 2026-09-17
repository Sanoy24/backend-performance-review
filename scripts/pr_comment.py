#!/usr/bin/env python3
"""Render a machine-readable review as a pull-request comment.

Standard library only. Writes Markdown to stdout.

    python scripts/pr_comment.py --review review.json > comment.md

The comment is short on purpose. A check that appears on every pull request is read in a
few seconds or not at all, and the full report already exists for anyone who wants it. Its
first screen is a decision surface: assessment, top actions, key unknowns, and the first
validation commands. Detailed finding context follows. What must survive the compression:

- **The verdict, and what it means.** Especially `UNKNOWN`, which readers will otherwise
  pattern-match to `PASS`.
- **What was not covered.** This is what makes a `PASS` worth anything.
- **The conditions on each finding.** A finding without the workload that makes it matter is
  the generic-checklist failure this project exists to avoid.
- **Policy violations kept visually separate from findings**, because they are a different
  kind of claim with different evidence behind them.
"""

import argparse
import html
import json
import sys
from pathlib import Path

import validate_review
import action_policy

# The comment body carries em-dashes and arrows. Without this, writing it through a console
# on a non-UTF-8 codepage mangles them, which is confusing to debug because the file the
# action posts is fine and only the local preview is wrong.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

MARKER = "<!-- backend-performance-review -->"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

VERDICT_BADGE = {
    "PASS": "**PASS**",
    "WARN": "**WARN**",
    "FAIL": "**FAIL**",
    "UNKNOWN": "**UNKNOWN**",
}

VERDICT_MEANING = {
    "PASS": "No performance regression found in the changed paths.",
    "WARN": "Worth a look, but nothing asserted with enough confidence to block on.",
    "FAIL": "A performance regression this change introduces, asserted with confidence.",
    "UNKNOWN": "The changed paths could not be analyzed properly. This is **not** a pass — "
               "it means the review could not look, not that it looked and found nothing.",
}

UNKNOWN_REASON = {
    "no-evidence-exists": "no evidence exists",
    "technology-unsupported": "the technology is not supported deeply enough",
    "out-of-scope": "it was outside this review's scope",
    "not-examined": "it was not examined",
}


def compact(value, limit=220):
    """Keep decision-surface prose to one bounded line; detail remains below."""
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    shortened = value[:limit - 1].rsplit(" ", 1)[0]
    return (shortened or value[:limit - 1]) + "…"


def summary_line(review):
    findings = review.get("findings") or []
    by_priority = {}
    for finding in findings:
        by_priority[finding.get("priority")] = by_priority.get(finding.get("priority"), 0) + 1
    parts = ["%d %s" % (by_priority[p], p)
             for p in ("P0", "P1", "P2", "P3") if p in by_priority]
    if not parts:
        return "No findings"
    noun = "finding" if len(findings) == 1 else "findings"
    return "%d %s: %s" % (len(findings), noun, ", ".join(parts))


def ranked_findings(review):
    """Return a stable decision order: priority first, then report-local id."""
    return sorted(
        review.get("findings") or [],
        key=lambda finding: (finding.get("priority") or "P9", finding.get("id") or ""))


def first_validation_commands(findings, limit=3):
    """Collect at most one concrete command per top finding, without duplicates."""
    result, seen = [], set()
    for finding in findings:
        validation = finding.get("validation") or {}
        for item in validation.get("commands") or []:
            command = item.get("command", "").strip()
            if not command or command in seen:
                continue
            seen.add(command)
            result.append((finding, item))
            break
        if len(result) == limit:
            break
    return result


def render(review, fail_on="never"):
    lines = [MARKER, "## Backend Performance Review", ""]

    verdict = validate_review.review_summary(review)["verdict"]
    if verdict:
        lines.append("%s — %s" % (VERDICT_BADGE.get(verdict, verdict),
                                  VERDICT_MEANING.get(verdict, "")))
        lines.append("")

    findings = ranked_findings(review)
    completeness = review.get("completeness") or {}
    unknowns = completeness.get("unknowns") or []

    lines.append("### Assessment")
    lines.append("")
    if not findings:
        # Zero findings is a valid, successful result — but it must never be presented as a
        # clean bill of health, which is a much stronger claim than the review can make.
        lines.append("No findings. This means nothing material was found in what was "
                     "reviewed — see coverage below before reading it as more than that.")
    else:
        lines.append("%s." % summary_line(review))
    lines.append("Review confidence: **%s**. Ranking basis: **%s**." % (
        completeness.get("review_confidence", "not recorded"),
        completeness.get("ranking_method", "not recorded")))
    lines.append("")

    lines.append("### Top actions")
    lines.append("")
    if findings:
        for index, finding in enumerate(findings[:3], 1):
            lines.append("%d. **%s (%s)** — %s" % (
                index, finding.get("id", "Finding"), finding.get("priority", "—"),
                compact(finding.get("recommendation", "Validate before choosing a change."))))
            if finding.get("conditions"):
                lines.append("   _Why now:_ %s" % compact(finding["conditions"]))
    else:
        lines.append("No code change is recommended from this review. Resolve the highest-value "
                     "unknown or gather runtime evidence before optimizing.")
    lines.append("")

    lines.append("### Key unknowns")
    lines.append("")
    if unknowns:
        for unknown in unknowns[:3]:
            resolution = unknown.get("what_would_resolve_it")
            lines.append("- **%s** — %s%s" % (
                compact(unknown.get("subject", "Unknown"), 120),
                UNKNOWN_REASON.get(unknown.get("reason"), "not determined"),
                (". Resolve with: " + compact(resolution, 180)) if resolution else ""))
        if len(unknowns) > 3:
            lines.append("- _%d further unknown(s) in coverage below._" % (len(unknowns) - 3))
    else:
        lines.append("No decision-changing unknowns were recorded; confirm the coverage table "
                     "before treating that as complete knowledge.")
    lines.append("")

    lines.append("### Validate first")
    lines.append("")
    commands = first_validation_commands(findings[:3])
    if commands:
        for finding, item in commands:
            lines.append("- **%s · %s** — %s" % (
                finding.get("id", "Finding"), item.get("safety", "safety-not-recorded"),
                compact(item.get("purpose", "Run this check before changing priority."))))
            lines.append("<pre><code>%s</code></pre>" % html.escape(item["command"]))
    elif findings:
        for finding in findings[:3]:
            validation = finding.get("validation") or {}
            lines.append("- **%s · %s** — No executable command was supplied. Measure: %s" % (
                finding.get("id", "Finding"),
                validation.get("safety", "safety-not-recorded"),
                validation.get("metric", "the affected path")))
    else:
        lines.append("No finding-specific validation command is warranted. Use the unknowns "
                     "above to choose the next measurement.")
    lines.append("")

    if findings:
        lines.append("### Finding detail")
        lines.append("")
    for finding in findings[:5]:
        location = finding.get("location") or {}
        where = location.get("file", "")
        if location.get("line"):
            where += ":%d" % location["line"]
        lines.append("### %s — %s / %s confidence"
                     % (finding.get("id"), finding.get("priority"),
                        finding.get("confidence")))
        lines.append("")
        lines.append(finding.get("problem", ""))
        lines.append("")
        lines.append("`%s`" % where)
        lines.append("")
        if finding.get("conditions"):
            lines.append("**Matters when:** %s" % finding["conditions"])
            lines.append("")
        if finding.get("why_this_might_not_matter"):
            lines.append("**May not matter if:** %s" % finding["why_this_might_not_matter"])
            lines.append("")
        if finding.get("recommendation"):
            lines.append("**Suggested:** %s" % finding["recommendation"])
            lines.append("")

    if len(findings) > 5:
        lines.append("_%d further finding(s) in the full report._" % (len(findings) - 5))
        lines.append("")

    improvements = review.get("improvements") or []
    if improvements:
        lines.append("### Improvements in this change")
        lines.append("")
        for item in improvements:
            lines.append("- **%s** — %s" % (item.get("status", ""),
                                            item.get("description", "")))
        lines.append("")

    violations = [v for v in (review.get("policy_violations") or [])
                  if v.get("status") != "met"]
    if violations:
        lines.append("### Policy")
        lines.append("")
        lines.append("_Breaches of this repository's own declared limits. These are not "
                     "measurements of performance — they are facts about rules the team "
                     "chose, and may be perfectly acceptable._")
        lines.append("")
        for violation in violations:
            if violation.get("status") == "not-evaluable":
                lines.append("- `%s` — could not be evaluated: %s"
                             % (violation.get("rule"),
                                violation.get("why_not_evaluable", "no baseline")))
            else:
                lines.append("- `%s` — declared %s, observed %s"
                             % (violation.get("rule"), violation.get("declared"),
                                violation.get("observed")))
        lines.append("")

    lines.append("### Coverage")
    lines.append("")
    lines.append("| | |")
    lines.append("|:--|:--|")
    lines.append("| Review confidence | %s |" % completeness.get("review_confidence", "—"))
    lines.append("| Evidence available | %s |"
                 % completeness.get("evidence_available", "—"))
    lines.append("| Ranking | %s |" % completeness.get("ranking_method", "—"))

    if unknowns:
        lines.append("| Not determined | %d item(s) |" % len(unknowns))
    lines.append("")

    if unknowns:
        lines.append("<details><summary>What this review could not determine</summary>")
        lines.append("")
        for unknown in unknowns:
            lines.append("- **%s** — %s%s"
                         % (unknown.get("subject"), unknown.get("reason"),
                            (". Would be resolved by: " + unknown["what_would_resolve_it"])
                            if unknown.get("what_would_resolve_it") else ""))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if (review.get("runtime_evidence") or []) == []:
        lines.append("_No runtime evidence was supplied, so no finding here is measured. "
                     "Every claim is derived from the code._")
        lines.append("")

    lines.append("<sub>%s</sub>" % action_policy.footer_for(review.get("mode"), fail_on))
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", required=True)
    parser.add_argument("--fail-on", choices=action_policy.FAIL_ON_VALUES, default="never",
                        help="configured Action gate, reflected accurately in the footer")
    args = parser.parse_args(argv)
    with open(args.review, encoding="utf-8") as handle:
        review = json.load(handle)
    problems, _summary = validate_review.validate_and_summarize(review, SCHEMA_DIR)
    if problems:
        print("review is not publishable:", file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    sys.stdout.write(render(review, args.fail_on))
    return 0


if __name__ == "__main__":
    sys.exit(main())
