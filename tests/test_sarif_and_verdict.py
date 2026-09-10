"""Tests for the publishing path: verdict, SARIF, pull-request comment, and validation.

The verdict is the change-scoped review's whole output as far as CI is concerned, so the
tests here are mostly about the ways it could lie: claiming PASS while carrying a confirmed
regression, or collapsing UNKNOWN into PASS. The second is the damaging one, because it
converts a gap in the review into a false assurance and the reader cannot tell.

Run with: python -m unittest discover -s tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import to_sarif  # noqa: E402
import pr_comment  # noqa: E402
import validate_review  # noqa: E402

EXAMPLE_REVIEW = ROOT / "docs" / "examples" / "review.example.json"


def finding(**overrides):
    base = {
        "id": "PERF-001",
        "stable_id": "a3f19c47b2e08d51",
        "root_cause_id": "ROOT-001",
        "category": "data-access",
        "severity": "High",
        "confidence": "High",
        "priority": "P1",
        "location": {"file": "src/orders/service.py", "line": 84,
                     "symbol": "OrderService.list"},
        "problem": "One query per row.",
        "conditions": "Matters above a handful of rows.",
        "counter_evidence": [],
        "impact": {"position": "critical-path", "frequency": "per-item",
                   "growth": "O(n)", "blast_radius": "endpoint"},
        "recommendation": "Batch the lookup.",
        "validation": {"falsifier": "Query count unchanged."},
    }
    base.update(overrides)
    return base


def change_scoped(**overrides):
    base = {"mode": "change-scoped", "findings": [], "completeness": {}}
    base.update(overrides)
    return base


class VerdictDerivationTests(unittest.TestCase):

    def test_a_full_review_has_no_verdict(self):
        self.assertIsNone(to_sarif.derive_verdict({"mode": "full", "findings": []}))

    def test_no_findings_is_a_pass(self):
        self.assertEqual(to_sarif.derive_verdict(change_scoped()), "PASS")

    def test_a_high_severity_high_confidence_finding_fails(self):
        review = change_scoped(findings=[finding(severity="High", confidence="High")])
        self.assertEqual(to_sarif.derive_verdict(review), "FAIL")

    def test_a_critical_confirmed_finding_fails(self):
        review = change_scoped(
            findings=[finding(severity="Critical", confidence="Confirmed", priority="P0")])
        self.assertEqual(to_sarif.derive_verdict(review), "FAIL")

    def test_a_high_severity_but_low_confidence_finding_only_warns(self):
        # A hypothesis is not a regression. Blocking on one is how a check gets muted.
        review = change_scoped(
            findings=[finding(severity="High", confidence="Low", priority="P2")])
        self.assertEqual(to_sarif.derive_verdict(review), "WARN")

    def test_a_confident_but_minor_finding_only_warns(self):
        review = change_scoped(
            findings=[finding(severity="Low", confidence="Confirmed", priority="P2")])
        self.assertEqual(to_sarif.derive_verdict(review), "WARN")

    def test_an_unanalyzable_change_is_unknown_not_pass(self):
        # The single most damaging thing this mode can do is report "I could not look" as
        # "I looked and found nothing".
        review = change_scoped(completeness={"unknowns": [
            {"subject": "the changed worker", "reason": "technology-unsupported"}]})
        self.assertEqual(to_sarif.derive_verdict(review), "UNKNOWN")

    def test_unexamined_code_is_also_unknown(self):
        review = change_scoped(completeness={"unknowns": [
            {"subject": "half the diff", "reason": "not-examined"}]})
        self.assertEqual(to_sarif.derive_verdict(review), "UNKNOWN")

    def test_a_missing_measurement_does_not_make_a_review_unknown(self):
        # "No evidence exists" is the normal state of a static review. If it forced UNKNOWN,
        # every review would be UNKNOWN and the verdict would carry no information.
        review = change_scoped(completeness={"unknowns": [
            {"subject": "production request rate", "reason": "no-evidence-exists"}]})
        self.assertEqual(to_sarif.derive_verdict(review), "PASS")

    def test_unknown_outranks_a_finding(self):
        review = change_scoped(
            findings=[finding()],
            completeness={"unknowns": [
                {"subject": "the rest of the diff", "reason": "not-examined"}]})
        self.assertEqual(to_sarif.derive_verdict(review), "UNKNOWN")


class SarifConversionTests(unittest.TestCase):

    def test_the_committed_example_converts(self):
        review = json.loads(EXAMPLE_REVIEW.read_text(encoding="utf-8"))
        sarif = to_sarif.to_sarif(review)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(len(sarif["runs"]), 1)
        self.assertEqual(len(sarif["runs"][0]["results"]), 1)

    def test_level_follows_priority_not_severity(self):
        # A High-severity finding at Low confidence is P2 — a hypothesis. Mapping severity
        # alone would render it as an error beside a confirmed regression.
        hypothesis = to_sarif.to_sarif({"findings": [
            finding(severity="High", confidence="Low", priority="P2")]})
        confirmed = to_sarif.to_sarif({"findings": [
            finding(severity="High", confidence="Confirmed", priority="P0")]})
        self.assertEqual(hypothesis["runs"][0]["results"][0]["level"], "warning")
        self.assertEqual(confirmed["runs"][0]["results"][0]["level"], "error")

    def test_stable_id_becomes_the_fingerprint(self):
        sarif = to_sarif.to_sarif({"findings": [finding()]})
        fingerprints = sarif["runs"][0]["results"][0]["partialFingerprints"]
        self.assertEqual(fingerprints["backendPerformanceReview/v1"], "a3f19c47b2e08d51")

    def test_a_finding_that_moves_keeps_its_fingerprint(self):
        moved = finding(location={"file": "src/orders/service.py", "line": 205,
                                  "symbol": "OrderService.list"})
        first = to_sarif.to_sarif({"findings": [finding()]})["runs"][0]["results"][0]
        second = to_sarif.to_sarif({"findings": [moved]})["runs"][0]["results"][0]
        self.assertEqual(first["partialFingerprints"], second["partialFingerprints"])
        self.assertNotEqual(
            first["locations"][0]["physicalLocation"]["region"]["startLine"],
            second["locations"][0]["physicalLocation"]["region"]["startLine"])

    def test_zero_findings_produces_valid_empty_sarif(self):
        # Zero findings is a valid, successful result and must not be an error path.
        sarif = to_sarif.to_sarif({"mode": "full", "findings": [],
                                   "completeness": {"review_confidence": "Medium"}})
        self.assertEqual(sarif["runs"][0]["results"], [])
        self.assertEqual(sarif["runs"][0]["properties"]["findingsReported"], 0)
        self.assertEqual(sarif["runs"][0]["properties"]["reviewConfidence"], "Medium")

    def test_the_message_carries_the_conditions(self):
        # A performance finding stripped of the workload that makes it matter is the
        # generic-checklist failure. Most SARIF consumers show only this text.
        sarif = to_sarif.to_sarif({"findings": [finding()]})
        text = sarif["runs"][0]["results"][0]["message"]["text"]
        self.assertIn("Matters when:", text)

    def test_an_empty_counter_evidence_list_is_stated_not_omitted(self):
        sarif = to_sarif.to_sarif({"findings": [finding(counter_evidence=[])]})
        self.assertIn("searched, none found",
                      sarif["runs"][0]["results"][0]["message"]["text"])

    def test_adjacent_findings_are_excluded_by_default(self):
        # This tool has no security methodology; publishing SEC- items into a security
        # dashboard would present them as the output of a scanner it is not.
        review = {"findings": [], "adjacent_findings": [{
            "id": "SEC-001", "kind": "security", "confidence": "High", "risk": "High",
            "problem": "Timing-unsafe comparison.", "recommendation": "Use a constant-time compare.",
            "assessed_properly_by": "a dedicated security review"}]}
        self.assertEqual(to_sarif.to_sarif(review)["runs"][0]["results"], [])

    def test_adjacent_findings_are_never_errors_when_included(self):
        review = {"findings": [], "adjacent_findings": [{
            "id": "SEC-001", "kind": "security", "confidence": "High", "risk": "High",
            "problem": "Timing-unsafe comparison.", "recommendation": "Use a constant-time compare.",
            "assessed_properly_by": "a dedicated security review"}]}
        results = to_sarif.to_sarif(review, include_adjacent=True)["runs"][0]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["level"], "note")
        self.assertTrue(results[0]["properties"]["outOfScope"])

    def test_one_rule_per_category_not_per_finding(self):
        review = {"findings": [finding(), finding(id="PERF-002", stable_id="b" * 16),
                               finding(id="PERF-003", stable_id="c" * 16,
                                       category="concurrency")]}
        rules = to_sarif.to_sarif(review)["runs"][0]["tool"]["driver"]["rules"]
        self.assertEqual(sorted(r["id"] for r in rules),
                         ["perf/concurrency", "perf/data-access"])

    def test_a_mismatched_verdict_is_surfaced(self):
        review = change_scoped(verdict="PASS",
                               findings=[finding(severity="Critical", confidence="High")])
        sarif = to_sarif.to_sarif(review)
        self.assertEqual(sarif["runs"][0]["properties"]["verdict"], "PASS")
        self.assertEqual(sarif["runs"][0]["properties"]["derivedVerdict"], "FAIL")


class PullRequestCommentTests(unittest.TestCase):
    """The comment is what most people will ever read of a review, so the things most easily
    lost in compression are the things tested here."""

    def test_the_marker_is_first_so_the_comment_can_be_updated(self):
        # Without it the action posts a new comment per push, and the check gets muted.
        body = pr_comment.render({"findings": []})
        self.assertTrue(body.startswith(pr_comment.MARKER))

    def test_zero_findings_is_not_presented_as_a_clean_bill_of_health(self):
        body = pr_comment.render({"findings": [], "completeness": {
            "review_confidence": "Low"}})
        self.assertIn("nothing material was found in what was reviewed", body)
        self.assertIn("Review confidence", body)
        self.assertIn("Low", body)

    def test_unknown_is_spelled_out_as_not_a_pass(self):
        body = pr_comment.render(change_scoped(verdict="UNKNOWN"))
        self.assertIn("not** a pass", body)

    def test_the_absence_of_runtime_evidence_is_stated(self):
        body = pr_comment.render({"findings": [finding()], "runtime_evidence": []})
        self.assertIn("No runtime evidence was supplied", body)

    def test_conditions_survive_into_the_comment(self):
        body = pr_comment.render({"findings": [finding()]})
        self.assertIn("Matters when:", body)

    def test_policy_violations_are_separated_from_findings(self):
        body = pr_comment.render({"findings": [], "policy_violations": [
            {"rule": "max_db_queries_per_request", "declared": "20", "observed": "24",
             "status": "violated"}]})
        self.assertIn("### Policy", body)
        # It must read as a rule the team chose, not as a measurement of performance.
        self.assertIn("These are not measurements of performance", " ".join(body.split()))
        self.assertIn("facts about rules the team chose", " ".join(body.split()))
        # And it must not be filed among the findings.
        self.assertLess(body.index("### Coverage"), body.index("<sub>"))
        self.assertNotIn("max_db_queries_per_request", body.split("### Policy")[0])

    def test_an_unevaluable_budget_says_so_rather_than_claiming_it_was_met(self):
        body = pr_comment.render({"findings": [], "policy_violations": [
            {"rule": "p95_latency", "observed": "unknown", "status": "not-evaluable",
             "why_not_evaluable": "no baseline was supplied"}]})
        self.assertIn("could not be evaluated", body)
        self.assertIn("no baseline was supplied", body)

    def test_a_met_policy_rule_is_not_reported_as_a_violation(self):
        body = pr_comment.render({"findings": [], "policy_violations": [
            {"rule": "max_page_size", "observed": "50", "status": "met"}]})
        self.assertNotIn("### Policy", body)

    def test_the_comment_is_capped_and_says_so(self):
        findings = [finding(id="PERF-%03d" % i, stable_id="%016x" % i) for i in range(9)]
        body = pr_comment.render({"findings": findings})
        self.assertIn("4 further finding(s)", body)

    def test_improvements_are_credited(self):
        body = pr_comment.render({"findings": [], "improvements": [
            {"description": "The N+1 in the orders path is now a batched lookup.",
             "status": "resolved"}]})
        self.assertIn("Improvements in this change", body)
        self.assertIn("resolved", body)


class ReviewValidationTests(unittest.TestCase):
    """The action validates before publishing, because a malformed review is much cheaper to
    reject here than to discover after it has been posted to someone's pull request."""

    SCHEMAS = ROOT / "schemas"

    def valid(self):
        return json.loads(EXAMPLE_REVIEW.read_text(encoding="utf-8"))

    def test_the_committed_example_validates(self):
        self.assertEqual(validate_review.validate(self.valid(), self.SCHEMAS), [])

    def test_a_priority_that_contradicts_the_matrix_is_caught(self):
        review = self.valid()
        review["findings"][0]["priority"] = "P0"   # High/High derives P1
        problems = validate_review.validate(review, self.SCHEMAS)
        self.assertTrue(any("matrix derives as P1" in p for p in problems), problems)

    def test_confirmed_confidence_without_runtime_evidence_is_caught(self):
        # Confirmed asserts a cited runtime artifact exists. A review that lists none cannot
        # also claim one.
        review = self.valid()
        review["findings"][0]["confidence"] = "Confirmed"
        problems = validate_review.validate(review, self.SCHEMAS)
        self.assertTrue(any("no runtime evidence" in p for p in problems), problems)

    def test_a_dangling_root_cause_reference_is_caught(self):
        review = self.valid()
        review["findings"][0]["root_cause_id"] = "ROOT-999"
        problems = validate_review.validate(review, self.SCHEMAS)
        self.assertTrue(any("ROOT-999" in p for p in problems), problems)

    def test_duplicate_finding_ids_are_caught(self):
        review = self.valid()
        review["findings"].append(json.loads(json.dumps(review["findings"][0])))
        problems = validate_review.validate(review, self.SCHEMAS)
        self.assertTrue(any("duplicate finding id" in p for p in problems), problems)

    def test_a_schema_violation_is_caught(self):
        review = self.valid()
        del review["findings"][0]["counter_evidence"]
        problems = validate_review.validate(review, self.SCHEMAS)
        self.assertTrue(any("counter_evidence" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
