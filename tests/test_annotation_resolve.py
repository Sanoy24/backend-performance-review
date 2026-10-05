"""Tests for content-bound human resolution of independent annotations."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))
sys.path.insert(0, str(ROOT / "scripts"))

import annotation_intake as intake  # noqa: E402
import annotation_resolve as resolver  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402

from tests.test_annotation_intake import BASE, annotation, item  # noqa: E402


def intake_report(first=None, second=None):
    return intake.compare(first or annotation(), second or annotation("expert-b"))


def resolution_for(report, truth=None):
    if truth is None:
        truth = annotation()
        truth.pop("annotation")
    decisions = []
    for unit in resolver._collect_units(report):
        kind = unit["unit_kind"]
        if kind == "candidate_pair":
            decision = {
                "unit_id": unit["unit_id"],
                "outcome": "include",
                "final_item_ids": [unit["reviewer_a"]["id"]],
                "reason": "The cited code and mechanism describe the same adjudicated item.",
            }
        elif kind == "unmatched_item":
            decision = {
                "unit_id": unit["unit_id"],
                "outcome": "include",
                "final_item_ids": [unit["id"]],
                "reason": "Manual code inspection confirmed the unmatched item.",
            }
        elif kind == "scope_disagreement":
            decision = {
                "unit_id": unit["unit_id"],
                "outcome": "context_resolved",
                "final_item_ids": [],
                "reason": "The resolved ground truth records the adjudicated context value.",
            }
        else:
            decision = {
                "unit_id": unit["unit_id"],
                "outcome": "ambiguity_resolved",
                "final_item_ids": [],
                "reason": "The final item mapping records the manually selected pairing.",
            }
        decisions.append(decision)
    return {
        "schema_version": 1,
        "intake_content_sha256": resolver.scorer.content_digest(report),
        "adjudicator": "expert-c",
        "adjudicated_at": "2026-09-20T11:00:00Z",
        "decisions": decisions,
        "resolved_ground_truth": truth,
    }


class ResolutionTests(unittest.TestCase):
    def test_valid_resolution_emits_schema_valid_ground_truth_with_provenance(self):
        report = intake_report()
        result = resolver.resolve(report, resolution_for(report))
        self.assertEqual(result["annotation"]["annotated_by"], "expert-c")
        self.assertEqual(result["annotation"]["method"],
                         "independent-expert-adjudication")
        self.assertIn("intake_sha256=", result["annotation"]["notes"])
        self.assertIn("resolution_sha256=", result["annotation"]["notes"])
        self.assertEqual(schema_lite.validate_file(
            result, ROOT / "schemas/ground-truth.schema.json"), [])

    def test_resolution_must_bind_to_the_exact_intake_report(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["intake_content_sha256"] = "0" * 64
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "does not bind"):
            resolver.resolve(report, resolution)

    def test_every_intake_unit_needs_exactly_one_decision(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["decisions"].pop()
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "coverage differs"):
            resolver.resolve(report, resolution)

        resolution = resolution_for(report)
        resolution["decisions"].append(copy.deepcopy(resolution["decisions"][0]))
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "exactly one"):
            resolver.resolve(report, resolution)

    def test_decision_outcome_must_match_the_unit_kind(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["decisions"][0]["outcome"] = "context_resolved"
        resolution["decisions"][0]["final_item_ids"] = []
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "invalid for"):
            resolver.resolve(report, resolution)

    def test_final_items_must_be_cited_by_an_include_decision(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["decisions"][0]["outcome"] = "exclude"
        resolution["decisions"][0]["final_item_ids"] = []
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "lack an include"):
            resolver.resolve(report, resolution)

    def test_include_decisions_cannot_name_missing_final_items(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["decisions"][0]["final_item_ids"] = ["GT-NOT-THERE"]
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "missing final items"):
            resolver.resolve(report, resolution)

    def test_decisions_cannot_map_a_forbidden_trap_into_an_issue_bucket(self):
        report = intake_report()
        resolution = resolution_for(report)
        forbidden_decision = next(
            decision for decision in resolution["decisions"]
            if decision["final_item_ids"] == ["A-F1"])
        forbidden_decision["final_item_ids"] = ["A-1"]
        with self.assertRaisesRegex(resolver.AnnotationResolutionError,
                                    "issue/forbidden families"):
            resolver.resolve(report, resolution)

    def test_adjudicator_must_be_distinct_and_follow_both_annotations(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["adjudicator"] = "EXPERT-A"
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "distinct"):
            resolver.resolve(report, resolution)

        resolution = resolution_for(report)
        resolution["adjudicated_at"] = "2026-09-20T10:00:00Z"
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "follow both"):
            resolver.resolve(report, resolution)

    def test_resolution_cannot_change_the_pinned_context(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["resolved_ground_truth"]["repository"]["commit"] = "a" * 40
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "repository.commit"):
            resolver.resolve(report, resolution)

    def test_resolution_cannot_supply_its_own_annotation_provenance(self):
        report = intake_report()
        resolution = resolution_for(report)
        resolution["resolved_ground_truth"]["annotation"] = annotation()["annotation"]
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "is generated"):
            resolver.resolve(report, resolution)

    def test_tampered_intake_units_and_summary_are_rejected(self):
        report = intake_report()
        report["issues"]["candidate_pairs"][0]["unit_id"] = "ARU-0000000000000000"
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "does not match"):
            resolver.resolve(report, resolution_for(report))

        report = intake_report()
        report["summary"]["adjudication_units"] += 1
        with self.assertRaisesRegex(resolver.AnnotationResolutionError,
                                    "summary does not match"):
            resolver.resolve(report, resolution_for(report))

        report = intake_report()
        report["reviewers"][0]["role"] = "reviewer_b"
        with self.assertRaisesRegex(resolver.AnnotationResolutionError, "roles are invalid"):
            resolver.resolve(report, resolution_for(report))

    def test_scope_and_matching_ambiguities_require_explicit_decisions(self):
        first = annotation(
            expected=[item("A-1"), item("A-2")],
            change_scope={"diff_base": BASE, "expected_verdict": "PASS",
                          "rationale": "bounded change"})
        second = annotation(
            "expert-b", expected=[item("B-1"), item("B-2")],
            change_scope={"diff_base": BASE, "expected_verdict": "WARN",
                          "rationale": "query risk"})
        report = intake_report(first, second)
        truth = copy.deepcopy(first)
        truth.pop("annotation")
        resolution = resolution_for(report, truth)
        kinds = {unit["unit_kind"] for unit in resolver._collect_units(report)}
        self.assertIn("scope_disagreement", kinds)
        self.assertIn("matching_ambiguity", kinds)
        result = resolver.resolve(report, resolution)
        self.assertEqual(result["change_scope"]["expected_verdict"], "PASS")

    def test_cli_emits_only_the_resolved_ground_truth(self):
        report = intake_report()
        resolution = resolution_for(report)
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "intake.json"
            resolution_path = Path(directory) / "resolution.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "benchmark/annotation_resolve.py"),
                "--intake", str(report_path), "--resolution", str(resolution_path),
            ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn("expected", output)
        self.assertNotIn("held_out_ready", output)


if __name__ == "__main__":
    unittest.main()
