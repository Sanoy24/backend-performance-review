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

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})
SUPPORTED_SPECS = frozenset({"backend-performance-review/2.0"})


def _object(value):
    return value if isinstance(value, dict) else {}


def _array(value):
    return value if isinstance(value, list) else []


def derive_verdict(review):
    """Return the only verdict the review contents permit, or None for a full review."""
    review = _object(review)
    if review.get("mode") != "change-scoped":
        return None

    unknowns = _array(_object(review.get("completeness")).get("unknowns"))
    if any(item.get("reason") in ("technology-unsupported", "not-examined")
           for item in unknowns if isinstance(item, dict)):
        return "UNKNOWN"

    findings = _array(review.get("findings"))
    if not findings:
        return "PASS"
    if any(finding.get("severity") in ("Critical", "High")
           and finding.get("confidence") in ("Confirmed", "High")
           for finding in findings if isinstance(finding, dict)):
        return "FAIL"
    return "WARN"


def review_summary(review):
    """Return the publishable summary; its verdict is always mechanically derived."""
    review = _object(review)
    derived = derive_verdict(review)
    return {
        "mode": review.get("mode"),
        "verdict": derived,
        "declared_verdict": review.get("verdict"),
        "derived_verdict": derived,
        "findings": len(_array(review.get("findings"))),
    }


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


def _semantic_problems(review):
    problems = []
    review = _object(review)

    schema_version = review.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(
            "unsupported schema_version %r; supported: %s. See CHANGELOG.md for migration "
            "guidance" % (schema_version, ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))))

    spec = _object(review.get("reproducibility")).get("spec")
    if spec not in SUPPORTED_SPECS:
        problems.append(
            "unsupported methodology spec %r; supported: %s. Re-run the review with the "
            "current skills/backend-performance-review/SKILL.md"
            % (spec, ", ".join(sorted(SUPPORTED_SPECS))))

    mode = review.get("mode")
    declared_verdict = review.get("verdict")
    derived_verdict = derive_verdict(review)
    if mode == "full" and declared_verdict is not None:
        problems.append("full reviews must not declare verdict; verdict is change-scoped only")
    elif mode == "change-scoped":
        if declared_verdict is None:
            problems.append(
                "change-scoped reviews must declare the mechanically derived verdict %s"
                % derived_verdict)
        elif declared_verdict != derived_verdict:
            problems.append(
                "verdict mismatch: review declares %s, but its contents derive %s"
                % (declared_verdict, derived_verdict))

    completeness = _object(review.get("completeness"))
    unknowns = [item for item in _array(completeness.get("unknowns"))
                if isinstance(item, dict)]
    records_incomplete_coverage = any(
        item.get("reason") == "not-examined" for item in unknowns)
    for label, prefix in (("critical paths", "critical_paths"),
                          ("shared resources", "shared_resources")):
        identified = completeness.get(prefix + "_identified")
        analyzed = completeness.get(prefix + "_analyzed")
        identified_is_int = isinstance(identified, int) and not isinstance(identified, bool)
        analyzed_is_int = isinstance(analyzed, int) and not isinstance(analyzed, bool)
        if identified_is_int != analyzed_is_int:
            problems.append(
                "%s coverage must declare both %s_identified and %s_analyzed"
                % (label, prefix, prefix))
        elif identified_is_int and analyzed_is_int:
            if analyzed > identified:
                problems.append(
                    "%s coverage analyzes %d but identifies only %d"
                    % (label, analyzed, identified))
            elif analyzed < identified and not records_incomplete_coverage:
                problems.append(
                    "%s coverage analyzes %d of %d without a completeness unknown whose "
                    "reason is not-examined"
                    % (label, analyzed, identified))
    findings = [item for item in _array(review.get("findings")) if isinstance(item, dict)]
    finding_by_id = {}
    for finding in findings:
        finding_id = finding.get("id")
        if finding_id in finding_by_id:
            problems.append("duplicate finding id %s" % finding_id)
        elif finding_id:
            finding_by_id[finding_id] = finding

    causes = [item for item in _array(review.get("root_causes")) if isinstance(item, dict)]
    cause_by_id = {}
    for cause in causes:
        cause_id = cause.get("id")
        if cause_id in cause_by_id:
            problems.append("duplicate root cause id %s" % cause_id)
        elif cause_id:
            cause_by_id[cause_id] = cause

    for finding in findings:
        finding_id = finding.get("id")
        cause_id = finding.get("root_cause_id")
        if not cause_id:
            continue
        cause = cause_by_id.get(cause_id)
        if cause is None:
            problems.append("%s references %s, which no root_causes[] entry declares"
                            % (finding_id, cause_id))
        elif finding_id not in _array(cause.get("findings")):
            problems.append(
                "%s references %s, but that root cause does not list %s in findings[]"
                % (finding_id, cause_id, finding_id))

    for cause in causes:
        cause_id = cause.get("id")
        for finding_id in _array(cause.get("findings")):
            finding = finding_by_id.get(finding_id)
            if finding is None:
                problems.append("%s references %s, which no findings[] entry declares"
                                % (cause_id, finding_id))
            elif finding.get("root_cause_id") != cause_id:
                problems.append(
                    "%s lists %s, but that finding points to root cause %s"
                    % (cause_id, finding_id, finding.get("root_cause_id")))

    runtime_by_id = {}
    for artifact in _array(review.get("runtime_evidence")):
        if not isinstance(artifact, dict):
            continue
        artifact_id = artifact.get("id")
        if artifact_id in runtime_by_id:
            problems.append("duplicate runtime evidence id %s" % artifact_id)
        elif artifact_id:
            runtime_by_id[artifact_id] = artifact

    for finding in findings:
        finding_id = finding.get("id")
        valid_runtime_citations = 0
        for evidence in _array(finding.get("evidence")):
            if not isinstance(evidence, dict):
                continue
            reference = evidence.get("runtime_evidence_id")
            if evidence.get("kind") == "runtime":
                if not reference:
                    problems.append(
                        "%s has runtime evidence without runtime_evidence_id" % finding_id)
                elif reference not in runtime_by_id:
                    problems.append(
                        "%s cites %s, which no review.runtime_evidence[] artifact declares"
                        % (finding_id, reference))
                else:
                    valid_runtime_citations += 1
            elif reference:
                problems.append(
                    "%s uses runtime_evidence_id %s on non-runtime evidence"
                    % (finding_id, reference))
        if finding.get("confidence") == "Confirmed" and not valid_runtime_citations:
            problems.append(
                "%s claims Confirmed confidence but cites no valid runtime artifact; "
                "Confirmed requires finding evidence with kind=runtime and "
                "runtime_evidence_id" % finding_id)

    return problems


def validate(review, schema_dir):
    problems = list(schema_lite.validate_file(review, schema_dir / "review.schema.json"))
    if not isinstance(review, dict):
        return problems
    problems.extend(_semantic_problems(review))

    skill_md = find_skill_md(schema_dir)
    if skill_md:
        matrix = parse_priority_matrix(skill_md.read_text(encoding="utf-8"))
        for finding in _array(review.get("findings")):
            if not isinstance(finding, dict):
                continue
            expected = matrix.get((finding.get("severity"), finding.get("confidence")))
            if expected and finding.get("priority") != expected:
                problems.append(
                    "%s is %s/%s, which the matrix derives as %s, but it declares %s"
                    % (finding.get("id"), finding.get("severity"), finding.get("confidence"),
                       expected, finding.get("priority")))

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
        for finding in _array(review.get("findings")):
            if not isinstance(finding, dict):
                continue
            if (not isinstance(finding.get("location"), dict)
                    or not isinstance(finding.get("category"), str)):
                # Shape errors are already reported by the schema validator; the canonical
                # algorithm intentionally assumes its documented input shape.
                continue
            expected = compute_stable_id.compute_for_finding(finding)
            actual = finding.get("stable_id")
            if actual != expected:
                problems.append(
                    "%s has stable_id %r, but the canonical algorithm "
                    "(scripts/compute_stable_id.py) computes %r from its own location/"
                    "category — recompute it, do not hand-invent one"
                    % (finding.get("id"), actual, expected))
            if isinstance(actual, str) and actual in seen_stable_ids:
                problems.append(
                    "%s and %s share stable_id %r — a known, accepted collision case "
                    "(same file, symbol, and category) but worth a human glance to confirm "
                    "they are not actually the same finding reported twice"
                    % (seen_stable_ids[actual], finding.get("id"), actual))
            elif isinstance(actual, str) and actual:
                seen_stable_ids[actual] = finding.get("id")

    return problems


def validate_and_summarize(review, schema_dir):
    """Authoritative publishability result used by every output adapter."""
    return validate(review, schema_dir), review_summary(review)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", required=True)
    parser.add_argument("--schema-dir", default=str(HERE.parent / "schemas"))
    parser.add_argument("--summary-json", action="store_true",
                        help="write the authoritative derived summary as JSON")
    args = parser.parse_args(argv)

    with open(args.review, encoding="utf-8") as handle:
        try:
            review = json.load(handle)
        except json.JSONDecodeError as exc:
            print("not valid JSON: %s" % exc, file=sys.stderr)
            return 1

    problems, summary = validate_and_summarize(review, Path(args.schema_dir))
    if problems:
        print("%d problem(s) in %s:" % (len(problems), args.review), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1

    if args.summary_json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print("%s is a valid review (%d finding(s))"
              % (args.review, summary["findings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
