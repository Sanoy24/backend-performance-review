"""Contracts for the decision-first human report and pull-request summary."""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pr_comment  # noqa: E402
import validate_review  # noqa: E402


TEMPLATE = ROOT / "skills" / "backend-performance-review" / "templates" / "review-report.md"
SKILL = ROOT / "skills" / "backend-performance-review" / "SKILL.md"
EXAMPLE = ROOT / "docs" / "examples" / "review.example.json"


def finding(number):
    finding_id = "PERF-%03d" % number
    return {
        "id": finding_id,
        "priority": "P1",
        "confidence": "High",
        "location": {"file": "src/service_%02d.py" % number, "line": number},
        "problem": "Repeated work on path %d." % number,
        "conditions": "Matters when path %d handles a variable-size result." % number,
        "recommendation": "Batch the work for path %d." % number,
        "validation": {
            "metric": "Operations per request on path %d" % number,
            "falsifier": "Operation count does not change.",
            "safety": "safe-on-production",
            "commands": [{
                "command": "python -m unittest tests.test_path_%02d" % number,
                "purpose": "Establish the operation-count baseline for %s." % finding_id,
                "safety": "safe-on-production",
            }],
        },
    }


def review(count):
    return {
        "mode": "full",
        "findings": [finding(index) for index in range(1, count + 1)],
        "runtime_evidence": [],
        "completeness": {
            "review_confidence": "Medium",
            "evidence_available": "uninstrumented",
            "ranking_method": "structural-signals-only",
            "unknowns": [
                {
                    "subject": "Unknown %d" % index,
                    "reason": "no-evidence-exists",
                    "what_would_resolve_it": "Measurement %d" % index,
                }
                for index in range(1, 5)
            ],
        },
    }


class FullReportContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")

    def test_first_section_is_a_bounded_decision_surface(self):
        decision = self.template.split("## 2. Scope and method", 1)[0]
        headings = [
            "### Overall assessment",
            "### Top three actions",
            "### Key unknowns",
            "### Validation commands",
        ]
        positions = [decision.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("under 400 words", decision)
        self.assertIn("at most three", decision.lower())

    def test_detailed_reasoning_remains_after_the_decision_summary(self):
        decision_end = self.template.index("## 2. Scope and method")
        for heading in (
                "## 7. Findings", "**Evidence**", "**Counter-evidence**",
                "**Alternatives**", "**Trade-offs**", "## 9. Validation plan"):
            with self.subTest(heading=heading):
                self.assertGreater(self.template.index(heading), decision_end)
        self.assertIn("do not gain brevity by deleting required reasoning", self.skill)

    def test_machine_review_accepts_structured_validation_commands(self):
        document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        document["findings"][0]["validation"]["commands"] = [{
            "command": "python -m unittest tests.test_orders",
            "purpose": "Measure query count before changing the implementation.",
            "safety": "safe-on-production",
        }]
        self.assertEqual(validate_review.validate(document, ROOT / "schemas"), [])


class PullRequestDecisionSurfaceTests(unittest.TestCase):

    def test_zero_findings_recommends_no_change_and_invents_no_command(self):
        body = pr_comment.render(review(0))
        self.assertIn("### Assessment", body)
        self.assertIn("No code change is recommended", body)
        self.assertIn("No finding-specific validation command is warranted", body)
        self.assertNotIn("### Finding detail", body)

    def test_one_finding_puts_action_unknown_and_command_before_detail(self):
        body = pr_comment.render(review(1))
        ordered = [
            body.index("### Assessment"),
            body.index("### Top actions"),
            body.index("### Key unknowns"),
            body.index("### Validate first"),
            body.index("### Finding detail"),
        ]
        self.assertEqual(ordered, sorted(ordered))
        self.assertIn("python -m unittest tests.test_path_01", body)

    def test_five_findings_surface_exactly_three_actions(self):
        body = pr_comment.render(review(5))
        actions = body.split("### Top actions", 1)[1].split("### Key unknowns", 1)[0]
        for number in range(1, 4):
            self.assertIn("PERF-%03d" % number, actions)
        self.assertNotIn("PERF-004", actions)
        self.assertNotIn("PERF-005", actions)
        self.assertEqual(body.count(" / High confidence"), 5)

    def test_validation_commands_never_jump_past_the_top_three_actions(self):
        document = review(5)
        for item in document["findings"][:3]:
            item["validation"]["commands"] = []
        body = pr_comment.render(document)
        self.assertNotIn("tests.test_path_04", body)
        self.assertNotIn("tests.test_path_05", body)
        self.assertIn("No executable command was supplied", body)

    def test_more_than_fifteen_findings_stays_bounded_and_discloses_the_rest(self):
        body = pr_comment.render(review(16))
        actions = body.split("### Top actions", 1)[1].split("### Key unknowns", 1)[0]
        self.assertEqual(actions.count("**PERF-"), 3)
        self.assertEqual(body.count(" / High confidence"), 5)
        self.assertIn("11 further finding(s) in the full report", body)
        self.assertIn("1 further unknown(s) in coverage below", body)


if __name__ == "__main__":
    unittest.main()
