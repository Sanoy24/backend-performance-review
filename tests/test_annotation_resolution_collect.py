"""Tests for fail-closed collection of campaign adjudication results."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

import annotation_campaign as campaign  # noqa: E402
import annotation_collect as annotation_collector  # noqa: E402
import annotation_resolution_collect as resolution_collector  # noqa: E402

from tests.test_annotation_campaign import manifest, valid_annotation  # noqa: E402
from tests.test_annotation_resolve import resolution_for  # noqa: E402


class AnnotationResolutionCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.campaign_dir = self.root / "campaign"
        self.submissions_dir = self.root / "submissions"
        self.collection_dir = self.root / "collection"
        self.resolutions_dir = self.root / "resolutions"
        self.output_dir = self.root / "resolved"
        self.manifest = manifest()
        coordinator = campaign.export_packages(self.manifest, self.campaign_dir)
        self.submissions_dir.mkdir()
        for index, record in enumerate(coordinator["packages"], start=1):
            annotation = valid_annotation(
                self.manifest["cases"][0], record["package_id"])
            annotation["annotation"]["annotated_by"] = "expert-%d" % index
            annotation["annotation"]["annotated_at"] = (
                "2026-09-22T%02d:00:00Z" % (8 + index))
            (self.submissions_dir / (record["package_id"] + ".json")).write_text(
                json.dumps(annotation, indent=index), encoding="utf-8")
        annotation_collector.collect(
            self.campaign_dir, self.submissions_dir, self.collection_dir,
            "2026-09-22T12:00:00Z")

        self.resolutions_dir.mkdir()
        self.case_id = self.manifest["cases"][0]["id"]
        intake_path = self.collection_dir / "cases" / self.case_id / "intake.json"
        self.report = json.loads(intake_path.read_text(encoding="utf-8"))
        truth = valid_annotation(self.manifest["cases"][0])
        truth.pop("annotation")
        self.resolution = resolution_for(self.report, truth)
        self.resolution["adjudicated_at"] = "2026-09-22T13:00:00Z"
        self.resolution_path = self.resolutions_dir / (self.case_id + ".json")
        self._write_resolution()

    def _write_resolution(self):
        self.resolution_path.write_text(
            json.dumps(self.resolution, indent=3), encoding="utf-8")

    def _collect(self):
        return resolution_collector.collect(
            self.collection_dir, self.resolutions_dir, self.output_dir,
            "2026-09-22T14:00:00Z")

    def test_collect_preserves_resolution_and_emits_resolved_truth(self):
        raw_resolution = self.resolution_path.read_bytes()
        result = self._collect()
        self.assertEqual(result["status"], "resolved_pending_protocol_review")
        self.assertFalse(result["held_out_ready"])
        self.assertRegex(result["collection_raw_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["collection_content_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["resolution_collector_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["resolver_sha256"], r"^[0-9a-f]{64}$")
        case = result["cases"][0]
        self.assertEqual(case["adjudicator"], "expert-c")
        copied = self.output_dir / case["resolution_path"]
        self.assertEqual(copied.read_bytes(), raw_resolution)
        truth = json.loads((self.output_dir / case["ground_truth_path"]).read_text(
            encoding="utf-8"))
        self.assertEqual(truth["annotation"]["method"],
                         "independent-expert-adjudication")
        self.assertRegex(case["ground_truth_raw_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(case["ground_truth_content_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue((self.output_dir / "resolution-collection.json").is_file())

    def test_missing_or_extra_resolution_fails_before_output(self):
        self.resolution_path.unlink()
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "differ from expected"):
            self._collect()
        self.assertFalse(self.output_dir.exists())

        self._write_resolution()
        (self.resolutions_dir / "unexpected.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "differ from expected"):
            self._collect()

    def test_changed_submission_or_intake_is_rejected(self):
        collection = json.loads((self.collection_dir / "collection.json").read_text(
            encoding="utf-8"))
        submission = self.collection_dir / collection["cases"][0]["submissions"][0]["path"]
        original = submission.read_bytes()
        submission.write_bytes(original + b"\n")
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "raw submission digest changed"):
            self._collect()

        submission.write_bytes(original)
        intake = self.collection_dir / collection["cases"][0]["intake_path"]
        report = json.loads(intake.read_text(encoding="utf-8"))
        report["status"] = "tampered"
        intake.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "intake content changed"):
            self._collect()

    def test_resolution_must_bind_to_the_collected_intake(self):
        self.resolution["intake_content_sha256"] = "0" * 64
        self._write_resolution()
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "does not bind"):
            self._collect()

    def test_intake_is_rederived_from_preserved_submissions(self):
        manifest_path = self.collection_dir / "collection.json"
        collection = json.loads(manifest_path.read_text(encoding="utf-8"))
        case = collection["cases"][0]
        intake_path = self.collection_dir / case["intake_path"]
        report = json.loads(intake_path.read_text(encoding="utf-8"))
        report["context"]["repository"]["workload"] = "silently changed workload"
        intake_path.write_text(json.dumps(report), encoding="utf-8")
        case["intake_content_sha256"] = resolution_collector._digest(report)
        manifest_path.write_text(json.dumps(collection), encoding="utf-8")
        self.resolution["intake_content_sha256"] = case["intake_content_sha256"]
        self.resolution["resolved_ground_truth"]["repository"]["workload"] = (
            "silently changed workload")
        self._write_resolution()
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "not derived from its preserved submissions"):
            self._collect()

    def test_adjudicator_must_remain_independent(self):
        self.resolution["adjudicator"] = "EXPERT-1"
        self._write_resolution()
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "distinct"):
            self._collect()

    def test_finalization_must_follow_adjudication(self):
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "follow every adjudication"):
            resolution_collector.collect(
                self.collection_dir, self.resolutions_dir, self.output_dir,
                "2026-09-22T13:00:00Z")

    def test_collection_paths_cannot_be_redirected(self):
        manifest_path = self.collection_dir / "collection.json"
        collection = json.loads(manifest_path.read_text(encoding="utf-8"))
        collection["cases"][0]["intake_path"] = "../intake.json"
        manifest_path.write_text(json.dumps(collection), encoding="utf-8")
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "intake path is invalid"):
            self._collect()

    def test_existing_destination_is_never_overwritten(self):
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(
                resolution_collector.AnnotationResolutionCollectionError,
                "already exists"):
            self._collect()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_cli_emits_resolution_collection_summary(self):
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/annotation_resolution_collect.py"),
            "--collection-dir", str(self.collection_dir),
            "--resolutions-dir", str(self.resolutions_dir),
            "--output-dir", str(self.output_dir),
            "--finalized-at", "2026-09-22T14:00:00Z",
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "resolved_pending_protocol_review")
        self.assertFalse(result["held_out_ready"])


if __name__ == "__main__":
    unittest.main()
