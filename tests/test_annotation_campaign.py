"""Tests for blinded independent-annotation campaign packages."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

import annotation_campaign as campaign  # noqa: E402
import annotation_submission as submission  # noqa: E402


HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"


def manifest(scoped=True):
    case = {
        "id": "orders-case",
        "repository": {
            "name": "orders",
            "url": "https://example.test/backend/orders.git",
            "commit": HEAD,
            "workload": "100 orders returned per interactive request",
            "language": "Python",
        },
        "license": {
            "spdx": "MIT",
            "evidence_url": "https://example.test/backend/orders/LICENSE",
        },
        "reviewer_slots": 2,
    }
    if scoped:
        case["change_scope"] = {"diff_base": BASE}
    return {
        "schema_version": 1,
        "campaign_id": "independent-pilot-1",
        "frozen_at": "2026-09-21T12:00:00Z",
        "cases": [case],
    }


def valid_annotation(case, package_id="packet-0123456789abcdef"):
    value = {
        "repository": copy.deepcopy(case["repository"]),
        "annotation": {
            "annotated_by": "expert-a",
            "annotated_at": "2026-09-22T09:00:00Z",
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
            "expected_verdict": "PASS",
            "rationale": "The bounded change adds no material work on the assigned path.",
        }
    return value


class CampaignValidationTests(unittest.TestCase):
    def test_plan_freezes_two_opaque_slots_without_claiming_readiness(self):
        plan = campaign.prepare(manifest())
        self.assertEqual(plan["reviewer_packages"], 2)
        self.assertFalse(plan["held_out_ready"])
        packages = plan["cases"][0]["packages"]
        self.assertEqual(len(packages), 2)
        self.assertEqual(len(set(packages)), 2)
        self.assertTrue(all(value.startswith("packet-") for value in packages))
        self.assertTrue(all("orders" not in value for value in packages))

    def test_plan_is_deterministic_and_bound_to_manifest_content(self):
        first = campaign.prepare(manifest())
        second = campaign.prepare(manifest())
        self.assertEqual(first, second)
        changed = manifest()
        changed["cases"][0]["repository"]["workload"] = "one order per request"
        changed_plan = campaign.prepare(changed)
        self.assertNotEqual(changed_plan["manifest_sha256"], first["manifest_sha256"])
        self.assertNotEqual(changed_plan["cases"][0]["packages"],
                            first["cases"][0]["packages"])
        self.assertRegex(first["protocol_sha256"], r"^[0-9a-f]{64}$")

    def test_rejects_duplicate_cases_abbreviated_commits_and_same_diff_base(self):
        duplicate = manifest()
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "IDs must be unique"):
            campaign.prepare(duplicate)

        abbreviated = manifest()
        abbreviated["cases"][0]["repository"]["commit"] = "0123456"
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "invalid campaign"):
            campaign.prepare(abbreviated)

        same_base = manifest()
        same_base["cases"][0]["change_scope"]["diff_base"] = HEAD
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "must differ"):
            campaign.prepare(same_base)

    def test_requires_two_reviewers_license_evidence_and_timezone(self):
        one = manifest()
        one["cases"][0]["reviewer_slots"] = 1
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "invalid campaign"):
            campaign.prepare(one)

        no_license = manifest()
        no_license["cases"][0]["license"]["evidence_url"] = ""
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "invalid campaign"):
            campaign.prepare(no_license)

        naive = manifest()
        naive["frozen_at"] = "2026-09-21T12:00:00"
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "invalid campaign"):
            campaign.prepare(naive)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.destination = self.parent / "campaign"
        self.manifest = manifest()

    def test_export_separates_opaque_packets_from_coordinator_mapping(self):
        coordinator = campaign.export_packages(self.manifest, self.destination)
        self.assertFalse(coordinator["held_out_ready"])
        self.assertRegex(coordinator["protocol_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(coordinator["protocol_files"]), len(campaign.HELPERS))
        self.assertEqual(len(coordinator["packages"]), 2)
        self.assertTrue((self.destination / "coordinator.json").is_file())
        for record in coordinator["packages"]:
            packet = self.destination / record["package_id"]
            self.assertTrue((packet / "TASK.md").is_file())
            self.assertFalse((packet / "coordinator.json").exists())
            self.assertFalse((packet / "skills").exists())
            assignment = json.loads((packet / "assignment.json").read_text(
                encoding="utf-8"))
            self.assertNotIn("campaign_id", assignment)
            self.assertNotIn("case", assignment)
            self.assertNotIn("slot", assignment)

    def test_exported_tree_digest_matches_the_coordinator_record(self):
        coordinator = campaign.export_packages(self.manifest, self.destination)
        for record in coordinator["packages"]:
            packet = self.destination / record["package_id"]
            files = {path.relative_to(packet).as_posix(): path.read_bytes()
                     for path in packet.rglob("*") if path.is_file()}
            self.assertEqual(campaign._tree_digest(files), record["package_sha256"])

    def test_packet_does_not_leak_internal_case_campaign_or_peer_identity(self):
        coordinator = campaign.export_packages(self.manifest, self.destination)
        first, second = coordinator["packages"]
        packet = self.destination / first["package_id"]
        payload = b"\n".join(path.read_bytes() for path in packet.rglob("*")
                             if path.is_file())
        self.assertNotIn(self.manifest["campaign_id"].encode("utf-8"), payload)
        self.assertNotIn(self.manifest["cases"][0]["id"].encode("utf-8"), payload)
        self.assertNotIn(second["package_id"].encode("utf-8"), payload)

    def test_export_refuses_to_overwrite_any_existing_destination(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(campaign.AnnotationCampaignError, "already exists"):
            campaign.export_packages(self.manifest, self.destination)

    def test_packet_validator_accepts_a_completed_assigned_annotation(self):
        coordinator = campaign.export_packages(self.manifest, self.destination)
        packet = self.destination / coordinator["packages"][0]["package_id"]
        annotation = valid_annotation(
            self.manifest["cases"][0], coordinator["packages"][0]["package_id"])
        (packet / "annotation.json").write_text(
            json.dumps(annotation), encoding="utf-8")
        completed = subprocess.run([
            sys.executable, "benchmark/annotation_submission.py",
            "--assignment", "assignment.json", "--annotation", "annotation.json",
            "--schema-dir", "schemas",
        ], cwd=packet, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(coordinator["packages"][0]["package_id"], completed.stdout)

    def test_cli_plan_and_export_do_not_run_or_claim_an_annotation(self):
        manifest_path = self.parent / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        planned = subprocess.run([
            sys.executable, str(ROOT / "benchmark/annotation_campaign.py"),
            "--manifest", str(manifest_path), "--plan-only",
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(planned.stdout)["status"], "plan_only")
        exported = subprocess.run([
            sys.executable, str(ROOT / "benchmark/annotation_campaign.py"),
            "--manifest", str(manifest_path), "--export-dir", str(self.destination),
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(json.loads(exported.stdout)["status"],
                         "awaiting_independent_annotations")


class SubmissionValidationTests(unittest.TestCase):
    def setUp(self):
        case = manifest()["cases"][0]
        self.assignment = campaign._assignment(
            case, "packet-0123456789abcdef", "2026-09-21T12:00:00Z")
        self.annotation = valid_annotation(case)

    def test_rejects_repository_substitution_and_missing_assigned_scope(self):
        changed = copy.deepcopy(self.annotation)
        changed["repository"]["commit"] = "f" * 40
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "exactly match"):
            submission.validate(self.assignment, changed)

        missing_scope = copy.deepcopy(self.annotation)
        missing_scope.pop("change_scope")
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "assigned change_scope"):
            submission.validate(self.assignment, missing_scope)

    def test_rejects_placeholder_reviewer_and_duplicate_cross_bucket_ids(self):
        placeholder = copy.deepcopy(self.annotation)
        placeholder["annotation"]["annotated_by"] = "REPLACE_WITH_REVIEWER_ID"
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "identify the reviewer"):
            submission.validate(self.assignment, placeholder)

        duplicate = copy.deepcopy(self.annotation)
        duplicate["expected"] = [{
            "id": "GT-1", "location": {"file": "a.py"},
            "category": "data-access", "mechanism": "per-row query",
        }]
        duplicate["acceptable"] = [{
            "id": "GT-1", "location": {"file": "b.py"},
            "category": "memory", "mechanism": "avoidable copy",
        }]
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "unique across"):
            submission.validate(self.assignment, duplicate)

    def test_rejects_missing_packet_binding_and_pre_freeze_completion(self):
        unbound = copy.deepcopy(self.annotation)
        unbound["annotation"]["notes"] = ""
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "must preserve annotation_package_id"):
            submission.validate(self.assignment, unbound)

        early = copy.deepcopy(self.annotation)
        early["annotation"]["annotated_at"] = "2026-09-21T11:59:59Z"
        with self.assertRaisesRegex(submission.AnnotationSubmissionError,
                                    "follow the frozen assignment"):
            submission.validate(self.assignment, early)

    def test_unscoped_assignment_rejects_post_hoc_change_scope(self):
        case = manifest(scoped=False)["cases"][0]
        assignment = campaign._assignment(
            case, "packet-0123456789abcdef", "2026-09-21T12:00:00Z")
        annotation = valid_annotation(case)
        annotation["change_scope"] = {
            "diff_base": BASE, "expected_verdict": "PASS", "rationale": "not assigned"}
        with self.assertRaisesRegex(submission.AnnotationSubmissionError, "unassigned"):
            submission.validate(assignment, annotation)


if __name__ == "__main__":
    unittest.main()
