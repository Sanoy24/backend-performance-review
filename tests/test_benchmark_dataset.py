"""The historical benchmark corpus must not masquerade as held-out evaluation."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))
import dataset  # noqa: E402


class BenchmarkDatasetTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.truth_dir = self.root / "benchmark/ground-truth"
        self.truth_dir.mkdir(parents=True)
        self.truth_path = self.truth_dir / "orders.json"
        self.truth_path.write_bytes((ROOT / "tests/fixtures/example-ground-truth.json").read_bytes())
        truth = json.loads(self.truth_path.read_text(encoding="utf-8"))
        truth["repository"]["url"] = "https://example.com/orders"
        self.truth_path.write_text(json.dumps(truth), encoding="utf-8")
        self.manifest_path = self.root / "benchmark/dataset.json"
        self.review_path = self.root / "review.json"
        review = json.loads((ROOT / "docs/examples/review.example.json").read_text(
            encoding="utf-8"))
        review["reproducibility"]["model"] = "fixture-model"
        self.review_path.write_text(json.dumps(review), encoding="utf-8")
        self.manifest = {
            "schema_version": 1, "dataset_version": "0.1.0",
            "cases": {"orders": {
                "split": "held_out", "pre_registered_before_review": True,
                "license": {"spdx": "MIT", "evidence_url": "https://example.com/LICENSE"},
                "annotations": [{"version": 1, "sha256": self._digest(),
                                 "origin": "injected fixture established before review"}],
            }},
        }
        self._save_manifest()

    def _digest(self):
        return dataset.annotation_digest(self.truth_path)

    def _save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def _score_cli(self, truth_path, *extra):
        return subprocess.run([
            sys.executable, str(ROOT / "benchmark/scoring/score.py"), "score",
            "--dataset", str(self.manifest_path), "--truth", str(truth_path),
            "--review", str(self.review_path), *extra,
        ], capture_output=True, text=True, check=False)

    def test_annotation_digest_is_platform_independent(self):
        self.truth_path.write_bytes(b'{\n  "case": "orders"\n}\n')
        lf_digest = dataset.annotation_digest(self.truth_path)
        self.truth_path.write_bytes(b'{\r\n  "case": "orders"\r\n}\r\n')
        self.assertEqual(dataset.annotation_digest(self.truth_path), lf_digest)

    def test_committed_corpus_is_development_only(self):
        summary = dataset.validate(ROOT)
        self.assertEqual(summary["splits"], {"development": 10, "held_out": 0})
        self.assertFalse(summary["held_out_ready"])
        with self.assertRaisesRegex(dataset.DatasetError, "development-only"):
            dataset.require_held_out("gin-realworld", ROOT)

    def test_independent_pinned_case_can_be_held_out(self):
        summary = dataset.validate(self.root)
        self.assertTrue(summary["held_out_ready"])
        path, provenance = dataset.require_held_out("orders", self.root)
        self.assertEqual(path, self.truth_path)
        self.assertEqual(provenance["dataset_version"], "0.1.0")
        self.assertEqual(provenance["annotation_version"], 1)

    def test_classify_truth_recognizes_registered_copy(self):
        copy = self.root / "copy.json"
        copy.write_bytes(self.truth_path.read_bytes())
        for path in (self.truth_path, copy):
            registration = dataset.classify_truth(path, self.root)
            self.assertEqual(registration["case"], "orders")
            self.assertEqual(registration["split"], "held_out")
            self.assertEqual(registration["annotation_sha256"], self._digest())

    def test_raw_score_rejects_registered_held_out_truth_and_copy(self):
        copy = self.root / "copy.json"
        copy.write_bytes(self.truth_path.read_bytes())
        for path in (self.truth_path, copy):
            with self.subTest(path=path):
                completed = self._score_cli(path, "--json")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("requires evaluate --case orders", completed.stderr)

    def test_raw_score_labels_registered_development_case_exploratory(self):
        self.manifest["cases"]["orders"]["split"] = "development"
        self._save_manifest()
        completed = self._score_cli(self.truth_path, "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["evidence_tier"], "exploratory")
        self.assertEqual(result["dataset"]["split"], "development")
        self.assertEqual(result["dataset"]["case"], "orders")
        report = self._score_cli(self.truth_path)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertTrue(report.stdout.startswith("EXPLORATORY"))

    def test_raw_score_labels_unregistered_truth_exploratory(self):
        other = self.root / "other.json"
        truth = json.loads(self.truth_path.read_text(encoding="utf-8"))
        truth["annotation"]["notes"] = "Different annotation content."
        other.write_text(json.dumps(truth), encoding="utf-8")
        completed = self._score_cli(other, "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["evidence_tier"], "exploratory")
        self.assertNotIn("dataset", result)

    def test_raw_score_fails_closed_when_registry_is_invalid(self):
        self.manifest["cases"]["orders"]["annotations"][0]["sha256"] = "0" * 64
        self._save_manifest()
        completed = self._score_cli(self.truth_path)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("changed without a new version", completed.stderr)

    def test_annotation_drift_requires_new_version(self):
        truth = json.loads(self.truth_path.read_text(encoding="utf-8"))
        truth["annotation"]["notes"] = "A revised explanation."
        self.truth_path.write_text(json.dumps(truth), encoding="utf-8")
        with self.assertRaisesRegex(dataset.DatasetError, "changed without a new version"):
            dataset.validate(self.root)
        self.manifest["cases"]["orders"]["annotations"].append({
            "version": 2, "sha256": self._digest(), "origin": "manual adjudication",
            "reason": "Clarified existing evidence", "affected_runs": [],
        })
        self._save_manifest()
        self.assertTrue(dataset.validate(self.root)["held_out_ready"])

    def test_post_baseline_change_needs_reason_and_affected_runs(self):
        self.manifest["cases"]["orders"]["annotations"].append({
            "version": 2, "sha256": self._digest(), "origin": "manual adjudication",
        })
        self._save_manifest()
        with self.assertRaisesRegex(dataset.DatasetError, "reason and affected_runs"):
            dataset.validate(self.root)

    def test_unregistered_truth_file_is_rejected(self):
        (self.truth_dir / "unregistered.json").write_bytes(self.truth_path.read_bytes())
        with self.assertRaisesRegex(dataset.DatasetError, "registry differs"):
            dataset.validate(self.root)

    def test_treatment_derived_truth_cannot_be_held_out(self):
        truth = json.loads(self.truth_path.read_text(encoding="utf-8"))
        truth["annotation"]["method"] = "blind-pass-derived"
        self.truth_path.write_text(json.dumps(truth), encoding="utf-8")
        self.manifest["cases"]["orders"]["annotations"][0]["sha256"] = self._digest()
        self._save_manifest()
        with self.assertRaisesRegex(dataset.DatasetError, "treatment-derived or unknown truth"):
            dataset.validate(self.root)

    def test_held_out_truth_must_predate_review(self):
        self.manifest["cases"]["orders"]["pre_registered_before_review"] = False
        self._save_manifest()
        with self.assertRaisesRegex(dataset.DatasetError, "must predate review runs"):
            dataset.validate(self.root)

    def test_held_out_requires_verified_source_metadata(self):
        record = self.manifest["cases"]["orders"]
        record["license"] = {"spdx": None, "evidence_url": None}
        self._save_manifest()
        with self.assertRaisesRegex(dataset.DatasetError, "full commit and documented license"):
            dataset.validate(self.root)

    def test_held_out_requires_full_commit(self):
        truth = json.loads(self.truth_path.read_text(encoding="utf-8"))
        truth["repository"]["commit"] = "a397d119"
        self.truth_path.write_text(json.dumps(truth), encoding="utf-8")
        self.manifest["cases"]["orders"]["annotations"][0]["sha256"] = self._digest()
        self._save_manifest()
        with self.assertRaisesRegex(dataset.DatasetError, "full commit and documented license"):
            dataset.validate(self.root)

    def test_evaluate_cli_labels_a_held_out_result(self):
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/scoring/score.py"), "evaluate",
            "--dataset", str(self.manifest_path), "--case", "orders",
            "--review", str(self.review_path), "--json",
        ], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        provenance = json.loads(completed.stdout)["dataset"]
        self.assertEqual(provenance["split"], "held_out")
        self.assertEqual(provenance["annotation_sha256"], self._digest())

    def test_evaluate_cli_rejects_historical_treatment_path(self):
        treatment = self.root / "benchmark/ab-results/treatment.json"
        treatment.parent.mkdir(parents=True)
        treatment.write_bytes(self.review_path.read_bytes())
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/scoring/score.py"), "evaluate",
            "--dataset", str(self.manifest_path), "--case", "orders",
            "--review", str(treatment),
        ], capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("historical treatment output", completed.stderr)

    def test_evaluate_cli_rejects_wrong_repository_commit(self):
        review = json.loads(self.review_path.read_text(encoding="utf-8"))
        review["reproducibility"]["repository"]["commit"] = "f" * 40
        self.review_path.write_text(json.dumps(review), encoding="utf-8")
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/scoring/score.py"), "evaluate",
            "--dataset", str(self.manifest_path), "--case", "orders",
            "--review", str(self.review_path),
        ], capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("repository and commit must match", completed.stderr)

    def test_evaluate_cli_rejects_placeholder_model(self):
        review = json.loads(self.review_path.read_text(encoding="utf-8"))
        review["reproducibility"]["model"] = "<model>"
        self.review_path.write_text(json.dumps(review), encoding="utf-8")
        completed = subprocess.run([
            sys.executable, str(ROOT / "benchmark/scoring/score.py"), "evaluate",
            "--dataset", str(self.manifest_path), "--case", "orders",
            "--review", str(self.review_path),
        ], capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("name the actual model", completed.stderr)


if __name__ == "__main__":
    unittest.main()
