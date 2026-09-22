"""Tests for verified collection of blinded annotation campaign submissions."""

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
import annotation_collect as collector  # noqa: E402

from tests.test_annotation_campaign import manifest, valid_annotation  # noqa: E402


class AnnotationCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.campaign_dir = self.root / "campaign"
        self.submissions_dir = self.root / "submissions"
        self.output_dir = self.root / "collected"
        self.manifest = manifest()
        self.coordinator = campaign.export_packages(
            self.manifest, self.campaign_dir)
        self.submissions_dir.mkdir()
        for index, record in enumerate(self.coordinator["packages"], start=1):
            annotation = valid_annotation(
                self.manifest["cases"][0], record["package_id"])
            annotation["annotation"]["annotated_by"] = "expert-%d" % index
            annotation["annotation"]["annotated_at"] = (
                "2026-09-22T%02d:00:00Z" % (8 + index))
            (self.submissions_dir / (record["package_id"] + ".json")).write_text(
                json.dumps(annotation, indent=index), encoding="utf-8")

    def _collect(self):
        return collector.collect(
            self.campaign_dir, self.submissions_dir, self.output_dir,
            "2026-09-22T12:00:00Z")

    def test_collect_verifies_and_freezes_two_submissions_plus_intake(self):
        result = self._collect()
        self.assertEqual(result["status"], "requires_human_adjudication")
        self.assertFalse(result["held_out_ready"])
        self.assertRegex(result["coordinator_raw_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["coordinator_content_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["collector_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["intake_tool_sha256"], r"^[0-9a-f]{64}$")
        case = result["cases"][0]
        self.assertEqual([entry["reviewer"] for entry in case["submissions"]],
                         ["expert-1", "expert-2"])
        self.assertRegex(case["intake_content_sha256"], r"^[0-9a-f]{64}$")
        intake_path = self.output_dir / case["intake_path"]
        intake_report = json.loads(intake_path.read_text(encoding="utf-8"))
        self.assertEqual(intake_report["status"], "requires_human_adjudication")
        self.assertFalse(intake_report["held_out_ready"])

    def test_collector_preserves_exact_submission_bytes_and_both_hashes(self):
        source_record = self.coordinator["packages"][0]
        source = self.submissions_dir / (source_record["package_id"] + ".json")
        raw = source.read_bytes()
        result = self._collect()
        copied = self.output_dir / result["cases"][0]["submissions"][0]["path"]
        self.assertEqual(copied.read_bytes(), raw)
        record = result["cases"][0]["submissions"][0]
        self.assertRegex(record["raw_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(record["content_sha256"], r"^[0-9a-f]{64}$")

    def test_missing_or_extra_submission_fails_before_writing_output(self):
        first = self.coordinator["packages"][0]["package_id"]
        (self.submissions_dir / (first + ".json")).unlink()
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "differ from expected"):
            self._collect()
        self.assertFalse(self.output_dir.exists())

        annotation = valid_annotation(self.manifest["cases"][0], first)
        (self.submissions_dir / (first + ".json")).write_text(
            json.dumps(annotation), encoding="utf-8")
        (self.submissions_dir / "unexpected.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "differ from expected"):
            self._collect()

    def test_changed_packet_or_protocol_is_rejected(self):
        packet_id = self.coordinator["packages"][0]["package_id"]
        task = self.campaign_dir / packet_id / "TASK.md"
        original = task.read_bytes()
        task.write_bytes(original + b"changed\n")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "packet content changed"):
            self._collect()

        task.write_bytes(original)
        coordinator_path = self.campaign_dir / "coordinator.json"
        coordinator = json.loads(coordinator_path.read_text(encoding="utf-8"))
        coordinator["protocol_sha256"] = "0" * 64
        coordinator_path.write_text(json.dumps(coordinator), encoding="utf-8")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "protocol"):
            self._collect()

    def test_submission_must_remain_bound_to_its_packet(self):
        first = self.coordinator["packages"][0]["package_id"]
        path = self.submissions_dir / (first + ".json")
        annotation = json.loads(path.read_text(encoding="utf-8"))
        annotation["annotation"]["notes"] = ""
        path.write_text(json.dumps(annotation), encoding="utf-8")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "must preserve annotation_package_id"):
            self._collect()

    def test_reviewers_must_be_distinct(self):
        second = self.coordinator["packages"][1]["package_id"]
        path = self.submissions_dir / (second + ".json")
        annotation = json.loads(path.read_text(encoding="utf-8"))
        annotation["annotation"]["annotated_by"] = "EXPERT-1"
        path.write_text(json.dumps(annotation), encoding="utf-8")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "distinct reviewers"):
            self._collect()

    def test_collection_time_must_follow_every_annotation(self):
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "follow every annotation"):
            collector.collect(
                self.campaign_dir, self.submissions_dir, self.output_dir,
                "2026-09-22T09:30:00Z")

    def test_existing_destination_is_never_overwritten(self):
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(collector.AnnotationCollectionError,
                                    "already exists"):
            self._collect()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_cli_emits_collection_summary(self):
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/annotation_collect.py"),
            "--campaign-dir", str(self.campaign_dir),
            "--submissions-dir", str(self.submissions_dir),
            "--output-dir", str(self.output_dir),
            "--collected-at", "2026-09-22T12:00:00Z",
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "requires_human_adjudication")
        self.assertTrue((self.output_dir / "collection.json").is_file())


if __name__ == "__main__":
    unittest.main()
