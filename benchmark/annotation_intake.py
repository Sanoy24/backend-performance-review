#!/usr/bin/env python3
"""Validate two blinded expert annotations and prepare a disagreement report.

This is intake tooling, not adjudication. Location-based matching only proposes candidate
pairs. It cannot establish that two prose mechanisms describe the same defect, and its
output is never sufficient to register a held-out benchmark case.
"""

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "scoring"))
sys.path.insert(0, str(ROOT / "scripts"))

import score as scorer  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402


COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
CONTEXT_FIELDS = ("name", "commit", "url", "workload")
ISSUE_FIELDS = ("bucket", "category", "severity", "max_confidence", "mechanism")
FORBIDDEN_FIELDS = ("category", "why_not")


class AnnotationIntakeError(ValueError):
    """An annotation cannot safely participate in independent comparison."""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationIntakeError("cannot read %s: %s" % (path, exc)) from exc


def _timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AnnotationIntakeError(
            "%s must be a timezone-aware ISO timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationIntakeError("%s must be a timezone-aware ISO timestamp" % label)
    return parsed


def _validate_annotation(value, label):
    errors = schema_lite.validate_file(value, ROOT / "schemas/ground-truth.schema.json")
    if errors:
        raise AnnotationIntakeError("%s is not valid ground truth: %s" % (label, errors[0]))

    repository = value["repository"]
    for field in CONTEXT_FIELDS:
        if not isinstance(repository.get(field), str) or not repository[field].strip():
            raise AnnotationIntakeError("%s repository.%s is required" % (label, field))
    if not COMMIT_SHA.fullmatch(repository["commit"]):
        raise AnnotationIntakeError("%s repository.commit must be a full commit SHA" % label)

    annotation = value.get("annotation")
    if not isinstance(annotation, dict):
        raise AnnotationIntakeError("%s annotation provenance is required" % label)
    if annotation.get("method") != "expert-manual-review":
        raise AnnotationIntakeError(
            "%s annotation.method must be expert-manual-review" % label)
    reviewer = annotation.get("annotated_by")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise AnnotationIntakeError("%s annotation.annotated_by is required" % label)
    _timestamp(annotation.get("annotated_at"), "%s annotation.annotated_at" % label)

    identifiers = []
    for bucket in ("expected", "acceptable", "forbidden"):
        identifiers.extend(item["id"] for item in value[bucket])
    duplicates = sorted({identifier for identifier in identifiers
                         if identifiers.count(identifier) > 1})
    if duplicates:
        raise AnnotationIntakeError(
            "%s item IDs must be unique across buckets: %s" % (label, duplicates))

    change_scope = value.get("change_scope")
    if change_scope and change_scope["diff_base"] == repository["commit"]:
        raise AnnotationIntakeError("%s change_scope.diff_base must differ from commit" % label)


def _require_same_context(a, b):
    for field in CONTEXT_FIELDS:
        if a["repository"].get(field) != b["repository"].get(field):
            raise AnnotationIntakeError(
                "annotations must use the same repository.%s" % field)
    scope_a = a.get("change_scope")
    scope_b = b.get("change_scope")
    if bool(scope_a) != bool(scope_b):
        raise AnnotationIntakeError(
            "annotations must use the same scope: change_scope is present in only one")
    if scope_a and scope_a["diff_base"] != scope_b["diff_base"]:
        raise AnnotationIntakeError(
            "annotations must use the same change_scope.diff_base")


def _bucketed(annotation, buckets):
    result = []
    for bucket in buckets:
        for item in annotation[bucket]:
            result.append((bucket, item))
    return result


def _location_only(item):
    """Remove judgment fields so matching can only suggest a shared code location."""
    candidate = copy.deepcopy(item)
    candidate.pop("category", None)
    candidate.pop("also_acceptable_categories", None)
    return candidate


def _symmetric_location_specificity(reviewer_b, reviewer_a):
    """Accept either reviewer's declared primary or alternative location convention."""
    forward = scorer.match_specificity(reviewer_b, reviewer_a)
    reverse = scorer.match_specificity(reviewer_a, reviewer_b)
    compatible = [value for value in (forward, reverse) if value is not None]
    return max(compatible) if compatible else None


def _differences(a, b, fields):
    return [field for field in fields if a.get(field) != b.get(field)]


def _item_view(bucket, item, fields):
    """Keep the report self-contained without copying unrelated annotation metadata."""
    view = {"id": item["id"], "bucket": bucket, "location": item["location"]}
    for field in ("also_locations", "also_acceptable_categories") + tuple(fields):
        if field != "bucket" and field in item:
            view[field] = item[field]
    return view


def _candidate_report(a, b, buckets, fields, label):
    bucketed_a = _bucketed(a, buckets)
    bucketed_b = _bucketed(b, buckets)
    by_id_a = {item["id"]: (bucket, item) for bucket, item in bucketed_a}
    by_id_b = {item["id"]: (bucket, item) for bucket, item in bucketed_b}
    match_a = [_location_only(item) for _bucket, item in bucketed_a]
    match_b = [_location_only(item) for _bucket, item in bucketed_b]
    assignment = scorer.optimal_assignment(
        match_a, match_b, label, specificity_fn=_symmetric_location_specificity)

    pairs = []
    for record in assignment["records"]:
        bucket_a, item_a = by_id_a[record["item"]]
        bucket_b, item_b = by_id_b[record["finding"]]
        comparison_a = dict(item_a, bucket=bucket_a)
        comparison_b = dict(item_b, bucket=bucket_b)
        pairs.append({
            "reviewer_a": _item_view(bucket_a, item_a, fields),
            "reviewer_b": _item_view(bucket_b, item_b, fields),
            "location_match": record["specificity"],
            "judgment_differences": _differences(comparison_a, comparison_b, fields),
            "resolution_required": True,
            "resolution_reason": "human_mechanism_confirmation_required",
        })

    unmatched_ids_a = {entry["id"] for entry in assignment["unmatched_items"]}
    unmatched_ids_b = {entry["id"] for entry in assignment["unmatched_findings"]}
    unmatched_a = [_item_view(bucket, item, fields)
                   for bucket, item in bucketed_a if item["id"] in unmatched_ids_a]
    unmatched_b = [_item_view(bucket, item, fields)
                   for bucket, item in bucketed_b if item["id"] in unmatched_ids_b]
    return {
        "candidate_pairs": pairs,
        "unmatched_reviewer_a": sorted(unmatched_a, key=lambda value: (value["bucket"], value["id"])),
        "unmatched_reviewer_b": sorted(unmatched_b, key=lambda value: (value["bucket"], value["id"])),
        "ambiguities": assignment["ambiguities"],
    }


def _scope_disagreements(a, b):
    disagreements = []
    for field in ("language", "framework", "datastore"):
        if a["repository"].get(field) != b["repository"].get(field):
            disagreements.append({
                "field": "repository.%s" % field,
                "reviewer_a": a["repository"].get(field),
                "reviewer_b": b["repository"].get(field),
            })
    if a.get("change_scope"):
        for field in ("expected_verdict", "rationale", "unknowns"):
            if a["change_scope"].get(field) != b["change_scope"].get(field):
                disagreements.append({
                    "field": "change_scope.%s" % field,
                    "reviewer_a": a["change_scope"].get(field),
                    "reviewer_b": b["change_scope"].get(field),
                })
    return disagreements


def compare(a, b):
    """Return deterministic intake output for two independently authored annotations."""
    _validate_annotation(a, "reviewer A")
    _validate_annotation(b, "reviewer B")
    reviewer_a = a["annotation"]["annotated_by"].strip()
    reviewer_b = b["annotation"]["annotated_by"].strip()
    if reviewer_a.casefold() == reviewer_b.casefold():
        raise AnnotationIntakeError("annotations must name distinct reviewers")
    _require_same_context(a, b)

    issues = _candidate_report(
        a, b, ("expected", "acceptable"), ISSUE_FIELDS, "issues")
    forbidden = _candidate_report(
        a, b, ("forbidden",), FORBIDDEN_FIELDS, "forbidden")
    scope_disagreements = _scope_disagreements(a, b)
    pair_count = len(issues["candidate_pairs"]) + len(forbidden["candidate_pairs"])
    unmatched_count = sum(len(group[key]) for group in (issues, forbidden)
                          for key in ("unmatched_reviewer_a", "unmatched_reviewer_b"))
    ambiguity_count = len(issues["ambiguities"]) + len(forbidden["ambiguities"])

    return {
        "schema_version": 1,
        "status": "requires_human_adjudication",
        "held_out_ready": False,
        "context": {
            "repository": {field: a["repository"][field] for field in CONTEXT_FIELDS},
            "diff_base": (a.get("change_scope") or {}).get("diff_base"),
        },
        "reviewers": [
            {"role": "reviewer_a", "id": reviewer_a,
             "annotated_at": a["annotation"]["annotated_at"],
             "content_sha256": scorer.content_digest(a)},
            {"role": "reviewer_b", "id": reviewer_b,
             "annotated_at": b["annotation"]["annotated_at"],
             "content_sha256": scorer.content_digest(b)},
        ],
        "issues": issues,
        "forbidden": forbidden,
        "scope_disagreements": scope_disagreements,
        "summary": {
            "candidate_pairs_requiring_confirmation": pair_count,
            "unmatched_items": unmatched_count,
            "scope_disagreements": len(scope_disagreements),
            "matching_ambiguities": ambiguity_count,
            "adjudication_units": (pair_count + unmatched_count
                                    + len(scope_disagreements) + ambiguity_count),
        },
        "limitations": [
            "Location matching proposes candidates; it does not establish mechanism equivalence.",
            "A human must resolve every candidate pair, unmatched item, scope disagreement, and ambiguity.",
            "This report does not prove reviewer independence or make a case eligible for held-out scoring.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotation-a", type=Path, required=True)
    parser.add_argument("--annotation-b", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = compare(_load(args.annotation_a), _load(args.annotation_b))
    except AnnotationIntakeError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
