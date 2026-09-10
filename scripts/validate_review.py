#!/usr/bin/env python3
"""Validate a machine-readable review against the schema, and against the priority matrix.

Standard library only.

    python scripts/validate_review.py --review review.json
    python scripts/validate_review.py --review review.json --schema-dir ./schemas

Exists as its own entry point because two different callers need it: the GitHub Action,
which must reject malformed output before publishing it anywhere, and anyone checking a
review by hand. Catching a bad review here is much cheaper than discovering it after it has
been posted to a pull request.

The priority check is not in the schema on purpose. The matrix is published in SKILL.md and
enforced from there, so it lives in exactly one place — see docs/architecture.md §11.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import json_schema_lite as schema_lite  # noqa: E402


def find_skill_scripts_dir(schema_dir):
    for candidate in (
        schema_dir.parent / "skills" / "backend-performance-review" / "scripts",
        HERE.parent / "skills" / "backend-performance-review" / "scripts",
    ):
        if (candidate / "compute_stable_id.py").is_file():
            return candidate
    return None


def parse_priority_matrix(skill_text):
    """Read {(severity, confidence): priority} out of the table SKILL.md publishes."""
    lines = skill_text.splitlines()
    matrix = {}
    for index, line in enumerate(lines):
        if "Severity ＼ Confidence" not in line:
            continue
        confidences = [c.strip() for c in line.strip().strip("|").split("|")][1:]
        for row in lines[index + 1:]:
            row = row.strip()
            if not row.startswith("|"):
                break
            cells = [c.strip() for c in row.strip("|").split("|")]
            if not cells or set(cells[0]) <= set(":- "):
                continue
            for confidence, priority in zip(confidences, cells[1:]):
                matrix[(cells[0], confidence)] = priority
        break
    return matrix


def find_skill_md(schema_dir):
    for candidate in (
        schema_dir.parent / "skills" / "backend-performance-review" / "SKILL.md",
        HERE.parent / "skills" / "backend-performance-review" / "SKILL.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def validate(review, schema_dir):
    problems = list(schema_lite.validate_file(review, schema_dir / "review.schema.json"))

    skill_md = find_skill_md(schema_dir)
    if skill_md:
        matrix = parse_priority_matrix(skill_md.read_text(encoding="utf-8"))
        for finding in review.get("findings") or []:
            expected = matrix.get((finding.get("severity"), finding.get("confidence")))
            if expected and finding.get("priority") != expected:
                problems.append(
                    "%s is %s/%s, which the matrix derives as %s, but it declares %s"
                    % (finding.get("id"), finding.get("severity"), finding.get("confidence"),
                       expected, finding.get("priority")))

    declared_causes = {c.get("id") for c in review.get("root_causes") or []}
    for finding in review.get("findings") or []:
        if finding.get("root_cause_id") and finding["root_cause_id"] not in declared_causes:
            problems.append("%s references %s, which no root_causes[] entry declares"
                            % (finding.get("id"), finding["root_cause_id"]))

    # A Confirmed grade asserts a cited runtime artifact exists. If the review says it had
    # none, the two statements cannot both be true.
    if not (review.get("runtime_evidence") or []):
        for finding in review.get("findings") or []:
            if finding.get("confidence") == "Confirmed":
                problems.append(
                    "%s claims Confirmed confidence, but the review lists no runtime "
                    "evidence — Confirmed requires a cited runtime artifact"
                    % finding.get("id"))

    seen_ids = set()
    for finding in review.get("findings") or []:
        if finding.get("id") in seen_ids:
            problems.append("duplicate finding id %s" % finding.get("id"))
        seen_ids.add(finding.get("id"))

    # stable_id must be the canonical, mechanically-computed value — not one the reviewing
    # agent invented by reasoning. The first real independent blind-pass run found two
    # agents disagreeing completely on a hand-computed stable_id despite agreeing on
    # everything else the id is supposed to track; this check is what makes that
    # mechanically impossible to ship silently going forward.
    scripts_dir = find_skill_scripts_dir(schema_dir)
    if scripts_dir:
        sys.path.insert(0, str(scripts_dir))
        import compute_stable_id  # noqa: E402
        seen_stable_ids = {}
        for finding in review.get("findings") or []:
            expected = compute_stable_id.compute_for_finding(finding)
            actual = finding.get("stable_id")
            if actual != expected:
                problems.append(
                    "%s has stable_id %r, but the canonical algorithm "
                    "(scripts/compute_stable_id.py) computes %r from its own location/"
                    "category — recompute it, do not hand-invent one"
                    % (finding.get("id"), actual, expected))
            if actual in seen_stable_ids:
                problems.append(
                    "%s and %s share stable_id %r — a known, accepted collision case "
                    "(same file, symbol, and category) but worth a human glance to confirm "
                    "they are not actually the same finding reported twice"
                    % (seen_stable_ids[actual], finding.get("id"), actual))
            elif actual:
                seen_stable_ids[actual] = finding.get("id")

    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", required=True)
    parser.add_argument("--schema-dir", default=str(HERE.parent / "schemas"))
    args = parser.parse_args(argv)

    with open(args.review, encoding="utf-8") as handle:
        try:
            review = json.load(handle)
        except json.JSONDecodeError as exc:
            print("not valid JSON: %s" % exc, file=sys.stderr)
            return 1

    problems = validate(review, Path(args.schema_dir))
    if problems:
        print("%d problem(s) in %s:" % (len(problems), args.review), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1

    print("%s is a valid review (%d finding(s))"
          % (args.review, len(review.get("findings") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
