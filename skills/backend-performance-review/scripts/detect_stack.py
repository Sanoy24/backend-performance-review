#!/usr/bin/env python3
"""Detect the technology stack of a backend repository.

An accelerator for the backend-performance-review skill, never a dependency: if this
script is unavailable or fails, the skill falls back to manual inspection as described
in methodology/discovery.md.

Design constraints:
  * Python 3.8+, standard library only. No third-party imports, no network access.
  * Read-only. Nothing is created, modified, or deleted.
  * Never reads files that look like secrets; their presence is noted, contents are not.
  * Emits JSON on stdout. Diagnostics go to stderr so stdout stays parseable.

Usage:
    python detect_stack.py [REPO_PATH] [--registry PATH] [--max-bytes N] [--pretty]

Output shape:
    {
      "repo": "...",
      "detected": {"datastore": [{
                     "signal": "postgres", "tier": "deep", "category": "relational",
                     "matched_on": [{"token": "postgres", "weak_evidence": false,
                                     "files": ["requirements.txt"]}],
                     "weak_evidence": false   # present and true only when every match
                   }], ...},                  # for this signal came from a non-manifest
                                               # YAML file (see content_kind())
      "references_to_load": [...],
      "tiers": {"postgres": "deep", ...},
      "notes": {"mysql": "...", ...},
      "secret_files_present": [...],
      "evidence_files": [...],
      "warnings": [...]   # includes a "weak evidence only for: ..." entry when applicable
    }
"""

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------------------
# What we look at, and what we refuse to look at
# --------------------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "bower_components",
    "venv", ".venv", "env", ".env.d", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", "target", "build", "dist", "out", ".next", ".nuxt",
    ".gradle", ".idea", ".vscode", "coverage", "htmlcov", ".terraform",
    "site-packages", "Pods", "DerivedData",
}

# Files whose CONTENT is never read. Presence is reported; contents are not.
SECRET_PATTERNS = [
    re.compile(r"(^|/)\.env($|\.|/)", re.I),
    re.compile(r"\.pem$", re.I),
    re.compile(r"\.key$", re.I),
    re.compile(r"\.pfx$", re.I),
    re.compile(r"\.p12$", re.I),
    re.compile(r"(^|/)credentials(\.|$)", re.I),
    re.compile(r"\.tfvars$", re.I),
    re.compile(r"(^|/)secrets?\.(ya?ml|json|toml|ini)$", re.I),
    re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$", re.I),
    re.compile(r"\.netrc$", re.I),
]

# Files whose content is worth reading for dependency and config signals.
CONTENT_FILES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "poetry.lock",
    "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg", "uv.lock", "constraints.txt",
    "go.mod", "go.sum",
    "cargo.toml", "cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "composer.json", "composer.lock",
    "gemfile", "gemfile.lock",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
    "compose.yaml",
    "procfile", "makefile", "justfile",
    "chart.yaml", "values.yaml", "serverless.yml", "serverless.yaml",
    "template.yaml", "template.yml", "netlify.toml", "vercel.json",
    "packages.lock.json",
}

CONTENT_SUFFIXES = (
    ".csproj", ".fsproj", ".sln", ".tf", ".tfvars.example",
    # Prisma's schema file (conventionally schema.prisma) is the only place a Prisma-based
    # Node/TypeScript project declares its actual datastore — `datasource db { provider =
    # "postgresql" }` etc. package.json/package-lock.json name only "@prisma/client" and
    # "prisma", neither of which is a datastore token, so without reading this file a
    # Prisma project's datastore signal never fires from any legitimate match at all.
    ".prisma",
)

# Kubernetes/Helm/compose manifests are matched by content, so scan small YAML too.
YAML_SUFFIXES = (".yaml", ".yml")

DEFAULT_MAX_BYTES = 256 * 1024      # per file
MAX_TOTAL_BYTES = 12 * 1024 * 1024  # overall corpus cap
MAX_FILES = 4000


# --------------------------------------------------------------------------------------
# Minimal registry reader
#
# registry.yaml uses a deliberately small subset of YAML so it can be read without a
# third-party parser: a top-level "version" scalar, then a sequence of mappings whose
# values are scalars or inline lists. Folded blocks ("notes: >") are joined into one line.
# If the registry grows beyond this subset, extend this reader rather than the format.
# --------------------------------------------------------------------------------------

def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            # A double-quoted token can itself contain an escaped quote, used to make a
            # match string like a literal `"node":` unambiguous against prose. Without
            # this unescape, a token written as "\"node\":" parses as the four literal
            # characters \"node\": and matches nothing, ever. Evaluation caught this: the
            # pre-existing node signal's quoted token had been dead since v0.1.0.
            inner = inner.replace('\\"', '"')
        return inner
    return value


def _parse_inline_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part) for part in inner.split(",") if part.strip()]
    return [_strip_quotes(value)]


def parse_registry(path):
    """Return (entries, warnings). Each entry is a dict of scalars and lists."""
    warnings = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw_lines = handle.read().splitlines()
    except OSError as exc:
        return [], ["registry unreadable: %s" % exc]

    # Join inline lists that span multiple lines, and fold "key: >" blocks.
    lines = []
    buffer = None
    folding_indent = None
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if folding_indent is None:
                continue
        if folding_indent is not None:
            indent = len(line) - len(line.lstrip())
            if stripped and indent > folding_indent:
                lines[-1] += " " + stripped
                continue
            folding_indent = None

        if buffer is not None:
            buffer += " " + stripped
            if buffer.count("[") <= buffer.count("]"):
                lines.append(buffer)
                buffer = None
            continue

        if stripped.count("[") > stripped.count("]"):
            buffer = line
            continue

        if re.search(r":\s*>\s*$", stripped):
            folding_indent = len(line) - len(line.lstrip())
            lines.append(re.sub(r":\s*>\s*$", ": ", line))
            continue

        lines.append(line)

    if buffer is not None:
        lines.append(buffer)

    entries = []
    current = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            stripped = stripped[2:].strip()

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if current is None:
            continue  # top-level scalar such as "version: 1"

        if value.startswith("["):
            current[key] = _parse_inline_list(value)
        elif key in ("match", "load"):
            current[key] = _parse_inline_list(value)
        else:
            current[key] = _strip_quotes(value)

    if current:
        entries.append(current)

    usable = [e for e in entries if e.get("signal") and e.get("match")]
    if not usable:
        warnings.append("registry parsed but contained no usable entries")
    return usable, warnings


# --------------------------------------------------------------------------------------
# Repository scan
# --------------------------------------------------------------------------------------

def is_secret(rel_path):
    normalized = rel_path.replace(os.sep, "/")
    return any(pattern.search(normalized) for pattern in SECRET_PATTERNS)


def content_kind(name, rel_path):
    """Classify why a file's content would be read, so a later match can be graded.

    "manifest" and "migration" are recognized dependency/schema files — a match inside one
    is real evidence a technology is actually used. "yaml" is the blanket catch-all for any
    other .yaml/.yml file (CI workflows, k8s values files, arbitrary config, even this
    project's own registry.yaml) — a match found only there is weaker: it is exactly the
    shape of the false positives found during behavioral evaluation (a lockfile hash or a
    bare English word colliding with a short match token). Returns None if content is not
    worth reading at all.
    """
    lowered = name.lower()
    if lowered in CONTENT_FILES:
        return "manifest"
    if lowered.endswith(CONTENT_SUFFIXES):
        return "manifest"
    if lowered.startswith("dockerfile"):
        return "manifest"
    if "migration" in rel_path.lower() and lowered.endswith((".sql", ".py", ".js", ".ts")):
        return "migration"
    if lowered.endswith(YAML_SUFFIXES):
        return "yaml"
    return None


def wants_content(name, rel_path):
    return content_kind(name, rel_path) is not None


def scan(repo, max_bytes):
    """Return (records, evidence_files, secret_files, warnings).

    records is a list of (rel_path, text, kind) tuples — one per filename (kind
    "filename", text is the path itself, so a match on the file's own name is still
    detected) plus one more per file whose content was read (kind from content_kind()).
    Keeping matches attributed to the specific file they came from, rather than one
    flattened corpus string, is what lets detect() report *which* file supports a match
    and grade manifest evidence above an incidental YAML hit.
    """
    records = []
    evidence = []
    secrets = []
    warnings = []
    total = 0
    seen = 0

    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".egg")]
        for name in filenames:
            seen += 1
            if seen > MAX_FILES:
                warnings.append("file limit reached (%d); scan is partial" % MAX_FILES)
                return records, evidence, secrets, warnings

            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            records.append((rel, rel, "filename"))

            if is_secret(rel):
                secrets.append(rel)
                continue

            kind = content_kind(name, rel)
            if kind is None:
                continue

            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > max_bytes:
                evidence.append(rel + " (truncated)")
            if total + min(size, max_bytes) > MAX_TOTAL_BYTES:
                warnings.append("content budget reached; scan is partial")
                return records, evidence, secrets, warnings

            try:
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read(max_bytes)
            except OSError as exc:
                warnings.append("unreadable: %s (%s)" % (rel, exc))
                continue

            total += len(content)
            records.append((rel, content, kind))
            evidence.append(rel)

    return records, evidence, secrets, warnings


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------

MAX_FILES_PER_TOKEN = 3

# content_kind() values that count as real evidence a dependency is actually used, as
# opposed to "yaml" — the blanket catch-all where a match is only as good as the file it
# happened to land in (a CI workflow, a k8s values file, this project's own registry.yaml).
STRONG_EVIDENCE_KINDS = {"filename", "manifest", "migration"}


def _record_texts(corpus):
    """Normalize either a records list (from scan()) or a legacy flat string into a list
    of (path, lowered_text, kind) triples. A plain string (as passed directly by callers
    and existing tests) carries no file provenance, so path is None and kind "unknown" —
    detect() still matches correctly, it just can't grade or attribute the evidence."""
    if isinstance(corpus, str):
        return [(None, corpus.lower(), "unknown")]
    return [(path, text.lower(), kind) for path, text, kind in corpus]


def detect(corpus, entries):
    records = _record_texts(corpus)
    detected = {}
    references = []
    tiers = {}
    notes = {}

    for entry in entries:
        signal = entry["signal"]
        tokens = [m for m in entry.get("match", []) if m]

        matched_on = []
        any_matched = False
        signal_has_strong_evidence = False
        signal_has_graded_evidence = False
        for token in sorted(set(tokens)):
            token_lower = token.lower()
            files = []
            token_kinds = set()
            for path, lowered_text, kind in records:
                if token_lower in lowered_text:
                    any_matched = True
                    token_kinds.add(kind)
                    if path is not None and path not in files:
                        files.append(path)
            if not token_kinds:
                continue

            entry_out = {"token": token}
            if token_kinds != {"unknown"}:
                # Real provenance exists — grade it. "unknown" means a legacy flat-string
                # corpus with no per-file attribution; there is nothing to grade.
                signal_has_graded_evidence = True
                is_weak = not (token_kinds & STRONG_EVIDENCE_KINDS)
                entry_out["weak_evidence"] = is_weak
                if not is_weak:
                    signal_has_strong_evidence = True
            if files:
                entry_out["files"] = sorted(files)[:MAX_FILES_PER_TOKEN]
                if len(files) > MAX_FILES_PER_TOKEN:
                    entry_out["file_count"] = len(files)
            matched_on.append(entry_out)

        if not any_matched:
            continue

        kind = entry.get("kind", "other")
        record = {
            "signal": signal,
            "matched_on": matched_on[:6],
            "tier": entry.get("tier", "generic"),
        }
        if entry.get("category"):
            record["category"] = entry["category"]
        if signal_has_graded_evidence and not signal_has_strong_evidence:
            record["weak_evidence"] = True
        detected.setdefault(kind, []).append(record)

        tiers[signal] = entry.get("tier", "generic")
        if entry.get("notes"):
            notes[signal] = " ".join(entry["notes"].split())

        for ref in entry.get("load", []):
            if ref not in references:
                references.append(ref)

    for kind in detected:
        detected[kind].sort(key=lambda r: r["signal"])
    return detected, references, tiers, notes


def order_references(references):
    """Category files before technology files; methodology is loaded by the skill itself."""
    def rank(path):
        if path.startswith("principles/"):
            return 0
        if path.startswith("databases/universal"):
            return 1
        if path.startswith("databases/"):
            return 2
        if path.startswith("runtimes/"):
            return 3
        if path.startswith("application/"):
            return 4
        if path.startswith("distributed/"):
            return 5
        if path.startswith("infrastructure/"):
            return 6
        if path.startswith("technology/"):
            return 7
        return 8
    return sorted(references, key=lambda p: (rank(p), p))


# --------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Detect the technology stack of a backend repository (read-only).")
    parser.add_argument("repo", nargs="?", default=".", help="repository path (default: .)")
    parser.add_argument("--registry", default=None,
                        help="path to registry.yaml (default: alongside this script's parent)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                        help="maximum bytes read per file")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("not a directory: %s" % repo, file=sys.stderr)
        return 2

    registry_path = args.registry or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, "registry.yaml")
    registry_path = os.path.normpath(registry_path)

    entries, warnings = parse_registry(registry_path)
    records, evidence, secrets, scan_warnings = scan(repo, args.max_bytes)
    warnings.extend(scan_warnings)

    detected, references, tiers, notes = detect(records, entries)

    if not detected:
        warnings.append(
            "no registry signal matched; fall back to manual inspection "
            "(methodology/discovery.md) and classify any datastore by category")

    weak_signals = sorted(
        rec["signal"]
        for records_for_kind in detected.values()
        for rec in records_for_kind
        if rec.get("weak_evidence")
    )
    if weak_signals:
        warnings.append(
            "weak evidence only for: %s — every match was found inside a non-manifest "
            "YAML file (CI config, k8s values, arbitrary docs, even this project's own "
            "registry.yaml), never a dependency manifest, lockfile, or matching filename; "
            "verify with an actual import or client call before treating as a real "
            "dependency" % ", ".join(weak_signals))

    result = {
        "repo": repo,
        "registry": registry_path,
        "detected": detected,
        "references_to_load": order_references(references),
        "tiers": tiers,
        "notes": notes,
        "secret_files_present": sorted(secrets),
        "evidence_files": sorted(set(evidence))[:200],
        "warnings": warnings,
        "disclaimer": (
            "Detection is evidence for a human or agent to verify, not a conclusion. "
            "A declared dependency is not proof of use. Contents of files matching "
            "secret patterns were never read."
        ),
    }

    json.dump(result, sys.stdout, indent=2 if args.pretty else None, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
