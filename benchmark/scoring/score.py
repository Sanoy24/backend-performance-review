#!/usr/bin/env python3
"""Score a machine-readable review against expert ground truth.

Standard library only, like every other script here.

    python benchmark/scoring/score.py score --truth GT.json --review REVIEW.json
    python benchmark/scoring/score.py stability --review A.json --review B.json

What it measures, and why each one is separate:

  precision / recall / F1     Per category as well as overall, because "good at N+1, poor at
                              concurrency" is actionable and a single blended number is not.
  severity calibration        A review that finds the right issue and calls everything
                              Critical is not useful. Measured as exact / within-one / large
                              disagreement against the annotator's severity.
  confidence calibration      Whether `High` actually means high. Buckets findings by claimed
                              confidence and reports how many were true positives. A label
                              that does not predict correctness is decoration.
  recommendation accuracy     Tracked separately from finding accuracy, because identifying a
                              problem correctly and then proposing the wrong fix is a
                              distinct failure with a distinct cause.
  restraint                   Forbidden items reported. These are the false-positive traps,
                              and on a healthy repository they are the whole test.

Matching is deliberately conservative: a finding matches a ground-truth item when they name
the same file and a compatible category. Ground truth does not encode `stable_id`, because
that would require the annotator to predict the implementation's hashing rather than describe
the defect.
"""

import argparse
import json
import sys
from collections import defaultdict

SEVERITY_ORDER = ["Informational", "Low", "Medium", "High", "Critical"]
CONFIDENCE_ORDER = ["Low", "Medium", "High", "Confirmed"]


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------

def normalize_path(path):
    return (path or "").replace("\\", "/").strip().lstrip("./").lower()


def same_file(a, b):
    """Compare paths tolerantly, but not loosely.

    Tolerant, because a review may report `backend/src/orders/service.py` where the
    annotation says `src/orders/service.py`; that is one defect, and failing to match it
    would score one correct finding as both a false negative and a false positive.

    Not loose: an earlier version fell back to comparing basenames, which matched
    `orders/service.py` against `users/service.py` — `service.py` is not a distinguishing
    name in any real repository. That failure mode is much worse than the one it fixed,
    because a wrong match is silent while an unmatched annotation prints a MISSED line the
    annotator can go and correct. Suffix matching only.
    """
    a, b = normalize_path(a), normalize_path(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith("/" + b) or b.endswith("/" + a)


def categories_for(item):
    allowed = {item.get("category")} | set(item.get("also_acceptable_categories", []))
    return {c for c in allowed if c}


def _location_matches(reported, candidate):
    """One candidate location (the item's primary `location`, or one entry of
    `also_locations`) against the finding's reported location. File must match; a symbol
    conflict (both sides name one, and they differ) rules the candidate out."""
    if not same_file(reported.get("file"), candidate.get("file")):
        return False
    expected_symbol = candidate.get("symbol")
    reported_symbol = reported.get("symbol")
    if expected_symbol and reported_symbol and expected_symbol != reported_symbol:
        return False
    return True


def matches(finding, item):
    """A finding matches a ground-truth item when its category is compatible and its
    location matches EITHER the item's primary `location` OR any of its `also_locations`.

    The multi-location list exists because two independent real reviews of the identical
    bug — a per-item query reached through a call chain — legitimately cited opposite ends
    of that chain: one the query-issuing method's definition, the other the call site that
    would actually need to change to fix it. Neither is more correct, so scoring one of them
    as a miss would be scoring the harness's own location convention, not the review.
    """
    reported = finding.get("location") or {}

    candidates = [item.get("location") or {}]
    candidates.extend(item.get("also_locations", []))
    if not any(_location_matches(reported, candidate) for candidate in candidates):
        return False

    allowed = categories_for(item)
    if allowed and finding.get("category") not in allowed:
        return False

    return True


def recommendation_text(finding):
    parts = [finding.get("recommendation", "")]
    for alternative in finding.get("alternatives", []):
        if alternative.get("preferred"):
            parts.append(alternative.get("option", ""))
            parts.append(alternative.get("why", ""))
    return " ".join(parts).lower()


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------

def score(truth, review):
    findings = review.get("findings", [])
    expected = truth.get("expected", [])
    acceptable = truth.get("acceptable", [])
    forbidden = truth.get("forbidden", [])

    unclaimed = list(findings)
    pairs = []          # (ground-truth item, finding) for expected items that were found
    missed = []

    for item in expected:
        hit = next((f for f in unclaimed if matches(f, item)), None)
        if hit is None:
            missed.append(item)
        else:
            unclaimed.remove(hit)
            pairs.append((item, hit))

    # Acceptable items absorb a finding without scoring it either way.
    tolerated = []
    for item in acceptable:
        hit = next((f for f in unclaimed if matches(f, item)), None)
        if hit is not None:
            unclaimed.remove(hit)
            tolerated.append((item, hit))

    # Anything left that lands on a forbidden item is a false positive the corpus predicted.
    trapped = []
    for item in forbidden:
        for finding in list(unclaimed):
            if matches(finding, item):
                unclaimed.remove(finding)
                trapped.append((item, finding))

    true_positives = len(pairs)
    false_negatives = len(missed)
    false_positives = len(trapped) + len(unclaimed)

    result = {
        "repository": truth.get("repository", {}).get("name"),
        "commit": truth.get("repository", {}).get("commit"),
        "counts": {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "tolerated": len(tolerated),
            "forbidden_reported": len(trapped),
            "unanticipated": len(unclaimed),
        },
        "overall": ratios(true_positives, false_positives, false_negatives),
        "per_category": per_category(pairs, missed, trapped, unclaimed),
        "severity_calibration": severity_calibration(pairs),
        "confidence_calibration": confidence_calibration(pairs, trapped, unclaimed),
        "confidence_ceiling_violations": ceiling_violations(pairs),
        "recommendation_accuracy": recommendation_accuracy(pairs),
        "restraint": {
            "forbidden_items": len(forbidden),
            "forbidden_reported": [
                {"id": item["id"], "finding": finding.get("id"), "why_not": item["why_not"]}
                for item, finding in trapped
            ],
        },
        "misses": [{"id": i["id"], "mechanism": i.get("mechanism")} for i in missed],
        "unanticipated_findings": [
            {"id": f.get("id"), "category": f.get("category"),
             "location": (f.get("location") or {}).get("file"),
             "severity": f.get("severity")}
            for f in unclaimed
        ],
    }
    return result


def ratios(true_positives, false_positives, false_negatives):
    precision = true_positives / (true_positives + false_positives) if (
        true_positives + false_positives) else None
    recall = true_positives / (true_positives + false_negatives) if (
        true_positives + false_negatives) else None
    if precision and recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = None
    return {
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
    }


def per_category(pairs, missed, trapped, unclaimed):
    buckets = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for item, _finding in pairs:
        buckets[item.get("category", "unknown")]["tp"] += 1
    for item in missed:
        buckets[item.get("category", "unknown")]["fn"] += 1
    for item, _finding in trapped:
        buckets[item.get("category", "unknown")]["fp"] += 1
    for finding in unclaimed:
        buckets[finding.get("category", "unknown")]["fp"] += 1

    return {
        category: dict(counts, **ratios(counts["tp"], counts["fp"], counts["fn"]))
        for category, counts in sorted(buckets.items())
    }


def severity_calibration(pairs):
    exact = within_one = large = 0
    disagreements = []
    for item, finding in pairs:
        expected, reported = item.get("severity"), finding.get("severity")
        if not expected or reported not in SEVERITY_ORDER:
            continue
        distance = abs(SEVERITY_ORDER.index(expected) - SEVERITY_ORDER.index(reported))
        if distance == 0:
            exact += 1
        elif distance == 1:
            within_one += 1
        else:
            large += 1
            disagreements.append({
                "finding": finding.get("id"), "expert": expected, "agent": reported,
            })
    scored = exact + within_one + large
    return {
        "scored": scored,
        "exact": exact,
        "within_one": within_one,
        "large_disagreement": large,
        "exact_rate": round(exact / scored, 3) if scored else None,
        "disagreements": disagreements,
    }


def confidence_calibration(pairs, trapped, unclaimed):
    """Does a confidence label predict correctness? Buckets every scored finding by the
    confidence it claimed, then reports what share of that bucket was actually right. A
    label that does not track correctness is decoration, and this is the number that says so.
    """
    buckets = defaultdict(lambda: {"correct": 0, "incorrect": 0})
    for _item, finding in pairs:
        buckets[finding.get("confidence", "unknown")]["correct"] += 1
    for _item, finding in trapped:
        buckets[finding.get("confidence", "unknown")]["incorrect"] += 1
    for finding in unclaimed:
        buckets[finding.get("confidence", "unknown")]["incorrect"] += 1

    out = {}
    for label, counts in buckets.items():
        total = counts["correct"] + counts["incorrect"]
        out[label] = dict(counts, total=total,
                          actual_correctness=round(counts["correct"] / total, 3) if total else None)
    return {key: out[key] for key in sorted(out, key=lambda k: (
        CONFIDENCE_ORDER.index(k) if k in CONFIDENCE_ORDER else -1), reverse=True)}


def ceiling_violations(pairs):
    """A finding may not claim more confidence than the available evidence can support.
    Getting the answer right does not license overstating how you know it."""
    violations = []
    for item, finding in pairs:
        ceiling = item.get("max_confidence")
        claimed = finding.get("confidence")
        if not ceiling or claimed not in CONFIDENCE_ORDER:
            continue
        if CONFIDENCE_ORDER.index(claimed) > CONFIDENCE_ORDER.index(ceiling):
            violations.append({
                "finding": finding.get("id"), "claimed": claimed, "ceiling": ceiling,
            })
    return violations


def recommendation_accuracy(pairs):
    """Scored separately from finding accuracy: correctly identifying a problem and then
    proposing the wrong fix is a distinct failure. The keyword test is crude, and is meant to
    catch one specific thing — recommending a cache where the work should have been removed.
    """
    scored = good = 0
    problems = []
    for item, finding in pairs:
        wanted = [k.lower() for k in item.get("expected_recommendation", [])]
        banned = [k.lower() for k in item.get("forbidden_recommendation", [])]
        if not wanted and not banned:
            continue
        scored += 1
        text = recommendation_text(finding)
        hit = any(k in text for k in wanted) if wanted else True
        violated = [k for k in banned if k in text]
        if hit and not violated:
            good += 1
        else:
            problems.append({
                "finding": finding.get("id"),
                "missing_any_of": wanted if not hit else [],
                "used_forbidden": violated,
            })
    return {
        "scored": scored,
        "appropriate": good,
        "rate": round(good / scored, 3) if scored else None,
        "problems": problems,
    }


# --------------------------------------------------------------------------------------
# Stability — two independent runs over the same code
# --------------------------------------------------------------------------------------

def stability(first, second):
    """Turns the hand-diffing in docs/evaluation.md §3.18 and §3.21 into a number, so the
    remaining repositories can be checked cheaply instead of by eye.

    Two known limitations, both found by running this against two real independent reviews
    of the same repository (not hypothesized in advance) — see benchmark/README.md
    "Known limitations, found by real use":

    `overlap`/`only_in_a`/`only_in_b` key on (file, category), which UNDERCOUNTS true
    agreement when two reviews cite the identical mechanism at opposite ends of one call
    chain (a query's call site versus its definition) — ground truth's `also_locations`
    exists for exactly this, but there is no ground truth here, only two raw reviews with no
    external arbiter of "same finding." Two runs disagreeing on file for the same real bug is
    a live, observed case, not a hypothetical one.

    `stable_id_agreement` is close to meaningless as specified today: SKILL.md/the schema
    describe `stable_id` as "derived from root cause, file, symbol, and mechanism" but do not
    mandate a canonical algorithm, so two independently-run agents computing "a hash" from
    the same inputs are not guaranteed to produce the same bytes even when they agree on
    every input. Confirmed empirically: two real reviews that agreed on the dominant finding's
    location, severity-within-one-level, and recommendation still had 0% stable_id agreement.
    Treat this field as informative only until a canonical algorithm is specified.
    """
    a, b = first.get("findings", []), second.get("findings", [])

    def key(finding):
        location = finding.get("location") or {}
        return (normalize_path(location.get("file")), finding.get("category"))

    keys_a, keys_b = {key(f) for f in a}, {key(f) for f in b}
    shared = keys_a & keys_b
    union = keys_a | keys_b

    by_key_a = {key(f): f for f in a}
    by_key_b = {key(f): f for f in b}
    severity_agree = sum(
        1 for k in shared if by_key_a[k].get("severity") == by_key_b[k].get("severity"))
    priority_agree = sum(
        1 for k in shared if by_key_a[k].get("priority") == by_key_b[k].get("priority"))
    stable_id_agree = sum(
        1 for k in shared if by_key_a[k].get("stable_id") == by_key_b[k].get("stable_id"))

    return {
        "findings": {"run_a": len(a), "run_b": len(b)},
        "overlap": round(len(shared) / len(union), 3) if union else None,
        "shared": len(shared),
        "only_in_a": sorted("%s (%s)" % k for k in keys_a - keys_b),
        "only_in_b": sorted("%s (%s)" % k for k in keys_b - keys_a),
        "severity_agreement": round(severity_agree / len(shared), 3) if shared else None,
        "priority_agreement": round(priority_agree / len(shared), 3) if shared else None,
        "stable_id_agreement": round(stable_id_agree / len(shared), 3) if shared else None,
        "caveats": [
            "overlap/only_in_a/only_in_b key on (file, category) and will undercount "
            "agreement when two reviews cite the identical mechanism at different points "
            "in one call chain; see this function's docstring.",
            "stable_id_agreement is not yet a reliable signal: no canonical hashing "
            "algorithm is mandated, so independently-run agents are not guaranteed to "
            "produce matching ids even for the identical finding.",
        ],
    }


# --------------------------------------------------------------------------------------

def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def render(result):
    lines = []
    counts, overall = result["counts"], result["overall"]
    lines.append("Repository: %s @ %s" % (result["repository"], (result["commit"] or "")[:12]))
    lines.append("")
    lines.append("  true positives   %d" % counts["true_positives"])
    lines.append("  false positives  %d  (%d forbidden, %d unanticipated)"
                 % (counts["false_positives"], counts["forbidden_reported"],
                    counts["unanticipated"]))
    lines.append("  false negatives  %d" % counts["false_negatives"])
    lines.append("  tolerated        %d  (acceptable, scored neither way)" % counts["tolerated"])
    lines.append("")
    lines.append("  precision %s   recall %s   F1 %s"
                 % (overall["precision"], overall["recall"], overall["f1"]))

    if result["per_category"]:
        lines.append("")
        lines.append("  per category:")
        for category, stats in result["per_category"].items():
            lines.append("    %-16s tp=%d fp=%d fn=%d  P=%s R=%s"
                         % (category, stats["tp"], stats["fp"], stats["fn"],
                            stats["precision"], stats["recall"]))

    severity = result["severity_calibration"]
    if severity["scored"]:
        lines.append("")
        lines.append("  severity: %d exact, %d within one, %d large disagreement"
                     % (severity["exact"], severity["within_one"],
                        severity["large_disagreement"]))
        for item in severity["disagreements"]:
            lines.append("    %s expert=%s agent=%s"
                         % (item["finding"], item["expert"], item["agent"]))

    if result["confidence_calibration"]:
        lines.append("")
        lines.append("  confidence, claimed vs actually correct:")
        for label, stats in result["confidence_calibration"].items():
            lines.append("    %-10s %s  (%d of %d)"
                         % (label, stats["actual_correctness"], stats["correct"],
                            stats["total"]))

    for violation in result["confidence_ceiling_violations"]:
        lines.append("  OVERSTATED  %s claimed %s, evidence supports at most %s"
                     % (violation["finding"], violation["claimed"], violation["ceiling"]))

    recommendation = result["recommendation_accuracy"]
    if recommendation["scored"]:
        lines.append("")
        lines.append("  recommendations appropriate: %d of %d (%s)"
                     % (recommendation["appropriate"], recommendation["scored"],
                        recommendation["rate"]))
        for problem in recommendation["problems"]:
            lines.append("    %s missing=%s forbidden=%s"
                         % (problem["finding"], problem["missing_any_of"],
                            problem["used_forbidden"]))

    if result["restraint"]["forbidden_reported"]:
        lines.append("")
        lines.append("  RESTRAINT FAILURES — reported a known non-problem:")
        for entry in result["restraint"]["forbidden_reported"]:
            lines.append("    %s (%s): %s" % (entry["finding"], entry["id"], entry["why_not"]))

    for miss in result["misses"]:
        lines.append("  MISSED  %s — %s" % (miss["id"], miss["mechanism"]))

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    scorer = subparsers.add_parser("score", help="score a review against ground truth")
    scorer.add_argument("--truth", required=True)
    scorer.add_argument("--review", required=True)
    scorer.add_argument("--json", action="store_true", help="emit JSON instead of a report")

    comparer = subparsers.add_parser(
        "stability", help="compare two independent reviews of the same code")
    comparer.add_argument("--review", required=True, action="append", dest="reviews",
                          help="pass twice")

    args = parser.parse_args(argv)

    if args.command == "score":
        result = score(load(args.truth), load(args.review))
        print(json.dumps(result, indent=2) if args.json else render(result))
        return 0

    if len(args.reviews) != 2:
        parser.error("stability needs exactly two --review arguments")
    print(json.dumps(stability(load(args.reviews[0]), load(args.reviews[1])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
