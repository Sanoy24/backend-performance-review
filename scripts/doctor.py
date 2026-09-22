#!/usr/bin/env python3
"""Diagnose a backend-performance-review installation without reading repository secrets.

Standard library only. The doctor inspects only known skill metadata files, performs one
create/delete probe in the requested output directory, and checks only whether `gh` exists.
It never scans application files, reads credentials, or invokes an authenticated command.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

MINIMUM_PYTHON = (3, 8)
SKILL_NAME = "backend-performance-review"


Check = namedtuple("Check", "name status detail remediation")
Check.__new__.__defaults__ = ((),)
Candidate = namedtuple("Candidate", "scope path")


def _absolute(path):
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def discover_candidates(project, home, explicit=()):
    """Return supported project/personal locations without recursively scanning either."""
    if explicit:
        raw = [Candidate("explicit", _absolute(path)) for path in explicit]
    else:
        project = _absolute(project)
        home = _absolute(home)
        raw = [
            Candidate("source checkout", project / "skills" / SKILL_NAME),
            Candidate("project / Claude Code", project / ".claude" / "skills" / SKILL_NAME),
            Candidate("project / agents", project / ".agents" / "skills" / SKILL_NAME),
            Candidate("project / OpenCode", project / ".opencode" / "skills" / SKILL_NAME),
            Candidate("personal / Claude Code", home / ".claude" / "skills" / SKILL_NAME),
            Candidate("personal / agents", home / ".agents" / "skills" / SKILL_NAME),
            Candidate("personal / OpenCode",
                      home / ".config" / "opencode" / "skills" / SKILL_NAME),
        ]

    candidates = []
    seen = set()
    for candidate in raw:
        key = os.path.normcase(str(candidate.path.resolve(strict=False)))
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return candidates


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return None, str(exc)


def _registry_entries(text):
    """Read the registry's signal/load structure, including wrapped inline load lists."""
    entries = []
    current = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        signal = re.match(r"-\s+signal:\s*([^\s#]+)", stripped)
        if signal:
            if current is not None:
                entries.append(current)
            current = {"signal": signal.group(1).strip("\"'"), "load": None}
        elif current is not None and stripped.startswith("load:"):
            value = stripped.partition(":")[2].strip()
            while "[" in value and "]" not in value and index + 1 < len(lines):
                index += 1
                value += " " + lines[index].strip()
            if value.startswith("[") and value.endswith("]"):
                current["load"] = [
                    item.strip().strip("\"'") for item in value[1:-1].split(",")
                    if item.strip()
                ]
        index += 1
    if current is not None:
        entries.append(current)
    return entries


def inspect_skill(candidate):
    """Inspect only SKILL.md and registry.yaml, plus existence of declared load targets."""
    skill_dir = candidate.path
    skill_errors = []
    registry_errors = []

    skill_text, error = _read_text(skill_dir / "SKILL.md")
    if error:
        skill_errors.append("SKILL.md is unreadable: %s" % error)
    elif not re.search(r"(?m)^name:\s*%s\s*$" % re.escape(SKILL_NAME), skill_text):
        skill_errors.append("SKILL.md does not declare name: %s" % SKILL_NAME)

    registry_text, error = _read_text(skill_dir / "registry.yaml")
    if error:
        registry_errors.append("registry.yaml is unreadable: %s" % error)
        return skill_errors, registry_errors, 0

    if not re.search(r"(?m)^version:\s*[^\s#]+", registry_text):
        registry_errors.append("registry.yaml has no top-level version")

    entries = _registry_entries(registry_text)
    if not entries:
        registry_errors.append("registry.yaml declares no signal entries")
    missing_loads = [entry["signal"] for entry in entries if entry["load"] is None]
    if missing_loads:
        registry_errors.append("registry entries have no inline load list: %s"
                               % ", ".join(missing_loads))

    resolved_root = skill_dir.resolve(strict=False)
    for entry in entries:
        for reference in entry["load"] or []:
            if not reference:
                continue
            target = (skill_dir / reference).resolve(strict=False)
            try:
                contained = os.path.commonpath((str(resolved_root), str(target))) == str(
                    resolved_root)
            except ValueError:
                contained = False
            if not contained:
                registry_errors.append("load target escapes the skill directory: %s" % reference)
            elif not target.is_file():
                registry_errors.append("load target does not exist: %s" % reference)

    return skill_errors, registry_errors, len(entries)


def _copy_remediation(project, platform=sys.platform):
    destination = _absolute(project) / ".claude" / "skills"
    if platform.startswith("win"):
        return (
            'New-Item -ItemType Directory -Force -Path "%s"' % destination,
            'Copy-Item -Recurse -Force "skills\\%s" "%s"' % (SKILL_NAME, destination),
            'py scripts\\doctor.py --project "%s"' % _absolute(project),
        )
    return (
        'mkdir -p "%s"' % destination,
        'cp -R "skills/%s" "%s/"' % (SKILL_NAME, destination),
        'python scripts/doctor.py --project "%s"' % _absolute(project),
    )


def check_python(version_info):
    version = tuple(version_info[:3])
    label = ".".join(str(part) for part in version)
    if version[:2] >= MINIMUM_PYTHON:
        return Check("Python", "PASS", "%s (requires 3.8+)" % label)
    return Check(
        "Python", "FAIL", "%s is below the required Python 3.8" % label,
        ("python3 --version", "Install Python 3.8+ and rerun this command."))


def check_installation(project, home, explicit=(), platform=sys.platform):
    candidates = discover_candidates(project, home, explicit)
    present = [candidate for candidate in candidates if candidate.path.is_dir()]
    remediation = _copy_remediation(project, platform)
    if not present:
        searched = ", ".join(str(candidate.path) for candidate in candidates)
        return [Check("Skill discovery", "FAIL", "no installation found; searched " + searched,
                      remediation)]

    inspected = []
    for candidate in present:
        skill_errors, registry_errors, entries = inspect_skill(candidate)
        inspected.append((candidate, skill_errors, registry_errors, entries))

    discovered = [item for item in inspected if not item[1]]
    checks = []
    if discovered:
        checks.append(Check(
            "Skill discovery", "PASS",
            "; ".join("%s: %s" % (item[0].scope, item[0].path) for item in discovered)))
    else:
        details = "; ".join(
            "%s: %s" % (item[0].path, ", ".join(item[1])) for item in inspected)
        checks.append(Check("Skill discovery", "FAIL", details, remediation))

    healthy = [item for item in discovered if not item[2]]
    if healthy:
        checks.append(Check(
            "Registry", "PASS",
            "; ".join("%s (%d signals)" % (item[0].path, item[3]) for item in healthy)))
    else:
        details = "; ".join(
            "%s: %s" % (item[0].path, ", ".join(item[2]))
            for item in inspected if item[2]
        ) or "no readable installed registry"
        checks.append(Check("Registry", "FAIL", details, remediation))

    unhealthy = [item for item in inspected if item[1] or item[2]]
    if healthy and unhealthy:
        checks.append(Check(
            "Duplicate installations", "WARN",
            "a healthy installation exists, but another copy is incomplete: %s"
            % "; ".join(str(item[0].path) for item in unhealthy),
            ("Remove or re-copy the incomplete duplicate so agents do not load a stale copy.",)))
    return checks


def check_output_directory(output_dir, platform=sys.platform):
    output_dir = _absolute(output_dir)
    if not output_dir.is_dir():
        command = ('New-Item -ItemType Directory -Force -Path "%s"' % output_dir
                   if platform.startswith("win") else 'mkdir -p "%s"' % output_dir)
        return Check(
            "Output directory", "FAIL", "%s is not an existing directory" % output_dir,
            (command,))
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".bpr-doctor-", dir=str(output_dir),
                delete=True) as handle:
            handle.write("writable\n")
            handle.flush()
    except OSError as exc:
        return Check(
            "Output directory", "FAIL", "%s is not writable: %s" % (output_dir, exc),
            ('python scripts/doctor.py --output "/path/to/writable/directory"',))
    return Check("Output directory", "PASS", "%s is writable" % output_dir)


def check_github_cli(requested, which=shutil.which, platform=sys.platform):
    if not requested:
        return Check("GitHub CLI", "SKIP", "not requested (use --github to check it)")
    executable = which("gh")
    if executable:
        return Check(
            "GitHub CLI", "PASS",
            "%s is available; authentication was deliberately not inspected" % executable)
    if platform.startswith("win"):
        install = "winget install --id GitHub.cli"
    elif platform == "darwin":
        install = "brew install gh"
    else:
        install = "sudo apt-get install gh"
    return Check(
        "GitHub CLI", "FAIL", "gh is not on PATH",
        (install, "See https://cli.github.com/ for other platforms."))


def run_checks(project, home, output_dir, explicit=(), github=False,
               version_info=sys.version_info, which=shutil.which, platform=sys.platform):
    checks = [check_python(version_info)]
    checks.extend(check_installation(project, home, explicit, platform))
    checks.append(check_output_directory(output_dir, platform))
    checks.append(check_github_cli(github, which, platform))
    return checks


def render_text(checks):
    lines = []
    for check in checks:
        lines.append("[%s] %s: %s" % (check.status, check.name, check.detail))
        for command in check.remediation:
            lines.append("       Try: %s" % command)
    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    lines.append("")
    lines.append("Doctor result: %d failure(s), %d warning(s)." % (failures, warnings))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, default=Path.cwd(),
                        help="project whose project-scoped skill directories are checked")
    parser.add_argument("--skill-dir", type=Path, action="append", default=[],
                        help="explicit skill directory; repeat to check more than one")
    parser.add_argument("--output", type=Path, default=Path.cwd(),
                        help="directory where review artifacts will be written")
    parser.add_argument("--github", action="store_true",
                        help="require the optional GitHub CLI executable")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)

    checks = run_checks(args.project, Path.home(), args.output, args.skill_dir, args.github)
    if args.json:
        json.dump({
            "ok": not any(check.status == "FAIL" for check in checks),
            "checks": [check._asdict() for check in checks],
        }, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(checks))
    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
