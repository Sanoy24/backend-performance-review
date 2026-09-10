"""Tests for the benchmark scoring harness.

The harness is the thing that will decide whether this project is getting better or worse,
so it gets the same treatment the invariant checker got: deliberate wrong answers, checked
to produce the expected score. A scorer nobody has tried to fool is not evidence.

Run with: python -m unittest discover -s tests
"""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "scoring"))
sys.path.insert(0, str(ROOT / "scripts"))

import score as scorer  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402

EXAMPLE_REVIEW = ROOT / "docs" / "examples" / "review.example.json"
FIXTURE = ROOT / "tests" / "fixtures" / "example-ground-truth.json"
SCHEMAS = ROOT / "schemas"


def finding(**overrides):
    base = {
        "id": "PERF-001",
        "category": "data-access",
        "severity": "High",
        "confidence": "High",
        "priority": "P1",
        "location": {"file": "src/orders/service.py", "line": 84},
        "recommendation": "Batch the related lookup into a single query.",
    }
    base.update(overrides)
    return base


def truth(**overrides):
    base = {
        "repository": {"name": "fixture", "commit": "0123456789abcdef"},
        "expected": [{
            "id": "GT-001",
            "location": {"file": "src/orders/service.py"},
            "category": "data-access",
            "mechanism": "one query per row",
            "severity": "High",
        }],
        "acceptable": [],
        "forbidden": [],
    }
    base.update(overrides)
    return base


class MatchingTests(unittest.TestCase):
    """Path matching has to be tolerant or one correct finding is scored as both a false
    negative and a false positive — which would make the metrics worse than useless."""

    def test_identical_paths_match(self):
        self.assertTrue(scorer.same_file("src/a.py", "src/a.py"))

    def test_a_longer_reported_path_matches_a_shorter_annotation(self):
        self.assertTrue(scorer.same_file("backend/src/orders/service.py",
                                         "src/orders/service.py"))

    def test_windows_separators_match_posix(self):
        self.assertTrue(scorer.same_file("src\\orders\\service.py", "src/orders/service.py"))

    def test_different_files_do_not_match(self):
        self.assertFalse(scorer.same_file("src/orders/service.py", "src/users/service.py"))

    def test_empty_paths_never_match(self):
        self.assertFalse(scorer.same_file("", ""))
        self.assertFalse(scorer.same_file(None, "src/a.py"))

    def test_a_reasonable_alternative_category_still_matches(self):
        item = {"location": {"file": "a.py"}, "category": "data-access",
                "also_acceptable_categories": ["concurrency"]}
        self.assertTrue(scorer.matches(finding(category="concurrency",
                                               location={"file": "a.py"}), item))

    def test_an_unrelated_category_does_not_match(self):
        item = {"location": {"file": "a.py"}, "category": "data-access"}
        self.assertFalse(scorer.matches(finding(category="memory",
                                                location={"file": "a.py"}), item))

    def test_a_conflicting_symbol_does_not_match(self):
        item = {"location": {"file": "a.py", "symbol": "OrderService.list"},
                "category": "data-access"}
        self.assertFalse(scorer.matches(
            finding(location={"file": "a.py", "symbol": "UserService.list"}), item))


class ScoringTests(unittest.TestCase):

    def test_a_perfect_review_scores_one(self):
        result = scorer.score(truth(), {"findings": [finding()]})
        self.assertEqual(result["overall"]["precision"], 1.0)
        self.assertEqual(result["overall"]["recall"], 1.0)
        self.assertEqual(result["counts"]["false_positives"], 0)

    def test_a_missed_finding_is_a_false_negative(self):
        result = scorer.score(truth(), {"findings": []})
        self.assertEqual(result["counts"]["false_negatives"], 1)
        self.assertEqual(result["overall"]["recall"], 0.0)
        self.assertEqual(result["misses"][0]["id"], "GT-001")

    def test_an_unanticipated_finding_is_a_false_positive(self):
        review = {"findings": [finding(), finding(id="PERF-002", category="memory",
                                                  location={"file": "src/other.py"})]}
        result = scorer.score(truth(), review)
        self.assertEqual(result["counts"]["unanticipated"], 1)
        self.assertEqual(result["overall"]["precision"], 0.5)

    def test_an_acceptable_finding_is_scored_neither_way(self):
        # A real issue the annotator did not consider important enough to require must not
        # be punished as a false positive; that would train the corpus against finding
        # anything the annotator missed.
        annotation = truth(acceptable=[{
            "id": "GT-A1", "location": {"file": "src/serializers.py"},
            "category": "serialization", "mechanism": "rebuilt helper per call",
        }])
        review = {"findings": [finding(), finding(id="PERF-002", category="serialization",
                                                  location={"file": "src/serializers.py"})]}
        result = scorer.score(annotation, review)
        self.assertEqual(result["counts"]["tolerated"], 1)
        self.assertEqual(result["counts"]["false_positives"], 0)
        self.assertEqual(result["overall"]["precision"], 1.0)

    def test_reporting_a_forbidden_item_is_a_false_positive(self):
        annotation = truth(forbidden=[{
            "id": "GT-F1", "location": {"file": "src/config_loader.py"},
            "category": "data-access",
            "why_not": "the loop runs over a fixed list of six configuration keys",
        }])
        review = {"findings": [finding(), finding(id="PERF-002",
                                                  location={"file": "src/config_loader.py"})]}
        result = scorer.score(annotation, review)
        self.assertEqual(result["counts"]["forbidden_reported"], 1)
        self.assertEqual(len(result["restraint"]["forbidden_reported"]), 1)
        self.assertEqual(result["overall"]["precision"], 0.5)

    def test_a_healthy_repository_is_expressible_and_passes_when_silent(self):
        # The case docs/evaluation.md has never cleanly obtained: the correct answer is
        # nothing. Expressed as an empty `expected` with populated `forbidden`.
        annotation = {
            "repository": {"name": "healthy", "commit": "0123456789abcdef"},
            "expected": [],
            "acceptable": [],
            "forbidden": [{
                "id": "GT-F1", "location": {"file": "src/config_loader.py"},
                "category": "data-access",
                "why_not": "iterates a fixed list of six configuration keys",
            }],
        }
        clean = scorer.score(annotation, {"findings": []})
        self.assertEqual(clean["counts"]["false_positives"], 0)
        self.assertEqual(clean["counts"]["false_negatives"], 0)
        self.assertEqual(clean["restraint"]["forbidden_reported"], [])

        manufactured = scorer.score(annotation, {
            "findings": [finding(location={"file": "src/config_loader.py"})]})
        self.assertEqual(manufactured["counts"]["false_positives"], 1)
        self.assertEqual(manufactured["overall"]["precision"], 0.0)

    def test_per_category_metrics_are_separated(self):
        annotation = truth(expected=[
            {"id": "GT-1", "location": {"file": "a.py"}, "category": "data-access",
             "mechanism": "x"},
            {"id": "GT-2", "location": {"file": "b.py"}, "category": "concurrency",
             "mechanism": "y"},
        ])
        review = {"findings": [finding(location={"file": "a.py"})]}
        result = scorer.score(annotation, review)
        self.assertEqual(result["per_category"]["data-access"]["tp"], 1)
        self.assertEqual(result["per_category"]["concurrency"]["fn"], 1)
        self.assertEqual(result["per_category"]["concurrency"]["recall"], 0.0)


class CalibrationTests(unittest.TestCase):

    def test_exact_severity_agreement(self):
        result = scorer.score(truth(), {"findings": [finding(severity="High")]})
        self.assertEqual(result["severity_calibration"]["exact"], 1)

    def test_one_level_off_is_not_a_large_disagreement(self):
        result = scorer.score(truth(), {"findings": [finding(severity="Medium")]})
        self.assertEqual(result["severity_calibration"]["within_one"], 1)
        self.assertEqual(result["severity_calibration"]["large_disagreement"], 0)

    def test_calling_a_medium_issue_critical_is_a_large_disagreement(self):
        # The failure the review named specifically: a system that finds the right issue and
        # calls everything Critical is not useful.
        annotation = truth(expected=[{
            "id": "GT-1", "location": {"file": "a.py"}, "category": "data-access",
            "mechanism": "x", "severity": "Medium"}])
        result = scorer.score(annotation, {
            "findings": [finding(severity="Critical", location={"file": "a.py"})]})
        self.assertEqual(result["severity_calibration"]["large_disagreement"], 1)
        self.assertEqual(result["severity_calibration"]["disagreements"][0]["expert"], "Medium")

    def test_confidence_buckets_report_actual_correctness(self):
        annotation = truth(forbidden=[{
            "id": "GT-F1", "location": {"file": "bad.py"}, "category": "data-access",
            "why_not": "bounded"}])
        review = {"findings": [
            finding(confidence="High"),
            finding(id="PERF-002", confidence="High", location={"file": "bad.py"}),
        ]}
        result = scorer.score(annotation, review)
        self.assertEqual(result["confidence_calibration"]["High"]["actual_correctness"], 0.5)

    def test_claiming_more_confidence_than_evidence_supports_is_flagged(self):
        annotation = truth(expected=[{
            "id": "GT-1", "location": {"file": "a.py"}, "category": "data-access",
            "mechanism": "x", "severity": "High", "max_confidence": "High"}])
        result = scorer.score(annotation, {
            "findings": [finding(confidence="Confirmed", location={"file": "a.py"})]})
        self.assertEqual(result["confidence_ceiling_violations"][0]["claimed"], "Confirmed")

    def test_confidence_within_the_ceiling_is_not_flagged(self):
        annotation = truth(expected=[{
            "id": "GT-1", "location": {"file": "a.py"}, "category": "data-access",
            "mechanism": "x", "severity": "High", "max_confidence": "High"}])
        result = scorer.score(annotation, {
            "findings": [finding(confidence="Medium", location={"file": "a.py"})]})
        self.assertEqual(result["confidence_ceiling_violations"], [])


class RecommendationTests(unittest.TestCase):
    """Finding accuracy and recommendation accuracy are tracked apart, because identifying a
    problem correctly and then proposing the wrong fix is a distinct failure."""

    ANNOTATION = {
        "repository": {"name": "fixture", "commit": "0123456789abcdef"},
        "expected": [{
            "id": "GT-001", "location": {"file": "src/orders/service.py"},
            "category": "data-access", "mechanism": "one query per row", "severity": "High",
            "expected_recommendation": ["batch", "single query", "join"],
            "forbidden_recommendation": ["redis", "cache"],
        }],
        "acceptable": [], "forbidden": [],
    }

    def test_the_right_fix_scores(self):
        result = scorer.score(self.ANNOTATION, {"findings": [
            finding(recommendation="Batch the lookup into a single query.")]})
        self.assertEqual(result["recommendation_accuracy"]["rate"], 1.0)

    def test_a_cargo_cult_fix_is_caught_even_though_the_finding_was_right(self):
        result = scorer.score(self.ANNOTATION, {"findings": [
            finding(recommendation="Add Redis to cache the per-row lookup.")]})
        self.assertEqual(result["overall"]["precision"], 1.0, "the finding itself was correct")
        self.assertEqual(result["recommendation_accuracy"]["rate"], 0.0)
        self.assertIn("redis",
                      result["recommendation_accuracy"]["problems"][0]["used_forbidden"])

    def test_the_preferred_alternative_counts_toward_the_recommendation(self):
        result = scorer.score(self.ANNOTATION, {"findings": [finding(
            recommendation="Remove the repeated datastore work.",
            alternatives=[{"option": "Batch the lookup", "preferred": True,
                           "why": "removes the work"}])]})
        self.assertEqual(result["recommendation_accuracy"]["rate"], 1.0)


class StabilityTests(unittest.TestCase):
    """Turns docs/evaluation.md §3.18's hand diff into a number."""

    def test_identical_runs_are_fully_stable(self):
        run = {"findings": [finding()]}
        result = scorer.stability(run, copy.deepcopy(run))
        self.assertEqual(result["overlap"], 1.0)
        self.assertEqual(result["severity_agreement"], 1.0)
        self.assertEqual(result["priority_agreement"], 1.0)

    def test_a_finding_present_in_only_one_run_lowers_overlap(self):
        a = {"findings": [finding()]}
        b = {"findings": [finding(), finding(id="PERF-002", category="memory",
                                             location={"file": "src/other.py"})]}
        self.assertEqual(scorer.stability(a, b)["overlap"], 0.5)
        self.assertEqual(len(scorer.stability(a, b)["only_in_b"]), 1)

    def test_the_same_finding_scored_differently_is_reported(self):
        a = {"findings": [finding(severity="High", priority="P1")]}
        b = {"findings": [finding(severity="Critical", priority="P0")]}
        result = scorer.stability(a, b)
        self.assertEqual(result["overlap"], 1.0, "same location and category")
        self.assertEqual(result["severity_agreement"], 0.0)
        self.assertEqual(result["priority_agreement"], 0.0)


class ShippedExampleTests(unittest.TestCase):
    """The committed example review must actually be scoreable — otherwise the schema and
    the harness agree only in principle."""

    def setUp(self):
        self.review = json.loads(EXAMPLE_REVIEW.read_text(encoding="utf-8"))

    def test_the_ci_fixture_is_schema_valid(self):
        annotation = json.loads(FIXTURE.read_text(encoding="utf-8"))
        errors = schema_lite.validate_file(annotation, SCHEMAS / "ground-truth.schema.json")
        self.assertEqual(errors, [])

    def test_the_example_review_scores_against_matching_ground_truth(self):
        # Deliberately the same file the CI workflow scores, so the two cannot drift: if the
        # example, the schema, and the scorer stop agreeing, this fails rather than CI
        # quietly succeeding on a fixture nothing else exercises.
        annotation = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = scorer.score(annotation, self.review)
        self.assertEqual(result["overall"]["precision"], 1.0)
        self.assertEqual(result["overall"]["recall"], 1.0)
        self.assertEqual(result["severity_calibration"]["exact"], 1)
        self.assertEqual(result["confidence_ceiling_violations"], [])
        self.assertEqual(result["recommendation_accuracy"]["rate"], 1.0)

    def test_ground_truth_fixtures_validate_against_the_schema(self):
        annotation = truth()
        errors = schema_lite.validate_file(annotation, SCHEMAS / "ground-truth.schema.json")
        self.assertEqual(errors, [])

    def test_the_schema_rejects_a_forbidden_item_with_no_justification(self):
        # "why_not" is required precisely because a forbidden item without a stated reason is
        # an opinion, and the corpus would then encode the annotator's taste as truth.
        annotation = truth(forbidden=[{"id": "GT-F1", "location": {"file": "a.py"}}])
        errors = schema_lite.validate_file(annotation, SCHEMAS / "ground-truth.schema.json")
        self.assertTrue(any("why_not" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
