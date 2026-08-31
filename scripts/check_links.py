#!/usr/bin/env python3
"""Verify every relative Markdown link in this repository resolves to a real file.

This is maintainer/CI tooling, not part of the skill itself — see
scripts/check_repo_invariants.py for that distinction.

Run from the repository root:
    python scripts/check_links.py

Exits non-zero and prints every broken link found, rather than stopping at the first one.
"""

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent

# Force UTF-8 output regardless of the host console's default codepage.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

# Matches Markdown links and images: [text](target) / ![alt](target). Deliberately not a
# full CommonMark parser — this repository's links are plain, and a stricter parser would
# add a dependency this project's tooling avoids.
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

EXCLUDED_DIRS = {".git", "__pycache__", "node_modules"}


def iter_markdown_files():
    for path in ROOT.rglob("*.md"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def is_external_or_non_file(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    scheme = urlsplit(target).scheme
    if scheme:  # http, https, mailto, etc.
        return True
    return False


def check_links():
    failures = []
    checked = 0

    for md_file in iter_markdown_files():
        text = md_file.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).strip()
            # Strip an optional Markdown title: [text](path "title")
            target = target.split(" ", 1)[0].strip('<>')
            if is_external_or_non_file(target):
                continue

            path_part = urlsplit(target).path
            if not path_part:
                continue
            path_part = unquote(path_part)

            resolved = (md_file.parent / path_part).resolve()
            checked += 1
            if not resolved.exists():
                line_no = text.count("\n", 0, match.start()) + 1
                failures.append(
                    f"{md_file.relative_to(ROOT)}:{line_no}: broken link to '{target}' "
                    f"(resolved: {resolved})"
                )

    return failures, checked


def main():
    failures, checked = check_links()
    if failures:
        print(f"{len(failures)} broken link(s) (of {checked} relative links checked):")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1

    print(f"All {checked} relative Markdown links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
