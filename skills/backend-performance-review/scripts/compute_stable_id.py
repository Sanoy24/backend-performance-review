#!/usr/bin/env python3
"""Compute a finding's canonical `stable_id`. Standard library only.

    python scripts/compute_stable_id.py --file src/orders/service.py --symbol OrderService.list --category data-access
    python scripts/compute_stable_id.py --finding finding.json   # reads location.file/.symbol and category from it

Why this exists, and what it replaces
--------------------------------------
The schema originally described `stable_id` as "derived from root cause, file, symbol, and
mechanism" and asserted that "two reviews of the same unfixed problem must produce the same
stable_id" — asking the *reviewing agent* to invent a hash from those inputs through
reasoning. The first real independent blind-pass run (docs/roadmap.md, benchmark/README.md
"The first real validation run") disproved this directly: two agents that agreed on a
finding's severity (within one level), confidence, and recommendation still produced
completely unrelated `stable_id` values, because nothing specified an actual algorithm —
each agent computed "a hash" in whatever way it chose.

This script is the fix: `stable_id` is no longer something a reviewing agent invents. It is
computed by running this script, the same way `detect_stack.py` is run rather than having an
agent guess a repository's stack from memory. Given the same inputs, it always produces the
same output, on any machine, under any model.

What changed about the definition, and why
--------------------------------------------
The old description included "mechanism" as an input. Mechanism is freeform prose, and two
agents describing the identical bug correctly use different words for it — hashing that text
would make `stable_id` *less* stable, not more, exactly backwards from its purpose. The
canonical algorithm below is deliberately narrower: it hashes only the finding's `location`
(file and symbol — never the line number) and `category`, sourced from the schema's own
`Location` object rather than from prose.

This narrower basis has a known, accepted limitation, not silently hidden: **two findings
in the same file and symbol, in the same category, are not distinguished from each other.**
A file with two independent bugs in one function collides. This is an intentional trade —
disambiguating further would require hashing something semantic (which reintroduces the
mechanism-text instability above) or an ordinal position (which is not stable across a
reviewer choosing to list findings in a different order). A collision here is visible and
checkable (two findings sharing a `stable_id` in one review is itself worth a warning), which
is a better failure mode than a value that looks unique and isn't reproducible.

What this does NOT fix
------------------------
It does not solve two independent reviews citing the *same* underlying bug at *different*
points in a call chain (one citing a query's definition, the other its call site) — that is
a semantic-understanding problem, not a data-structure one, and the two locations are
genuinely different `location.file`/`location.symbol` values. `also_locations` in
`schemas/ground-truth.schema.json` handles this on the ground-truth side, where a human
curator can assert two citations describe one bug. Comparing two raw reviews with no such
curator in the loop cannot resolve this automatically; `score.py stability`'s `caveats`
field says so rather than promising otherwise.
"""

import argparse
import hashlib
import json
import sys

STABLE_ID_LENGTH = 16


def normalize_file(file_path):
    if not file_path:
        return ""
    return file_path.strip().replace("\\", "/").lower()


def normalize_symbol(symbol):
    if not symbol:
        return ""
    return " ".join(symbol.split())


def compute(file_path, symbol, category):
    """The canonical algorithm. Deliberately simple and stdlib-only: a fixed-order,
    NUL-separated join of the three normalized inputs, SHA-256'd, hex-truncated. Anyone
    re-implementing this in another language needs only string normalization and SHA-256 —
    no external library, no locale-dependent behavior.
    """
    parts = [normalize_file(file_path), normalize_symbol(symbol), (category or "").strip()]
    canonical = "\x00".join(parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:STABLE_ID_LENGTH]


def compute_for_finding(finding):
    location = finding.get("location") or {}
    return compute(location.get("file"), location.get("symbol"), finding.get("category"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="location.file")
    parser.add_argument("--symbol", help="location.symbol, if any")
    parser.add_argument("--category", help="the finding's category")
    parser.add_argument("--finding", help="path to a JSON file containing one finding "
                                          "object (location.file/.symbol + category read "
                                          "from it; overrides --file/--symbol/--category)")
    args = parser.parse_args(argv)

    if args.finding:
        with open(args.finding, encoding="utf-8") as handle:
            finding = json.load(handle)
        stable_id = compute_for_finding(finding)
    else:
        if not args.file or not args.category:
            parser.error("--file and --category are required unless --finding is given")
        stable_id = compute(args.file, args.symbol, args.category)

    print(stable_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
