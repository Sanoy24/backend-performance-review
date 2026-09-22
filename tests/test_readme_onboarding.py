"""Keep the README's golden path executable, short, and complete."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
INSTALLATION = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")


class GoldenPathTests(unittest.TestCase):

    def test_quickstart_precedes_product_explanation(self):
        self.assertLess(README.index("## Quickstart: your first validated review"),
                        README.index("## Why this exists"))

    def test_quickstart_has_copyable_checkout_and_doctor_commands(self):
        self.assertIn(
            "git clone https://github.com/Sanoy24/backend-performance-review.git "
            "../backend-performance-review", README)
        self.assertIn(
            "python ../backend-performance-review/scripts/doctor.py --project . "
            "--skill-dir ../backend-performance-review/skills/backend-performance-review "
            "--output .", README)
        self.assertIn("Doctor result: 0 failure(s), 0 warning(s).", README)

    def test_exact_prompt_names_scope_safety_and_both_outputs(self):
        prompt = README.split("```text", 1)[1].split("```", 1)[0]
        self.assertIn("Follow ../backend-performance-review/skills/", prompt)
        self.assertIn("Review this entire repository", prompt)
        self.assertIn("Do not modify application", prompt)
        self.assertIn("performance-review.md", prompt)
        self.assertIn("performance-review.json", prompt)

    def test_expected_files_validation_command_and_success_are_explicit(self):
        self.assertIn("exactly two new review artifacts", README)
        self.assertIn("python ../backend-performance-review/scripts/validate_review.py "
                      "--review performance-review.json", README)
        self.assertIn("performance-review.json is a valid review (N finding(s))", README)
        self.assertIn("Zero findings is valid", README)

    def test_weak_finding_is_transformed_with_the_required_evidence_chain(self):
        section = README.split("### From a weak finding to a useful one", 1)[1].split(
            "### If it does not work", 1)[0]
        for required in ("**Weak:**", "**Evidence-based:**", "src/orders/service.py:84",
                         "counter-evidence", "This matters when", "Validate by recording",
                         "falsifier"):
            with self.subTest(required=required):
                self.assertIn(required, section)

    def test_troubleshooting_has_exactly_five_fast_paths(self):
        section = README.split("### If it does not work: two-minute troubleshooting", 1)[1]
        section = section.split("A short report or zero findings", 1)[0]
        rows = [line for line in section.splitlines()
                if line.startswith("| ") and not line.startswith("| Symptom")]
        rows = [line for line in rows if not re.match(r"^\|:?-", line)]
        self.assertEqual(len(rows), 5, rows)
        for row in rows:
            self.assertGreaterEqual(row.count("|"), 3)

    def test_platform_specific_commands_live_in_the_installation_guide(self):
        for command in ("/plugin marketplace add", "mkdir -p .claude/skills"):
            self.assertNotIn(command, README)
            self.assertIn(command, INSTALLATION)
        self.assertIn("README.md#quickstart-your-first-validated-review", INSTALLATION)

    def test_validator_contract_documentation_was_not_lost(self):
        section = README.split("### Machine-readable validation and publishing", 1)[1]
        self.assertIn("schema version `1.0`", section)
        self.assertIn("backend-performance-review/2.0", section)
        self.assertIn("RFC 3339 `date-time`", section)
        self.assertIn("`if`/`then`/`else`", section)


if __name__ == "__main__":
    unittest.main()
