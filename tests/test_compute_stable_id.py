"""Tests for scripts/compute_stable_id.py — the canonical, mechanical stable_id algorithm.

Exists because the first real independent blind-pass run found the previous design (an
agent invents a hash by "deriving" one from prose) produces completely unrelated ids for
two reviews that agreed on everything else. Every property this algorithm is supposed to
have is tested explicitly here, not assumed.

Run with: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "skills" / "backend-performance-review" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import compute_stable_id as sid  # noqa: E402


class DeterminismTests(unittest.TestCase):
    """The entire point: same inputs, same output, every time, on any machine."""

    def test_identical_inputs_produce_identical_output(self):
        a = sid.compute("src/orders/service.py", "OrderService.list", "data-access")
        b = sid.compute("src/orders/service.py", "OrderService.list", "data-access")
        self.assertEqual(a, b)

    def test_output_is_deterministic_across_many_calls(self):
        results = {sid.compute("a.py", "Foo.bar", "data-access") for _ in range(20)}
        self.assertEqual(len(results), 1)

    def test_matches_the_schema_pattern(self):
        result = sid.compute("a.py", "Foo.bar", "data-access")
        import re
        self.assertRegex(result, r"^[a-z0-9]{8,64}$")
        self.assertEqual(len(result), sid.STABLE_ID_LENGTH)


class NormalizationTests(unittest.TestCase):
    """Two agents describing the same location differently in superficial ways must not
    get different ids — that would reintroduce the exact instability this exists to fix."""

    def test_windows_and_posix_separators_match(self):
        a = sid.compute("src\\orders\\service.py", "X", "data-access")
        b = sid.compute("src/orders/service.py", "X", "data-access")
        self.assertEqual(a, b)

    def test_case_of_the_file_path_does_not_matter(self):
        a = sid.compute("Src/Orders/Service.py", "X", "data-access")
        b = sid.compute("src/orders/service.py", "X", "data-access")
        self.assertEqual(a, b)

    def test_incidental_whitespace_in_symbol_does_not_matter(self):
        a = sid.compute("a.py", "Foo.bar", "data-access")
        b = sid.compute("a.py", "  Foo.bar  ", "data-access")
        c = sid.compute("a.py", "Foo.bar", "data-access")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_a_missing_symbol_is_stable_and_distinct_from_any_symbol(self):
        no_symbol = sid.compute("a.py", None, "data-access")
        empty_symbol = sid.compute("a.py", "", "data-access")
        with_symbol = sid.compute("a.py", "Foo.bar", "data-access")
        self.assertEqual(no_symbol, empty_symbol)
        self.assertNotEqual(no_symbol, with_symbol)


class DistinctionTests(unittest.TestCase):
    """The other half of the job: genuinely different findings must not collide by
    accident, whatever normalization is applied."""

    def test_different_files_produce_different_ids(self):
        a = sid.compute("a.py", "Foo.bar", "data-access")
        b = sid.compute("b.py", "Foo.bar", "data-access")
        self.assertNotEqual(a, b)

    def test_different_symbols_in_the_same_file_produce_different_ids(self):
        a = sid.compute("a.py", "Foo.bar", "data-access")
        b = sid.compute("a.py", "Foo.baz", "data-access")
        self.assertNotEqual(a, b)

    def test_different_categories_produce_different_ids(self):
        a = sid.compute("a.py", "Foo.bar", "data-access")
        b = sid.compute("a.py", "Foo.bar", "concurrency")
        self.assertNotEqual(a, b)

    def test_mechanism_text_is_deliberately_not_an_input(self):
        # The whole reason the old design failed: two honest paraphrases of one bug must
        # hash identically, because mechanism text is not part of the algorithm at all.
        # There is no `mechanism` parameter to pass — this test is the documentation of
        # that fact via the function's actual signature.
        import inspect
        params = list(inspect.signature(sid.compute).parameters)
        self.assertEqual(params, ["file_path", "symbol", "category"])


class FindingHelperTests(unittest.TestCase):
    """compute_for_finding() is what callers actually use — it must read the same fields
    the schema defines (location.file, location.symbol, category), not line number."""

    def test_reads_location_and_category_from_a_finding_object(self):
        finding = {
            "category": "data-access",
            "location": {"file": "a.py", "line": 999, "symbol": "Foo.bar"},
        }
        expected = sid.compute("a.py", "Foo.bar", "data-access")
        self.assertEqual(sid.compute_for_finding(finding), expected)

    def test_line_number_has_no_effect(self):
        base = {"category": "data-access", "location": {"file": "a.py", "symbol": "Foo.bar"}}
        moved = {"category": "data-access",
                 "location": {"file": "a.py", "symbol": "Foo.bar", "line": 4000}}
        self.assertEqual(sid.compute_for_finding(base), sid.compute_for_finding(moved))

    def test_a_missing_location_does_not_crash(self):
        self.assertTrue(sid.compute_for_finding({"category": "data-access"}))


class CliTests(unittest.TestCase):
    """The CLI is what SKILL.md actually instructs an agent to run."""

    def test_cli_prints_the_same_value_the_function_computes(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sid.main(["--file", "a.py", "--symbol", "Foo.bar", "--category", "data-access"])
        printed = buf.getvalue().strip()
        self.assertEqual(printed, sid.compute("a.py", "Foo.bar", "data-access"))

    def test_cli_reads_a_finding_json_file(self):
        import io
        import contextlib
        import json
        import tempfile
        import os
        finding = {"category": "concurrency", "location": {"file": "x.go", "symbol": "Y"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "finding.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(finding, handle)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sid.main(["--finding", path])
        self.assertEqual(buf.getvalue().strip(), sid.compute_for_finding(finding))


if __name__ == "__main__":
    unittest.main()
