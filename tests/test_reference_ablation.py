"""Synthetic fixture checks for the three-arm reference-ablation scorer.

These tests prove the harness mechanics, not that any real reference helps a review.
"""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))
sys.path.insert(0, str(ROOT / "skills" / "backend-performance-review" / "scripts"))

import reference_ablation as ablation  # noqa: E402
import compute_stable_id as stable_ids  # noqa: E402


class ReferenceAblationTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

        for name in ("review.schema.json", "finding.schema.json",
                     "ground-truth.schema.json"):
            self._copy(ROOT / "schemas" / name, self.root / "schemas" / name)
        self._copy(ROOT / "skills" / "backend-performance-review" / "scripts"
                   / "compute_stable_id.py",
                   self.root / "skills" / "backend-performance-review" / "scripts"
                   / "compute_stable_id.py")
        self._copy(ROOT / "skills" / "backend-performance-review" / "templates"
                   / "review-report.md",
                   self.root / "skills" / "backend-performance-review" / "templates"
                   / "review-report.md")

        references = {
            "common": ["skills/backend-performance-review/SKILL.md"],
            "category": ["skills/backend-performance-review/principles/work.md"],
            "technology": ["skills/backend-performance-review/technology/example.md"],
            "full": ["skills/backend-performance-review/runtimes/example.md"],
        }
        for files in references.values():
            for name in files:
                self._write(name, "# Synthetic reference\n")

        truth = ROOT / "tests" / "fixtures" / "example-ground-truth.json"
        self._copy(truth, self.root / "truth.json")
        annotated = json.loads((self.root / "truth.json").read_text(encoding="utf-8"))
        annotated["repository"]["url"] = "https://example.com/orders.git"
        self._write("truth.json", json.dumps(annotated))
        example = json.loads((ROOT / "docs" / "examples" / "review.example.json").read_text(
            encoding="utf-8"))
        example["reproducibility"]["model"] = "same-model"
        self._write("tech.json", json.dumps(example))
        self._write("full.json", json.dumps(example))
        category = copy.deepcopy(example)
        category["findings"] = []
        category["root_causes"] = []
        category["decision_changing_questions"] = []
        self._write("category.json", json.dumps(category))

        self._write("prompt.txt", "Review the pinned repository under the supplied references.\n")
        self.manifest = {
            "model": "same-model",
            "prompt": "prompt.txt",
            "prompt_sha256": hashlib.sha256((self.root / "prompt.txt").read_bytes()).hexdigest(),
            "cases": [{
                "id": "orders-fixture", "truth": "truth.json",
                "references": references,
            }],
        }
        self._add_trial()

    def _copy(self, source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    def _write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _add_trial(self):
        plan = ablation.prepare(self.manifest, self.root)
        bundles = plan["cases"][0]["bundles"]
        runs = {}
        for arm, file, tokens, reference_tokens, seconds, cost in (
                ("category_only", "category.json", 900, 100, 10, 0.01),
                ("category_technology", "tech.json", 1100, 200, 12, 0.012),
                ("full_routed", "full.json", 1300, 300, 15, 0.015)):
            runs[arm] = {
                "review": file, "audited_by": "fixture reviewer",
                "unsupported_claims": [], "context_tokens": tokens,
                "reference_tokens": reference_tokens, "elapsed_seconds": seconds,
                "cost_usd": cost, "prompt_sha256": self.manifest["prompt_sha256"],
                "bundle_sha256": bundles[arm]["sha256"],
                "tokenizer": "fixture counter", "usage_source": "fixture usage record",
                "cost_source": "fixture billing record",
            }
        self.manifest["cases"][0]["trials"] = [{
            "id": "first", "order": list(ablation.ARMS), "arms": runs,
        }]

    def test_prepare_freezes_distinct_monotonic_bundles(self):
        plan = ablation.prepare(self.manifest, self.root)
        bundles = plan["cases"][0]["bundles"]
        self.assertEqual([len(bundles[arm]["files"]) for arm in ablation.ARMS], [2, 3, 4])
        self.assertEqual(len({bundles[arm]["sha256"] for arm in ablation.ARMS}), 3)
        self.assertEqual(plan["status"], "plan_only")

    def test_category_to_technology_gain_is_measured_not_assumed(self):
        result = ablation.compare(self.manifest, self.root)
        trial = result["cases"][0]["trials"][0]
        delta = trial["deltas"]["category_only_to_category_technology"]
        self.assertEqual(result["status"], "scored")
        self.assertEqual(delta["expected_gained"], ["GT-001"])
        self.assertEqual(delta["quality_score"], 1)
        self.assertEqual(delta["context_tokens"], 200)
        self.assertEqual(delta["reference_tokens"], 100)
        self.assertAlmostEqual(delta["cost_usd"], 0.002)
        self.assertEqual(result["transitions"][
            "category_technology_to_full_routed"]["mean_quality_delta"], 0)

    def test_unsupported_claim_penalizes_the_increment(self):
        full = self.manifest["cases"][0]["trials"][0]["arms"]["full_routed"]
        full["unsupported_claims"] = [{
            "finding_id": "PERF-001", "reason": "Unsupported numeric improvement estimate",
        }]
        result = ablation.compare(self.manifest, self.root)
        delta = result["cases"][0]["trials"][0]["deltas"][
            "category_technology_to_full_routed"]
        self.assertEqual(delta["quality_score"], -1)

    def test_changed_recommendation_is_visible_without_inventing_a_quality_gain(self):
        altered = json.loads((self.root / "full.json").read_text(encoding="utf-8"))
        altered["findings"][0]["recommendation"] = "Measure this path before changing it."
        self._write("full.json", json.dumps(altered))
        delta = ablation.compare(self.manifest, self.root)["cases"][0]["trials"][0][
            "deltas"]["category_technology_to_full_routed"]
        self.assertEqual(delta["expected_changed"], ["GT-001"])
        self.assertEqual(delta["quality_score"], 0)

    def test_unmatched_finding_withholds_quality_pending_adjudication(self):
        altered = json.loads((self.root / "full.json").read_text(encoding="utf-8"))
        altered["findings"][0]["location"]["file"] = "src/other/service.py"
        altered["findings"][0]["stable_id"] = stable_ids.compute_for_finding(
            altered["findings"][0])
        self._write("full.json", json.dumps(altered))
        result = ablation.compare(self.manifest, self.root)
        self.assertEqual(result["status"], "needs_adjudication")
        self.assertIsNone(result["transitions"][
            "category_technology_to_full_routed"]["mean_quality_delta"])

    def test_missing_arm_is_not_a_three_way_ablation(self):
        self.manifest["cases"][0]["trials"][0]["arms"].pop("full_routed")
        with self.assertRaisesRegex(ablation.AblationError, "all three arms"):
            ablation.compare(self.manifest, self.root)

    def test_stale_reference_bundle_is_rejected(self):
        self._write("skills/backend-performance-review/technology/example.md", "# Changed\n")
        with self.assertRaisesRegex(ablation.AblationError, "frozen plan"):
            ablation.compare(self.manifest, self.root)

    def test_changed_prompt_cannot_be_scored_as_the_same_trial(self):
        self._write("prompt.txt", "A different request\n")
        with self.assertRaisesRegex(ablation.AblationError, "common prompt"):
            ablation.compare(self.manifest, self.root)

    def test_missing_usage_provenance_is_rejected(self):
        run = self.manifest["cases"][0]["trials"][0]["arms"]["category_only"]
        run.pop("usage_source")
        with self.assertRaisesRegex(ablation.AblationError, "usage_source"):
            ablation.compare(self.manifest, self.root)

    def test_unsupported_claim_needs_an_audit_reason(self):
        run = self.manifest["cases"][0]["trials"][0]["arms"]["full_routed"]
        run["unsupported_claims"] = [{"finding_id": "PERF-001"}]
        with self.assertRaisesRegex(ablation.AblationError, "audit reasons"):
            ablation.compare(self.manifest, self.root)

    def test_review_commit_must_match_truth(self):
        altered = json.loads((self.root / "tech.json").read_text(encoding="utf-8"))
        altered["reproducibility"]["repository"]["commit"] = "f" * 40
        self._write("tech.json", json.dumps(altered))
        with self.assertRaisesRegex(ablation.AblationError, "match ground truth"):
            ablation.compare(self.manifest, self.root)

    def test_reference_path_cannot_escape_checkout(self):
        self.manifest["cases"][0]["references"]["technology"] = ["../secret.md"]
        with self.assertRaises(ablation.AblationError):
            ablation.prepare(self.manifest, self.root)

    def test_cli_plan_only_reads_a_manifest_without_running_reviews(self):
        self._write("manifest.json", json.dumps(self.manifest))
        completed = subprocess.run(
            [sys.executable, str(ROOT / "benchmark" / "reference_ablation.py"),
             "--manifest", str(self.root / "manifest.json"),
             "--checkout", str(self.root), "--plan-only"],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "plan_only")

    def test_export_packages_hide_truth_and_unassigned_references(self):
        destination = self.root / "run-packs"
        result = ablation.export_run_packs(self.manifest, destination, self.root)
        self.assertEqual(result["status"], "unrun")
        self.assertEqual([slot["arm"] for slot in result["slots"]], list(ablation.ARMS))
        category = destination / "orders-fixture" / "first" / "slot-1"
        technology = destination / "orders-fixture" / "first" / "slot-2"
        full = destination / "orders-fixture" / "first" / "slot-3"
        self.assertTrue((category / "skills/backend-performance-review/SKILL.md").is_file())
        self.assertFalse((category / "skills/backend-performance-review/technology/example.md").exists())
        self.assertTrue((technology / "skills/backend-performance-review/technology/example.md").is_file())
        self.assertFalse((technology / "skills/backend-performance-review/runtimes/example.md").exists())
        self.assertTrue((full / "skills/backend-performance-review/runtimes/example.md").is_file())
        for package in (category, technology, full):
            self.assertFalse((package / "truth.json").exists())
            self.assertFalse((package / "coordinator.json").exists())
            self.assertTrue((package / "schemas/review.schema.json").is_file())
            self.assertTrue((package / "skills/backend-performance-review/templates/review-report.md").is_file())
            self.assertIn("read-only", (package / "TASK.md").read_text(encoding="utf-8"))
        self.assertTrue((destination / "coordinator.json").is_file())

    def test_export_does_not_overwrite_an_existing_directory(self):
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaisesRegex(ablation.AblationError, "already exists"):
            ablation.export_run_packs(self.manifest, destination, self.root)

    def test_export_rejects_unsafe_trial_id_before_writing(self):
        self.manifest["cases"][0]["trials"][0]["id"] = "../outside"
        destination = self.root / "run-packs"
        with self.assertRaisesRegex(ablation.AblationError, "safe package name"):
            ablation.export_run_packs(self.manifest, destination, self.root)
        self.assertFalse(destination.exists())

    def test_cli_exports_packs_without_running_reviews(self):
        self._write("manifest.json", json.dumps(self.manifest))
        destination = self.root / "cli-packs"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "benchmark" / "reference_ablation.py"),
             "--manifest", str(self.root / "manifest.json"),
             "--checkout", str(self.root), "--export-dir", str(destination)],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "unrun")
        self.assertTrue((destination / "orders-fixture/first/slot-1/TASK.md").is_file())


if __name__ == "__main__":
    unittest.main()
