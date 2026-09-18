"""Contracts for repository-informed, decision-valued workload interviews."""

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_review  # noqa: E402


METHOD = (ROOT / "skills" / "backend-performance-review" / "methodology" /
          "workload.md")
SKILL = ROOT / "skills" / "backend-performance-review" / "SKILL.md"
SCHEMA = ROOT / "schemas" / "review.schema.json"
EXAMPLE = ROOT / "docs" / "examples" / "review.example.json"


class AdaptiveWorkloadContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.method = METHOD.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        cls.example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def adaptive_document(self):
        document = copy.deepcopy(self.example)
        document["workload"]["inputs"] = [
            {
                "question": "Is the GET /orders result set bounded in repository code?",
                "answer": "No pagination maximum or query limit is present.",
                "source": "repository",
                "asked": False,
                "evidence_checked": ["src/orders/service.py:80-88"],
            },
            {
                "question": "Typical and maximum orders returned by GET /orders",
                "answer": "Not answered",
                "source": "unanswered",
                "asked": True,
                "evidence_checked": [
                    "src/orders/service.py:80-88 has no bound",
                    "No repository artifact supplies production row counts",
                ],
                "decision_dimensions": ["severity", "recommendation"],
                "expected_decision_value": "highest",
            },
        ]
        document["decision_changing_questions"][0].update({
            "decision_dimensions": ["severity", "recommendation"],
            "expected_decision_value": "highest",
        })
        return document

    def test_repository_inference_precedes_question_selection(self):
        inference = self.method.index("Resolve repository-answerable questions before asking")
        interview = self.method.index("## 2. Select the workload interview")
        self.assertLess(inference, interview)
        for source in (
                "deployment manifests", "configuration", "load tests", "route, query"):
            with self.subTest(source=source):
                self.assertIn(source, self.method)
        self.assertIn("asked: false", self.method)
        self.assertIn("exact evidence checked", self.method)

    def test_questions_must_change_a_review_decision(self):
        gate = self.method.split("### Apply the decision-change gate", 1)[1].split(
            "### Rank by expected decision value", 1)[0]
        for decision in ("Severity", "Confidence", "Recommendation"):
            with self.subTest(decision=decision):
                self.assertIn(decision, gate)
        self.assertIn("at least two plausible answers", gate)
        self.assertIn("Discard questions", gate)

    def test_expected_value_is_ordinal_and_the_cap_is_not_a_quota(self):
        self.assertIn("zero to seven questions", self.method)
        self.assertIn("maximum, not a quota", " ".join(self.skill.split()))
        self.assertIn("do not invent a numeric score or probability", self.method)
        for value in ("highest", "high", "medium", "low"):
            self.assertIn("`%s`" % value, self.method)

    def test_schema_records_adaptive_selection_context(self):
        inputs = self.schema["properties"]["workload"]["properties"]["inputs"]
        properties = inputs["items"]["properties"]
        self.assertTrue({
            "asked", "evidence_checked", "decision_dimensions",
            "expected_decision_value",
        } <= set(properties))
        self.assertEqual(
            validate_review.validate(self.adaptive_document(), ROOT / "schemas"), [])

    def test_zero_question_interview_is_valid(self):
        document = self.adaptive_document()
        document["workload"]["inputs"] = [
            item for item in document["workload"]["inputs"]
            if item.get("asked") is not True
        ]
        self.assertEqual(validate_review.validate(document, ROOT / "schemas"), [])

    def test_seven_question_maximum_is_enforced(self):
        document = self.adaptive_document()
        seed = document["workload"]["inputs"][1]
        for number in range(2, 9):
            extra = copy.deepcopy(seed)
            extra["question"] = "Decision-changing question %d" % number
            document["workload"]["inputs"].append(extra)
        problems = validate_review.validate(document, ROOT / "schemas")
        self.assertTrue(any("seven is the maximum" in problem for problem in problems))

    def test_repository_answer_cannot_be_marked_as_asked(self):
        document = self.adaptive_document()
        inferred = document["workload"]["inputs"][0]
        inferred.update({
            "asked": True,
            "decision_dimensions": ["confidence"],
            "expected_decision_value": "highest",
        })
        problems = validate_review.validate(document, ROOT / "schemas")
        self.assertTrue(any(
            "already answered by repository evidence" in problem for problem in problems))

    def test_asked_question_requires_selection_evidence_and_decision_metadata(self):
        document = self.adaptive_document()
        asked = document["workload"]["inputs"][1]
        asked.pop("evidence_checked")
        asked.pop("decision_dimensions")
        asked.pop("expected_decision_value")
        problems = validate_review.validate(document, ROOT / "schemas")
        for fragment in (
                "must record evidence_checked", "must name a severity",
                "must declare expected_decision_value"):
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in problem for problem in problems))

    def test_asked_questions_are_ordered_by_expected_decision_value(self):
        document = self.adaptive_document()
        first = document["workload"]["inputs"][1]
        first["expected_decision_value"] = "low"
        second = copy.deepcopy(first)
        second["question"] = "A later but higher-value question"
        second["expected_decision_value"] = "highest"
        document["workload"]["inputs"].append(second)
        problems = validate_review.validate(document, ROOT / "schemas")
        self.assertTrue(any(
            "ordered by expected decision value" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
