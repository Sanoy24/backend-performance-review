#!/usr/bin/env python3
"""Plan and export blinded packages for independent expert annotation."""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import json_schema_lite as schema_lite  # noqa: E402


HELPERS = (
    "benchmark/annotation_submission.py",
    "scripts/json_schema_lite.py",
    "schemas/ground-truth.schema.json",
    "schemas/finding.schema.json",
)


class AnnotationCampaignError(ValueError):
    """A campaign cannot be frozen or safely exported."""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationCampaignError("cannot read %s: %s" % (path, exc)) from exc


def _timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AnnotationCampaignError(
            "%s must be a timezone-aware ISO timestamp" % label) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationCampaignError(
            "%s must be a timezone-aware ISO timestamp" % label)


def _canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _package_id(manifest_digest, protocol_digest, case_id, slot):
    digest = _canonical_digest({
        "manifest_sha256": manifest_digest, "protocol_sha256": protocol_digest,
        "case_id": case_id, "slot": slot})
    return "packet-" + digest[:16]


def _tree_digest(files):
    records = [{"path": path, "sha256": hashlib.sha256(content).hexdigest()}
               for path, content in sorted(files.items())]
    return _canonical_digest(records)


def _helper_contents(root):
    contents = {}
    for relative in HELPERS:
        path = Path(root) / relative
        if not path.is_file():
            raise AnnotationCampaignError("required package helper is missing: %s" % relative)
        contents[relative] = path.read_bytes()
    return contents


def prepare(manifest, root=ROOT):
    """Validate and freeze a campaign plan without creating reviewer packages."""
    errors = schema_lite.validate_file(
        manifest, Path(root) / "schemas/annotation-campaign.schema.json")
    if errors:
        raise AnnotationCampaignError("invalid campaign: %s" % errors[0])
    _timestamp(manifest["frozen_at"], "frozen_at")

    case_ids = [case["id"] for case in manifest["cases"]]
    if len(set(case_ids)) != len(case_ids):
        raise AnnotationCampaignError("campaign case IDs must be unique")
    total_slots = sum(case["reviewer_slots"] for case in manifest["cases"])
    if total_slots > 100:
        raise AnnotationCampaignError("campaign cannot export more than 100 reviewer slots")

    manifest_digest = _canonical_digest(manifest)
    helper_contents = _helper_contents(root)
    protocol_files = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(helper_contents.items())]
    protocol_digest = _canonical_digest(protocol_files)
    planned_cases = []
    package_ids = set()
    for case in manifest["cases"]:
        repository = case["repository"]
        for field in ("name", "url", "commit", "workload"):
            if not repository[field].strip():
                raise AnnotationCampaignError(
                    "%s: repository.%s cannot be blank" % (case["id"], field))
        license_record = case["license"]
        if not license_record["spdx"].strip():
            raise AnnotationCampaignError(
                "%s: license.spdx cannot be blank" % case["id"])
        scope = case.get("change_scope")
        if scope and scope["diff_base"] == repository["commit"]:
            raise AnnotationCampaignError(
                "%s: diff base and head commit must differ" % case["id"])
        slots = []
        for slot in range(1, case["reviewer_slots"] + 1):
            package_id = _package_id(
                manifest_digest, protocol_digest, case["id"], slot)
            if package_id in package_ids:
                raise AnnotationCampaignError("opaque package ID collision")
            package_ids.add(package_id)
            slots.append(package_id)
        planned_cases.append({
            "id": case["id"],
            "repository": repository["name"],
            "commit": repository["commit"],
            "reviewer_slots": case["reviewer_slots"],
            "packages": slots,
        })

    return {
        "status": "plan_only",
        "held_out_ready": False,
        "campaign_id": manifest["campaign_id"],
        "frozen_at": manifest["frozen_at"],
        "manifest_sha256": manifest_digest,
        "protocol_files": protocol_files,
        "protocol_sha256": protocol_digest,
        "reviewer_packages": total_slots,
        "cases": planned_cases,
        "limitations": [
            "Package export does not clone or verify the target repository commit.",
            "License evidence, reviewer identity, independence, and isolation need human verification.",
            "This plan is not a public pre-registration and does not create held-out evidence.",
        ],
    }


def _assignment(case, package_id, frozen_at):
    value = {
        "schema_version": 1,
        "package_id": package_id,
        "frozen_at": frozen_at,
        "repository": case["repository"],
        "license": case["license"],
        "mode": "change_scoped" if case.get("change_scope") else "full",
    }
    if case.get("change_scope"):
        value["change_scope"] = case["change_scope"]
    return value


def _template(case, package_id):
    value = {
        "repository": case["repository"],
        "annotation": {
            "annotated_by": "REPLACE_WITH_REVIEWER_ID",
            "annotated_at": "REPLACE_WITH_TIMEZONE_AWARE_TIMESTAMP",
            "method": "expert-manual-review",
            "notes": "annotation_package_id=%s" % package_id,
        },
        "expected": [],
        "acceptable": [],
        "forbidden": [],
    }
    if case.get("change_scope"):
        value["change_scope"] = {
            "diff_base": case["change_scope"]["diff_base"],
            "expected_verdict": "REPLACE_WITH_PASS_WARN_FAIL_OR_UNKNOWN",
            "rationale": "REPLACE_WITH_EVIDENCE_BASED_RATIONALE",
        }
    return value


def _task(case, package_id):
    scope = ("This is a change-scoped annotation. Compare diff base %s with head %s and "
             "complete change_scope in annotation.json.\n\n"
             % (case["change_scope"]["diff_base"], case["repository"]["commit"]) if
             case.get("change_scope") else
             "This is a full-repository annotation at the pinned commit. Do not add "
             "change_scope.\n\n")
    return (
        "# Independent expert performance annotation\n\n"
        "Package: `%s`\n\n"
        "Use a separate checkout of `%s` at exact commit `%s`. The assumed workload is:\n\n"
        "> %s\n\n"
        "%s"
        "Work independently. Do not read this project's skill, benchmark ground truth, "
        "treatment or control reports, another reviewer's submission, or coordinator.json. "
        "Do not discuss judgments with another reviewer until both submissions are frozen.\n\n"
        "Copy `submission.template.json` to `annotation.json`. Put must-find material issues "
        "in `expected`, real but non-required issues in `acceptable`, and tempting claims "
        "that code evidence disproves in `forbidden`. Each forbidden item needs factual "
        "counter-evidence in `why_not`. Judge severity against the assigned workload. Use "
        "`also_locations` or `also_acceptable_categories` only when both alternatives are "
        "genuinely defensible. An empty expected list is allowed; it is not a claim that "
        "nothing was inspected.\n\n"
        "Identify yourself with a stable reviewer ID and a timezone-aware completion time. "
        "Return only `annotation.json`; do not add findings merely to make the submission "
        "look substantial. Before returning it, run:\n\n"
        "```\n"
        "python benchmark/annotation_submission.py --assignment assignment.json "
        "--annotation annotation.json --schema-dir schemas\n"
        "```\n"
    ) % (package_id, case["repository"]["url"], case["repository"]["commit"],
         case["repository"]["workload"], scope)


def export_packages(manifest, destination, root=ROOT):
    """Write opaque reviewer packages and a separate coordinator mapping."""
    root = Path(root)
    plan = prepare(manifest, root)
    destination = Path(destination)
    if destination.exists():
        raise AnnotationCampaignError(
            "export destination already exists: %s" % destination)
    if not destination.parent.is_dir():
        raise AnnotationCampaignError(
            "export destination parent does not exist: %s" % destination.parent)

    helper_contents = _helper_contents(root)

    packages = []
    for case, planned in zip(manifest["cases"], plan["cases"]):
        for slot, package_id in enumerate(planned["packages"], start=1):
            files = dict(helper_contents)
            files["assignment.json"] = _json_bytes(
                _assignment(case, package_id, manifest["frozen_at"]))
            files["submission.template.json"] = _json_bytes(
                _template(case, package_id))
            files["TASK.md"] = _task(case, package_id).encode("utf-8")
            packages.append({
                "case": case["id"], "slot": slot, "package_id": package_id,
                "repository": case["repository"]["name"],
                "commit": case["repository"]["commit"],
                "package_sha256": _tree_digest(files), "files": files,
            })

    destination.mkdir()
    for package in packages:
        package_root = destination / package["package_id"]
        package_root.mkdir()
        for relative, content in package["files"].items():
            target = package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    coordinator = {
        "status": "awaiting_independent_annotations",
        "held_out_ready": False,
        "campaign_id": plan["campaign_id"],
        "frozen_at": plan["frozen_at"],
        "manifest_sha256": plan["manifest_sha256"],
        "protocol_sha256": plan["protocol_sha256"],
        "packages": [{key: value for key, value in package.items() if key != "files"}
                     for package in packages],
        "next_step": ("Assign each package to a distinct expert, freeze returned annotations, "
                      "then run annotation_intake.py for each case."),
    }
    (destination / "coordinator.json").write_bytes(_json_bytes(coordinator))
    return coordinator


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=ROOT)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if args.plan_only and args.export_dir:
        parser.error("choose --plan-only or --export-dir, not both")
    try:
        manifest = _load(args.manifest)
        result = (export_packages(manifest, args.export_dir, args.checkout)
                  if args.export_dir else prepare(manifest, args.checkout))
    except AnnotationCampaignError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
