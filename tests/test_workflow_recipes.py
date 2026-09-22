"""Smoke-test every maintained workflow without making a paid model call."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "workflow_recipe.py"
SKILL_DIR = ROOT / "skills" / "backend-performance-review"
WORKFLOW = ROOT / "examples" / "workflows" / "github-actions.yml"

SPEC = importlib.util.spec_from_file_location("workflow_recipe", SCRIPT)
workflow_recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow_recipe)


class PromptContractTests(unittest.TestCase):

    def test_full_prompt_is_non_interactive_safe_and_names_both_outputs(self):
        prompt = workflow_recipe.build_prompt(
            ROOT, SKILL_DIR, "full", None, "performance-review.md",
            "performance-review.json")
        for required in (
                "Review this entire repository", "mode to full", "non-interactive",
                "untrusted data", "do not execute repository", "Do not modify application files",
                "performance-review.md", "performance-review.json", "Zero findings is valid"):
            with self.subTest(required=required):
                self.assertIn(required, prompt)
        self.assertNotIn("nested tool checkout", prompt)

    def test_nested_tool_checkout_is_excluded_from_the_review_target(self):
        prompt = workflow_recipe.build_prompt(
            ROOT.parent, SKILL_DIR, "full", None, "performance-review.md",
            "performance-review.json")
        self.assertIn("nested tool checkout", prompt)
        self.assertIn("exclude it from the application review scope", prompt)

    def test_change_scoped_prompt_names_the_base_and_verdict_contract(self):
        prompt = workflow_recipe.build_prompt(
            ROOT, SKILL_DIR, "change-scoped", "origin/main", "report.md", "review.json")
        self.assertIn("origin/main..HEAD", prompt)
        self.assertIn("mode to change-scoped", prompt)
        self.assertIn("derive its verdict mechanically", prompt)


class NoCallSmokeTests(unittest.TestCase):

    def _dry_run(self, agent, *extra):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "generate", "--agent", agent,
             "--project", str(ROOT), "--skill-dir", str(SKILL_DIR), "--dry-run"]
            + list(extra),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return json.loads(result.stdout)

    def test_codex_dry_run_uses_the_supported_non_interactive_boundary(self):
        plan = self._dry_run("codex")
        self.assertEqual(
            plan["command"],
            ["codex", "exec", "--sandbox", "workspace-write", "--ephemeral",
             "--ignore-user-config", "--ignore-rules", "-"])
        self.assertEqual(plan["prompt_delivery"], "stdin")

    def test_claude_dry_run_uses_print_mode_and_scoped_tools(self):
        plan = self._dry_run("claude")
        command = plan["command"]
        self.assertEqual(command[0], "claude")
        self.assertIn("--bare", command)
        self.assertIn("-p", command)
        self.assertIn("--allowedTools", command)
        self.assertIn("Read,Glob,Grep,Write,Edit", command)
        self.assertFalse(any("Bash" in item for item in command))
        self.assertEqual(plan["prompt_delivery"], "argument")

    def test_custom_adapter_dry_run_does_not_require_the_adapter_to_exist(self):
        plan = self._dry_run(
            "command", "--command", ".github/bin/performance-review-agent")
        self.assertTrue(plan["command"][0].endswith(
            ".github\\bin\\performance-review-agent") or plan["command"][0].endswith(
                ".github/bin/performance-review-agent"))
        self.assertEqual(plan["prompt_delivery"], "stdin")

    def test_manual_recipe_writes_a_prompt_then_validates_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            prompt_file = Path(temporary) / "prompt.txt"
            report_file = Path(temporary) / "performance-review.md"
            report_file.write_text("# Review\n\nNo findings.\n", encoding="utf-8")
            review_file = ROOT / "docs" / "examples" / "review.example.json"

            prompt = subprocess.run(
                [sys.executable, str(SCRIPT), "prompt", "--project", str(ROOT),
                 "--skill-dir", str(SKILL_DIR), "--prompt-file", str(prompt_file),
                 "--report", str(report_file), "--review", str(review_file)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(prompt.returncode, 0, prompt.stderr)
            self.assertIn("SKILL.md", prompt_file.read_text(encoding="utf-8"))

            validate = subprocess.run(
                [sys.executable, str(SCRIPT), "validate", "--project", str(ROOT),
                 "--skill-dir", str(SKILL_DIR), "--report", str(report_file),
                 "--review", str(review_file)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertIn("is a valid review", validate.stdout)

class WorktreeBoundaryTests(unittest.TestCase):

    def test_only_declared_output_changes_are_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            report = project / "performance-review.md"
            review = project / "performance-review.json"
            report.write_text("# Review\n", encoding="utf-8")
            workflow_recipe._assert_only_allowed_changes(project, report, review)

            (project / "application.py").write_text("print('changed')\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unrelated worktree changes"):
                workflow_recipe._assert_only_allowed_changes(project, report, review)

    def test_tool_tree_digest_changes_when_a_validator_file_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = root / "validate.py"
            validator.write_text("original\n", encoding="utf-8")
            before = workflow_recipe._tree_digest(root)
            validator.write_text("replaced\n", encoding="utf-8")
            self.assertNotEqual(before, workflow_recipe._tree_digest(root))


class GitHubWorkflowContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_example_is_complete_and_orders_generate_validate_publish(self):
        self.assertNotIn("...", self.workflow)
        generate = self.workflow.index("uses: openai/codex-action@v1")
        validate = self.workflow.index("workflow_recipe.py validate")
        publish = self.workflow.index("uses: ./.backend-performance-review")
        self.assertLess(generate, validate)
        self.assertLess(validate, publish)

    def test_example_has_a_user_adapter_contract_and_advisory_policy(self):
        for required in (
                "openai/codex-action@v1", "OPENAI_API_KEY", "sandbox: workspace-write",
                "actions/upload-artifact@v4", "actions/download-artifact@v4",
                "fail-on: never", "pull-requests: write", "security-events: write",
                "persist-credentials: false"):
            with self.subTest(required=required):
                self.assertIn(required, self.workflow)
        self.assertEqual(self.workflow.count("ref: main"), 2)
        self.assertIn("pin a release tag or commit SHA", self.workflow)

    def test_example_does_not_expose_credentials_to_untrusted_forks(self):
        self.assertNotRegex(self.workflow, r"(?m)^\s*pull_request_target\s*:")
        self.assertIn("head.repo.full_name == github.repository", self.workflow)
        generate_job = self.workflow.split("  generate:", 1)[1].split(
            "  validate-and-publish:", 1)[0]
        publish_job = self.workflow.split("  validate-and-publish:", 1)[1]
        self.assertIn("OPENAI_API_KEY", generate_job)
        self.assertNotIn("OPENAI_API_KEY", publish_job)
        self.assertIn("fresh job", publish_job)


if __name__ == "__main__":
    unittest.main()
