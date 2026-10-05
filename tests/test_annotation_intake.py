"""Tests for independent-annotation intake and disagreement reporting."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

import annotation_intake as intake  # noqa: E402


COMMIT = "0123456789abcdef0123456789abcdef01234567"
BASE = "89abcdef0123456789abcdef0123456789abcdef"


def item(identifier="A-1", **overrides):
    value = {
        "id": identifier,
        "location": {"file": "src/orders.py", "symbol": "list_orders"},
        "category": "data-access",
        "mechanism": "one query is issued for every order",
        "severity": "High",
    }
    value.update(overrides)
    return value


def annotation(reviewer="expert-a", **overrides):
    value = {
        "repository": {
            "name": "shop",
            "commit": COMMIT,
            "url": "https://example.test/shop",
            "workload": "100 orders per request",
            "language": "Python",
        },
        "annotation": {
            "annotated_by": reviewer,
            "annotated_at": "2026-09-20T10:00:00Z",
            "method": "expert-manual-review",
        },
        "expected": [item()],
        "acceptable": [],
        "forbidden": [{
            "id": "A-F1",
            "location": {"file": "src/config.py", "symbol": "load_features"},
            "why_not": "the list is a fixed deployment configuration",
        }],
    }
    value.update(overrides)
    return value


class ValidationTests(unittest.TestCase):
    def test_requires_two_distinct_expert_reviewers(self):
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "distinct reviewers"):
            intake.compare(annotation(), annotation())

        with self.assertRaisesRegex(intake.AnnotationIntakeError, "distinct reviewers"):
            intake.compare(annotation("Expert-A"), annotation(" expert-a "))

    def test_rejects_treatment_derived_annotation(self):
        second = annotation("expert-b")
        second["annotation"]["method"] = "blind-pass-derived"
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "expert-manual-review"):
            intake.compare(annotation(), second)

    def test_rejects_an_abbreviated_commit(self):
        first = annotation()
        second = annotation("expert-b")
        first["repository"]["commit"] = "0123456"
        second["repository"]["commit"] = "0123456"
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "full commit SHA"):
            intake.compare(first, second)

    def test_rejects_a_naive_timestamp(self):
        second = annotation("expert-b")
        second["annotation"]["annotated_at"] = "2026-09-20T10:00:00"
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "timezone-aware"):
            intake.compare(annotation(), second)

    def test_rejects_mismatched_workload(self):
        second = annotation("expert-b")
        second["repository"]["workload"] = "one order per request"
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "repository.workload"):
            intake.compare(annotation(), second)

    def test_rejects_duplicate_ids_across_buckets(self):
        second = annotation("expert-b")
        second["acceptable"] = [item("A-F1")]
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "unique across buckets"):
            intake.compare(annotation(), second)

    def test_requires_the_same_change_scope(self):
        first = annotation(change_scope={
            "diff_base": BASE, "expected_verdict": "PASS", "rationale": "bounded change"})
        with self.assertRaisesRegex(intake.AnnotationIntakeError, "same scope"):
            intake.compare(first, annotation("expert-b"))


class ComparisonTests(unittest.TestCase):
    def test_candidate_pair_surfaces_bucket_category_and_severity_disagreement(self):
        second = annotation("expert-b", expected=[], acceptable=[item(
            "B-9", category="concurrency", severity="Medium",
            mechanism="pool capacity is consumed once per order")])
        report = intake.compare(annotation(), second)
        pair = report["issues"]["candidate_pairs"][0]
        self.assertEqual((pair["reviewer_a"]["id"], pair["reviewer_a"]["bucket"]),
                         ("A-1", "expected"))
        self.assertEqual((pair["reviewer_b"]["id"], pair["reviewer_b"]["bucket"]),
                         ("B-9", "acceptable"))
        self.assertIn("mechanism", pair["reviewer_a"])
        self.assertEqual(pair["judgment_differences"],
                         ["bucket", "category", "severity", "mechanism"])
        self.assertTrue(pair["resolution_required"])

    def test_unmatched_items_are_explicit(self):
        second = annotation("expert-b", expected=[item(
            "B-2", location={"file": "src/payments.py"})])
        report = intake.compare(annotation(), second)
        self.assertEqual(report["issues"]["candidate_pairs"], [])
        self.assertEqual(report["issues"]["unmatched_reviewer_a"][0]["id"], "A-1")
        self.assertEqual(report["issues"]["unmatched_reviewer_b"][0]["id"], "B-2")
        self.assertIn("location", report["issues"]["unmatched_reviewer_a"][0])

    def test_forbidden_candidate_can_match_when_only_one_reviewer_supplies_category(self):
        second = annotation("expert-b")
        second["forbidden"][0]["id"] = "B-F4"
        second["forbidden"][0]["category"] = "memory"
        report = intake.compare(annotation(), second)
        pair = report["forbidden"]["candidate_pairs"][0]
        self.assertEqual(pair["judgment_differences"], ["category"])

    def test_alternative_locations_match_regardless_of_which_reviewer_declares_them(self):
        first = annotation(expected=[item(
            "A-1", location={"file": "src/api.py", "symbol": "list_orders"})])
        second_item = item(
            "B-1", location={"file": "src/orders.py", "symbol": "load_orders"},
            also_locations=[{"file": "src/api.py", "symbol": "list_orders"}])
        second = annotation("expert-b", expected=[second_item])
        forward = intake.compare(first, second)["issues"]["candidate_pairs"]
        reverse = intake.compare(second, first)["issues"]["candidate_pairs"]
        self.assertEqual(len(forward), 1)
        self.assertEqual(len(reverse), 1)

    def test_change_scope_judgments_are_disagreements_not_context_rejections(self):
        first = annotation(change_scope={
            "diff_base": BASE, "expected_verdict": "PASS", "rationale": "bounded change"})
        second = annotation("expert-b", change_scope={
            "diff_base": BASE, "expected_verdict": "WARN", "rationale": "query risk"})
        report = intake.compare(first, second)
        self.assertEqual([entry["field"] for entry in report["scope_disagreements"]],
                         ["change_scope.expected_verdict", "change_scope.rationale"])

    def test_output_never_claims_held_out_readiness(self):
        report = intake.compare(annotation(), annotation("expert-b"))
        self.assertFalse(report["held_out_ready"])
        self.assertEqual(report["status"], "requires_human_adjudication")
        self.assertGreater(report["summary"]["adjudication_units"], 0)

    def test_list_order_does_not_change_candidate_pairs(self):
        first = annotation(expected=[item("A-1"), item(
            "A-2", location={"file": "src/users.py"})])
        second = annotation("expert-b", expected=[item(
            "B-1", location={"file": "src/users.py"}), item("B-2")])
        reordered_first = copy.deepcopy(first)
        reordered_second = copy.deepcopy(second)
        reordered_first["expected"].reverse()
        reordered_second["expected"].reverse()
        original = intake.compare(first, second)["issues"]
        reordered = intake.compare(reordered_first, reordered_second)["issues"]
        self.assertEqual(original, reordered)

    def test_equal_location_assignments_are_exposed_as_ambiguous(self):
        first = annotation(expected=[item("A-1"), item("A-2")])
        second = annotation("expert-b", expected=[item("B-1"), item("B-2")])
        report = intake.compare(first, second)
        self.assertGreater(report["summary"]["matching_ambiguities"], 0)
        self.assertGreater(len(report["issues"]["ambiguities"]), 0)

    def test_cli_emits_json_with_frozen_input_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "a.json"
            second_path = Path(directory) / "b.json"
            first_path.write_text(json.dumps(annotation()), encoding="utf-8")
            second_path.write_text(json.dumps(annotation("expert-b")), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "benchmark/annotation_intake.py"),
                "--annotation-a", str(first_path), "--annotation-b", str(second_path),
            ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertRegex(report["reviewers"][0]["content_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
