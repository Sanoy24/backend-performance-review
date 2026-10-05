"""Contracts separating broad candidate discovery from selective reporting."""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pr_comment  # noqa: E402
import validate_review  # noqa: E402


METHOD = (ROOT / "skills" / "backend-performance-review" / "methodology" /
          "bottleneck-analysis.md")
SKILL = ROOT / "skills" / "backend-performance-review" / "SKILL.md"
TEMPLATE = ROOT / "skills" / "backend-performance-review" / "templates" / "review-report.md"
SCHEMA = ROOT / "schemas" / "review.schema.json"
EXAMPLE = ROOT / "docs" / "examples" / "review.example.json"


class CandidateFlowContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.method = METHOD.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_private_ledger_records_candidate_evidence_path_and_disposition(self):
        section = self.method.split("## 2. Keep a private candidate ledger", 1)[1].split(
            "## 3.", 1)[0]
        normalized_section = " ".join(section.split())
        for required in (
                "private working state", "Candidate", "Evidence checked",
                "Critical path or shared resource", "Disposition", "not a third review artifact"):
            with self.subTest(required=required):
                self.assertIn(required, normalized_section)

    def test_selection_pipeline_applies_budget_only_after_discovery_and_merging(self):
        output = self.method.split("## 11. Output of this phase", 1)[1]
        stages = [
            "Complete candidate discovery",
            "Give every candidate a disposition",
            "Merge promoted candidates",
            "Score the merged findings",
            "Only now apply the output budget",
        ]
        positions = [output.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("presentation depth only", self.skill)

    def test_final_sweep_covers_every_path_and_shared_resource(self):
        sweep = self.method.split("## 7. Final coverage sweep", 1)[1].split(
            "## 8.", 1)[0]
        self.assertIn("every identified critical path", sweep)
        self.assertIn("every shared pool", sweep)
        self.assertIn("not-examined", sweep)
        self.assertIn("Zero promoted findings", sweep)

    def test_only_revisitable_discards_reach_the_public_report(self):
        considered = self.template.split("### Considered and not reported", 1)[1].split(
            "### Adjacent findings", 1)[0]
        for required in (
                "plausible, material", "path/resource", "evidence checked",
                "discard reason", "revisit condition", "private candidate ledger"):
            with self.subTest(required=required):
                self.assertIn(required, considered)

    def test_schema_preserves_revisitable_discard_context(self):
        item = self.schema["properties"]["considered_not_reported"]["items"]["properties"]
        self.assertTrue({"path", "evidence_checked", "revisit_when"} <= set(item))

        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        document["considered_not_reported"].append({
            "observation": "The shared pool may queue under bursts.",
            "why_discarded": "The configured bound covers known worker concurrency.",
            "path": "GET /orders -> primary connection pool",
            "evidence_checked": ["config/pool.yml:4 caps workers below pool capacity"],
            "revisit_when": "Pool wait time becomes non-zero under the peak scenario.",
        })
        self.assertEqual(validate_review.validate(document, ROOT / "schemas"), [])

    def test_pr_summary_exposes_coverage_sweep_counts(self):
        body = pr_comment.render({
            "mode": "full",
            "findings": [],
            "completeness": {
                "critical_paths_identified": 4,
                "critical_paths_analyzed": 4,
                "shared_resources_identified": 3,
                "shared_resources_analyzed": 2,
            },
        })
        self.assertIn("| Critical paths analyzed | 4 / 4 |", body)
        self.assertIn("| Shared resources analyzed | 2 / 3 |", body)


if __name__ == "__main__":
    unittest.main()
