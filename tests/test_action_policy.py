"""Tests for the composite Action's configuration and merge-gate policy."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import action_policy  # noqa: E402


VALID_ENV = {
    "BPR_FAIL_ON": "never",
    "BPR_UPLOAD_SARIF": "false",
    "BPR_COMMENT": "false",
    "BPR_INCLUDE_ADJACENT": "false",
    "BPR_STRICT_VERDICT": "true",
}


class ConfigurationTests(unittest.TestCase):

    def test_all_documented_configuration_is_accepted(self):
        for fail_on in action_policy.FAIL_ON_VALUES:
            for boolean in ("true", "false"):
                environment = dict(VALID_ENV, BPR_FAIL_ON=fail_on)
                for variable in action_policy.BOOLEAN_INPUTS.values():
                    environment[variable] = boolean
                with self.subTest(fail_on=fail_on, boolean=boolean):
                    self.assertIsNone(action_policy.validate_config(environment))

    def test_unknown_fail_on_is_rejected(self):
        with self.assertRaisesRegex(action_policy.ConfigurationError, "fail-on"):
            action_policy.validate_config(dict(VALID_ENV, BPR_FAIL_ON="warning"))

    def test_every_invalid_boolean_like_input_is_rejected(self):
        for name, variable in action_policy.BOOLEAN_INPUTS.items():
            environment = dict(VALID_ENV)
            environment[variable] = "yes"
            with self.subTest(name=name):
                with self.assertRaisesRegex(action_policy.ConfigurationError, name):
                    action_policy.validate_config(environment)

    def test_action_inputs_never_appear_inside_shell_source(self):
        action = (ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
        run_source = []
        in_block = False
        block_indent = None
        for line in action:
            indent = len(line) - len(line.lstrip())
            if line.lstrip().startswith("run:"):
                in_block = True
                block_indent = indent
                run_source.append(line)
                continue
            if in_block and line.strip() and indent <= block_indent:
                in_block = False
            if in_block:
                run_source.append(line)
        self.assertNotIn("${{ inputs.", "\n".join(run_source))

    def test_comment_update_is_scoped_to_the_expected_bot_author(self):
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn('.user.login == "github-actions[bot]"', action)


class VerdictPolicyTests(unittest.TestCase):

    def test_gate_matrix(self):
        expected = {
            "never": {None: False, "PASS": False, "WARN": False,
                      "FAIL": False, "UNKNOWN": False},
            "fail": {None: False, "PASS": False, "WARN": False,
                     "FAIL": True, "UNKNOWN": False},
            "warn": {None: False, "PASS": False, "WARN": True,
                     "FAIL": True, "UNKNOWN": False},
        }
        for fail_on, verdicts in expected.items():
            for verdict, result in verdicts.items():
                with self.subTest(fail_on=fail_on, verdict=verdict):
                    self.assertEqual(action_policy.should_fail(verdict, fail_on), result)

    def test_unexpected_verdict_is_rejected(self):
        with self.assertRaisesRegex(action_policy.ConfigurationError, "verdict"):
            action_policy.should_fail("MAYBE", "never")

    def test_footer_describes_each_gate_and_full_mode(self):
        self.assertIn("does not block", action_policy.footer_for("change-scoped", "never"))
        self.assertIn("FAIL blocks", action_policy.footer_for("change-scoped", "fail"))
        self.assertIn("WARN and FAIL block",
                      action_policy.footer_for("change-scoped", "warn"))
        self.assertIn("does not apply", action_policy.footer_for("full", "warn"))
        self.assertIn("UNKNOWN do not", action_policy.footer_for("change-scoped", "warn"))


if __name__ == "__main__":
    unittest.main()
