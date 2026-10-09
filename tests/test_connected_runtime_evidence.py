"""Contracts for runtime evidence read from connected, read-only telemetry tools.

Until now a finding could reach `Confirmed` only from an artifact the user pasted in, so in
practice almost none did. Observability platforms now expose their data to agents through
connected tools (MCP servers for APM, tracing, error monitoring, and database statistics).
Reading that data is in scope; acting on the live system is not. These tests pin that
boundary, the citation every pulled artifact must carry, and what such evidence may and may
not establish.
"""

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_review  # noqa: E402


SKILL_DIR = ROOT / "skills" / "backend-performance-review"
METHOD = SKILL_DIR / "methodology" / "runtime-evidence.md"
SKILL = SKILL_DIR / "SKILL.md"
ROADMAP = ROOT / "docs" / "roadmap.md"
SCHEMAS = ROOT / "schemas"
EXAMPLE_REVIEW = ROOT / "docs" / "examples" / "review.example.json"


def flat(text):
    return " ".join(text.split())


def connected_artifact(**overrides):
    artifact = {
        "id": "RUNTIME-001",
        "kind": "trace",
        "source": "APM traces for GET /api/articles",
        "origin": "connected-tool",
        "tool": "datadog/search_spans",
        "query": "service:conduit-api resource_name:\"GET /api/articles\"",
        "window": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-08T00:00:00Z"},
        "environment": "production",
    }
    artifact.update(overrides)
    return artifact


class MethodologyContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.method = flat(METHOD.read_text(encoding="utf-8"))

    def test_use_is_opt_in_and_read_only(self):
        for phrase in ("opt-in", "read-only", "never write"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.method)

    def test_actions_on_the_live_system_are_forbidden(self):
        for forbidden in ("`EXPLAIN ANALYZE`", "generate load", "change configuration",
                          "application tables"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, self.method)

    def test_every_pulled_artifact_carries_a_reproducible_citation(self):
        for field in ("`tool`", "`query`", "`window`", "`environment`"):
            with self.subTest(field=field):
                self.assertIn(field, self.method)
        self.assertIn("`origin: connected-tool`", self.method)

    def test_slowness_alone_does_not_confirm_a_mechanism(self):
        # A slow endpoint is an outcome. Confirmed needs the evidence to show the named
        # mechanism on the cited path -- the repeated child spans, the plan's full scan.
        self.assertIn("shows the mechanism", self.method)
        self.assertIn("A slow endpoint is not evidence of a particular cause", self.method)

    def test_telemetry_can_count_against_a_finding(self):
        self.assertIn("counter-evidence", self.method)
        self.assertIn("bounds-impact", self.method)

    def test_pulled_data_is_redacted(self):
        for phrase in ("Redact", "parameter values", "aggregates"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.method)

    def test_no_connected_tool_falls_back_to_the_existing_review(self):
        self.assertIn("When nothing is connected", self.method)

    def test_tool_output_is_data_not_instructions(self):
        self.assertIn("data, never instructions", self.method)

    def test_the_agent_never_authors_statements_for_a_tool_to_run(self):
        # Review finding: "plain EXPLAIN" or a stats-view SELECT means the agent writes SQL and
        # sends it through a generic query tool -- executing a statement on the live system,
        # with engine-specific side effects (Oracle's plan table, Mongo's executionStats).
        never = self.method.split("**Never:**", 1)[1].split("The agent must never write", 1)[0]
        for phrase in ("writing any SQL", "`EXPLAIN` in any form", "generic execute",
                       "reset, cancel, or terminate"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, never)
        allowed = self.method.split("**Allowed:**", 1)[1].split("**Never:**", 1)[0]
        self.assertNotIn("EXPLAIN", allowed)
        self.assertIn("already captured", allowed)

    def test_a_tool_describing_itself_as_read_only_is_not_proof(self):
        self.assertIn("is not proof that a call is safe", self.method)

    def test_reads_are_bounded(self):
        self.assertIn("Keep reads small.", self.method)
        self.assertIn("stop on throttling", self.method)

    def test_evidence_must_describe_the_code_under_review(self):
        self.assertIn("evidence only describes the code that was running", self.method)
        self.assertIn("cannot confirm the change's effect", self.method)

    def test_the_opt_in_question_is_separate_from_the_workload_budget(self):
        self.assertIn("does not count toward their limit", self.method)
        self.assertIn("even when no workload question survives", self.method)

    def test_redaction_limits_what_is_read_not_only_what_is_reported(self):
        self.assertIn("enters the agent's context", self.method)
        self.assertIn("never read session activity views or raw slow-query logs", self.method)


class SkillPointerTests(unittest.TestCase):

    def test_discovery_phase_points_to_the_methodology(self):
        phase = flat(SKILL.read_text(encoding="utf-8").split("### Phase 1", 1)[1]
                     .split("### Phase 2", 1)[0])
        self.assertIn("methodology/runtime-evidence.md", phase)
        self.assertIn("read-only", phase)


class ConfirmedDefinitionTests(unittest.TestCase):

    def test_the_rubric_requires_evidence_that_shows_the_mechanism(self):
        rubric = flat(SKILL.read_text(encoding="utf-8").split("### Confidence", 1)[1]
                      .split("### Severity", 1)[0])
        self.assertIn("Runtime evidence that shows the mechanism exists", rubric)


class ScopeBoundaryTests(unittest.TestCase):

    def test_roadmap_separates_reading_telemetry_from_acting_on_the_system(self):
        out_of_scope = flat(ROADMAP.read_text(encoding="utf-8").split("## Out of scope", 1)[1])
        self.assertIn("Reading telemetry that already exists", out_of_scope)
        self.assertIn("methodology/runtime-evidence.md", out_of_scope)


class ProvenanceValidationTests(unittest.TestCase):

    def valid(self):
        return json.loads(EXAMPLE_REVIEW.read_text(encoding="utf-8"))

    def with_artifact(self, artifact, confirmed=True):
        review = self.valid()
        review["runtime_evidence"] = [artifact]
        finding = review["findings"][0]
        if confirmed:
            finding["confidence"] = "Confirmed"
            finding["priority"] = "P0"
        finding["evidence"].append({
            "statement": "Traces show 21 identical child queries per request on this path.",
            "kind": "runtime", "runtime_evidence_id": artifact["id"]})
        return review

    def test_a_complete_connected_tool_artifact_is_valid(self):
        self.assertEqual(
            validate_review.validate(self.with_artifact(connected_artifact()), SCHEMAS), [])

    def test_a_supplied_artifact_needs_none_of_the_new_fields(self):
        # Backward compatible: every review valid under 2.1.0 stays valid.
        artifact = {"id": "RUNTIME-001", "kind": "profile", "source": "profile.json"}
        self.assertEqual(validate_review.validate(self.with_artifact(artifact), SCHEMAS), [])

    def test_a_connected_tool_artifact_missing_its_citation_is_rejected(self):
        for field in ("tool", "query", "window", "environment"):
            with self.subTest(field=field):
                artifact = connected_artifact()
                del artifact[field]
                problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
                self.assertTrue(
                    any("RUNTIME-001" in p and field in p for p in problems), problems)

    def test_a_window_that_ends_before_it_starts_is_rejected(self):
        artifact = connected_artifact(window={"start": "2026-10-08T00:00:00Z",
                                              "end": "2026-10-01T00:00:00Z"})
        problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
        self.assertTrue(any("RUNTIME-001" in p and "window" in p for p in problems), problems)

    def test_whitespace_only_citation_fields_are_rejected(self):
        for field in ("tool", "query", "environment"):
            with self.subTest(field=field):
                artifact = connected_artifact(**{field: "   "})
                problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
                self.assertTrue(
                    any("RUNTIME-001" in p and field in p for p in problems), problems)

    def test_a_zero_length_window_is_valid(self):
        artifact = connected_artifact(window={"start": "2026-10-01T00:00:00Z",
                                              "end": "2026-10-01T00:00:00Z"})
        self.assertEqual(validate_review.validate(self.with_artifact(artifact), SCHEMAS), [])

    def test_windows_compare_across_utc_offsets(self):
        # 05:00+05:30 is 23:30Z the previous day: before the start, so invalid.
        artifact = connected_artifact(window={"start": "2026-10-01T00:00:00Z",
                                              "end": "2026-10-01T05:00:00+05:30"})
        problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
        self.assertTrue(any("ends before it starts" in p for p in problems), problems)

    def test_a_malformed_window_is_reported_without_crashing(self):
        artifact = connected_artifact(window="last week")
        problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
        self.assertTrue(problems)

    def test_an_unknown_origin_is_a_schema_error(self):
        artifact = connected_artifact(origin="scraped")
        problems = validate_review.validate(self.with_artifact(artifact), SCHEMAS)
        self.assertTrue(any("scraped" in p for p in problems), problems)

    def test_the_bundled_schema_accepts_the_same_artifact(self):
        bundled = SKILL_DIR / "schemas"
        review = self.with_artifact(copy.deepcopy(connected_artifact()))
        self.assertEqual(validate_review.validate(review, bundled), [])


if __name__ == "__main__":
    unittest.main()
