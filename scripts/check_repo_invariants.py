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
import json
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

sys.path.insert(0, str(ROOT / "scripts"))
import json_schema_lite as schema_lite  # noqa: E402

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
# 10. Every published version string agrees, and matches the newest CHANGELOG entry
#
# The version is declared independently in five places: the plugin manifest, both fields
# of the marketplace manifest, SKILL.md's frontmatter, and the README badge. Nothing keeps
# them in sync by construction, and a stale one is easy to miss because the skill still
# works — it just tells users the wrong version. v0.3.0 shipped with the README badge and
# two docs still reading v0.1.0/v0.2.0 before this check existed.
# ---------------------------------------------------------------------------

def check_version_coherence():
    changelog_text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog_text, re.MULTILINE)
    if not changelog_match:
        fail("CHANGELOG.md: could not find a '## [X.Y.Z]' heading to determine "
             "the current version")
        return
    current = changelog_match.group(1)

    sources = {}

    plugin_json = ROOT / ".claude-plugin" / "plugin.json"
    sources[f"{plugin_json.relative_to(ROOT)} version"] = json.loads(
        plugin_json.read_text(encoding="utf-8"))["version"]

    marketplace_json = ROOT / ".claude-plugin" / "marketplace.json"
    marketplace = json.loads(marketplace_json.read_text(encoding="utf-8"))
    sources[f"{marketplace_json.relative_to(ROOT)} version"] = marketplace["version"]
    sources[f"{marketplace_json.relative_to(ROOT)} plugins[0].version"] = (
        marketplace["plugins"][0]["version"])

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    skill_match = re.search(r"^\s*version:\s*(\d+\.\d+\.\d+)\s*$", skill_text, re.MULTILINE)
    if skill_match:
        sources["SKILL.md metadata.version"] = skill_match.group(1)
    else:
        fail("SKILL.md: could not find 'metadata.version' in the frontmatter")

    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_match = re.search(r"badge/version-(\d+\.\d+\.\d+)-", readme_text)
    if readme_match:
        sources["README.md version badge"] = readme_match.group(1)
    else:
        fail("README.md: could not find the version badge to check")

    for label, value in sources.items():
        if value != current:
            fail(f"{label} is '{value}', but CHANGELOG.md's newest entry is "
                 f"'{current}' — these must match")


# ---------------------------------------------------------------------------
# 11. Every `deep`-tier entry loads exactly one technology/ file, and every
#     technology/*.md file is loaded by exactly one `deep`-tier entry
#
# check_registry() already verifies every load[] path resolves to a real file. This check
# verifies the other direction of the deep-tier promise: a `deep` entry with no
# technology/ file in its load list is mistagged (it's actually conceptual), a
# non-`deep` entry loading one is under-tagged, and an orphan technology/*.md file nothing
# points to is dead weight nothing would ever load.
# ---------------------------------------------------------------------------

def check_technology_registry_consistency(entries):
    tech_dir = SKILL / "technology"
    all_tech_files = {f"technology/{p.name}" for p in tech_dir.glob("*.md")}
    loaded_tech_files = set()

    for entry in entries:
        signal = entry.get("signal", "<unnamed>")
        tier = entry.get("tier")
        tech_refs = [r for r in entry.get("load", []) if r.startswith("technology/")]

        if tier == "deep" and not tech_refs:
            fail(f"registry.yaml: '{signal}' is tier: deep but loads no technology/ "
                 f"file — either write one or demote the tier")
        if tier != "deep" and tech_refs:
            fail(f"registry.yaml: '{signal}' is tier: {tier} but loads "
                 f"{tech_refs} — a technology/ file in the load list means it should be "
                 f"tier: deep")

        loaded_tech_files.update(tech_refs)

    orphans = all_tech_files - loaded_tech_files
    if orphans:
        fail(f"technology/ file(s) not loaded by any registry entry: "
             f"{sorted(orphans)} — either wire them into registry.yaml or remove them")


# ---------------------------------------------------------------------------
# 12. The roadmap's promotion candidates must not name an already-`deep` signal
#
# Promoting a technology and forgetting to remove it from docs/roadmap.md's "Technology
# promotion candidates" list is a real, previously observed failure: Elasticsearch and
# Cassandra both stayed listed there for a full release after their promotion to `deep`.
# ---------------------------------------------------------------------------

def check_roadmap_freshness(entries):
    roadmap_file = ROOT / "docs" / "roadmap.md"
    if not roadmap_file.exists():
        fail("docs/roadmap.md does not exist")
        return
    text = roadmap_file.read_text(encoding="utf-8")

    section_match = re.search(
        r"## Technology promotion candidates\n(.*?)\n## ", text, re.DOTALL)
    if not section_match:
        fail("docs/roadmap.md: could not find the "
             "'## Technology promotion candidates' section")
        return
    section = section_match.group(1)

    # Only the bolded bullet headers are actual candidates — surrounding prose legitimately
    # mentions other engines for comparison (e.g. "differs from PostgreSQL/MySQL") or as
    # historical context (a promotion noted as no longer listed), and must not be checked.
    bullet_headers = " ".join(
        re.findall(r"^-\s+\*\*(.+?)\*\*", section, re.MULTILINE))

    for entry in entries:
        if entry.get("tier") != "deep":
            continue
        signal = entry.get("signal", "")
        # Short tokens (e.g. 'go') would false-positive against ordinary prose, so only
        # check tokens long enough to be an unambiguous product-name match.
        candidates = [signal] + [m for m in entry.get("match", []) if len(m) >= 5]
        for token in candidates:
            if len(token) < 5:
                continue
            if re.search(r"\b" + re.escape(token) + r"\b", bullet_headers, re.IGNORECASE):
                fail(f"docs/roadmap.md: 'Technology promotion candidates' still names "
                     f"'{token}', but registry.yaml's '{signal}' signal is already "
                     f"tier: deep — remove it from the roadmap")
                break


# ---------------------------------------------------------------------------
# 14. The finding schema must agree with SKILL.md, and the worked example must validate
#
# The schema restates enums that SKILL.md also publishes (severity, confidence, priority,
# category, tags). Restating them is what makes the JSON output self-describing, and it is
# also exactly how the two drift apart: a rubric edit in SKILL.md leaves the schema behind,
# and reports keep validating against a scale nobody uses any more. So every shared enum is
# checked in both directions here.
#
# The severity/confidence -> priority matrix is deliberately NOT duplicated into the schema.
# JSON Schema can express it, as twenty if/then branches, but then the matrix would exist in
# three places instead of two. It is enforced here instead, read from the table SKILL.md
# already publishes — the same single-source-of-truth argument check_priority_matrix_
# consistency() makes for the README copy.
# ---------------------------------------------------------------------------

SCHEMAS = ROOT / "schemas"
EXAMPLE_REVIEW = ROOT / "docs" / "examples" / "review.example.json"


def _load_json(path, label):
    if not path.is_file():
        fail(f"{label}: {path.relative_to(ROOT)} does not exist")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{label}: {path.relative_to(ROOT)} is not valid JSON — {exc}")
        return None


def _backticked_first_column(section):
    """Rubric tables in SKILL.md put the level in a backticked first cell."""
    return [m for m in re.findall(r"^\|\s*`([^`]+)`\s*\|", section, re.MULTILINE)]


def _skill_section(skill_text, heading):
    match = re.search(
        r"^%s\n(.*?)(?=^#{2,3} )" % re.escape(heading),
        skill_text, re.DOTALL | re.MULTILINE)
    return match.group(1) if match else ""


def parse_priority_matrix(skill_text):
    """Return {(severity, confidence): priority} from SKILL.md's published matrix."""
    lines = skill_text.splitlines()
    confidences, matrix = [], {}
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
                continue  # the |:--|:--| separator row
            severity = cells[0]
            for confidence, priority in zip(confidences, cells[1:]):
                matrix[(severity, confidence)] = priority
        break
    return matrix


def check_finding_schema_agrees_with_skill():
    finding_schema = _load_json(SCHEMAS / "finding.schema.json", "finding schema")
    review_schema = _load_json(SCHEMAS / "review.schema.json", "review schema")
    if finding_schema is None or review_schema is None:
        return None, None

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    properties = finding_schema.get("properties", {})

    category_match = re.search(
        r"^Category:\s+(.+(?:\n\s{4,}.+)*)", skill_text, re.MULTILINE)
    if category_match:
        declared = {tok.strip() for tok in category_match.group(1).split("|") if tok.strip()}
        in_schema = set(properties.get("category", {}).get("enum", []))
        for missing in sorted(declared - in_schema):
            fail(f"schemas/finding.schema.json: category enum is missing '{missing}', "
                 f"which SKILL.md's Category: line declares")
        for stray in sorted(in_schema - declared):
            fail(f"schemas/finding.schema.json: category enum has '{stray}', which is not "
                 f"in SKILL.md's Category: line — the two have drifted apart")

    for heading, field in (("### Confidence — an evidence grade", "confidence"),
                           ("### Severity — from four factors", "severity")):
        levels = _backticked_first_column(_skill_section(skill_text, heading))
        if not levels:
            fail(f"SKILL.md: could not read the levels out of '{heading}'")
            continue
        in_schema = properties.get(field, {}).get("enum", [])
        if levels != in_schema:
            fail(f"schemas/finding.schema.json: {field} enum {in_schema} does not match "
                 f"SKILL.md's {levels} — a rubric level was added, removed, or renamed "
                 f"without updating the schema")

    matrix = parse_priority_matrix(skill_text)
    if not matrix:
        fail("SKILL.md: could not parse the priority matrix")
        return finding_schema, review_schema

    priorities = sorted(set(matrix.values()))
    in_schema = sorted(properties.get("priority", {}).get("enum", []))
    if priorities != in_schema:
        fail(f"schemas/finding.schema.json: priority enum {in_schema} does not match the "
             f"priorities the matrix can produce, {priorities}")

    return finding_schema, review_schema


def check_example_review_validates(review_schema):
    """The worked example is the only place a full review document is committed, so it is
    the only thing that can prove the schema describes a review someone could actually
    write. It lives in docs/examples/ — outside skills/, so it can never be loaded as a
    reference (docs/architecture.md §9) — and it deliberately contains no invented runtime
    numbers, for the same reason."""
    if review_schema is None:
        return
    instance = _load_json(EXAMPLE_REVIEW, "example review")
    if instance is None:
        return

    errors = schema_lite.validate(
        instance, review_schema, base_dir=SCHEMAS, filename="review.schema.json")
    for error in errors[:20]:
        fail(f"docs/examples/review.example.json: {error}")
    if len(errors) > 20:
        fail(f"docs/examples/review.example.json: {len(errors) - 20} further "
             f"schema error(s) not shown")

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    matrix = parse_priority_matrix(skill_text)
    declared_causes = {c.get("id") for c in instance.get("root_causes", [])}
    for finding in instance.get("findings", []):
        key = (finding.get("severity"), finding.get("confidence"))
        expected = matrix.get(key)
        if expected and finding.get("priority") != expected:
            fail(f"docs/examples/review.example.json: {finding.get('id')} is "
                 f"{key[0]}/{key[1]}, which the matrix derives as {expected}, "
                 f"but it declares {finding.get('priority')}")
        if finding.get("root_cause_id") not in declared_causes:
            fail(f"docs/examples/review.example.json: {finding.get('id')} references "
                 f"{finding.get('root_cause_id')}, which no root_causes[] entry declares")


def check_schema_is_referenced():
    """A schema nothing points at is a schema nobody will keep current."""
    template = SKILL / "templates" / "review-report.md"
    if template.is_file() and "finding.schema.json" not in template.read_text(encoding="utf-8"):
        fail("templates/review-report.md does not mention finding.schema.json — the report "
             "template is where an agent learns the machine-readable output exists")


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
    check_version_coherence()
    _finding_schema, review_schema = check_finding_schema_agrees_with_skill()
    check_example_review_validates(review_schema)
    check_schema_is_referenced()
    if entries:
        check_tier_summary_counts(entries)
        check_technology_registry_consistency(entries)
        check_roadmap_freshness(entries)

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
