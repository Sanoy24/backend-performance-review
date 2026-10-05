#!/usr/bin/env python3
"""Verify and freeze returned blinded annotations for human adjudication."""

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

import annotation_campaign as campaign  # noqa: E402
import annotation_intake as intake  # noqa: E402
import annotation_submission as submission  # noqa: E402


PACKAGE_ID = re.compile(r"^packet-[0-9a-f]{16}$")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AnnotationCollectionError(ValueError):
    """A campaign handoff or returned annotation cannot be audited safely."""


def _load(path):
    try:
        raw = Path(path).read_bytes()
        return json.loads(raw.decode("utf-8")), raw
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationCollectionError("cannot read %s: %s" % (path, exc)) from exc


def _timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AnnotationCollectionError(
            "%s must be a timezone-aware ISO timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationCollectionError(
            "%s must be a timezone-aware ISO timestamp" % label)
    return parsed


def _packet_files(packet):
    if not packet.is_dir() or packet.is_symlink():
        raise AnnotationCollectionError("packet is missing or not a regular directory: %s"
                                        % packet.name)
    files = {}
    for path in packet.rglob("*"):
        if path.is_symlink():
            raise AnnotationCollectionError("packet contains a symbolic link: %s" % path)
        if path.is_file():
            files[path.relative_to(packet).as_posix()] = path.read_bytes()
    return files


def _validate_coordinator(value):
    if (not isinstance(value, dict)
            or value.get("status") != "awaiting_independent_annotations"
            or value.get("held_out_ready") is not False):
        raise AnnotationCollectionError("unsupported or ineligible coordinator record")
    for field in ("manifest_sha256", "protocol_sha256"):
        if not SHA256.fullmatch(str(value.get(field, ""))):
            raise AnnotationCollectionError("coordinator %s is invalid" % field)
    if not SAFE_ID.fullmatch(str(value.get("campaign_id", ""))):
        raise AnnotationCollectionError("coordinator campaign_id is invalid")
    protocol_files = value.get("protocol_files")
    if not isinstance(protocol_files, list) or not protocol_files:
        raise AnnotationCollectionError("coordinator protocol_files are missing")
    protocol_paths = set()
    for record in protocol_files:
        path = record.get("path") if isinstance(record, dict) else None
        digest = record.get("sha256") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or set(record) != {"path", "sha256"}
                or not isinstance(path, str) or not path or "\\" in path or ":" in path
                or path.startswith("/") or any(part in ("", ".", "..")
                                              for part in path.split("/"))):
            raise AnnotationCollectionError("coordinator protocol path is unsafe")
        if path in protocol_paths or not SHA256.fullmatch(str(digest or "")):
            raise AnnotationCollectionError("coordinator protocol file record is invalid")
        protocol_paths.add(path)
    if campaign._canonical_digest(protocol_files) != value["protocol_sha256"]:
        raise AnnotationCollectionError(
            "coordinator protocol digest does not match protocol_files")
    _timestamp(value.get("frozen_at"), "coordinator frozen_at")
    records = value.get("packages")
    if not isinstance(records, list) or not records:
        raise AnnotationCollectionError("coordinator has no packages")

    packages = set()
    cases = {}
    for record in records:
        if not isinstance(record, dict):
            raise AnnotationCollectionError("coordinator package record is invalid")
        package_id = record.get("package_id")
        case_id = record.get("case")
        slot = record.get("slot")
        if not PACKAGE_ID.fullmatch(str(package_id or "")):
            raise AnnotationCollectionError("coordinator package ID is invalid")
        if package_id in packages:
            raise AnnotationCollectionError("coordinator package IDs must be unique")
        packages.add(package_id)
        if not SAFE_ID.fullmatch(str(case_id or "")):
            raise AnnotationCollectionError("coordinator case ID is invalid")
        if slot not in (1, 2) or isinstance(slot, bool):
            raise AnnotationCollectionError("coordinator slot must be 1 or 2")
        if not SHA256.fullmatch(str(record.get("package_sha256", ""))):
            raise AnnotationCollectionError("coordinator package digest is invalid")
        cases.setdefault(case_id, []).append(record)
    for case_id, case_records in cases.items():
        if len(case_records) != 2 or {record["slot"] for record in case_records} != {1, 2}:
            raise AnnotationCollectionError(
                "%s must contain exactly slots 1 and 2" % case_id)
    return cases


def _submission_paths(directory, package_ids):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise AnnotationCollectionError("submissions directory is missing or unsafe")
    entries = list(directory.iterdir())
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise AnnotationCollectionError(
            "submissions directory may contain only regular JSON files")
    actual = {entry.name for entry in entries}
    expected = {package_id + ".json" for package_id in package_ids}
    if actual != expected:
        raise AnnotationCollectionError(
            "returned submissions differ from expected packets: missing %s; extra %s"
            % (sorted(expected - actual), sorted(actual - expected)))
    return {package_id: directory / (package_id + ".json")
            for package_id in package_ids}


def collect(campaign_dir, submissions_dir, destination, collected_at):
    """Verify all campaign artifacts, then write a frozen adjudication handoff."""
    campaign_dir = Path(campaign_dir)
    submissions_dir = Path(submissions_dir)
    destination = Path(destination)
    if campaign_dir.is_symlink():
        raise AnnotationCollectionError("campaign directory cannot be a symbolic link")
    if destination.exists():
        raise AnnotationCollectionError(
            "collection destination already exists: %s" % destination)
    if not destination.parent.is_dir():
        raise AnnotationCollectionError(
            "collection destination parent does not exist: %s" % destination.parent)
    resolved_destination = destination.resolve()
    for label, source in (("campaign", campaign_dir), ("submissions", submissions_dir)):
        try:
            resolved_destination.relative_to(source.resolve())
        except ValueError:
            continue
        raise AnnotationCollectionError(
            "collection destination cannot be inside the %s directory" % label)
    coordinator_path = campaign_dir / "coordinator.json"
    if coordinator_path.is_symlink():
        raise AnnotationCollectionError("coordinator.json cannot be a symbolic link")
    coordinator, raw_coordinator = _load(coordinator_path)
    cases = _validate_coordinator(coordinator)
    collected_time = _timestamp(collected_at, "collected_at")
    frozen_time = _timestamp(coordinator["frozen_at"], "coordinator frozen_at")
    if collected_time <= frozen_time:
        raise AnnotationCollectionError("collected_at must follow the campaign freeze time")

    package_records = {record["package_id"]: record
                       for records in cases.values() for record in records}
    paths = _submission_paths(submissions_dir, set(package_records))
    prepared = {}
    latest_annotation = frozen_time
    for package_id, record in sorted(package_records.items()):
        packet = campaign_dir / package_id
        files = _packet_files(packet)
        if campaign._tree_digest(files) != record["package_sha256"]:
            raise AnnotationCollectionError("packet content changed: %s" % package_id)
        try:
            protocol_files = [{
                "path": record["path"],
                "sha256": hashlib.sha256(files[record["path"]]).hexdigest(),
            } for record in coordinator["protocol_files"]]
        except KeyError as exc:
            raise AnnotationCollectionError(
                "%s is missing protocol file %s" % (package_id, exc.args[0])) from exc
        if campaign._canonical_digest(protocol_files) != coordinator["protocol_sha256"]:
            raise AnnotationCollectionError(
                "packet protocol differs from coordinator: %s" % package_id)

        assignment = json.loads(files["assignment.json"].decode("utf-8"))
        if (assignment.get("package_id") != package_id
                or assignment.get("frozen_at") != coordinator["frozen_at"]
                or assignment.get("repository", {}).get("name") != record.get("repository")
                or assignment.get("repository", {}).get("commit") != record.get("commit")):
            raise AnnotationCollectionError(
                "packet assignment differs from coordinator: %s" % package_id)
        annotation, raw_annotation = _load(paths[package_id])
        try:
            submission.validate(assignment, annotation, packet / "schemas")
        except submission.AnnotationSubmissionError as exc:
            raise AnnotationCollectionError(
                "%s: %s" % (package_id, exc)) from exc
        annotated_at = _timestamp(
            annotation["annotation"]["annotated_at"], "%s annotated_at" % package_id)
        latest_annotation = max(latest_annotation, annotated_at)
        prepared[package_id] = {
            "annotation": annotation,
            "raw": raw_annotation,
            "raw_sha256": hashlib.sha256(raw_annotation).hexdigest(),
            "content_sha256": intake.scorer.content_digest(annotation),
        }
    if collected_time <= latest_annotation:
        raise AnnotationCollectionError(
            "collected_at must follow every annotation completion time")

    outputs = {}
    case_summaries = []
    for case_id, records in sorted(cases.items()):
        records = sorted(records, key=lambda value: value["slot"])
        first = prepared[records[0]["package_id"]]
        second = prepared[records[1]["package_id"]]
        try:
            report = intake.compare(first["annotation"], second["annotation"])
        except intake.AnnotationIntakeError as exc:
            raise AnnotationCollectionError("%s: %s" % (case_id, exc)) from exc
        submissions = []
        for record, prepared_submission in zip(records, (first, second)):
            relative = "cases/%s/%s.json" % (case_id, record["package_id"])
            outputs[relative] = prepared_submission["raw"]
            annotation = prepared_submission["annotation"]
            submissions.append({
                "slot": record["slot"],
                "package_id": record["package_id"],
                "reviewer": annotation["annotation"]["annotated_by"].strip(),
                "annotated_at": annotation["annotation"]["annotated_at"],
                "raw_sha256": prepared_submission["raw_sha256"],
                "content_sha256": prepared_submission["content_sha256"],
                "path": relative,
            })
        intake_path = "cases/%s/intake.json" % case_id
        outputs[intake_path] = campaign._json_bytes(report)
        case_summaries.append({
            "case": case_id,
            "repository": records[0]["repository"],
            "commit": records[0]["commit"],
            "submissions": submissions,
            "intake_path": intake_path,
            "intake_content_sha256": intake.scorer.content_digest(report),
            "status": "requires_human_adjudication",
        })

    collection = {
        "schema_version": 1,
        "status": "requires_human_adjudication",
        "held_out_ready": False,
        "campaign_id": coordinator["campaign_id"],
        "coordinator_raw_sha256": hashlib.sha256(raw_coordinator).hexdigest(),
        "coordinator_content_sha256": intake.scorer.content_digest(coordinator),
        "manifest_sha256": coordinator["manifest_sha256"],
        "protocol_sha256": coordinator["protocol_sha256"],
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "intake_tool_sha256": hashlib.sha256(Path(intake.__file__).read_bytes()).hexdigest(),
        "frozen_at": coordinator["frozen_at"],
        "collected_at": collected_at,
        "cases": case_summaries,
        "next_step": ("Complete one content-bound annotation resolution for each intake_path; "
                      "do not register a held-out case before independent protocol review."),
    }
    outputs["collection.json"] = campaign._json_bytes(collection)

    destination.mkdir()
    for relative, content in outputs.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return collection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--submissions-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--collected-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = collect(args.campaign_dir, args.submissions_dir,
                         args.output_dir, args.collected_at)
    except AnnotationCollectionError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
