#!/usr/bin/env python3
"""Validate advisory structural-discovery hints; standard library only.

    python scripts/validate_structural_evidence.py --input hints.json
    python scripts/validate_structural_evidence.py --input hints.json --repo-root ./target

This checks the interchange contract, not whether a producer's edges are true. A review
must still inspect the cited source before promoting a hint to evidence or a finding.
"""

import argparse
import json
import sys
from pathlib import Path

import json_schema_lite as schema_lite


SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "structural-evidence.schema.json"
_LOCATION_FIELDS = {
    "call_edges": ("caller", "callee", "call_site"),
    "query_shapes": ("owner", "query_site", "evidence"),
    "changed_paths": ("changed", "affected", "evidence"),
}


def _safe_relative_path(value):
    if not isinstance(value, str) or not value:
        return False
    if (value.startswith("/") or "\\" in value or ":" in value
            or any(ord(character) < 32 for character in value)):
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _secret_path(value):
    name = value.rsplit("/", 1)[-1].lower()
    return (name == ".env" or name.startswith(".env.")
            or name.startswith("credentials") or name.endswith((".pem", ".key", ".tfvars")))


def validate(document, repo_root=None):
    """Return contract/provenance problems without opening any target source file."""
    problems = list(schema_lite.validate_file(document, SCHEMA))
    if not isinstance(document, dict):
        return problems

    root = Path(repo_root).resolve() if repo_root is not None else None
    for collection, fields in _LOCATION_FIELDS.items():
        items = document.get(collection, [])
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            for field in fields:
                location = item.get(field)
                if not isinstance(location, dict):
                    continue
                path = location.get("file")
                label = "%s[%d].%s.file" % (collection, index, field)
                if not _safe_relative_path(path):
                    problems.append("%s must be a normalized repository-relative path" % label)
                    continue
                if _secret_path(path):
                    problems.append("%s refers to a secret file, which must not be inspected"
                                    % label)
                    continue
                if root is not None:
                    candidate = (root / path).resolve()
                    try:
                        candidate.relative_to(root)
                        inside = True
                    except ValueError:
                        inside = False
                    if not inside or not candidate.is_file():
                        problems.append("%s does not resolve to a file inside the repository"
                                        % label)

            if collection == "call_edges":
                caller = item.get("caller")
                site = item.get("call_site")
                if isinstance(caller, dict) and isinstance(site, dict):
                    if (caller.get("file"), caller.get("symbol")) != (
                            site.get("file"), site.get("symbol")):
                        problems.append(
                            "call_edges[%d].call_site must be inside its caller symbol" % index)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="JSON evidence file from an optional producer")
    parser.add_argument("--repo-root", type=Path,
                        help="Optionally confirm paths stay inside the target repository")
    args = parser.parse_args(argv)

    try:
        with args.input.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("Cannot read structural evidence: %s" % exc, file=sys.stderr)
        return 2

    problems = validate(document, args.repo_root)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("Structural evidence contract valid; source claims still require review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
