#!/usr/bin/env python3
"""Select SWE-fficiency tasks and score whether a review localizes the expert's change.

SWE-fficiency (arXiv 2511.06090, dataset `swefficiency/swefficiency`, CC-BY 4.0) pairs a
repository snapshot and a slow workload with the patch an expert wrote to speed it up. Its
repositories are scientific Python libraries, not web backends, so a review of them measures
how far the skill's method transfers, not how well it reviews backends.

The question asked here is narrow: does the review point at the code the expert changed?

  select  pick tasks deterministically, before any review exists: per repository, order
          instances by SHA-256 of their id and take the first whose patch changes between
          one and `--max-source-files` source files
  score   compare one review.json with one expert patch

A finding's location is a *file hit* when it names a changed source file, and a *hunk hit*
when its line falls inside a changed hunk of the base file, widened by `--window` lines.
Tests, documentation, and benchmark files in the patch never count: they record the change,
they are not where the slowness lived. No model is invoked here.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
SOURCE_SUFFIXES = (".py", ".pyx", ".pxd", ".pxi", ".c", ".h", ".cpp", ".hpp", ".cc",
                   ".f", ".f90", ".src", ".pyi")
NON_SOURCE_PARTS = ("tests", "test", "testing", "doc", "docs", "asv_bench", "benchmarks",
                    "whatsnew", "changelog", "release")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def is_source(path):
    """A file whose change could be where the slowness lived."""
    parts = path.lower().split("/")
    name = parts[-1]
    if not name.endswith(SOURCE_SUFFIXES):
        return False
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return False
    return not any(part in NON_SOURCE_PARTS for part in parts[:-1])


def changed_regions(patch):
    """Map each changed source file to the base-side line ranges its hunks touch."""
    regions = {}
    current = None
    for line in patch.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            current = header.group(1)
            if not is_source(current):
                current = None
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk and current is not None:
            start = int(hunk.group(1))
            length = 1 if hunk.group(2) is None else int(hunk.group(2))
            end = start + length - 1 if length else start
            regions.setdefault(current, []).append((start, end))
    return regions


def _normalize(path, known):
    """Match a review's path to a patch path despite separators, './', or absolute roots."""
    cleaned = str(path or "").replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned in known:
        return cleaned
    for candidate in known:
        if cleaned.endswith("/" + candidate):
            return candidate
    return cleaned


def _ordered(findings):
    indexed = list(enumerate(findings))
    indexed.sort(key=lambda item: (PRIORITY_ORDER.get(item[1].get("priority"), 9), item[0]))
    return [finding for _, finding in indexed]


def localize(patch, review, window=20):
    regions = changed_regions(patch)
    findings = _ordered(review.get("findings") or [])
    file_hits, hunk_hits = [], []
    for finding in findings:
        location = finding.get("location") or {}
        path = _normalize(location.get("file"), regions)
        if path not in regions:
            continue
        file_hits.append(finding.get("id"))
        line = location.get("line")
        if isinstance(line, int) and any(start - window <= line <= end + window
                                         for start, end in regions[path]):
            hunk_hits.append(finding.get("id"))
    top = findings[0] if findings else None
    return {
        "source_files": sorted(regions),
        "window": window,
        "findings": len(findings),
        "file_hit": bool(file_hits),
        "hunk_hit": bool(hunk_hits),
        "file_hits": file_hits,
        "hunk_hits": hunk_hits,
        "top_finding": top.get("id") if top else None,
        "top_finding_file_hit": bool(top) and top.get("id") in file_hits,
    }


def select(rows, per_repo=1, max_source_files=3):
    """Choose tasks by a rule fixed before any review, never by looking at outcomes."""
    by_repo = {}
    for row in sorted(rows, key=lambda r: hashlib.sha256(
            r["instance_id"].encode("utf-8")).hexdigest()):
        count = len(changed_regions(row["patch"]))
        if not 1 <= count <= max_source_files:
            continue
        chosen = by_repo.setdefault(row["repo"], [])
        if len(chosen) < per_repo:
            chosen.append(row)
    return [row for repo in sorted(by_repo) for row in by_repo[repo]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    scorer = commands.add_parser("score", help="score one review against one expert patch")
    scorer.add_argument("--patch", required=True, type=Path)
    scorer.add_argument("--review", required=True, type=Path)
    scorer.add_argument("--window", type=int, default=20)
    chooser = commands.add_parser("select", help="select tasks from a JSON list of rows")
    chooser.add_argument("--rows", required=True, type=Path)
    chooser.add_argument("--per-repo", type=int, default=1)
    chooser.add_argument("--max-source-files", type=int, default=3)
    args = parser.parse_args(argv)

    if args.command == "score":
        result = localize(args.patch.read_text(encoding="utf-8"),
                          json.loads(args.review.read_text(encoding="utf-8")),
                          window=args.window)
    else:
        rows = json.loads(args.rows.read_text(encoding="utf-8"))
        result = [{"instance_id": r["instance_id"], "repo": r["repo"],
                   "base_commit": r["base_commit"],
                   "source_files": sorted(changed_regions(r["patch"]))}
                  for r in select(rows, args.per_repo, args.max_source_files)]
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
