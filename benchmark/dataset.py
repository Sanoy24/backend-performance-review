#!/usr/bin/env python3
"""Validate the versioned benchmark split and gate held-out scoring.

The committed corpus is development-only until independently established cases exist.
Annotation hashes and versions live here, separately from scorer implementation versions.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "benchmark" / "dataset.json"
sys.path.insert(0, str(ROOT / "scripts"))
import json_schema_lite as schema_lite  # noqa: E402

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
INDEPENDENT_METHODS = frozenset({
    "expert-manual-review", "documented-issue", "injected-defect"})


class DatasetError(ValueError):
    pass


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetError("cannot read %s: %s" % (path, exc))


def validate(root=ROOT, manifest_path=None):
    """Require complete registration and an immutable current annotation digest."""
    root = Path(root).resolve()
    manifest_path = Path(manifest_path) if manifest_path else root / "benchmark/dataset.json"
    manifest = _read_json(manifest_path)
    return _validate(root, manifest)


def _validate(root, manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise DatasetError("unsupported dataset schema_version")
    if not isinstance(manifest.get("dataset_version"), str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+", manifest["dataset_version"]):
        raise DatasetError("dataset_version must be an independent semantic version")
    cases = manifest.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise DatasetError("dataset must register its cases")
    truth_dir = root / "benchmark/ground-truth"
    discovered = {path.stem for path in truth_dir.glob("*.json")}
    if set(cases) != discovered:
        raise DatasetError("dataset registry differs from ground-truth files: missing %s; "
                           "extra %s" % (sorted(discovered - set(cases)),
                                         sorted(set(cases) - discovered)))

    counts = {"development": 0, "held_out": 0}
    incomplete_sources = []
    for case_id, record in sorted(cases.items()):
        if not CASE_ID.fullmatch(case_id) or not isinstance(record, dict):
            raise DatasetError("invalid case registration: %s" % case_id)
        split = record.get("split")
        if split not in counts:
            raise DatasetError("%s: split must be development or held_out" % case_id)
        if not isinstance(record.get("pre_registered_before_review"), bool):
            raise DatasetError("%s: pre-registration status must be explicit" % case_id)
        path = truth_dir / (case_id + ".json")
        if path.is_symlink():
            raise DatasetError("%s: ground truth must be a regular dataset file" % case_id)
        truth = _read_json(path)
        schema_errors = schema_lite.validate_file(
            truth, ROOT / "schemas/ground-truth.schema.json")
        if schema_errors:
            raise DatasetError("%s: invalid ground truth: %s" % (case_id, schema_errors[0]))
        repository = truth.get("repository") if isinstance(truth, dict) else None
        annotation = truth.get("annotation") if isinstance(truth, dict) else None
        if not isinstance(repository, dict) or not repository.get("name") or not repository.get("url"):
            raise DatasetError("%s: repository name and source URL are required" % case_id)
        if not isinstance(annotation, dict) or not annotation.get("method"):
            raise DatasetError("%s: annotation origin method is required" % case_id)
        history = record.get("annotations")
        if not isinstance(history, list) or not history:
            raise DatasetError("%s: annotation history is required" % case_id)
        for expected_version, entry in enumerate(history, start=1):
            if (not isinstance(entry, dict) or isinstance(entry.get("version"), bool)
                    or entry.get("version") != expected_version):
                raise DatasetError("%s: annotation versions must be contiguous from 1" % case_id)
            if not SHA256.fullmatch(str(entry.get("sha256", ""))):
                raise DatasetError("%s: annotation version %d needs SHA-256"
                                   % (case_id, expected_version))
            if not isinstance(entry.get("origin"), str) or not entry["origin"].strip():
                raise DatasetError("%s: annotation version %d needs an origin"
                                   % (case_id, expected_version))
            if expected_version > 1 and (not isinstance(entry.get("reason"), str)
                                         or not entry["reason"].strip()
                                         or not isinstance(entry.get("affected_runs"), list)):
                raise DatasetError("%s: post-baseline annotations need a reason and "
                                   "affected_runs list" % case_id)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if history[-1]["sha256"] != actual:
            raise DatasetError("%s: annotation changed without a new version and digest"
                               % case_id)
        license_record = record.get("license")
        if not isinstance(license_record, dict):
            raise DatasetError("%s: license metadata is required" % case_id)
        license_known = (isinstance(license_record.get("spdx"), str)
                         and bool(license_record["spdx"].strip())
                         and isinstance(license_record.get("evidence_url"), str)
                         and license_record["evidence_url"].startswith("https://"))
        pinned = bool(COMMIT_SHA.fullmatch(str(repository.get("commit", ""))))
        if not pinned or not license_known:
            incomplete_sources.append(case_id)
        if split == "held_out":
            if not record["pre_registered_before_review"]:
                raise DatasetError("%s: held-out truth must predate review runs" % case_id)
            if annotation["method"] not in INDEPENDENT_METHODS:
                raise DatasetError("%s: treatment-derived or unknown truth cannot be held out"
                                   % case_id)
            if not pinned or not license_known:
                raise DatasetError("%s: held-out case needs full commit and documented "
                                   "license metadata" % case_id)
        counts[split] += 1
    return {"dataset_version": manifest["dataset_version"], "cases": len(cases),
            "splits": counts, "held_out_ready": counts["held_out"] > 0,
            "source_metadata_incomplete": incomplete_sources}


def require_held_out(case_id, root=ROOT, manifest_path=None):
    """Return a truth path only for a validated held-out case."""
    root = Path(root).resolve()
    manifest = _read_json(Path(manifest_path) if manifest_path else root / "benchmark/dataset.json")
    summary = _validate(root, manifest)
    record = manifest["cases"].get(case_id)
    if record is None:
        raise DatasetError("unknown dataset case: %s" % case_id)
    if record["split"] != "held_out":
        raise DatasetError("%s is development-only, not held-out evidence" % case_id)
    current = record["annotations"][-1]
    return root / "benchmark/ground-truth" / (case_id + ".json"), {
        "dataset_version": summary["dataset_version"],
        "annotation_version": current["version"],
        "annotation_sha256": current["sha256"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        summary = validate(args.root, args.dataset)
    except DatasetError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
