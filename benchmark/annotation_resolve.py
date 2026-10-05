#!/usr/bin/env python3
"""Validate human disagreement decisions and emit resolved benchmark ground truth.

The command consumes a frozen report from ``annotation_intake.py`` and a resolution file.
It checks complete decision coverage and traceability, then emits ordinary schema-valid
ground truth. It does not register a dataset case or establish reviewer independence.
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "scoring"))
sys.path.insert(0, str(ROOT / "scripts"))

import annotation_intake as intake_tool  # noqa: E402
import score as scorer  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402


SHA256 = re.compile(r"^[0-9a-f]{64}$")
ITEM_KINDS = frozenset({"candidate_pair", "unmatched_item"})
OUTCOME_BY_KIND = {
    "candidate_pair": frozenset({"include", "exclude"}),
    "unmatched_item": frozenset({"include", "exclude"}),
    "scope_disagreement": frozenset({"context_resolved"}),
    "matching_ambiguity": frozenset({"ambiguity_resolved"}),
}


class AnnotationResolutionError(ValueError):
    """A resolution is stale, incomplete, inconsistent, or not auditable."""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationResolutionError("cannot read %s: %s" % (path, exc)) from exc


def _expected_unit_id(group, role, unit):
    kind = unit.get("unit_kind")
    if kind == "candidate_pair":
        return intake_tool._unit_id("%s-candidate-pair" % group, {
            "reviewer_a": unit.get("reviewer_a", {}).get("id"),
            "reviewer_b": unit.get("reviewer_b", {}).get("id"),
        })
    if kind == "unmatched_item":
        return intake_tool._unit_id("%s-unmatched-item" % group, {
            "reviewer": role, "id": unit.get("id"), "bucket": unit.get("bucket")})
    if kind == "matching_ambiguity":
        value = {key: copy.deepcopy(item) for key, item in unit.items()
                 if key not in ("unit_id", "unit_kind")}
        return intake_tool._unit_id("%s-matching-ambiguity" % group, value)
    if kind == "scope_disagreement":
        return intake_tool._unit_id("scope-disagreement", unit.get("field"))
    raise AnnotationResolutionError("unknown intake unit kind: %r" % kind)


def _collect_units(report):
    units = []
    for group in ("issues", "forbidden"):
        section = report.get(group)
        if not isinstance(section, dict):
            raise AnnotationResolutionError("intake report is missing %s" % group)
        for key, role, expected_kind in (
                ("candidate_pairs", None, "candidate_pair"),
                ("unmatched_reviewer_a", "reviewer_a", "unmatched_item"),
                ("unmatched_reviewer_b", "reviewer_b", "unmatched_item"),
                ("ambiguities", None, "matching_ambiguity")):
            values = section.get(key)
            if not isinstance(values, list):
                raise AnnotationResolutionError("intake report %s.%s must be a list"
                                                % (group, key))
            for unit in values:
                if not isinstance(unit, dict) or unit.get("unit_kind") != expected_kind:
                    raise AnnotationResolutionError(
                        "intake report %s.%s has an invalid unit" % (group, key))
                expected_id = _expected_unit_id(group, role, unit)
                if unit.get("unit_id") != expected_id:
                    raise AnnotationResolutionError(
                        "intake unit ID does not match its content: %s" % unit.get("unit_id"))
                units.append(unit)
    disagreements = report.get("scope_disagreements")
    if not isinstance(disagreements, list):
        raise AnnotationResolutionError("intake report scope_disagreements must be a list")
    for unit in disagreements:
        if not isinstance(unit, dict) or unit.get("unit_kind") != "scope_disagreement":
            raise AnnotationResolutionError("intake report has an invalid scope disagreement")
        if unit.get("unit_id") != _expected_unit_id(None, None, unit):
            raise AnnotationResolutionError(
                "intake unit ID does not match its content: %s" % unit.get("unit_id"))
        units.append(unit)

    identifiers = [unit["unit_id"] for unit in units]
    if len(set(identifiers)) != len(identifiers):
        raise AnnotationResolutionError("intake report contains duplicate unit IDs")
    return units


def _validate_intake(report):
    if (not isinstance(report, dict) or report.get("schema_version") != 1
            or report.get("status") != "requires_human_adjudication"
            or report.get("held_out_ready") is not False):
        raise AnnotationResolutionError("unsupported or ineligible intake report")
    context = report.get("context")
    repository = context.get("repository") if isinstance(context, dict) else None
    if not isinstance(repository, dict):
        raise AnnotationResolutionError("intake report repository context is missing")
    for field in intake_tool.CONTEXT_FIELDS:
        if not isinstance(repository.get(field), str) or not repository[field].strip():
            raise AnnotationResolutionError(
                "intake report context repository.%s is required" % field)

    reviewers = report.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 2:
        raise AnnotationResolutionError("intake report must contain exactly two reviewers")
    reviewer_ids = []
    for index, reviewer in enumerate(reviewers):
        if not isinstance(reviewer, dict):
            raise AnnotationResolutionError("intake reviewer provenance is invalid")
        reviewer_id = reviewer.get("id")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise AnnotationResolutionError("intake reviewer ID is required")
        if reviewer.get("role") != ("reviewer_a" if index == 0 else "reviewer_b"):
            raise AnnotationResolutionError("intake reviewer roles are invalid")
        if not SHA256.fullmatch(str(reviewer.get("content_sha256", ""))):
            raise AnnotationResolutionError("intake reviewer content digest is invalid")
        try:
            intake_tool._timestamp(reviewer.get("annotated_at"), "reviewer annotated_at")
        except intake_tool.AnnotationIntakeError as exc:
            raise AnnotationResolutionError(str(exc)) from exc
        reviewer_ids.append(reviewer_id.strip())
    if reviewer_ids[0].casefold() == reviewer_ids[1].casefold():
        raise AnnotationResolutionError("intake report reviewers must be distinct")
    units = _collect_units(report)
    summary = report.get("summary")
    actual = {
        "candidate_pairs_requiring_confirmation": sum(
            unit["unit_kind"] == "candidate_pair" for unit in units),
        "unmatched_items": sum(
            unit["unit_kind"] == "unmatched_item" for unit in units),
        "scope_disagreements": sum(
            unit["unit_kind"] == "scope_disagreement" for unit in units),
        "matching_ambiguities": sum(
            unit["unit_kind"] == "matching_ambiguity" for unit in units),
        "adjudication_units": len(units),
    }
    if summary != actual:
        raise AnnotationResolutionError("intake summary does not match its units")
    return units


def _ground_truth_ids(truth):
    identifiers = {}
    for bucket in ("expected", "acceptable", "forbidden"):
        for item in truth[bucket]:
            if item["id"] in identifiers:
                raise AnnotationResolutionError(
                    "resolved ground-truth item IDs must be unique across buckets")
            identifiers[item["id"]] = bucket
    return identifiers


def _validate_context(report, truth):
    expected_repository = report["context"]["repository"]
    for field in intake_tool.CONTEXT_FIELDS:
        if truth["repository"].get(field) != expected_repository[field]:
            raise AnnotationResolutionError(
                "resolved ground truth changed repository.%s" % field)
    expected_base = report["context"].get("diff_base")
    actual_scope = truth.get("change_scope")
    if expected_base is None and actual_scope is not None:
        raise AnnotationResolutionError(
            "resolved ground truth added an unreviewed change scope")
    if expected_base is not None and (not isinstance(actual_scope, dict)
                                      or actual_scope.get("diff_base") != expected_base):
        raise AnnotationResolutionError(
            "resolved ground truth changed or omitted change_scope.diff_base")


def resolve(report, resolution):
    """Validate one resolution artifact and return schema-valid resolved ground truth."""
    units = _validate_intake(report)
    errors = schema_lite.validate_file(
        resolution, ROOT / "schemas/annotation-resolution.schema.json")
    if errors:
        raise AnnotationResolutionError("invalid resolution: %s" % errors[0])
    if resolution["intake_content_sha256"] != scorer.content_digest(report):
        raise AnnotationResolutionError("resolution does not bind to this intake report")

    adjudicator = resolution["adjudicator"].strip()
    if not adjudicator:
        raise AnnotationResolutionError("adjudicator cannot be blank")
    reviewers = report["reviewers"]
    if adjudicator.casefold() in {entry["id"].strip().casefold() for entry in reviewers}:
        raise AnnotationResolutionError("adjudicator must be distinct from both reviewers")
    try:
        adjudicated_at = intake_tool._timestamp(
            resolution["adjudicated_at"], "adjudicated_at")
        latest_annotation = max(
            intake_tool._timestamp(entry["annotated_at"], "annotated_at")
            for entry in reviewers)
    except intake_tool.AnnotationIntakeError as exc:
        raise AnnotationResolutionError(str(exc)) from exc
    if adjudicated_at <= latest_annotation:
        raise AnnotationResolutionError(
            "adjudicated_at must follow both source annotations")

    decisions = resolution["decisions"]
    decision_ids = [decision["unit_id"] for decision in decisions]
    if len(set(decision_ids)) != len(decision_ids):
        raise AnnotationResolutionError("each intake unit needs exactly one decision")
    expected = {unit["unit_id"]: unit for unit in units}
    missing = sorted(set(expected) - set(decision_ids))
    unknown = sorted(set(decision_ids) - set(expected))
    if missing or unknown:
        raise AnnotationResolutionError(
            "resolution decision coverage differs from intake units: missing %s; unknown %s"
            % (missing, unknown))

    truth = copy.deepcopy(resolution["resolved_ground_truth"])
    if "annotation" in truth:
        raise AnnotationResolutionError(
            "resolved_ground_truth.annotation is generated from adjudication provenance")
    _validate_context(report, truth)
    final_buckets = _ground_truth_ids(truth)
    final_ids = set(final_buckets)
    cited_ids = set()
    for decision in decisions:
        if not decision["reason"].strip():
            raise AnnotationResolutionError(
                "%s decision reason cannot be blank" % decision["unit_id"])
        unit = expected[decision["unit_id"]]
        kind = unit["unit_kind"]
        outcome = decision["outcome"]
        references = decision["final_item_ids"]
        if outcome not in OUTCOME_BY_KIND[kind]:
            raise AnnotationResolutionError(
                "%s outcome %s is invalid for %s" % (decision["unit_id"], outcome, kind))
        if outcome == "include" and not references:
            raise AnnotationResolutionError(
                "%s include decision needs final_item_ids" % decision["unit_id"])
        if outcome != "include" and references:
            raise AnnotationResolutionError(
                "%s %s decision cannot name final items"
                % (decision["unit_id"], outcome))
        missing_final = sorted(set(references) - final_ids)
        if missing_final:
            raise AnnotationResolutionError(
                "%s names missing final items: %s" % (decision["unit_id"], missing_final))
        if kind in ITEM_KINDS:
            source_bucket = (unit["reviewer_a"]["bucket"]
                             if kind == "candidate_pair" else unit["bucket"])
            expected_family = ("forbidden" if source_bucket == "forbidden" else "issue")
            wrong_family = sorted(
                identifier for identifier in references
                if ("forbidden" if final_buckets[identifier] == "forbidden" else "issue")
                != expected_family)
            if wrong_family:
                raise AnnotationResolutionError(
                    "%s maps across issue/forbidden families: %s"
                    % (decision["unit_id"], wrong_family))
        cited_ids.update(references)
    uncited = sorted(final_ids - cited_ids)
    if uncited:
        raise AnnotationResolutionError(
            "resolved ground-truth items lack an include decision: %s" % uncited)

    resolution_digest = scorer.content_digest(resolution)
    reviewer_names = ", ".join(entry["id"].strip() for entry in reviewers)
    truth["annotation"] = {
        "annotated_by": adjudicator,
        "annotated_at": resolution["adjudicated_at"],
        "method": "independent-expert-adjudication",
        "notes": ("Resolved independent annotations from %s; intake_sha256=%s; "
                  "resolution_sha256=%s"
                  % (reviewer_names, resolution["intake_content_sha256"],
                     resolution_digest)),
    }
    errors = schema_lite.validate_file(truth, ROOT / "schemas/ground-truth.schema.json")
    if errors:
        raise AnnotationResolutionError(
            "generated ground truth is invalid: %s" % errors[0])
    return truth


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--resolution", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        truth = resolve(_load(args.intake), _load(args.resolution))
    except (AnnotationResolutionError, intake_tool.AnnotationIntakeError) as exc:
        parser.error(str(exc))
    print(json.dumps(truth, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
