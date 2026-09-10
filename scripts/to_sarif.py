#!/usr/bin/env python3
"""Convert a machine-readable review into SARIF 2.1.0.

Standard library only.

    python scripts/to_sarif.py --review review.json > review.sarif
    python scripts/to_sarif.py --review review.json --include-adjacent

SARIF is what lets a review appear in GitHub code scanning, or any other tool that speaks
it. Three mapping decisions are deliberate and worth stating, because each one is a place a
naive conversion would misrepresent the review:

**`level` comes from priority, not severity.** SARIF has one axis; this methodology has two,
and priority is the derived value that already accounts for both. Mapping severity alone
would render a `High`-severity/`Low`-confidence finding — a hypothesis — as an `error`
alongside a confirmed one. Priority is exactly the field that exists to prevent that.

**`stable_id` becomes the fingerprint.** GitHub tracks an alert across commits by
`partialFingerprints`. A fingerprint derived from a line number produces a new alert every
time someone adds an import above the code; `stable_id` is derived from root cause, file,
symbol, and mechanism precisely so it survives that.

**Adjacent findings are excluded by default.** `SEC-`/`COR-`/`MAINT-` items are real, but
this skill has no security or correctness methodology (SKILL.md Hard Rule 8). Publishing
them into a security dashboard would present them as the output of a security scanner, which
is the exact misrepresentation that rule exists to prevent. `--include-adjacent` is
available for a reader who understands what they are getting.
"""

import argparse
import json
import sys

TOOL_NAME = "backend-performance-review"
INFORMATION_URI = "https://github.com/Sanoy24/backend-performance-review"

# Priority, not severity. See the module docstring.
PRIORITY_TO_LEVEL = {"P0": "error", "P1": "error", "P2": "warning", "P3": "note"}

# Descending, so a higher number is more urgent, matching SARIF's convention for `rank`.
PRIORITY_TO_RANK = {"P0": 100.0, "P1": 75.0, "P2": 40.0, "P3": 10.0}

CATEGORY_DESCRIPTIONS = {
    "data-access": "How the application reads and writes its datastore.",
    "concurrency": "Pools, locks, workers, event loops, and contention between them.",
    "serialization": "Encoding and decoding cost, and payload size.",
    "io": "Blocking and non-blocking input/output, including cache access.",
    "memory": "Allocation, retention, and unbounded buffering.",
    "networking": "Service-to-service calls, timeouts, and the request surface.",
    "infrastructure": "Container limits, autoscaling, and deployment topology.",
    "observability": "Whether behavior can be measured at all.",
    "cost": "Resource consumption with a direct monetary consequence.",
}

VERDICT_ORDER = ["PASS", "WARN", "FAIL", "UNKNOWN"]


def derive_verdict(review):
    """Derive the change-scoped verdict from the findings, the way priority is derived from
    the matrix — so a review cannot quietly claim PASS while carrying a confirmed regression.

    Returns None for a full review, which has no verdict.
    """
    if review.get("mode") != "change-scoped":
        return None

    completeness = review.get("completeness") or {}
    unknowns = completeness.get("unknowns") or []
    blocked = [u for u in unknowns
               if u.get("reason") in ("technology-unsupported", "not-examined")]
    if blocked:
        return "UNKNOWN"

    findings = review.get("findings") or []
    if not findings:
        return "PASS"

    for finding in findings:
        if (finding.get("severity") in ("Critical", "High")
                and finding.get("confidence") in ("Confirmed", "High")):
            return "FAIL"
    return "WARN"


def rules_for(findings):
    """One rule per category actually used. A rule per finding would make every alert its
    own rule, which is unusable in any SARIF consumer."""
    rules, seen = [], []
    for finding in findings:
        category = finding.get("category")
        if not category or category in seen:
            continue
        seen.append(category)
        rules.append({
            "id": "perf/" + category,
            "name": category.replace("-", " ").title().replace(" ", ""),
            "shortDescription": {"text": "Performance: %s" % category},
            "fullDescription": {
                "text": CATEGORY_DESCRIPTIONS.get(category, "Backend performance finding."),
            },
            "helpUri": INFORMATION_URI,
            "properties": {"tags": ["performance", category]},
        })
    return rules


def message_for(finding):
    """The message a reader sees in a diff view. It carries the conditions deliberately: a
    performance finding without the workload that makes it matter is the generic-checklist
    failure this project exists to avoid, and a SARIF consumer usually shows only this text.
    """
    parts = [finding.get("problem", "").strip()]
    conditions = (finding.get("conditions") or "").strip()
    if conditions:
        parts.append("Matters when: " + conditions)
    caveat = (finding.get("why_this_might_not_matter") or "").strip()
    if caveat:
        parts.append("May not matter if: " + caveat)
    counter = finding.get("counter_evidence") or []
    if not counter:
        parts.append("Counter-evidence: searched, none found.")
    return "\n\n".join(p for p in parts if p)


def result_for(finding):
    location = finding.get("location") or {}
    priority = finding.get("priority")

    result = {
        "ruleId": "perf/" + (finding.get("category") or "unknown"),
        "level": PRIORITY_TO_LEVEL.get(priority, "note"),
        "rank": PRIORITY_TO_RANK.get(priority, 10.0),
        "message": {"text": message_for(finding)},
        "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": location.get("file", "")},
                **({"region": {"startLine": location["line"]}} if location.get("line") else {}),
            },
        }],
        "properties": {
            "id": finding.get("id"),
            "severity": finding.get("severity"),
            "confidence": finding.get("confidence"),
            "priority": priority,
            "evidenceQuality": finding.get("evidence_quality"),
            "rootCause": finding.get("root_cause_id"),
            "growth": (finding.get("impact") or {}).get("growth"),
            "amplification": (finding.get("impact") or {}).get("amplification"),
            "counterEvidenceChecked": len(finding.get("counter_evidence") or []),
            "recommendation": finding.get("recommendation"),
            "validationFalsifier": (finding.get("validation") or {}).get("falsifier"),
        },
    }

    stable_id = finding.get("stable_id")
    if stable_id:
        result["partialFingerprints"] = {"backendPerformanceReview/v1": stable_id}
    if location.get("symbol"):
        result["locations"][0]["logicalLocations"] = [
            {"fullyQualifiedName": location["symbol"]}]
    return result


def adjacent_result_for(item):
    """Deliberately `note`, whatever the risk. This tool has no security methodology, so
    emitting an out-of-scope observation at `error` would assert a rigor it does not have —
    the same reason these never carry a performance Severity or Priority."""
    return {
        "ruleId": "adjacent/" + (item.get("kind") or "observation"),
        "level": "note",
        "message": {"text": "%s\n\nOutside performance scope. Assess with: %s"
                            % (item.get("problem", ""),
                               item.get("assessed_properly_by", "a dedicated review"))},
        "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": item.get("location") or ""}}}],
        "properties": {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "confidence": item.get("confidence"),
            "risk": item.get("risk"),
            "outOfScope": True,
        },
    }


def adjacent_rules_for(items):
    rules, seen = [], []
    for item in items:
        kind = item.get("kind") or "observation"
        if kind in seen:
            continue
        seen.append(kind)
        rules.append({
            "id": "adjacent/" + kind,
            "name": "Adjacent" + kind.title(),
            "shortDescription": {"text": "Out of scope: %s" % kind},
            "fullDescription": {"text":
                "Found while reading code for performance, and reported with the same "
                "evidence discipline — but this tool has no dedicated %s methodology and "
                "does not score it. Treat as a pointer to a proper review, not as its "
                "output." % kind},
            "helpUri": INFORMATION_URI,
            "properties": {"tags": ["out-of-scope", kind]},
        })
    return rules


def to_sarif(review, include_adjacent=False):
    findings = review.get("findings") or []
    reproducibility = review.get("reproducibility") or {}
    completeness = review.get("completeness") or {}

    rules = rules_for(findings)
    results = [result_for(f) for f in findings]

    if include_adjacent:
        adjacent = review.get("adjacent_findings") or []
        rules += adjacent_rules_for(adjacent)
        results += [adjacent_result_for(item) for item in adjacent]

    declared = review.get("verdict")
    derived = derive_verdict(review)

    run = {
        "tool": {"driver": {
            "name": TOOL_NAME,
            "version": reproducibility.get("skill_version", "unknown"),
            "informationUri": INFORMATION_URI,
            "rules": rules,
        }},
        "results": results,
        "properties": {
            "mode": review.get("mode"),
            "verdict": declared,
            "derivedVerdict": derived,
            "reviewConfidence": completeness.get("review_confidence"),
            "evidenceAvailable": completeness.get("evidence_available"),
            "rankingMethod": completeness.get("ranking_method"),
            "policyViolations": len(review.get("policy_violations") or []),
            "improvements": len(review.get("improvements") or []),
            # Zero findings is a valid, successful result — but a SARIF consumer showing an
            # empty run cannot distinguish "nothing found" from "nothing looked at", so the
            # review's own coverage statement travels with it.
            "findingsReported": len(findings),
        },
    }

    commit = (reproducibility.get("repository") or {}).get("commit")
    if commit:
        run["versionControlProvenance"] = [{
            "repositoryUri": (reproducibility.get("repository") or {}).get("name", ""),
            "revisionId": commit,
        }]

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", required=True, help="machine-readable review JSON")
    parser.add_argument("--include-adjacent", action="store_true",
                        help="also emit SEC-/COR-/MAINT- items (see the module docstring "
                             "for why this is off by default)")
    parser.add_argument("--strict-verdict", action="store_true",
                        help="fail if a change-scoped review's declared verdict does not "
                             "match the one derived from its findings")
    args = parser.parse_args(argv)

    with open(args.review, encoding="utf-8") as handle:
        review = json.load(handle)

    declared, derived = review.get("verdict"), derive_verdict(review)
    if declared and derived and declared != derived:
        message = ("verdict mismatch: review declares %s, findings derive %s"
                   % (declared, derived))
        if args.strict_verdict:
            print(message, file=sys.stderr)
            return 1
        print("warning: " + message, file=sys.stderr)

    json.dump(to_sarif(review, args.include_adjacent), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
