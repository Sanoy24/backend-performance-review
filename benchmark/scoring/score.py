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
the same file and a compatible category. Candidate relationships are solved as a
maximum-cardinality bipartite assignment, then by an explicit specificity order, so harmless
JSON list reordering cannot change the result. Ground truth does not encode `stable_id`, because
that would require the annotator to predict the implementation's hashing rather than describe
the defect.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dataset as benchmark_dataset  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import validate_review  # noqa: E402

SEVERITY_ORDER = ["Informational", "Low", "Medium", "High", "Critical"]
CONFIDENCE_ORDER = ["Low", "Medium", "High", "Confirmed"]


class AdjudicationError(ValueError):
    """A post-run candidate adjudication does not bind to this scored review."""


def content_digest(value):
    """SHA-256 of parsed JSON, insensitive to formatting and key order."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    return match_specificity(finding, item) is not None


def match_specificity(finding, item):
    """Return the best compatible-match specificity, or ``None`` when there is no edge.

    The tuple is ordered from most to least important. Assignment maximizes the summed tuple
    lexicographically after first maximizing cardinality:

    1. primary ``location`` rather than an ``also_locations`` alternative;
    2. the same explicit symbol on both sides rather than an omitted symbol;
    3. an exact normalized file path rather than a suffix-only path match;
    4. the primary category rather than an ``also_acceptable_categories`` alternative.

    Keeping this as an explicit tuple makes the benchmark's preference auditable instead of
    hiding it in traversal order.
    """
    allowed = categories_for(item)
    reported_category = finding.get("category")
    if allowed and reported_category not in allowed:
        return None

    reported = finding.get("location") or {}
    candidates = [item.get("location") or {}]
    candidates.extend(item.get("also_locations", []))
    best = None
    for index, candidate in enumerate(candidates):
        if not _location_matches(reported, candidate):
            continue
        expected_symbol = candidate.get("symbol")
        reported_symbol = reported.get("symbol")
        specificity = (
            int(index == 0),
            int(bool(expected_symbol) and expected_symbol == reported_symbol),
            int(normalize_path(reported.get("file")) == normalize_path(candidate.get("file"))),
            int(bool(reported_category) and reported_category == item.get("category")),
        )
        best = max(best, specificity) if best is not None else specificity
    return best


def _object_key(value):
    """Stable ordering key independent of the order objects appeared in input JSON."""
    return (str(value.get("id") or ""),
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _specificity_value(specificity, base):
    value = 0
    for component in specificity:
        value = value * base + component
    return value


def _hungarian_max(weights):
    """Maximum-weight rectangular assignment for rows <= columns.

    This is the O(rows^2 * columns) Hungarian algorithm. Callers add one dummy column per
    row, so every row can remain unmatched without ever selecting an incompatible real edge.
    """
    row_count = len(weights)
    if not row_count:
        return []
    column_count = len(weights[0])
    if row_count > column_count:
        raise ValueError("assignment requires at least as many columns as rows")

    # The conventional implementation minimizes cost; negating weights makes it maximize.
    costs = [[-weight for weight in row] for row in weights]
    u = [0] * (row_count + 1)
    v = [0] * (column_count + 1)
    p = [0] * (column_count + 1)
    way = [0] * (column_count + 1)

    for row in range(1, row_count + 1):
        p[0] = row
        column = 0
        minimum = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column] = True
            active_row = p[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, column_count + 1):
                if used[candidate]:
                    continue
                reduced = costs[active_row - 1][candidate - 1] - u[active_row] - v[candidate]
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    way[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(column_count + 1):
                if used[candidate]:
                    u[p[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if p[column] == 0:
                break
        while True:
            previous = way[column]
            p[column] = p[previous]
            column = previous
            if column == 0:
                break

    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if p[column]:
            assignment[p[column] - 1] = column - 1
    return assignment


def _solve_assignment(items, findings, excluded=frozenset()):
    """Solve one already-sorted bucket and return edges plus its comparable objective."""
    if not items or not findings:
        return [], (0, 0)

    max_pairs = min(len(items), len(findings))
    base = max_pairs + 1
    specificity = {}
    max_specificity = _specificity_value((1, 1, 1, 1), base)
    match_bonus = (max_pairs + 1) * (max_specificity + 1)
    incompatible = -match_bonus * (len(items) + 1)
    weights = []

    for item_index, item in enumerate(items):
        row = []
        for finding_index, finding in enumerate(findings):
            edge = (item_index, finding_index)
            edge_specificity = match_specificity(finding, item)
            specificity[edge] = edge_specificity
            if edge in excluded or edge_specificity is None:
                row.append(incompatible)
            else:
                row.append(match_bonus + _specificity_value(edge_specificity, base))
        row.extend([0] * len(items))
        weights.append(row)

    columns = _hungarian_max(weights)
    selected = []
    for item_index, column in enumerate(columns):
        edge = (item_index, column)
        if column >= len(findings) or edge in excluded or specificity.get(edge) is None:
            continue
        edge_specificity = specificity[edge]
        selected.append((item_index, column, edge_specificity))

    objective = (
        len(selected),
        sum(_specificity_value(edge_specificity, base)
            for _item_index, _finding_index, edge_specificity in selected),
    )
    return selected, objective


def _specificity_record(specificity):
    return {
        "primary_location": bool(specificity[0]),
        "exact_symbol": bool(specificity[1]),
        "exact_file": bool(specificity[2]),
        "primary_category": bool(specificity[3]),
    }


def _pair_records(edges, items, findings):
    return [
        {
            "item": items[item_index].get("id"),
            "finding": findings[finding_index].get("id"),
            "specificity": _specificity_record(specificity),
        }
        for item_index, finding_index, specificity in edges
    ]


def optimal_assignment(items, findings, bucket):
    """Return a canonical maximum-cardinality, maximum-specificity bipartite assignment.

    Input order is discarded before solving. If forbidding any selected edge still permits
    the same objective, a representative equal-score assignment is emitted for adjudication.
    """
    items = sorted(items, key=_object_key)
    findings = sorted(findings, key=_object_key)
    selected, objective = _solve_assignment(items, findings)
    selected_records = _pair_records(selected, items, findings)

    alternatives = []
    seen = set()
    for item_index, finding_index, _specificity in selected:
        alternative, alternative_objective = _solve_assignment(
            items, findings, excluded=frozenset({(item_index, finding_index)}))
        if alternative_objective != objective:
            continue
        signature = tuple((entry["item"], entry["finding"])
                          for entry in _pair_records(alternative, items, findings))
        if signature in seen:
            continue
        seen.add(signature)
        alternatives.append({
            "bucket": bucket,
            "reason": "equal_cardinality_and_specificity",
            "selected": selected_records,
            "alternative": _pair_records(alternative, items, findings),
        })

    matched_items = {item_index for item_index, _finding_index, _specificity in selected}
    matched_findings = {finding_index for _item_index, finding_index, _specificity in selected}
    return {
        "pairs": [(items[item_index], findings[finding_index])
                  for item_index, finding_index, _specificity in selected],
        "records": selected_records,
        "unmatched_items": [item for index, item in enumerate(items)
                            if index not in matched_items],
        "unmatched_findings": [finding for index, finding in enumerate(findings)
                               if index not in matched_findings],
        "ambiguities": sorted(alternatives,
                              key=lambda value: json.dumps(value, sort_keys=True)),
    }


def _unmatched_item_record(item, findings):
    candidates = sorted((finding.get("id") for finding in findings if matches(finding, item)),
                        key=lambda value: str(value or ""))
    return {
        "id": item.get("id"),
        "reason": ("compatible_findings_assigned_elsewhere" if candidates
                   else "no_compatible_finding"),
        "compatible_findings": candidates,
    }


def _unmatched_finding_record(finding, buckets):
    candidates = [
        {"bucket": bucket, "id": item.get("id")}
        for bucket, items in buckets
        for item in items
        if matches(finding, item)
    ]
    candidates.sort(key=lambda value: (value["bucket"], str(value["id"] or "")))
    return {
        "id": finding.get("id"),
        "reason": ("compatible_ground_truth_items_assigned_elsewhere" if candidates
                   else "no_compatible_ground_truth_item"),
        "compatible_items": candidates,
    }


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

def score(truth, review, rejection_adjudication=None):
    findings = sorted(review.get("findings", []), key=_object_key)
    expected = sorted(truth.get("expected", []), key=_object_key)
    acceptable = sorted(truth.get("acceptable", []), key=_object_key)
    forbidden = sorted(truth.get("forbidden", []), key=_object_key)

    # Bucket priority is semantic, not incidental list order: required findings are claimed
    # first, optional real findings second, and known non-problems last. Within each bucket,
    # assignment is globally optimal and independent of JSON ordering.
    expected_assignment = optimal_assignment(expected, findings, "expected")
    pairs = expected_assignment["pairs"]
    missed = expected_assignment["unmatched_items"]

    claimed = {id(finding) for _item, finding in pairs}
    after_expected = [finding for finding in findings if id(finding) not in claimed]

    acceptable_assignment = optimal_assignment(acceptable, after_expected, "acceptable")
    tolerated = acceptable_assignment["pairs"]
    claimed.update(id(finding) for _item, finding in tolerated)
    after_acceptable = [finding for finding in findings if id(finding) not in claimed]

    # A ground-truth item and finding each participate in at most one assignment. Duplicate
    # reports of one forbidden issue remain false positives, but only one is classified as
    # the annotated trap; the rest are transparently reported as unanticipated duplicates.
    forbidden_assignment = optimal_assignment(forbidden, after_acceptable, "forbidden")
    trapped = forbidden_assignment["pairs"]
    claimed.update(id(finding) for _item, finding in trapped)
    unclaimed = [finding for finding in findings if id(finding) not in claimed]

    true_positives = len(pairs)
    false_negatives = len(missed)
    false_positives = len(trapped) + len(unclaimed)
    rejection_summary = None
    if rejection_adjudication is not None:
        rejection_summary = adjudicate_candidate_rejections(
            truth, review, rejection_adjudication, missed, tolerated, trapped)

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
        "case_outcome": case_outcome(truth, review, trapped, rejection_summary),
        "per_category": per_category(pairs, missed, trapped, unclaimed),
        "severity_calibration": severity_calibration(pairs),
        "confidence_calibration": confidence_calibration(pairs, trapped, unclaimed),
        "confidence_ceiling_violations": ceiling_violations(pairs),
        "recommendation_accuracy": recommendation_accuracy(pairs),
        "matching": {
            "algorithm": "maximum-cardinality-maximum-specificity-v1",
            "bucket_priority": ["expected", "acceptable", "forbidden"],
            "specificity_order": [
                "primary_location", "exact_symbol", "exact_file", "primary_category",
            ],
            "assignments": {
                "expected": expected_assignment["records"],
                "acceptable": acceptable_assignment["records"],
                "forbidden": forbidden_assignment["records"],
            },
            "ambiguities": sorted(
                expected_assignment["ambiguities"]
                + acceptable_assignment["ambiguities"]
                + forbidden_assignment["ambiguities"],
                key=lambda value: json.dumps(value, sort_keys=True),
            ),
            "unmatched": {
                "expected": [_unmatched_item_record(item, findings) for item in missed],
                "acceptable": [
                    _unmatched_item_record(item, findings)
                    for item in acceptable_assignment["unmatched_items"]
                ],
                "forbidden": [
                    _unmatched_item_record(item, findings)
                    for item in forbidden_assignment["unmatched_items"]
                ],
                "findings": [
                    _unmatched_finding_record(finding, (
                        ("expected", expected),
                        ("acceptable", acceptable),
                        ("forbidden", forbidden),
                    ))
                    for finding in unclaimed
                ],
            },
        },
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


def adjudicate_candidate_rejections(truth, review, adjudication, missed, tolerated, trapped):
    """Score a complete, human-supplied mapping made after the review was frozen."""
    required = {"schema_version", "truth_content_sha256", "review_content_sha256",
                "adjudicator", "adjudicated_at", "decisions"}
    if not isinstance(adjudication, dict) or set(adjudication) != required:
        raise AdjudicationError("adjudication must contain exactly the required fields")
    if type(adjudication["schema_version"]) is not int or adjudication["schema_version"] != 1:
        raise AdjudicationError("unsupported adjudication schema_version")
    for label, content in (("truth", truth), ("review", review)):
        if adjudication[label + "_content_sha256"] != content_digest(content):
            raise AdjudicationError("%s content digest differs from adjudicated artifact" % label)
    adjudicator = adjudication["adjudicator"]
    if not isinstance(adjudicator, str) or not adjudicator.strip():
        raise AdjudicationError("adjudicator must be named")
    timestamp = adjudication["adjudicated_at"]
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AdjudicationError("adjudicated_at must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdjudicationError("adjudicated_at must be a timezone-aware ISO timestamp")
    reproducibility = review.get("reproducibility")
    generated_at = (reproducibility.get("generated_at")
                    if isinstance(reproducibility, dict) else None)
    if generated_at is not None:
        try:
            generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise AdjudicationError("review generated_at must be a valid timestamp") from exc
        if generated.tzinfo is None or generated.utcoffset() is None or parsed < generated:
            raise AdjudicationError("adjudication must follow the frozen review")

    candidates = review.get("considered_not_reported", [])
    if not isinstance(candidates, list):
        raise AdjudicationError("considered_not_reported must be a candidate list")
    decisions = adjudication["decisions"]
    if not isinstance(decisions, list) or len(decisions) != len(candidates):
        raise AdjudicationError("adjudication needs one decision per candidate")
    truth_ids = [item["id"] for bucket in ("expected", "acceptable", "forbidden")
                 for item in truth.get(bucket, [])]
    if len(truth_ids) != len(set(truth_ids)):
        raise AdjudicationError("ground-truth IDs must be unique for candidate adjudication")
    missed_ids = {item["id"] for item in missed}
    optional_ids = {item["id"] for item in truth.get("acceptable", [])} - {
        item["id"] for item, _finding in tolerated}
    avoided_ids = {item["id"] for item in truth.get("forbidden", [])} - {
        item["id"] for item, _finding in trapped}
    seen_indices, seen_truth_ids = set(), set()
    counts = {"correct_rejection": 0, "acceptable_nonreport": 0,
              "incorrect_rejection": 0, "unresolved": 0}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise AdjudicationError("each candidate decision must be an object")
        judgment = decision.get("judgment")
        expected_fields = {"candidate_index", "judgment", "reason"}
        if judgment in ("correct_rejection", "acceptable_nonreport", "incorrect_rejection"):
            expected_fields.add("ground_truth_id")
        if set(decision) != expected_fields or judgment not in counts:
            raise AdjudicationError("invalid candidate decision fields or judgment")
        index = decision["candidate_index"]
        if type(index) is not int or index < 0 or index >= len(candidates) or index in seen_indices:
            raise AdjudicationError("candidate indices must be unique and in range")
        seen_indices.add(index)
        if not isinstance(decision["reason"], str) or not decision["reason"].strip():
            raise AdjudicationError("each decision needs an adjudication reason")
        if judgment != "unresolved":
            truth_id = decision["ground_truth_id"]
            eligible = {"correct_rejection": avoided_ids,
                        "acceptable_nonreport": optional_ids,
                        "incorrect_rejection": missed_ids}[judgment]
            if not isinstance(truth_id, str) or truth_id not in eligible:
                bucket = {"correct_rejection": "unreported forbidden",
                          "acceptable_nonreport": "unreported acceptable",
                          "incorrect_rejection": "missed required"}[judgment]
                raise AdjudicationError("decision must name a %s ground-truth item" % bucket)
            if truth_id in seen_truth_ids:
                raise AdjudicationError("one ground-truth item cannot count for two candidates")
            seen_truth_ids.add(truth_id)
        counts[judgment] += 1

    decided = counts["correct_rejection"] + counts["incorrect_rejection"]
    return {
        "reported": len(candidates),
        "adjudicated_correct": counts["correct_rejection"],
        "adjudicated_acceptable": counts["acceptable_nonreport"],
        "adjudicated_incorrect": counts["incorrect_rejection"],
        "unresolved": counts["unresolved"],
        "correct_rate": round(counts["correct_rejection"] / decided, 3) if decided else None,
        "adjudicator": adjudicator,
    }


def case_outcome(truth, review, trapped, rejection_summary=None):
    """Expose no-finding behavior without treating silence as proof of a healthy system."""
    expected = truth.get("expected", [])
    findings = review.get("findings", [])
    abstained = not findings
    change_scoped = review.get("mode") == "change-scoped"
    return {
        "no_required_findings": not expected,
        "abstention": {
            "occurred": abstained,
            "matches_annotation": (not expected) if abstained else None,
        },
        "known_traps_avoided": len(truth.get("forbidden", [])) - len(trapped),
        "unknown_verdict": {
            "declared": (review.get("verdict") == "UNKNOWN") if change_scoped else None,
            "derived": (validate_review.derive_verdict(review) == "UNKNOWN")
            if change_scoped else None,
            # Ground truth does not currently adjudicate whether uncertainty was warranted.
            "correctness": None,
        },
        "candidate_rejections": rejection_summary if rejection_summary is not None else {
            "reported": len(review.get("considered_not_reported") or []),
            # Free-text candidates have no independently adjudicated trap mapping.
            "adjudicated_correct": None,
        },
    }


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

def _stable_id(finding):
    value = finding.get("stable_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _location_category_key(finding):
    location = finding.get("location") or {}
    return (normalize_path(location.get("file")), finding.get("category") or "unknown")


def _multiset_comparison(first, second, key_function, label_function):
    """Compare without collapsing repeated keys; each key owns a list, never one finding."""
    groups_a = defaultdict(list)
    groups_b = defaultdict(list)
    for finding in sorted(first, key=_object_key):
        key = key_function(finding)
        if key is not None:
            groups_a[key].append(finding)
    for finding in sorted(second, key=_object_key):
        key = key_function(finding)
        if key is not None:
            groups_b[key].append(finding)

    pairs = []
    only_a = []
    only_b = []
    union_count = 0
    for key in sorted(set(groups_a) | set(groups_b), key=str):
        left = groups_a.get(key, [])
        right = groups_b.get(key, [])
        shared_count = min(len(left), len(right))
        union_count += max(len(left), len(right))
        pairs.extend((key, left[index], right[index]) for index in range(shared_count))
        only_a.extend(label_function(key, finding) for finding in left[shared_count:])
        only_b.extend(label_function(key, finding) for finding in right[shared_count:])

    return {
        "overlap": round(len(pairs) / union_count, 3) if union_count else None,
        "shared": len(pairs),
        "union": union_count,
        "only_in_a": sorted(only_a),
        "only_in_b": sorted(only_b),
        "pairs": pairs,
        "groups_a": groups_a,
        "groups_b": groups_b,
    }


def _stable_label(stable_id, finding):
    return "%s (%s)" % (stable_id, finding.get("id") or "missing-report-id")


def _approximate_label(key, finding):
    path, category = key
    return "%s (%s; %s)" % (path, category, finding.get("id") or "missing-report-id")


def _collision_records(groups):
    return [
        {
            "stable_id": stable_id,
            "count": len(findings),
            "findings": sorted((finding.get("id") for finding in findings),
                               key=lambda value: str(value or "")),
        }
        for stable_id, findings in sorted(groups.items())
        if len(findings) > 1
    ]


def stability(first, second):
    """Compare two runs using canonical stable IDs without collapsing duplicate findings.

    `overlap` is multiset Jaccard overlap over findings that carry `stable_id`. Findings with
    no ID are excluded from that primary metric and listed explicitly. A separate
    `approximate_location_category` diagnostic preserves the historical file/category view,
    also as a multiset, but never presents it as semantic identity.

    Severity and priority agreement are computed only for stable IDs that occur exactly once
    in both runs. A repeated stable ID is a visible collision, so arbitrarily pairing those
    findings would manufacture calibration evidence.
    """
    a = sorted(first.get("findings", []), key=_object_key)
    b = sorted(second.get("findings", []), key=_object_key)
    identified_a = [finding for finding in a if _stable_id(finding) is not None]
    identified_b = [finding for finding in b if _stable_id(finding) is not None]
    missing_a = [finding for finding in a if _stable_id(finding) is None]
    missing_b = [finding for finding in b if _stable_id(finding) is None]

    stable = _multiset_comparison(identified_a, identified_b, _stable_id, _stable_label)
    approximate = _multiset_comparison(
        a, b, _location_category_key, _approximate_label)

    comparable_pairs = [
        (left, right)
        for stable_id, left, right in stable["pairs"]
        if len(stable["groups_a"][stable_id]) == 1
        and len(stable["groups_b"][stable_id]) == 1
    ]
    severity_agree = sum(
        left.get("severity") == right.get("severity") for left, right in comparable_pairs)
    priority_agree = sum(
        left.get("priority") == right.get("priority") for left, right in comparable_pairs)
    comparable_count = len(comparable_pairs)

    stable_id_overlap = stable["overlap"]
    collisions = {
        "run_a": _collision_records(stable["groups_a"]),
        "run_b": _collision_records(stable["groups_b"]),
    }
    return {
        "findings": {"run_a": len(a), "run_b": len(b)},
        "stable_ids_present": {
            "run_a": len(identified_a), "run_b": len(identified_b),
        },
        "missing_stable_ids": {
            "run_a": [finding.get("id") for finding in missing_a],
            "run_b": [finding.get("id") for finding in missing_b],
        },
        "overlap": stable_id_overlap,
        "stable_id_overlap": stable_id_overlap,
        # Deprecated compatibility alias. Stable IDs are now the primary comparison key, so
        # a second notion of "agreement" would be the same quantity under a misleading name.
        "stable_id_agreement": stable_id_overlap,
        "shared": stable["shared"],
        "only_in_a": stable["only_in_a"],
        "only_in_b": stable["only_in_b"],
        "stable_id_collisions": collisions,
        "calibration_pairs": comparable_count,
        "calibration_pairs_excluded_by_collision": stable["shared"] - comparable_count,
        "severity_agreement": (
            round(severity_agree / comparable_count, 3) if comparable_count else None),
        "priority_agreement": (
            round(priority_agree / comparable_count, 3) if comparable_count else None),
        "approximate_location_category": {
            "overlap": approximate["overlap"],
            "shared": approximate["shared"],
            "union": approximate["union"],
            "only_in_a": approximate["only_in_a"],
            "only_in_b": approximate["only_in_b"],
        },
        "caveats": [
            "Canonical stable_id is derived from normalized file, symbol, and category. "
            "Two reviews that cite the same mechanism at different points in one call chain "
            "will still have different IDs and require human adjudication.",
            "approximate_location_category is diagnostic only: it can overstate agreement "
            "for distinct mechanisms in one file/category and understate agreement across "
            "different call-chain citation locations.",
            "Repeated canonical IDs are retained as separate findings and reported in "
            "stable_id_collisions; their severity and priority pairs are excluded rather "
            "than assigned arbitrarily.",
        ],
    }


# --------------------------------------------------------------------------------------
# Discipline metrics — computed without ground truth
#
# These exist for benchmark/ab-comparison.md. Comparing a methodology-guided review against
# an unguided one cannot lean on precision and recall, because this corpus was partly derived
# from methodology-guided output and is therefore biased toward it. Everything below is
# computed from the review and the repository alone, so no annotation is involved and that
# bias has nowhere to enter.
#
# Every metric flags candidates; none declares a violation. A number absent from the
# repository may still be legitimate — supplied by the user, or shown as a derivation — and
# only a reader can tell which. Same division of labour as the ground-truth corpus: the
# machine finds, a human decides.
# --------------------------------------------------------------------------------------

DIGITS = re.compile(r"\d+(?:\.\d+)?")

# Deliberately requires a unit. A bare integer in prose is usually a count, a version, or a
# line number; it is the unit that turns a number into a performance claim a reader will act
# on and cannot check.
NUMBER_WITH_UNIT = re.compile(
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:ms|milliseconds?|µs|us|microseconds?|ns|nanoseconds?"
    r"|secs?|seconds?|mins?|minutes?|hours?"
    r"|%|percent"
    r"|[kmgt]i?b\b|bytes?"
    r"|rps|qps|req/s|requests?/s(?:ec)?|ops/s|queries/s"
    r"|x\b|×)",
    re.IGNORECASE)

RUNTIME_ARTIFACT = re.compile(
    r"\b(?:profil\w+|pprof|explain|analyz\w+|benchmark\w*|trace[sd]?|tracing|span"
    r"|flame\s?graph|metrics?|load[- ]test\w*|apm|dashboard|histogram)\b",
    re.IGNORECASE)

# The list SKILL.md Hard Rule 5 names by name.
CARGO_CULT = re.compile(
    r"\b(?:cach(?:e|es|ing)|redis|memcached"
    r"|add(?:ing)?\s+(?:an?\s+)?index|indexes|indices"
    r"|async|asynchronous|parallelis\w+|parallelize"
    r"|shard(?:ing|ed)?|denormali[sz]\w+|microservices?"
    r"|more\s+(?:servers|instances|replicas)|scale\s+(?:out|horizontally))\b",
    re.IGNORECASE)

TEXT_FIELDS = ("problem", "evidence", "impact", "conditions", "recommendation",
               "trade_offs", "validation", "why_this_might_not_matter")

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target",
             ".venv", "venv", "__pycache__", ".idea", ".gradle", ".next",
             ".claude", ".agents", ".opencode", ".codex"}
SKIP_DIR_NAMES = {name.casefold() for name in SKIP_DIRS}

MAX_SCANNED_FILE_BYTES = 2_000_000


def _strings(value):
    """Every string anywhere inside a schema value, which may be str, list, or object."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            for found in _strings(item):
                yield found
    elif isinstance(value, dict):
        for item in value.values():
            for found in _strings(item):
                yield found


def finding_text(finding, fields=TEXT_FIELDS):
    parts = []
    for field in fields:
        parts.extend(_strings(finding.get(field)))
    return "\n".join(parts)


def repo_number_tokens(repo_root):
    """Every numeric literal appearing anywhere in the repository's text files.

    Used to ask one question of each number in a review: does this value exist in the code at
    all? The test is deliberately generous — a match anywhere in any file counts — so it
    under-flags rather than over-flags. A "50" from an unrelated port number will absolve a
    fabricated "50%". That asymmetry is the right one: everything this flags is worth a
    human's attention, and the count is a floor, never a total.
    """
    tokens = set()
    root = os.path.realpath(os.path.abspath(repo_root))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name.casefold() not in SKIP_DIR_NAMES
            and _is_within_repo(os.path.join(dirpath, name), root)
        ]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not _is_within_repo(path, root):
                continue
            try:
                if os.path.getsize(path) > MAX_SCANNED_FILE_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
            except OSError:
                continue
            tokens.update(DIGITS.findall(text))
    return tokens


def _is_within_repo(path, root):
    """Do not let a repository symlink make numeric evidence come from outside it."""
    resolved_path = os.path.realpath(os.path.abspath(path))
    resolved_root = os.path.realpath(os.path.abspath(root))
    try:
        return os.path.commonpath([resolved_path, resolved_root]) == resolved_root
    except ValueError:
        return False


def _rate(count, total):
    return None if not total else round(count / float(total), 3)


def discipline(review, repo_tokens=None):
    """Metrics that need no ground truth, only the review and (optionally) the repository."""
    findings = review.get("findings") or []
    total = len(findings)

    cited, falsifiable, conditioned, cargo_cult, ceiling, unsourced = [], [], [], [], [], []

    for finding in findings:
        fid = finding.get("id")

        if (finding.get("location") or {}).get("file"):
            cited.append(fid)

        if str(finding.get("why_this_might_not_matter") or "").strip() \
                or finding.get("counter_evidence"):
            falsifiable.append(fid)

        has_conditions = bool(str(finding.get("conditions") or "").strip())
        if has_conditions:
            conditioned.append(fid)

        recommendation = "\n".join(_strings(finding.get("recommendation")))
        if CARGO_CULT.search(recommendation) and not has_conditions:
            cargo_cult.append({"id": fid, "recommendation": recommendation[:200]})

        if finding.get("confidence") == "Confirmed" \
                and not RUNTIME_ARTIFACT.search(finding_text(finding, ("evidence",))):
            ceiling.append(fid)

        if repo_tokens is not None:
            claims = []
            for match in NUMBER_WITH_UNIT.finditer(finding_text(finding)):
                literal = DIGITS.search(match.group(0))
                if literal and literal.group(0).replace(",", "") not in repo_tokens:
                    claims.append(match.group(0).strip())
            if claims:
                unsourced.append({"id": fid, "claims": sorted(set(claims))})

    result = {
        "findings": total,
        "rates": {
            "citation": _rate(len(cited), total),
            "falsifiability": _rate(len(falsifiable), total),
            "conditioned_recommendation": _rate(len(conditioned), total),
            "cargo_cult": _rate(len(cargo_cult), total),
            "confidence_ceiling_violation": _rate(len(ceiling), total),
        },
        "flagged": {
            "uncited": [f.get("id") for f in findings
                        if not (f.get("location") or {}).get("file")],
            "unfalsifiable": [f.get("id") for f in findings if f.get("id") not in falsifiable],
            "unconditioned": [f.get("id") for f in findings if f.get("id") not in conditioned],
            "cargo_cult": cargo_cult,
            "confidence_ceiling": ceiling,
        },
        "caveats": [
            "Every entry is a candidate for adjudication, not a proven violation. The "
            "machine finds; a human decides, exactly as with the ground-truth corpus.",
            "cargo_cult flags a named remedy offered with no stated workload condition. A "
            "remedy that names its conditions is not flagged, however wrong it may be — "
            "this measures whether Hard Rule 5 was followed, not whether the fix is right.",
        ],
    }

    if repo_tokens is None:
        result["unsourced_numbers"] = None
        result["rates"]["unsourced_number"] = None
        result["caveats"].append(
            "Unsourced-number detection was skipped: no --repo was given, so no numeric "
            "claim could be checked against the code.")
    else:
        result["unsourced_numbers"] = unsourced
        result["rates"]["unsourced_number"] = _rate(len(unsourced), total)
        result["caveats"].append(
            "unsourced_number is a floor, not a total. A number counts as sourced if it "
            "appears anywhere in any repository file, so an unrelated coincidence absolves "
            "a fabricated figure. It under-reports by construction.")

    if not total:
        result["caveats"].append(
            "Zero findings: every rate is null rather than zero. A review that reported "
            "nothing has no discipline rate to measure, and scoring it 0.0 would read as a "
            "failure when it may be the correct answer.")

    return result


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

    outcome = result["case_outcome"]
    if outcome["abstention"]["occurred"]:
        lines.append("  abstained; matches annotated required set: %s"
                     % ("yes" if outcome["abstention"]["matches_annotation"] else "no"))
    if outcome["no_required_findings"]:
        lines.append("  no required findings in annotation; known traps avoided: %d of %d"
                     % (outcome["known_traps_avoided"], result["restraint"]["forbidden_items"]))
    if outcome["unknown_verdict"]["declared"] is not None:
        lines.append("  UNKNOWN verdict: declared=%s derived=%s (correctness unadjudicated)"
                     % (outcome["unknown_verdict"]["declared"],
                        outcome["unknown_verdict"]["derived"]))
    rejected = outcome["candidate_rejections"]
    if rejected["adjudicated_correct"] is not None:
        lines.append("  candidate rejections: %d correct traps, %d acceptable omissions, "
                     "%d incorrect, %d unresolved"
                     % (rejected["adjudicated_correct"],
                        rejected["adjudicated_acceptable"],
                        rejected["adjudicated_incorrect"], rejected["unresolved"]))

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

    if result["matching"]["ambiguities"]:
        lines.append("")
        lines.append("  AMBIGUOUS MATCHING — equal-score assignments need adjudication:")
        for ambiguity in result["matching"]["ambiguities"]:
            selected = ", ".join("%s->%s" % (pair["item"], pair["finding"])
                                 for pair in ambiguity["selected"])
            alternative = ", ".join("%s->%s" % (pair["item"], pair["finding"])
                                    for pair in ambiguity["alternative"])
            lines.append("    %s: selected [%s], alternative [%s]"
                         % (ambiguity["bucket"], selected, alternative))

    for miss in result["misses"]:
        lines.append("  MISSED  %s — %s" % (miss["id"], miss["mechanism"]))

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    scorer = subparsers.add_parser("score", help="exploratory score against supplied truth")
    scorer.add_argument("--truth", required=True)
    scorer.add_argument("--review", required=True)
    scorer.add_argument("--dataset", type=Path, default=benchmark_dataset.DATASET)
    scorer.add_argument("--rejection-adjudication", type=Path)
    scorer.add_argument("--json", action="store_true", help="emit JSON instead of a report")

    evaluator = subparsers.add_parser(
        "evaluate", help="score only a registered, independently established held-out case")
    evaluator.add_argument("--case", required=True)
    evaluator.add_argument("--review", required=True)
    evaluator.add_argument("--dataset", type=Path, default=benchmark_dataset.DATASET)
    evaluator.add_argument("--rejection-adjudication", type=Path)
    evaluator.add_argument("--json", action="store_true", help="emit JSON instead of a report")

    comparer = subparsers.add_parser(
        "stability", help="compare two independent reviews of the same code")
    comparer.add_argument("--review", required=True, action="append", dest="reviews",
                          help="pass twice")

    disciplined = subparsers.add_parser(
        "discipline",
        help="ground-truth-independent metrics (see benchmark/ab-comparison.md)")
    disciplined.add_argument("--review", required=True)
    disciplined.add_argument(
        "--repo",
        help="repository the review describes; without it, numeric claims are not checked")

    args = parser.parse_args(argv)
    rejection_adjudication = None
    if args.command in ("score", "evaluate") and args.rejection_adjudication:
        try:
            rejection_adjudication = load(args.rejection_adjudication)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            parser.error("cannot read rejection adjudication: %s" % exc)

    if args.command == "score":
        dataset_root = args.dataset.resolve().parent.parent
        try:
            registration = benchmark_dataset.classify_truth(
                args.truth, dataset_root, args.dataset)
        except benchmark_dataset.DatasetError as exc:
            parser.error(str(exc))
        if registration and registration["split"] == "held_out":
            parser.error("registered held-out truth requires evaluate --case %s"
                         % registration["case"])
        truth = load(args.truth)
        review = load(args.review)
        try:
            result = score(truth, review, rejection_adjudication)
        except AdjudicationError as exc:
            parser.error(str(exc))
        result["adjudication_fingerprints"] = {
            "truth_content_sha256": content_digest(truth),
            "review_content_sha256": content_digest(review),
        }
        result["evidence_tier"] = "exploratory"
        if registration:
            result["dataset"] = registration
        print(json.dumps(result, indent=2) if args.json
              else "EXPLORATORY — not held-out evaluation\n" + render(result))
        return 0

    if args.command == "evaluate":
        dataset_root = args.dataset.resolve().parent.parent
        try:
            truth_path, provenance = benchmark_dataset.require_held_out(
                args.case, dataset_root, args.dataset)
        except benchmark_dataset.DatasetError as exc:
            parser.error(str(exc))
        review_path = Path(args.review).resolve()
        parts = [part.lower() for part in review_path.parts]
        if any(parts[i:i + 2] == ["benchmark", "ab-results"]
               for i in range(len(parts) - 1)):
            parser.error("historical treatment output cannot be a held-out review")
        truth = load(truth_path)
        review = load(review_path)
        errors = validate_review.validate(
            review, Path(__file__).resolve().parents[2] / "schemas")
        if errors:
            parser.error("invalid held-out review: %s" % errors[0])
        reproducibility = review["reproducibility"]
        source = reproducibility["repository"]
        pinned = truth["repository"]
        if source.get("name") != pinned["name"] or source.get("commit") != pinned["commit"]:
            parser.error("held-out review repository and commit must match ground truth")
        try:
            registered_at = benchmark_dataset.parse_timestamp(
                provenance["pre_registered_at"], "pre_registered_at")
            annotation_at = benchmark_dataset.parse_timestamp(
                provenance["annotation_recorded_at"], "annotation_recorded_at")
            generated_at = benchmark_dataset.parse_timestamp(
                reproducibility.get("generated_at"), "review generated_at")
        except benchmark_dataset.DatasetError as exc:
            parser.error(str(exc))
        if generated_at <= registered_at:
            parser.error("held-out review must postdate pre-registration")
        if generated_at <= annotation_at:
            parser.error("held-out review must postdate the current annotation version")
        model = reproducibility.get("model")
        if not isinstance(model, str) or not model.strip() or model.strip().startswith("<"):
            parser.error("held-out review must name the actual model")
        try:
            result = score(truth, review, rejection_adjudication)
        except AdjudicationError as exc:
            parser.error(str(exc))
        result["adjudication_fingerprints"] = {
            "truth_content_sha256": content_digest(truth),
            "review_content_sha256": content_digest(review),
        }
        result["dataset"] = {"case": args.case, "split": "held_out", **provenance}
        heading = ("HELD-OUT %s | dataset %s | annotation v%d\n" % (
            args.case, provenance["dataset_version"], provenance["annotation_version"]))
        print(json.dumps(result, indent=2) if args.json else heading + render(result))
        return 0

    if args.command == "discipline":
        tokens = repo_number_tokens(args.repo) if args.repo else None
        print(json.dumps(discipline(load(args.review), tokens), indent=2))
        return 0

    if len(args.reviews) != 2:
        parser.error("stability needs exactly two --review arguments")
    print(json.dumps(stability(load(args.reviews[0]), load(args.reviews[1])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
