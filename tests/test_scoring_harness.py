"""Tests for the benchmark scoring harness.

The harness is the thing that will decide whether this project is getting better or worse,
so it gets the same treatment the invariant checker got: deliberate wrong answers, checked
to produce the expected score. A scorer nobody has tried to fool is not evidence.

Run with: python -m unittest discover -s tests
"""

import copy
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "scoring"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "backend-performance-review" / "scripts"))

import score as scorer  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402
import compute_stable_id as stable_ids  # noqa: E402

EXAMPLE_REVIEW = ROOT / "docs" / "examples" / "review.example.json"
FIXTURE = ROOT / "tests" / "fixtures" / "example-ground-truth.json"
PERMUTATION_FIXTURE = ROOT / "tests" / "fixtures" / "permutation-matching-case.json"
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
    if "stable_id" not in overrides:
        base["stable_id"] = stable_ids.compute_for_finding(base)
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

    def test_a_finding_at_an_also_location_matches(self):
        # Discovered from two real independent reviews of the identical bug citing opposite
        # ends of one call chain: the query-issuing definition and the call site that would
        # actually need to change. Neither is more correct.
        item = {"location": {"file": "users/models.go", "symbol": "isFollowing"},
                "also_locations": [{"file": "articles/serializers.go",
                                    "symbol": "ArticleSerializer.Response"}],
                "category": "data-access"}
        self.assertTrue(scorer.matches(
            finding(location={"file": "users/models.go", "symbol": "isFollowing"}), item))
        self.assertTrue(scorer.matches(
            finding(location={"file": "articles/serializers.go",
                              "symbol": "ArticleSerializer.Response"}), item))

    def test_a_third_unrelated_location_does_not_match_via_also_locations(self):
        item = {"location": {"file": "users/models.go"},
                "also_locations": [{"file": "articles/serializers.go"}],
                "category": "data-access"}
        self.assertFalse(scorer.matches(finding(location={"file": "unrelated/file.go"}), item))

    def test_also_locations_still_enforces_its_own_symbol(self):
        # An also_location with a symbol is just as strict as the primary location — matching
        # the file alone is not enough if that entry names a conflicting symbol.
        item = {"location": {"file": "a.py"},
                "also_locations": [{"file": "b.py", "symbol": "Wanted"}],
                "category": "data-access"}
        self.assertFalse(scorer.matches(
            finding(location={"file": "b.py", "symbol": "Different"}), item))


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
        self.assertEqual(clean["case_outcome"]["abstention"], {
            "occurred": True, "matches_annotation": True})
        self.assertEqual(clean["case_outcome"]["known_traps_avoided"], 1)
        self.assertIn("known traps avoided: 1 of 1", scorer.render(clean))

        manufactured = scorer.score(annotation, {
            "findings": [finding(location={"file": "src/config_loader.py"})]})
        self.assertEqual(manufactured["counts"]["false_positives"], 1)
        self.assertEqual(manufactured["overall"]["precision"], 0.0)
        self.assertEqual(manufactured["case_outcome"]["abstention"], {
            "occurred": False, "matches_annotation": None})
        self.assertEqual(manufactured["case_outcome"]["known_traps_avoided"], 0)

    def test_abstaining_on_required_finding_conflicts_with_annotation(self):
        result = scorer.score(truth(), {"findings": []})
        self.assertEqual(result["case_outcome"]["abstention"], {
            "occurred": True, "matches_annotation": False})
        self.assertFalse(result["case_outcome"]["no_required_findings"])

    def test_unknown_is_reported_separately_from_abstention(self):
        annotation = truth(expected=[])
        review = {
            "mode": "change-scoped", "verdict": "UNKNOWN", "findings": [],
            "completeness": {"unknowns": [{"subject": "worker", "reason": "not-examined"}]},
        }
        result = scorer.score(annotation, review)
        self.assertEqual(result["case_outcome"]["abstention"], {
            "occurred": True, "matches_annotation": True})
        self.assertEqual(result["case_outcome"]["unknown_verdict"], {
            "declared": True, "derived": True, "expected": None,
            "scope_matches": None, "correctness": None})
        self.assertEqual(result["case_outcome"]["verdict"], {
            "declared": "UNKNOWN", "derived": "UNKNOWN", "expected": None,
            "scope_matches": None, "correctness": None})
        self.assertIn("(unadjudicated)", scorer.render(result))
        self.assertIsNone(scorer.score(annotation, {"findings": []})["case_outcome"]
                          ["unknown_verdict"]["declared"])

    def test_unknown_correctness_requires_expert_change_scope_truth(self):
        annotation = truth(expected=[], change_scope={
            "diff_base": "1111111111111111111111111111111111111111",
            "expected_verdict": "UNKNOWN",
            "rationale": "The changed worker cannot be examined from the supplied source.",
            "unknowns": [{"subject": "worker", "reason": "not-examined",
                          "what_would_resolve_it": "Supply the generated worker source."}],
        })
        review = {
            "mode": "change-scoped", "verdict": "UNKNOWN", "findings": [],
            "reproducibility": {"repository": {
                "diff_base": "1111111111111111111111111111111111111111"}},
            "completeness": {"unknowns": [{"subject": "worker", "reason": "not-examined"}]},
        }
        outcome = scorer.score(annotation, review)["case_outcome"]["unknown_verdict"]
        self.assertEqual(outcome, {
            "declared": True, "derived": True, "expected": True,
            "scope_matches": True, "correctness": True})
        self.assertEqual(scorer.score(annotation, review)["case_outcome"]["verdict"], {
            "declared": "UNKNOWN", "derived": "UNKNOWN", "expected": "UNKNOWN",
            "scope_matches": True, "correctness": True})

        annotation["change_scope"]["expected_verdict"] = "PASS"
        outcome = scorer.score(annotation, review)["case_outcome"]["unknown_verdict"]
        self.assertEqual(outcome["expected"], False)
        self.assertFalse(outcome["correctness"])

    def test_unknown_correctness_is_withheld_for_a_different_diff_base(self):
        annotation = truth(expected=[], change_scope={
            "diff_base": "1" * 40, "expected_verdict": "UNKNOWN",
            "rationale": "The relevant source is unavailable.",
            "unknowns": [{"subject": "worker", "reason": "not-examined",
                          "what_would_resolve_it": "Supply the worker source."}],
        })
        review = {
            "mode": "change-scoped", "verdict": "UNKNOWN", "findings": [],
            "reproducibility": {"repository": {"diff_base": "2" * 40}},
            "completeness": {"unknowns": [{"subject": "worker", "reason": "not-examined"}]},
        }
        outcome = scorer.score(annotation, review)["case_outcome"]["unknown_verdict"]
        self.assertFalse(outcome["scope_matches"])
        self.assertIsNone(outcome["correctness"])

    def test_candidate_rejection_correctness_is_not_inferred_from_free_text(self):
        annotation = truth(expected=[], forbidden=[{
            "id": "GT-F1", "location": {"file": "src/config_loader.py"},
            "why_not": "bounded configuration list",
        }])
        review = {"findings": [], "considered_not_reported": [{
            "observation": "Possible cache opportunity",
            "why_discarded": "The list is bounded",
            "location": "src/config_loader.py",
        }]}
        outcome = scorer.score(annotation, review)["case_outcome"]
        self.assertEqual(outcome["candidate_rejections"], {
            "reported": 1, "adjudicated_correct": None})

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


class CandidateRejectionAdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.annotation = truth(forbidden=[{
            "id": "GT-F1", "location": {"file": "src/config_loader.py"},
            "category": "data-access", "why_not": "fixed configuration list",
        }])
        self.review = {"findings": [], "considered_not_reported": [{
            "observation": "Possible repeated query", "why_discarded": "Fixed configuration list",
            "location": "src/config_loader.py",
        }]}

    def adjudication(self, decisions):
        return {
            "schema_version": 1,
            "truth_content_sha256": scorer.content_digest(self.annotation),
            "review_content_sha256": scorer.content_digest(self.review),
            "adjudicator": "independent engineer",
            "adjudicated_at": "2026-09-20T12:00:00Z",
            "decisions": decisions,
        }

    def test_correct_rejection_requires_explicit_adjudication(self):
        decision = {"candidate_index": 0, "judgment": "correct_rejection",
                    "ground_truth_id": "GT-F1", "reason": "The list has fixed size."}
        result = scorer.score(self.annotation, self.review, self.adjudication([decision]))
        self.assertEqual(result["case_outcome"]["candidate_rejections"], {
            "reported": 1, "adjudicated_correct": 1, "adjudicated_incorrect": 0,
            "adjudicated_acceptable": 0, "unresolved": 0, "correct_rate": 1.0,
            "adjudicator": "independent engineer",
        })

    def test_rejecting_a_missed_required_finding_is_incorrect(self):
        self.review["considered_not_reported"][0] = {
            "observation": "One query per order", "why_discarded": "Assumed cheap",
            "location": "src/orders/service.py",
        }
        decision = {"candidate_index": 0, "judgment": "incorrect_rejection",
                    "ground_truth_id": "GT-001", "reason": "The defect is material."}
        result = scorer.score(self.annotation, self.review, self.adjudication([decision]))
        self.assertEqual(result["case_outcome"]["candidate_rejections"]["correct_rate"], 0.0)
        self.assertEqual(result["case_outcome"]["candidate_rejections"]
                         ["adjudicated_incorrect"], 1)

    def test_unresolved_candidate_is_not_counted_as_correct(self):
        decision = {"candidate_index": 0, "judgment": "unresolved",
                    "reason": "Workload information is missing."}
        result = scorer.score(self.annotation, self.review, self.adjudication([decision]))
        summary = result["case_outcome"]["candidate_rejections"]
        self.assertEqual(summary["unresolved"], 1)
        self.assertIsNone(summary["correct_rate"])

    def test_optional_finding_can_be_adjudicated_as_acceptable_nonreport(self):
        self.annotation["acceptable"] = [{
            "id": "GT-A1", "location": {"file": "src/config_loader.py"},
            "category": "data-access", "mechanism": "minor optional work",
        }]
        decision = {"candidate_index": 0, "judgment": "acceptable_nonreport",
                    "ground_truth_id": "GT-A1", "reason": "Valid but not material."}
        summary = scorer.score(self.annotation, self.review, self.adjudication([decision]))[
            "case_outcome"]["candidate_rejections"]
        self.assertEqual(summary["adjudicated_acceptable"], 1)
        self.assertIsNone(summary["correct_rate"])

    def test_stale_review_digest_is_rejected(self):
        adjudication = self.adjudication([{
            "candidate_index": 0, "judgment": "unresolved", "reason": "No workload",
        }])
        self.review["considered_not_reported"][0]["why_discarded"] = "Changed after adjudication"
        with self.assertRaisesRegex(ValueError, "review content digest"):
            scorer.score(self.annotation, self.review, adjudication)

    def test_truth_digest_is_bound_to_the_annotation(self):
        adjudication = self.adjudication([{
            "candidate_index": 0, "judgment": "unresolved", "reason": "No workload",
        }])
        self.annotation["forbidden"][0]["why_not"] = "Changed after adjudication"
        with self.assertRaisesRegex(ValueError, "truth content digest"):
            scorer.score(self.annotation, self.review, adjudication)

    def test_adjudication_cannot_predate_review_generation(self):
        self.review["reproducibility"] = {"generated_at": "2026-09-21T12:00:00Z"}
        decision = {"candidate_index": 0, "judgment": "unresolved", "reason": "No workload"}
        with self.assertRaisesRegex(ValueError, "must follow the frozen review"):
            scorer.score(self.annotation, self.review, self.adjudication([decision]))

    def test_content_digest_ignores_json_key_order(self):
        self.assertEqual(scorer.content_digest({"a": 1, "b": [2]}),
                         scorer.content_digest({"b": [2], "a": 1}))

    def test_correct_rejection_cannot_claim_a_reported_trap(self):
        self.review["findings"] = [finding(location={"file": "src/config_loader.py"})]
        decision = {"candidate_index": 0, "judgment": "correct_rejection",
                    "ground_truth_id": "GT-F1", "reason": "Claimed avoidance."}
        with self.assertRaisesRegex(ValueError, "unreported forbidden"):
            scorer.score(self.annotation, self.review, self.adjudication([decision]))

    def test_every_candidate_needs_one_decision(self):
        with self.assertRaisesRegex(ValueError, "one decision per candidate"):
            scorer.score(self.annotation, self.review, self.adjudication([]))

    def test_adjudication_rejects_ambiguous_and_duplicate_candidate_mapping(self):
        self.review["considered_not_reported"].append({
            "observation": "Another trap", "why_discarded": "Bounded",
        })
        decisions = [
            {"candidate_index": 0, "judgment": "correct_rejection",
             "ground_truth_id": "GT-F1", "reason": "Bounded."},
            {"candidate_index": 0, "judgment": "unresolved", "reason": "No evidence."},
        ]
        with self.assertRaisesRegex(ValueError, "indices must be unique"):
            scorer.score(self.annotation, self.review, self.adjudication(decisions))
        decisions[1]["candidate_index"] = 1
        decisions[1]["judgment"] = "correct_rejection"
        decisions[1]["ground_truth_id"] = "GT-F1"
        with self.assertRaisesRegex(ValueError, "cannot count for two candidates"):
            scorer.score(self.annotation, self.review, self.adjudication(decisions))

    def test_ambiguous_ground_truth_ids_cannot_be_adjudicated(self):
        self.annotation["forbidden"][0]["id"] = "GT-001"
        decision = {"candidate_index": 0, "judgment": "unresolved", "reason": "Ambiguous"}
        with self.assertRaisesRegex(ValueError, "ground-truth IDs must be unique"):
            scorer.score(self.annotation, self.review, self.adjudication([decision]))

    def test_cli_accepts_frozen_post_run_adjudication(self):
        decision = {"candidate_index": 0, "judgment": "correct_rejection",
                    "ground_truth_id": "GT-F1", "reason": "Fixed configuration list."}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth_path, review_path = root / "truth.json", root / "review.json"
            adjudication_path = root / "adjudication.json"
            truth_path.write_text(json.dumps(self.annotation), encoding="utf-8")
            review_path.write_text(json.dumps(self.review), encoding="utf-8")
            adjudication_path.write_text(json.dumps(self.adjudication([decision])),
                                         encoding="utf-8")
            completed = subprocess.run([
                sys.executable, str(ROOT / "benchmark/scoring/score.py"), "score",
                "--truth", str(truth_path), "--review", str(review_path),
                "--rejection-adjudication", str(adjudication_path), "--json",
            ], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["case_outcome"]["candidate_rejections"]
                         ["adjudicated_correct"], 1)
        self.assertEqual(output["adjudication_fingerprints"]["review_content_sha256"],
                         scorer.content_digest(self.review))


class OptimalAssignmentTests(unittest.TestCase):
    """Matching is a graph assignment problem, not a first-compatible-item search."""

    def setUp(self):
        case = json.loads(PERMUTATION_FIXTURE.read_text(encoding="utf-8"))
        self.annotation = case["truth"]
        self.review = case["review"]

    def test_optimal_assignment_finds_two_matches_where_greedy_finds_one(self):
        result = scorer.score(self.annotation, self.review)

        self.assertEqual(result["counts"]["true_positives"], 2)
        self.assertEqual(result["counts"]["false_negatives"], 0)
        self.assertEqual(result["counts"]["false_positives"], 0)

    def test_finding_and_expected_permutations_do_not_change_output(self):
        baseline = scorer.score(self.annotation, self.review)

        for expected in itertools.permutations(self.annotation["expected"]):
            for findings in itertools.permutations(self.review["findings"]):
                annotation = copy.deepcopy(self.annotation)
                review = copy.deepcopy(self.review)
                annotation["expected"] = list(expected)
                review["findings"] = list(findings)
                self.assertEqual(scorer.score(annotation, review), baseline)

    def test_acceptable_and_forbidden_permutations_do_not_change_output(self):
        broad = {
            "id": "GT-BROAD", "location": {"file": "shared.py"},
            "category": "data-access", "mechanism": "broad",
        }
        specific = {
            "id": "GT-SPECIFIC", "location": {"file": "shared.py", "symbol": "wanted"},
            "category": "data-access", "mechanism": "specific",
        }
        findings = [
            finding(id="PERF-SPECIFIC", location={"file": "shared.py", "symbol": "wanted"}),
            finding(id="PERF-OTHER", location={"file": "shared.py", "symbol": "other"}),
        ]

        for bucket, count_key in (("acceptable", "tolerated"),
                                  ("forbidden", "forbidden_reported")):
            annotation = truth(expected=[])
            annotation[bucket] = [broad, specific]
            if bucket == "forbidden":
                for item in annotation[bucket]:
                    item["why_not"] = "bounded fixture"
            baseline = scorer.score(annotation, {"findings": findings})
            self.assertEqual(baseline["counts"][count_key], 2)

            for items_order in itertools.permutations(annotation[bucket]):
                for findings_order in itertools.permutations(findings):
                    permuted = copy.deepcopy(annotation)
                    permuted[bucket] = list(items_order)
                    self.assertEqual(
                        scorer.score(permuted, {"findings": list(findings_order)}), baseline)

    def test_equal_score_assignments_are_reported_for_adjudication(self):
        annotation = truth(expected=[
            {"id": "GT-A", "location": {"file": "shared.py"},
             "category": "data-access", "mechanism": "first"},
            {"id": "GT-B", "location": {"file": "shared.py"},
             "category": "data-access", "mechanism": "second"},
        ])
        review = {"findings": [
            finding(id="PERF-A", location={"file": "shared.py"}),
            finding(id="PERF-B", location={"file": "shared.py"}),
        ]}

        ambiguities = scorer.score(annotation, review)["matching"]["ambiguities"]

        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(ambiguities[0]["bucket"], "expected")
        self.assertEqual(ambiguities[0]["reason"],
                         "equal_cardinality_and_specificity")
        self.assertNotEqual(ambiguities[0]["selected"], ambiguities[0]["alternative"])
        self.assertIn("AMBIGUOUS MATCHING", scorer.render(scorer.score(annotation, review)))

    def test_primary_symbol_matches_win_when_cardinality_is_equal(self):
        annotation = truth(expected=[
            {
                "id": "GT-A", "location": {"file": "a.py", "symbol": "A"},
                "also_locations": [{"file": "b.py", "symbol": "B"}],
                "category": "data-access", "mechanism": "first",
            },
            {
                "id": "GT-B", "location": {"file": "b.py", "symbol": "B"},
                "also_locations": [{"file": "a.py", "symbol": "A"}],
                "category": "data-access", "mechanism": "second",
            },
        ])
        review = {"findings": [
            finding(id="PERF-A", location={"file": "a.py", "symbol": "A"}),
            finding(id="PERF-B", location={"file": "b.py", "symbol": "B"}),
        ]}

        assignments = scorer.score(annotation, review)["matching"]["assignments"]["expected"]

        self.assertEqual([(pair["item"], pair["finding"]) for pair in assignments], [
            ("GT-A", "PERF-A"), ("GT-B", "PERF-B"),
        ])
        self.assertTrue(all(pair["specificity"]["primary_location"] for pair in assignments))
        self.assertTrue(all(pair["specificity"]["exact_symbol"] for pair in assignments))

    def test_every_unmatched_item_has_a_deterministic_explanation(self):
        annotation = truth(
            expected=[{
                "id": "GT-MISS", "location": {"file": "missing.py"},
                "category": "memory", "mechanism": "not found",
            }],
            acceptable=[{
                "id": "GT-OPTIONAL", "location": {"file": "optional.py"},
                "category": "serialization", "mechanism": "not reported",
            }],
            forbidden=[{
                "id": "GT-TRAP", "location": {"file": "trap.py"},
                "category": "concurrency", "why_not": "bounded fixture",
            }],
        )
        review = {"findings": [
            finding(id="PERF-UNKNOWN", category="io", location={"file": "unknown.py"}),
        ]}

        unmatched = scorer.score(annotation, review)["matching"]["unmatched"]

        self.assertEqual(unmatched["expected"][0]["reason"], "no_compatible_finding")
        self.assertEqual(unmatched["acceptable"][0]["reason"], "no_compatible_finding")
        self.assertEqual(unmatched["forbidden"][0]["reason"], "no_compatible_finding")
        self.assertEqual(unmatched["findings"][0]["reason"],
                         "no_compatible_ground_truth_item")


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

    def test_caveats_are_always_present(self):
        # A canonical ID now exists. The remaining caveat is semantic: two reviews may cite
        # opposite ends of one call chain, which changes the location-derived ID.
        result = scorer.stability({"findings": []}, {"findings": []})
        self.assertIn("caveats", result)
        self.assertTrue(any("call chain" in caveat for caveat in result["caveats"]))
        self.assertFalse(any("no canonical" in caveat for caveat in result["caveats"]))

    def test_identical_runs_are_fully_stable(self):
        run = {"findings": [finding()]}
        result = scorer.stability(run, copy.deepcopy(run))
        self.assertEqual(result["overlap"], 1.0)
        self.assertEqual(result["stable_id_overlap"], 1.0)
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

    def test_two_findings_with_one_stable_id_are_not_collapsed(self):
        # The canonical algorithm deliberately collides for two distinct findings sharing a
        # file, symbol, and category. Stability must preserve multiplicity even in that case.
        first_finding = finding(location={"file": "shared.py", "symbol": "shared"})
        second_finding = finding(id="PERF-002",
                                 location={"file": "shared.py", "symbol": "shared"})
        self.assertEqual(first_finding["stable_id"], second_finding["stable_id"])

        result = scorer.stability(
            {"findings": [first_finding, second_finding]},
            {"findings": [copy.deepcopy(first_finding)]},
        )

        self.assertEqual(result["overlap"], 0.5)
        self.assertEqual(result["shared"], 1)
        self.assertEqual(len(result["only_in_a"]), 1)
        self.assertEqual(result["stable_id_collisions"]["run_a"][0]["count"], 2)
        self.assertEqual(result["approximate_location_category"]["overlap"], 0.5)

    def test_canonical_ids_from_identical_inputs_agree_across_runs(self):
        run_a = finding(id="PERF-001", location={
            "file": "src/orders/service.py", "line": 10, "symbol": "OrderService.list",
        })
        run_b = finding(id="PERF-900", location={
            "file": "SRC\\ORDERS\\SERVICE.PY", "line": 999, "symbol": "OrderService.list",
        })
        self.assertEqual(run_a["stable_id"], run_b["stable_id"])

        result = scorer.stability({"findings": [run_a]}, {"findings": [run_b]})

        self.assertEqual(result["stable_id_overlap"], 1.0)
        self.assertEqual(result["shared"], 1)

    def test_findings_without_stable_ids_use_only_the_named_approximation(self):
        run_a = finding(stable_id=None)
        run_b = finding(id="PERF-900", stable_id=None)

        result = scorer.stability({"findings": [run_a]}, {"findings": [run_b]})

        self.assertIsNone(result["overlap"])
        self.assertEqual(result["missing_stable_ids"]["run_a"], ["PERF-001"])
        self.assertEqual(result["missing_stable_ids"]["run_b"], ["PERF-900"])
        self.assertEqual(result["approximate_location_category"]["overlap"], 1.0)


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

    def test_change_scope_truth_requires_a_pinned_base_verdict_and_rationale(self):
        annotation = truth(change_scope={
            "diff_base": "1" * 40,
            "expected_verdict": "UNKNOWN",
            "rationale": "The changed generated source is intentionally unavailable.",
            "unknowns": [{"subject": "generated source", "reason": "not-examined",
                          "what_would_resolve_it": "Supply the generated source."}],
        })
        self.assertEqual(schema_lite.validate_file(
            annotation, SCHEMAS / "ground-truth.schema.json"), [])
        for missing in ("diff_base", "expected_verdict", "rationale"):
            invalid = copy.deepcopy(annotation)
            del invalid["change_scope"][missing]
            errors = schema_lite.validate_file(
                invalid, SCHEMAS / "ground-truth.schema.json")
            self.assertTrue(any(missing in error for error in errors), errors)

        invalid = copy.deepcopy(annotation)
        del invalid["change_scope"]["unknowns"]
        errors = schema_lite.validate_file(invalid, SCHEMAS / "ground-truth.schema.json")
        self.assertTrue(any("unknowns" in error for error in errors), errors)

    def test_change_scope_truth_rejects_an_abbreviated_diff_base(self):
        annotation = truth(change_scope={
            "diff_base": "1234567", "expected_verdict": "PASS",
            "rationale": "The change has no material performance effect.",
        })
        errors = schema_lite.validate_file(annotation, SCHEMAS / "ground-truth.schema.json")
        self.assertTrue(any("diff_base" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()


class DisciplineMetricTests(unittest.TestCase):
    """The ground-truth-independent metrics behind benchmark/ab-comparison.md.

    These decide an A/B comparison this project has a stake in the outcome of, so each one is
    tested by handing it output it should flag and output it should not. A metric that only
    ever agrees with its author is not measurement.
    """

    def test_citation_rate_counts_findings_naming_a_file(self):
        review = {"findings": [finding(), finding(id="PERF-002", location={})]}
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["citation"], 0.5)
        self.assertEqual(result["flagged"]["uncited"], ["PERF-002"])

    def test_falsifiability_accepts_either_counter_evidence_or_why_not(self):
        review = {"findings": [
            finding(id="A", why_this_might_not_matter="Orders may be bounded in the low tens."),
            finding(id="B", counter_evidence=["No memoization wraps the lookup."]),
            finding(id="C"),
        ]}
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["falsifiability"], round(2 / 3.0, 3))
        self.assertEqual(result["flagged"]["unfalsifiable"], ["C"])

    def test_an_empty_counter_evidence_array_does_not_count_as_falsifiable(self):
        # An empty array is a positive assertion that a search found nothing, but this metric
        # measures what a *reader* is given to argue with, and an empty array gives them none.
        review = {"findings": [finding(counter_evidence=[])]}
        self.assertEqual(scorer.discipline(review)["rates"]["falsifiability"], 0.0)

    def test_conditioned_recommendation_rate_requires_non_empty_conditions(self):
        review = {"findings": [
            finding(id="A", conditions="Matters above a few dozen orders per request."),
            finding(id="B", conditions="   "),
        ]}
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["conditioned_recommendation"], 0.5)
        self.assertEqual(result["flagged"]["unconditioned"], ["B"])

    def test_cargo_cult_flags_a_named_remedy_with_no_stated_condition(self):
        review = {"findings": [finding(recommendation="Add a Redis cache in front of it.")]}
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["cargo_cult"], 1.0)
        self.assertEqual(result["flagged"]["cargo_cult"][0]["id"], "PERF-001")

    def test_the_same_remedy_is_not_cargo_cult_once_conditions_are_stated(self):
        # The rule being measured is Hard Rule 5 — state the workload the change pays off
        # under — not "never say Redis". Conflating the two would make the metric dishonest.
        review = {"findings": [finding(
            recommendation="Add a Redis cache in front of it.",
            conditions="Pays off above ~80% read ratio with tolerable staleness.")]}
        self.assertEqual(scorer.discipline(review)["rates"]["cargo_cult"], 0.0)

    def test_confirmed_without_a_runtime_artifact_is_flagged(self):
        review = {"findings": [finding(
            confidence="Confirmed", evidence="The loop issues a query per row.")]}
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["confidence_ceiling_violation"], 1.0)

    def test_confirmed_citing_a_runtime_artifact_is_not_flagged(self):
        review = {"findings": [finding(
            confidence="Confirmed",
            evidence="EXPLAIN ANALYZE on the endpoint shows 47 sequential scans.")]}
        self.assertEqual(
            scorer.discipline(review)["rates"]["confidence_ceiling_violation"], 0.0)

    def test_a_number_absent_from_the_repository_is_flagged(self):
        review = {"findings": [finding(problem="Tail latency reaches 800ms under load.")]}
        result = scorer.discipline(review, repo_tokens={"84", "20"})
        self.assertEqual(result["rates"]["unsourced_number"], 1.0)
        self.assertEqual(result["unsourced_numbers"][0]["claims"], ["800ms"])

    def test_a_number_present_in_the_repository_is_not_flagged(self):
        review = {"findings": [finding(problem="The page size of 20 items bounds the loop.")]}
        result = scorer.discipline(review, repo_tokens={"20"})
        self.assertEqual(result["rates"]["unsourced_number"], 0.0)

    def test_agent_installation_numbers_are_not_repository_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("PAGE_SIZE = 20\n", encoding="utf-8")
            skill = root / ".claude" / "skills" / "backend-performance-review"
            skill.mkdir(parents=True)
            (skill / "reference.md").write_text(
                "A generic example mentions 800ms.\n", encoding="utf-8")

            tokens = scorer.repo_number_tokens(root)
            self.assertIn("20", tokens)
            self.assertNotIn("800", tokens)

    def test_a_bare_number_with_no_unit_is_never_flagged(self):
        # Line numbers, versions and counts are not performance claims. Flagging them would
        # bury the one kind of number that actually misleads a reader.
        review = {"findings": [finding(problem="Go 1.21 in a loop over 3 collections.")]}
        result = scorer.discipline(review, repo_tokens=set())
        self.assertEqual(result["rates"]["unsourced_number"], 0.0)

    def test_numeric_checking_is_skipped_and_said_so_without_a_repo(self):
        review = {"findings": [finding(problem="Tail latency reaches 800ms.")]}
        result = scorer.discipline(review)
        self.assertIsNone(result["rates"]["unsourced_number"])
        self.assertIsNone(result["unsourced_numbers"])
        self.assertTrue(any("skipped" in c for c in result["caveats"]))

    def test_zero_findings_yields_null_rates_not_zero(self):
        # A review that correctly reported nothing must not read as having failed every
        # discipline check. Hard Rule 4 makes zero findings a success case.
        result = scorer.discipline({"findings": []})
        self.assertEqual(result["findings"], 0)
        for name, value in result["rates"].items():
            self.assertIsNone(value, "%s should be null on a zero-finding review" % name)

    def test_nested_string_fields_are_searched_not_just_flat_ones(self):
        review = {"findings": [finding(validation=["Measure p99; it sits near 800ms today."])]}
        result = scorer.discipline(review, repo_tokens=set())
        self.assertEqual(result["rates"]["unsourced_number"], 1.0)

    def test_the_shipped_example_review_passes_every_discipline_check(self):
        review = json.loads(EXAMPLE_REVIEW.read_text(encoding="utf-8"))
        result = scorer.discipline(review)
        self.assertEqual(result["rates"]["citation"], 1.0)
        self.assertEqual(result["rates"]["conditioned_recommendation"], 1.0)
        self.assertEqual(result["rates"]["falsifiability"], 1.0)
        self.assertEqual(result["rates"]["cargo_cult"], 0.0)
        self.assertEqual(result["rates"]["confidence_ceiling_violation"], 0.0)
