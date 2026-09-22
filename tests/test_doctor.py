"""Regression tests for the installation doctor and every supported copy layout."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402


def make_skill(path, registry=None):
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: backend-performance-review\n---\n# Skill\n", encoding="utf-8")
    (path / "technology").mkdir()
    (path / "technology" / "python.md").write_text("# Python\n", encoding="utf-8")
    (path / "registry.yaml").write_text(
        registry or "version: 1\n- signal: python\n  load: [technology/python.md]\n",
        encoding="utf-8")
    return path


class DiscoveryTests(unittest.TestCase):

    PROJECT_LAYOUTS = (
        Path("skills/backend-performance-review"),
        Path(".claude/skills/backend-performance-review"),
        Path(".agents/skills/backend-performance-review"),
        Path(".opencode/skills/backend-performance-review"),
    )
    PERSONAL_LAYOUTS = (
        Path(".claude/skills/backend-performance-review"),
        Path(".agents/skills/backend-performance-review"),
        Path(".config/opencode/skills/backend-performance-review"),
    )

    def assert_healthy(self, project, home):
        checks = doctor.run_checks(project, home, project, version_info=(3, 8, 0))
        self.assertFalse([check for check in checks if check.status == "FAIL"], checks)
        self.assertTrue(any(check.name == "Skill discovery" for check in checks))
        self.assertTrue(any(check.name == "Registry" for check in checks))

    def test_every_supported_project_layout_is_discovered(self):
        for layout in self.PROJECT_LAYOUTS:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project, home = root / "project", root / "home"
                project.mkdir()
                home.mkdir()
                make_skill(project / layout)
                self.assert_healthy(project, home)

    def test_every_supported_personal_layout_is_discovered(self):
        for layout in self.PERSONAL_LAYOUTS:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project, home = root / "project", root / "home"
                project.mkdir()
                home.mkdir()
                make_skill(home / layout)
                self.assert_healthy(project, home)

    def test_explicit_directory_does_not_get_hidden_by_an_unrelated_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, home = root / "project", root / "home"
            project.mkdir()
            home.mkdir()
            explicit = make_skill(root / "custom" / "backend-performance-review")
            candidates = doctor.discover_candidates(project, home, [explicit])
            self.assertEqual([(item.scope, item.path) for item in candidates],
                             [("explicit", explicit)])

    def test_missing_installation_fails_with_copy_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, home = root / "project", root / "home"
            project.mkdir()
            home.mkdir()
            checks = doctor.check_installation(project, home)
            failure = next(check for check in checks if check.status == "FAIL")
            commands = "\n".join(failure.remediation)
            self.assertTrue("cp -R" in commands or "Copy-Item" in commands, commands)


class IntegrityTests(unittest.TestCase):

    def inspect(self, registry=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = make_skill(Path(temporary.name) / "skill", registry)
        candidate = doctor.Candidate("test", path)
        return path, doctor.inspect_skill(candidate)

    def test_registry_without_version_is_rejected(self):
        _path, (_skill_errors, registry_errors, _entries) = self.inspect(
            "- signal: python\n  load: [technology/python.md]\n")
        self.assertTrue(any("top-level version" in error for error in registry_errors),
                        registry_errors)

    def test_registry_with_a_missing_load_target_is_rejected(self):
        _path, (_skill_errors, registry_errors, _entries) = self.inspect(
            "version: 1\n- signal: python\n  load: [technology/missing.md]\n")
        self.assertTrue(any("does not exist" in error for error in registry_errors),
                        registry_errors)

    def test_wrapped_inline_load_lists_are_supported(self):
        _path, (_skill_errors, registry_errors, entries) = self.inspect(
            "version: 1\n- signal: python\n  load: [technology/python.md,\n"
            "         technology/python.md]\n")
        self.assertEqual(entries, 1)
        self.assertEqual(registry_errors, [])

    def test_non_utf8_registry_is_reported_as_unreadable(self):
        path, _result = self.inspect()
        (path / "registry.yaml").write_bytes(b"\xff\xfe")
        _skill_errors, registry_errors, entries = doctor.inspect_skill(
            doctor.Candidate("test", path))
        self.assertEqual(entries, 0)
        self.assertTrue(any("unreadable" in error for error in registry_errors),
                        registry_errors)

    def test_doctor_reads_only_known_skill_metadata(self):
        path, _result = self.inspect()
        secret = path.parent / ".env"
        secret.write_text("TOKEN=must-not-be-read\n", encoding="utf-8")
        original = Path.read_text
        read_paths = []

        def guarded_read_text(target, *args, **kwargs):
            read_paths.append(target)
            if target == secret:
                raise AssertionError("doctor read a secret file")
            return original(target, *args, **kwargs)

        with mock.patch.object(Path, "read_text", autospec=True,
                               side_effect=guarded_read_text):
            doctor.inspect_skill(doctor.Candidate("test", path))
        self.assertEqual({item.name for item in read_paths}, {"SKILL.md", "registry.yaml"})

    def test_incomplete_duplicate_warns_without_hiding_the_healthy_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, home = root / "project", root / "home"
            project.mkdir()
            home.mkdir()
            make_skill(project / ".claude/skills/backend-performance-review")
            broken = project / ".agents/skills/backend-performance-review"
            broken.mkdir(parents=True)
            checks = doctor.check_installation(project, home)
            self.assertFalse([check for check in checks if check.status == "FAIL"], checks)
            self.assertTrue(any(check.name == "Duplicate installations"
                                and check.status == "WARN" for check in checks), checks)


class EnvironmentTests(unittest.TestCase):

    def test_python_38_is_the_minimum(self):
        self.assertEqual(doctor.check_python((3, 8, 0)).status, "PASS")
        failure = doctor.check_python((3, 7, 9))
        self.assertEqual(failure.status, "FAIL")
        self.assertTrue(failure.remediation)

    def test_output_probe_accepts_writable_and_rejects_missing_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(doctor.check_output_directory(directory).status, "PASS")
            failure = doctor.check_output_directory(Path(directory) / "missing")
            self.assertEqual(failure.status, "FAIL")
            commands = "\n".join(failure.remediation)
            self.assertTrue("mkdir -p" in commands or "New-Item" in commands, commands)

    def test_github_cli_is_optional_and_never_checks_authentication(self):
        calls = []

        def found(name):
            calls.append(name)
            return "/usr/bin/gh"

        self.assertEqual(doctor.check_github_cli(False, found).status, "SKIP")
        self.assertEqual(calls, [])
        check = doctor.check_github_cli(True, found)
        self.assertEqual(check.status, "PASS")
        self.assertEqual(calls, ["gh"])
        self.assertIn("authentication was deliberately not inspected", check.detail)

    def test_missing_github_cli_has_platform_specific_remediation(self):
        expectations = {"win32": "winget install", "darwin": "brew install",
                        "linux": "apt-get install"}
        for platform, command in expectations.items():
            with self.subTest(platform=platform):
                check = doctor.check_github_cli(True, lambda _name: None, platform)
                self.assertEqual(check.status, "FAIL")
                self.assertIn(command, "\n".join(check.remediation))

    def test_json_cli_output_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, home = root / "project", root / "home"
            project.mkdir()
            home.mkdir()
            skill = make_skill(project / ".agents/skills/backend-performance-review")
            output = io.StringIO()
            with mock.patch.object(Path, "home", return_value=home), redirect_stdout(output):
                exit_code = doctor.main([
                    "--project", str(project), "--skill-dir", str(skill),
                    "--output", str(project), "--json"])
            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["checks"])


if __name__ == "__main__":
    unittest.main()
