"""The validator ships inside the skill, and works from an installed copy of it.

The third A/B comparison found a guided review that the project's validator rejected, but which
its agent believed valid: the validator lived at the repository root, so an agent using an
installed skill could not run it and wrote its own, weaker checker. These tests install only the
skill directory somewhere else and prove the bundled validator runs every check there -- not
just that it starts. Its priority-matrix and stable-ID checks are skipped silently when the
files they need are not found, so "it ran" is not the same as "it checked".
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "backend-performance-review"
EXAMPLE = ROOT / "docs" / "examples" / "review.example.json"

BUNDLED = [
    ("scripts/validate_review.py", "scripts/validate_review.py"),
    ("scripts/json_schema_lite.py", "scripts/json_schema_lite.py"),
    ("schemas/review.schema.json", "schemas/review.schema.json"),
    ("schemas/finding.schema.json", "schemas/finding.schema.json"),
]


class BundledValidatorTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.installed = Path(self.tmp.name) / "installed" / "backend-performance-review"
        shutil.copytree(str(SKILL), str(self.installed))

    def tearDown(self):
        self.tmp.cleanup()

    def run_bundled(self, review):
        path = Path(self.tmp.name) / "review.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-I", str(self.installed / "scripts" / "validate_review.py"),
             "--review", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def example(self):
        return json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_bundled_copies_are_identical_to_the_repository_copies(self):
        for root_rel, skill_rel in BUNDLED:
            with self.subTest(file=skill_rel):
                self.assertEqual((ROOT / root_rel).read_bytes(),
                                 (SKILL / skill_rel).read_bytes())

    def test_installed_copy_accepts_a_valid_review(self):
        result = self.run_bundled(self.example())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is a valid review", result.stdout)

    def test_installed_copy_runs_the_priority_matrix_check(self):
        review = self.example()
        review["findings"][0]["priority"] = "P3"
        result = self.run_bundled(review)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("which the matrix derives as", result.stdout + result.stderr)

    def test_installed_copy_runs_the_stable_id_check(self):
        review = self.example()
        review["findings"][0]["stable_id"] = "0000000000000000"
        result = self.run_bundled(review)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical algorithm", result.stdout + result.stderr)

    def test_skill_tells_the_agent_to_run_the_bundled_validator(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("${CLAUDE_SKILL_DIR}/scripts/validate_review.py", skill)


if __name__ == "__main__":
    unittest.main()
