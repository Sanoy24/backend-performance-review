#!/usr/bin/env python3
"""Build valid verdict fixtures for the live composite-Action CI tests."""

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "docs" / "examples" / "review.example.json"


def build():
    full = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    fail = copy.deepcopy(full)
    fail["mode"] = "change-scoped"
    fail["verdict"] = "FAIL"

    warn = copy.deepcopy(fail)
    warn["verdict"] = "WARN"
    warn["findings"][0]["severity"] = "Medium"
    warn["findings"][0]["priority"] = "P2"

    unknown = copy.deepcopy(fail)
    unknown["verdict"] = "UNKNOWN"
    unknown["completeness"].setdefault("unknowns", []).append({
        "subject": "the changed worker",
        "reason": "not-examined",
        "what_would_resolve_it": "Include the worker in the review scope",
    })

    return {"full": full, "fail": fail, "warn": warn, "unknown": unknown}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, review in build().items():
        (args.output_dir / (name + ".json")).write_text(
            json.dumps(review, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
