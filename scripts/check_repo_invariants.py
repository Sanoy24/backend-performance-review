#!/usr/bin/env python3
"""Verify the structural invariants CONTRIBUTING.md commits this repository to.

This is maintainer/CI tooling. It is not part of the skill itself and is not loaded by an
agent — the skill's own bundled script lives at
skills/backend-performance-review/scripts/detect_stack.py.

Run from the repository root:
    python scripts/check_repo_invariants.py

Exits non-zero on any failure, printing every failure found rather than stopping at the
first one, so a single CI run reports everything that needs fixing.
"""

import ast
import re
import sys
from pathlib import Path

# Force UTF-8 output regardless of the host console's default codepage. Without this, the
# em-dashes in this script's own messages get mangled on a Windows console using a non-UTF-8
# codepage, which is not the invariant being checked but is confusing when it happens.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "backend-performance-review"
DETECT_SCRIPT = SKILL / "scripts" / "detect_stack.py"

sys.path.insert(0, str(SKILL / "scripts"))
import detect_stack as detect  # noqa: E402

PRODUCT_NAME_PATTERN = re.compile(
    r"\b(postgres|postgresql|mysql|mariadb|mongodb|mongo|redis|cassandra|scylla|"
    r"dynamodb|neo4j|elasticsearch|opensearch|kafka|rabbitmq|sqlite|oracle|"
    r"cockroachdb|couchbase|firestore)\b",
    re.IGNORECASE,
)

REQUIRED_TECH_SECTIONS = [
    "1. Detection signals",
    "2. What differs from",
    "3. Diagnostics",
    "4. Common failure modes",
    "5. Configuration",
    "6. Version differences",
    "7. What this file does NOT cover",
]

LINE_SOFT_CAP = 400

failures = []
warnings = []


def fail(msg):
    failures.append(msg)


def warn(msg):
    warnings.append(msg)


# ---------------------------------------------------------------------------
# 1. registry.yaml parses cleanly, every entry complete, every reference resolves
# ---------------------------------------------------------------------------

def check_registry():
    entries, parse_warnings = detect.parse_registry(str(SKILL / "registry.yaml"))
    if parse_warnings:
        fail(f"registry.yaml: parser warnings: {parse_warnings}")
    if not entries:
        fail("registry.yaml: no entries parsed")
        return entries

    for entry in entries:
        signal = entry.get("signal", "<unnamed>")
        if not entry.get("load"):
            fail(f"registry.yaml: '{signal}' has no load list")
        if not entry.get("tier"):
            fail(f"registry.yaml: '{signal}' has no tier")
        if entry.get("tier") not in (None, "deep", "conceptual", "generic"):
            fail(f"registry.yaml: '{signal}' has invalid tier '{entry.get('tier')}'")

        load = entry.get("load", [])
        for ref in load:
            if not (SKILL / ref).exists():
                fail(f"registry.yaml: '{signal}' references missing file '{ref}'")

        tech_positions = [i for i, r in enumerate(load) if r.startswith("technology/")]
        other_positions = [i for i, r in enumerate(load) if not r.startswith("technology/")]
        if tech_positions and other_positions and min(tech_positions) < max(other_positions):
            fail(f"registry.yaml: '{signal}' loads a technology/ file before a "
                 f"category/principle file — category must come first")

    return entries


# ---------------------------------------------------------------------------
# 2. Database category files must never name a specific product
#
# This rule is scoped to databases/*.md only (CONTRIBUTING.md "The non-derivable content
# rule", docs/architecture.md "Category files never name a product"). It does not apply to
# methodology/ (which legitimately names connection-string schemes like `redis://` as
# detection signals) or to SKILL.md/methodology's anti-cargo-cult examples (which
# legitimately name Redis, Postgres, etc. as things not to recommend reflexively).
# ---------------------------------------------------------------------------

def check_no_product_names_leaked():
    dir_path = SKILL / "databases"
    if not dir_path.is_dir():
        fail("skills/backend-performance-review/databases/ does not exist")
        return
    for md_file in dir_path.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        match = PRODUCT_NAME_PATTERN.search(text)
        if match:
            line_no = text[:match.start()].count("\n") + 1
            fail(f"{md_file.relative_to(ROOT)}:{line_no}: names a specific product "
                 f"('{match.group(0)}') — category files must stay technology-agnostic; "
                 f"this belongs in technology/")


# ---------------------------------------------------------------------------
# 3. Every technology file has all seven mandatory sections
# ---------------------------------------------------------------------------

def check_technology_file_structure():
    tech_dir = SKILL / "technology"
    if not tech_dir.is_dir():
        fail("skills/backend-performance-review/technology/ does not exist")
        return
    files = sorted(tech_dir.glob("*.md"))
    if not files:
        fail("no technology files found")
    for md_file in files:
        text = md_file.read_text(encoding="utf-8")
        for section in REQUIRED_TECH_SECTIONS:
            if section not in text:
                fail(f"{md_file.relative_to(ROOT)}: missing required section '{section}'")


# ---------------------------------------------------------------------------
# 4. Reference files should stay under the soft line-count cap
# ---------------------------------------------------------------------------

def check_line_counts():
    for md_file in SKILL.rglob("*.md"):
        if md_file.name == "rubrics.md":
            continue  # explicitly exempted: expanded worked-example reference
        line_count = sum(1 for _ in md_file.open(encoding="utf-8"))
        if line_count > LINE_SOFT_CAP:
            warn(f"{md_file.relative_to(ROOT)}: {line_count} lines, over the "
                 f"{LINE_SOFT_CAP}-line soft cap — consider splitting or trimming")


# ---------------------------------------------------------------------------
# 5. Examples must live outside skills/ — never on the loadable reference path
# ---------------------------------------------------------------------------

def check_examples_not_loadable():
    stray = list(SKILL.rglob("*example*"))
    if stray:
        for path in stray:
            fail(f"{path.relative_to(ROOT)}: example content must live in docs/examples/, "
                 f"never under skills/, so it cannot be loaded as a reference")
    if not (ROOT / "docs" / "examples").is_dir():
        fail("docs/examples/ does not exist")
    elif not list((ROOT / "docs" / "examples").glob("*.md")):
        warn("docs/examples/ has no example files")


# ---------------------------------------------------------------------------
# 6. The priority matrix must be identical everywhere it is published
# ---------------------------------------------------------------------------

def extract_matrix_rows(text):
    lines = text.splitlines()
    rows = []
    capture = False
    for line in lines:
        if "Severity ＼ Confidence" in line:
            capture = True
            continue
        if capture:
            if line.strip().startswith("|"):
                rows.append(line.strip())
            elif rows:
                break
    return rows


def check_priority_matrix_consistency():
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    skill_rows = extract_matrix_rows(skill_text)
    readme_rows = extract_matrix_rows(readme_text)
    if not skill_rows:
        fail("SKILL.md: could not find the priority matrix table")
    if not readme_rows:
        fail("README.md: could not find the priority matrix table")
    if skill_rows and readme_rows and skill_rows != readme_rows:
        fail("The priority matrix in SKILL.md and README.md do not match. "
             "Priority must be derived identically everywhere it is published.")


# ---------------------------------------------------------------------------
# 7. The shipped detection script must import only the standard library
# ---------------------------------------------------------------------------

def check_detect_script_stdlib_only():
    stdlib_names = getattr(sys, "stdlib_module_names", None)
    if stdlib_names is None:
        warn("Python < 3.10: cannot verify stdlib-only imports automatically; "
             "check scripts/detect_stack.py's imports by hand")
        return

    tree = ast.parse(DETECT_SCRIPT.read_text(encoding="utf-8"), filename=str(DETECT_SCRIPT))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                continue
            modules = [node.module.split(".")[0]] if node.module else []
        else:
            continue
        for module in modules:
            if module not in stdlib_names and module != "detect_stack":
                fail(f"detect_stack.py imports non-stdlib module '{module}' — the "
                     f"script must run with the standard library only")


# ---------------------------------------------------------------------------
# 8. The published tier summary must match the registry's actual tier counts
# ---------------------------------------------------------------------------

def check_tier_summary_counts(entries):
    from collections import Counter
    tiers = Counter(e.get("tier") for e in entries)
    summary_file = ROOT / "docs" / "supported-technologies.md"
    text = summary_file.read_text(encoding="utf-8")
    match = re.search(
        r"(\d+)\s+deep\D+?(\d+)\s+conceptual\D+?(\d+)\s+generic",
        text,
    )
    if not match:
        warn(f"{summary_file.relative_to(ROOT)}: could not find the "
             f"'N deep / N conceptual / N generic' summary line to check against the registry")
        return
    published_counts = tuple(int(g) for g in match.groups())
    registry_counts = (tiers.get("deep", 0), tiers.get("conceptual", 0), tiers.get("generic", 0))
    if published_counts != registry_counts:
        fail(f"{summary_file.relative_to(ROOT)} tier summary {published_counts} does not "
             f"match registry.yaml {registry_counts} (deep, conceptual, generic)")


# ---------------------------------------------------------------------------
# 9. Every finding-format Category value resolves to a reference-routing table row
#
# The finding format's `Category:` enum in SKILL.md's "## Finding format" section and the
# "## Reference routing" table's Category column are maintained by hand in two different
# places. Nothing else keeps them in sync: an agent that classifies a finding as
# `Category: observability` with no routing row pointing anywhere for it has nothing to
# load — a silent gap the architecture self-check (docs/evaluation.md §1) is explicitly
# supposed to catch and did not, until this check existed.
# ---------------------------------------------------------------------------

def check_category_routing_coverage():
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    category_match = re.search(
        r"^Category:\s+(.+(?:\n\s{4,}.+)*)", skill_text, re.MULTILINE)
    if not category_match:
        fail("SKILL.md: could not find the 'Category:' enum line in ## Finding format")
        return
    declared = {tok.strip() for tok in category_match.group(1).split("|") if tok.strip()}

    routing_match = re.search(
        r"## Reference routing\n(.*?)\n## ", skill_text, re.DOTALL)
    if not routing_match:
        fail("SKILL.md: could not find the '## Reference routing' section")
        return
    routing_text = routing_match.group(1)

    table_rows = re.findall(
        r"^\|(.+)\|(.+)\|(.+)\|$", routing_text, re.MULTILINE)
    # First two matches are the header and the '---' separator row.
    data_rows = table_rows[2:]
    if not data_rows:
        fail("SKILL.md: reference-routing table has no Category column to check — "
             "expected a three-column '| When | Load | Category |' table")
        return

    routed = set()
    for _when, _load, category_cell in data_rows:
        for tok in category_cell.split(","):
            tok = tok.strip().strip("`")
            if tok and tok != "—":
                routed.add(tok)

    missing = declared - routed
    if missing:
        fail("SKILL.md: Category value(s) with no reference-routing row to load from: "
             + ", ".join(sorted(missing))
             + " — add a Category-column entry in '## Reference routing' pointing at the "
               "file that should be loaded for a finding of that category")

    stray = routed - declared
    if stray:
        fail("SKILL.md: reference-routing table's Category column names value(s) not in "
             "the Category: enum: " + ", ".join(sorted(stray))
             + " — the enum and the routing table have drifted apart")


# ---------------------------------------------------------------------------

def main():
    entries = check_registry()
    check_no_product_names_leaked()
    check_technology_file_structure()
    check_line_counts()
    check_examples_not_loadable()
    check_priority_matrix_consistency()
    check_detect_script_stdlib_only()
    check_category_routing_coverage()
    if entries:
        check_tier_summary_counts(entries)

    if warnings:
        print(f"{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  WARN  {w}")
        print()

    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1

    print("All repository invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
