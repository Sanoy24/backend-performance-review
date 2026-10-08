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

    def test_discard_is_reserved_for_candidates_that_are_not_real(self):
        # Breadth regression (benchmark/ab-results/second-comparison.md): on one pinned model,
        # guided reviews found real, minor issues and then discarded them, reporting 69% as many
        # real issues as plain reviews. Small impact must lower severity, not delete the finding.
        section = " ".join(self.method.split("## 2. Keep a private candidate ledger", 1)[1]
                           .split("## 3.", 1)[0].split())
        self.assertIn("`discard` is for candidates that are not real", section)
        self.assertIn("promoted even when its impact is small", section)
        for allowed in ("refutes", "unreachable", "merges into", "Informational"):
            with self.subTest(allowed=allowed):
                self.assertIn(allowed, section)

    def test_deployment_dependent_mechanisms_become_questions_not_findings(self):
        # Third comparison (benchmark/ab-results/third-comparison.md): the promote-if-real rule
        # led both guided Node runs to report "single process" -- real, but costly only if a
        # container gets more than one core, which no file in the repository says. That is the
        # finding the repository's forbidden trap exists to decline. Decided, before the next
        # comparison, that such a mechanism becomes a decision-changing question.
        section = " ".join(self.method.split("## 2. Keep a private candidate ledger", 1)[1]
                           .split("## 3.", 1)[0].split())
        self.assertIn("depends entirely on a fact the repository does not contain", section)
        self.assertIn("disposition is `question`", section)
        self.assertIn("never a finding", section)

    def test_final_sweep_covers_write_paths_growth_and_fixed_per_request_work(self):
        # The same comparison: every issue the guided runs never surfaced sat on a write path,
        # in data that grows without bound, or in work repeated on every request.
        sweep = " ".join(self.method.split("## 7. Final coverage sweep", 1)[1]
                         .split("## 8.", 1)[0].split())
        for area in ("**Write paths.**", "**Growth over time.**", "**Fixed work on every request.**"):
            with self.subTest(area=area):
                self.assertIn(area, sweep)

    def test_small_bounded_or_startup_only_is_not_a_reason_to_discard(self):
        # Fourth comparison (benchmark/ab-results/fourth-comparison.md): guided breadth fell to
        # 65% of plain, almost entirely in small items. Guided runs discarded a per-request
        # goroutine as "bounded" and a compressed binary as "startup-only" -- real mechanisms
        # the promote-if-real rule says to keep, at Low severity.
        section = " ".join(self.method.split("## 2. Keep a private candidate ledger", 1)[1]
                           .split("## 3.", 1)[0].split())
        self.assertIn("Small, bounded, rare, and startup-only are not discard reasons", section)

    def test_section_4_tests_set_severity_rather_than_licensing_discards(self):
        # §4 was titled "Discard aggressively" and its workload test called a small cost "not a
        # finding", contradicting §2's discard rule. Its tests now grade a real candidate.
        self.assertNotIn("Discard aggressively", self.method)
        section = " ".join(self.method.split("## 4.", 1)[1].split("## 5.", 1)[0].split())
        self.assertIn("lowers Severity", section)
        self.assertIn("§2", section)

    def test_final_sweep_covers_lifecycle_and_response_encoding(self):
        # Fourth comparison: the remaining misses were never reached at all -- a snapshot forced
        # on shutdown, a fixed startup sleep, double validation of responses, templates compiled
        # per call, no compression, static assets without content type or cache headers.
        sweep = " ".join(self.method.split("## 7. Final coverage sweep", 1)[1]
                         .split("## 8.", 1)[0].split())
        for area in ("**Startup, shutdown, and build.**", "**Encoding the response.**"):
            with self.subTest(area=area):
                self.assertIn(area, sweep)
        for cue in ("rebuilt or recompiled per call", "spawned per request"):
            with self.subTest(cue=cue):
                self.assertIn(cue, sweep)

    def test_skill_names_the_widened_sweep_and_the_discard_rule(self):
        phase = " ".join(self.skill.split("### Phase 6", 1)[1].split("Load:", 1)[0].split())
        self.assertIn("write paths", phase)
        self.assertIn("data growth", phase)
        self.assertIn("startup and shutdown", phase)
        self.assertIn("response encoding", phase)
        self.assertIn("even when minor, bounded, or startup-only", phase)
        self.assertIn("only refuted, unreachable, duplicate, or impact-free candidates are discarded", phase)
        self.assertIn("deployment-dependent", phase)

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
