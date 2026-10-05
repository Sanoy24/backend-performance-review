#!/usr/bin/env python3
"""Validate one expert annotation against its frozen blinded assignment."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import json_schema_lite as schema_lite  # noqa: E402


class AnnotationSubmissionError(ValueError):
    """A reviewer submission is malformed or differs from its assignment."""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationSubmissionError("cannot read %s: %s" % (path, exc)) from exc


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AnnotationSubmissionError(
            "annotation.annotated_at must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationSubmissionError(
            "annotation.annotated_at must be a timezone-aware ISO timestamp")
    return parsed


def validate(assignment, annotation, schema_dir=None):
    """Return the package ID after schema and assignment-specific checks pass."""
    if (not isinstance(assignment, dict) or assignment.get("schema_version") != 1
            or not isinstance(assignment.get("package_id"), str)
            or not isinstance(assignment.get("frozen_at"), str)):
        raise AnnotationSubmissionError("unsupported assignment")
    schema_dir = Path(schema_dir) if schema_dir else ROOT / "schemas"
    errors = schema_lite.validate_file(
        annotation, schema_dir / "ground-truth.schema.json")
    if errors:
        raise AnnotationSubmissionError("invalid ground truth: %s" % errors[0])

    expected_repository = assignment.get("repository")
    if not isinstance(expected_repository, dict):
        raise AnnotationSubmissionError("assignment repository is missing")
    if annotation["repository"] != expected_repository:
        raise AnnotationSubmissionError(
            "annotation repository metadata must exactly match assignment.json")

    provenance = annotation.get("annotation")
    if not isinstance(provenance, dict):
        raise AnnotationSubmissionError("annotation provenance is required")
    reviewer = provenance.get("annotated_by")
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or reviewer.startswith("REPLACE_")):
        raise AnnotationSubmissionError("annotation.annotated_by must identify the reviewer")
    if provenance.get("method") != "expert-manual-review":
        raise AnnotationSubmissionError(
            "annotation.method must be expert-manual-review")
    annotated_at = _timestamp(provenance.get("annotated_at"))
    try:
        frozen_at = datetime.fromisoformat(
            assignment["frozen_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationSubmissionError("assignment frozen_at is invalid") from exc
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise AnnotationSubmissionError("assignment frozen_at is invalid")
    if annotated_at <= frozen_at:
        raise AnnotationSubmissionError(
            "annotation.annotated_at must follow the frozen assignment")
    marker = "annotation_package_id=%s" % assignment["package_id"]
    if marker not in provenance.get("notes", "").split():
        raise AnnotationSubmissionError(
            "annotation.notes must preserve %s" % marker)

    identifiers = []
    for bucket in ("expected", "acceptable", "forbidden"):
        identifiers.extend(item["id"] for item in annotation[bucket])
    if len(set(identifiers)) != len(identifiers):
        raise AnnotationSubmissionError("item IDs must be unique across all buckets")

    expected_scope = assignment.get("change_scope")
    actual_scope = annotation.get("change_scope")
    if expected_scope is None and actual_scope is not None:
        raise AnnotationSubmissionError("annotation added an unassigned change scope")
    if expected_scope is not None and (not isinstance(actual_scope, dict)
                                       or actual_scope.get("diff_base")
                                       != expected_scope["diff_base"]):
        raise AnnotationSubmissionError(
            "annotation must adjudicate the assigned change_scope.diff_base")
    if (expected_scope is not None
            and str(actual_scope.get("expected_verdict", "")).startswith("REPLACE_")):
        raise AnnotationSubmissionError("change_scope.expected_verdict must be adjudicated")
    return assignment["package_id"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assignment", type=Path, default=Path("assignment.json"))
    parser.add_argument("--annotation", type=Path, default=Path("annotation.json"))
    parser.add_argument("--schema-dir", type=Path, default=Path("schemas"))
    args = parser.parse_args(argv)
    try:
        package_id = validate(
            _load(args.assignment), _load(args.annotation), args.schema_dir)
    except AnnotationSubmissionError as exc:
        parser.error(str(exc))
    print("Annotation is valid for %s." % package_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
