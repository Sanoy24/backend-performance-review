#!/usr/bin/env python3
"""Verify a complete campaign adjudication and freeze resolved ground truth."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import annotation_resolve as resolver  # noqa: E402


PACKAGE_ID = re.compile(r"^packet-[0-9a-f]{16}$")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


class AnnotationResolutionCollectionError(ValueError):
    """A collected campaign or adjudication handoff cannot be audited safely."""


def _load_bytes(raw, label):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationResolutionCollectionError(
            "cannot parse %s: %s" % (label, exc)) from exc


def _load(path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise AnnotationResolutionCollectionError(
            "cannot read %s: %s" % (path, exc)) from exc
    return _load_bytes(raw, path), raw


def _timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AnnotationResolutionCollectionError(
            "%s must be a timezone-aware ISO timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationResolutionCollectionError(
            "%s must be a timezone-aware ISO timestamp" % label)
    return parsed


def _digest(value):
    return resolver.scorer.content_digest(value)


def _read_relative(root, relative, label):
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or relative.startswith("/")
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise AnnotationResolutionCollectionError("%s path is unsafe" % label)
    target = root
    for part in relative.split("/"):
        target = target / part
        if target.is_symlink():
            raise AnnotationResolutionCollectionError(
                "%s cannot traverse a symbolic link" % label)
    if not target.is_file():
        raise AnnotationResolutionCollectionError("%s is missing" % label)
    try:
        target.resolve().relative_to(root.resolve())
        raw = target.read_bytes()
    except (OSError, ValueError) as exc:
        raise AnnotationResolutionCollectionError(
            "cannot read %s: %s" % (label, exc)) from exc
    return _load_bytes(raw, label), raw


def _validate_collection(value):
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("status") != "requires_human_adjudication"
            or value.get("held_out_ready") is not False):
        raise AnnotationResolutionCollectionError(
            "unsupported or ineligible annotation collection")
    if not SAFE_ID.fullmatch(str(value.get("campaign_id", ""))):
        raise AnnotationResolutionCollectionError("collection campaign_id is invalid")
    for field in ("coordinator_raw_sha256", "coordinator_content_sha256",
                  "manifest_sha256", "protocol_sha256", "collector_sha256",
                  "intake_tool_sha256"):
        if not SHA256.fullmatch(str(value.get(field, ""))):
            raise AnnotationResolutionCollectionError(
                "collection %s is invalid" % field)
    frozen_at = _timestamp(value.get("frozen_at"), "collection frozen_at")
    collected_at = _timestamp(value.get("collected_at"), "collection collected_at")
    if collected_at <= frozen_at:
        raise AnnotationResolutionCollectionError(
            "collection time must follow the campaign freeze time")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AnnotationResolutionCollectionError("collection has no cases")

    by_id = {}
    paths = set()
    all_packages = set()
    for case in cases:
        if (not isinstance(case, dict)
                or case.get("status") != "requires_human_adjudication"):
            raise AnnotationResolutionCollectionError(
                "collection case is invalid or ineligible")
        case_id = case.get("case")
        if not SAFE_ID.fullmatch(str(case_id or "")) or case_id in by_id:
            raise AnnotationResolutionCollectionError(
                "collection case IDs must be safe and unique")
        if (not isinstance(case.get("repository"), str)
                or not case["repository"].strip()
                or not COMMIT.fullmatch(str(case.get("commit", "")))):
            raise AnnotationResolutionCollectionError(
                "%s repository identity is invalid" % case_id)
        intake_path = case.get("intake_path")
        if intake_path != "cases/%s/intake.json" % case_id:
            raise AnnotationResolutionCollectionError(
                "%s intake path is invalid" % case_id)
        if not SHA256.fullmatch(str(case.get("intake_content_sha256", ""))):
            raise AnnotationResolutionCollectionError(
                "%s intake digest is invalid" % case_id)
        submissions = case.get("submissions")
        if not isinstance(submissions, list) or len(submissions) != 2:
            raise AnnotationResolutionCollectionError(
                "%s must contain exactly two submissions" % case_id)
        slots = set()
        packages = set()
        reviewers = set()
        for record in submissions:
            if not isinstance(record, dict):
                raise AnnotationResolutionCollectionError(
                    "%s submission record is invalid" % case_id)
            slot = record.get("slot")
            package_id = record.get("package_id")
            reviewer = record.get("reviewer")
            if slot not in (1, 2) or isinstance(slot, bool) or slot in slots:
                raise AnnotationResolutionCollectionError(
                    "%s submission slots must be exactly 1 and 2" % case_id)
            if (not PACKAGE_ID.fullmatch(str(package_id or ""))
                    or package_id in packages or package_id in all_packages):
                raise AnnotationResolutionCollectionError(
                    "collection package IDs must be valid and globally unique")
            if (not isinstance(reviewer, str) or not reviewer.strip()
                    or reviewer.strip().casefold() in reviewers):
                raise AnnotationResolutionCollectionError(
                    "%s reviewers must be present and distinct" % case_id)
            expected_path = "cases/%s/%s.json" % (case_id, package_id)
            if record.get("path") != expected_path:
                raise AnnotationResolutionCollectionError(
                    "%s submission path is invalid" % case_id)
            if expected_path in paths:
                raise AnnotationResolutionCollectionError(
                    "collection artifact paths must be unique")
            for field in ("raw_sha256", "content_sha256"):
                if not SHA256.fullmatch(str(record.get(field, ""))):
                    raise AnnotationResolutionCollectionError(
                        "%s submission %s is invalid" % (case_id, field))
            if _timestamp(record.get("annotated_at"), "%s annotated_at" % package_id) >= collected_at:
                raise AnnotationResolutionCollectionError(
                    "%s annotation must precede collection" % package_id)
            slots.add(slot)
            packages.add(package_id)
            all_packages.add(package_id)
            reviewers.add(reviewer.strip().casefold())
            paths.add(expected_path)
        if slots != {1, 2}:
            raise AnnotationResolutionCollectionError(
                "%s submission slots must be exactly 1 and 2" % case_id)
        if intake_path in paths:
            raise AnnotationResolutionCollectionError(
                "collection artifact paths must be unique")
        paths.add(intake_path)
        by_id[case_id] = case
    return by_id, collected_at


def _verify_case(collection_dir, case):
    submissions = sorted(case["submissions"], key=lambda record: record["slot"])
    reviewer_records = []
    annotations = []
    for record in submissions:
        annotation, raw = _read_relative(
            collection_dir, record["path"], "%s submission" % record["package_id"])
        if hashlib.sha256(raw).hexdigest() != record["raw_sha256"]:
            raise AnnotationResolutionCollectionError(
                "%s raw submission digest changed" % record["package_id"])
        if _digest(annotation) != record["content_sha256"]:
            raise AnnotationResolutionCollectionError(
                "%s submission content changed" % record["package_id"])
        provenance = annotation.get("annotation") if isinstance(annotation, dict) else None
        annotated_by = provenance.get("annotated_by") if isinstance(provenance, dict) else None
        if (not isinstance(provenance, dict)
                or not isinstance(annotated_by, str)
                or annotated_by.strip() != record["reviewer"]
                or provenance.get("annotated_at") != record["annotated_at"]):
            raise AnnotationResolutionCollectionError(
                "%s provenance differs from collection.json" % record["package_id"])
        marker = "annotation_package_id=%s" % record["package_id"]
        notes = provenance.get("notes")
        if not isinstance(notes, str) or marker not in notes.split():
            raise AnnotationResolutionCollectionError(
                "%s no longer preserves its package marker" % record["package_id"])
        reviewer_records.append(record)
        annotations.append(annotation)

    report, _ = _read_relative(
        collection_dir, case["intake_path"], "%s intake" % case["case"])
    if _digest(report) != case["intake_content_sha256"]:
        raise AnnotationResolutionCollectionError(
            "%s intake content changed" % case["case"])
    try:
        resolver._validate_intake(report)
    except resolver.AnnotationResolutionError as exc:
        raise AnnotationResolutionCollectionError(
            "%s intake is invalid: %s" % (case["case"], exc)) from exc
    try:
        derived_report = resolver.intake_tool.compare(*annotations)
    except resolver.intake_tool.AnnotationIntakeError as exc:
        raise AnnotationResolutionCollectionError(
            "%s source annotations are invalid: %s" % (case["case"], exc)) from exc
    if _digest(derived_report) != _digest(report):
        raise AnnotationResolutionCollectionError(
            "%s intake is not derived from its preserved submissions" % case["case"])
    repository = report["context"]["repository"]
    if (repository.get("name") != case["repository"]
            or repository.get("commit") != case["commit"]):
        raise AnnotationResolutionCollectionError(
            "%s intake repository differs from collection.json" % case["case"])
    for intake_reviewer, record in zip(report["reviewers"], reviewer_records):
        if (intake_reviewer["id"].strip() != record["reviewer"]
                or intake_reviewer["annotated_at"] != record["annotated_at"]
                or intake_reviewer["content_sha256"] != record["content_sha256"]):
            raise AnnotationResolutionCollectionError(
                "%s intake reviewer provenance differs from submissions" % case["case"])
    return report


def _resolution_paths(directory, case_ids):
    if not directory.is_dir() or directory.is_symlink():
        raise AnnotationResolutionCollectionError(
            "resolutions directory is missing or unsafe")
    entries = list(directory.iterdir())
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise AnnotationResolutionCollectionError(
            "resolutions directory may contain only regular JSON files")
    actual = {entry.name for entry in entries}
    expected = {case_id + ".json" for case_id in case_ids}
    if actual != expected:
        raise AnnotationResolutionCollectionError(
            "returned resolutions differ from expected cases: missing %s; extra %s"
            % (sorted(expected - actual), sorted(actual - expected)))
    return {case_id: directory / (case_id + ".json") for case_id in case_ids}


def collect(collection_dir, resolutions_dir, destination, finalized_at):
    """Verify every case resolution, then freeze resolved truth and provenance."""
    collection_dir = Path(collection_dir)
    resolutions_dir = Path(resolutions_dir)
    destination = Path(destination)
    if collection_dir.is_symlink():
        raise AnnotationResolutionCollectionError(
            "annotation collection directory cannot be a symbolic link")
    if destination.exists():
        raise AnnotationResolutionCollectionError(
            "resolution collection destination already exists: %s" % destination)
    if not destination.parent.is_dir():
        raise AnnotationResolutionCollectionError(
            "resolution collection destination parent does not exist: %s"
            % destination.parent)
    resolved_destination = destination.resolve()
    for label, source in (("collection", collection_dir),
                          ("resolutions", resolutions_dir)):
        try:
            resolved_destination.relative_to(source.resolve())
        except ValueError:
            continue
        raise AnnotationResolutionCollectionError(
            "destination cannot be inside the %s directory" % label)

    manifest_path = collection_dir / "collection.json"
    if manifest_path.is_symlink():
        raise AnnotationResolutionCollectionError(
            "collection.json cannot be a symbolic link")
    collection_manifest, raw_collection = _load(manifest_path)
    cases, collected_time = _validate_collection(collection_manifest)
    finalized_time = _timestamp(finalized_at, "finalized_at")
    if finalized_time <= collected_time:
        raise AnnotationResolutionCollectionError(
            "finalized_at must follow annotation collection")
    resolution_paths = _resolution_paths(resolutions_dir, set(cases))

    prepared = []
    for case_id, case in sorted(cases.items()):
        report = _verify_case(collection_dir, case)
        resolution, raw_resolution = _load(resolution_paths[case_id])
        try:
            truth = resolver.resolve(report, resolution)
        except resolver.AnnotationResolutionError as exc:
            raise AnnotationResolutionCollectionError(
                "%s: %s" % (case_id, exc)) from exc
        adjudicated_time = _timestamp(
            resolution["adjudicated_at"], "%s adjudicated_at" % case_id)
        if finalized_time <= adjudicated_time:
            raise AnnotationResolutionCollectionError(
                "finalized_at must follow every adjudication")
        prepared.append((case_id, case, resolution, raw_resolution, truth))

    outputs = {}
    case_summaries = []
    for case_id, case, resolution, raw_resolution, truth in prepared:
        resolution_path = "cases/%s/resolution.json" % case_id
        truth_path = "cases/%s/ground-truth.json" % case_id
        truth_bytes = (json.dumps(
            truth, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        outputs[resolution_path] = raw_resolution
        outputs[truth_path] = truth_bytes
        case_summaries.append({
            "case": case_id,
            "repository": case["repository"],
            "commit": case["commit"],
            "adjudicator": resolution["adjudicator"].strip(),
            "adjudicated_at": resolution["adjudicated_at"],
            "intake_content_sha256": case["intake_content_sha256"],
            "resolution_path": resolution_path,
            "resolution_raw_sha256": hashlib.sha256(raw_resolution).hexdigest(),
            "resolution_content_sha256": _digest(resolution),
            "ground_truth_path": truth_path,
            "ground_truth_raw_sha256": hashlib.sha256(truth_bytes).hexdigest(),
            "ground_truth_content_sha256": _digest(truth),
            "status": "resolved_pending_protocol_review",
        })

    result = {
        "schema_version": 1,
        "status": "resolved_pending_protocol_review",
        "held_out_ready": False,
        "campaign_id": collection_manifest["campaign_id"],
        "collection_raw_sha256": hashlib.sha256(raw_collection).hexdigest(),
        "collection_content_sha256": _digest(collection_manifest),
        "manifest_sha256": collection_manifest["manifest_sha256"],
        "protocol_sha256": collection_manifest["protocol_sha256"],
        "resolution_collector_sha256": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "resolver_sha256": hashlib.sha256(
            Path(resolver.__file__).read_bytes()).hexdigest(),
        "collected_at": collection_manifest["collected_at"],
        "finalized_at": finalized_at,
        "cases": case_summaries,
        "next_step": ("Complete independent protocol and source-provenance review before "
                      "registering any case; this artifact does not establish held-out "
                      "readiness."),
    }
    outputs["resolution-collection.json"] = (json.dumps(
        result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    destination.mkdir()
    for relative, content in outputs.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection-dir", type=Path, required=True)
    parser.add_argument("--resolutions-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--finalized-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = collect(args.collection_dir, args.resolutions_dir,
                         args.output_dir, args.finalized_at)
    except AnnotationResolutionCollectionError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
