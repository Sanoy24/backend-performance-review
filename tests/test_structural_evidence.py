"""Behavioral checks for the optional structural evidence interchange."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_structural_evidence as structural  # noqa: E402


def location(file, symbol, line):
    return {"file": file, "symbol": symbol, "line": line}


def evidence():
    return {
        "schema_version": "1.0",
        "producer": {"name": "example-indexer", "version": "0.1"},
        "repository_commit": "0123456789abcdef",
        "call_edges": [{
            "caller": location("app/routes.py", "orders", 20),
            "callee": location("app/service.py", "list_orders", 8),
            "call_site": location("app/routes.py", "orders", 22),
        }],
        "query_shapes": [{
            "owner": location("app/service.py", "list_orders", 8),
            "query_site": location("app/db.py", "fetch_order", 30),
            "evidence": location("app/service.py", "list_orders", 12),
            "operation": "read",
            "multiplicity": "per-item",
            "bound": "unknown",
        }],
        "changed_paths": [{
            "changed": location("app/routes.py", "orders", 22),
            "affected": location("app/db.py", "fetch_order", 30),
            "evidence": location("app/service.py", "list_orders", 12),
            "relation": "queries",
        }],
    }


class StructuralEvidenceTests(unittest.TestCase):

    def test_three_independent_evidence_types_validate(self):
        for collection in ("call_edges", "query_shapes", "changed_paths"):
            with self.subTest(collection=collection):
                document = evidence()
                for other in ("call_edges", "query_shapes", "changed_paths"):
                    if other != collection:
                        document.pop(other)
                self.assertEqual(structural.validate(document), [])

    def test_empty_advisory_result_is_valid(self):
        self.assertEqual(structural.validate({
            "schema_version": "1.0", "producer": {"name": "manual"},
        }), [])

    def test_every_edge_endpoint_needs_file_and_symbol(self):
        for collection, field in (("call_edges", "callee"),
                                  ("query_shapes", "query_site"),
                                  ("changed_paths", "affected")):
            with self.subTest(collection=collection):
                document = evidence()
                document[collection][0][field].pop("symbol")
                self.assertTrue(any("symbol" in error for error in
                                    structural.validate(document)))

    def test_every_edge_needs_relationship_provenance(self):
        for collection, field in (("call_edges", "call_site"),
                                  ("query_shapes", "evidence"),
                                  ("changed_paths", "evidence")):
            with self.subTest(collection=collection):
                document = evidence()
                document[collection][0].pop(field)
                self.assertTrue(any(field in error for error in
                                    structural.validate(document)))

    def test_call_site_must_be_inside_the_caller_symbol(self):
        document = evidence()
        document["call_edges"][0]["call_site"]["symbol"] = "another_handler"
        self.assertTrue(any("inside its caller symbol" in error for error in
                            structural.validate(document)))

    def test_unknown_shape_is_honest(self):
        document = evidence()
        document["query_shapes"][0].update({
            "operation": "unknown", "multiplicity": "unknown", "bound": "unknown",
        })
        self.assertEqual(structural.validate(document), [])

    def test_rejects_unsafe_or_secret_paths(self):
        for path in ("../private.py", "/etc/passwd", "C:/secrets.py",
                     "app\\routes.py", "app//routes.py", "https:source.py",
                     "app/route\n.py", ".env", "app/token.key"):
            with self.subTest(path=path):
                document = evidence()
                document["call_edges"][0]["caller"]["file"] = path
                self.assertTrue(structural.validate(document))

    def test_repository_check_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            document = evidence()
            problems = structural.validate(document, directory)
        self.assertTrue(any("does not resolve to a file" in error for error in problems))

    def test_repository_check_accepts_existing_paths_without_reading_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("app/routes.py", "app/service.py", "app/db.py"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(structural.validate(evidence(), root), [])

    def test_unrelated_producer_fields_are_rejected(self):
        document = copy.deepcopy(evidence())
        document["vendor_specific_index_id"] = "opaque"
        self.assertTrue(any("unexpected property" in error for error in
                            structural.validate(document)))

    def test_cli_checks_a_producer_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hints.json"
            path.write_text(json.dumps(evidence()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_structural_evidence.py"),
                 "--input", str(path)],
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source claims still require review", result.stdout)


if __name__ == "__main__":
    unittest.main()
