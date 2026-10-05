"""Contract tests for every JSON Schema keyword the stdlib validator supports."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import json_schema_lite as schema_lite  # noqa: E402


class JsonSchemaLiteKeywordTests(unittest.TestCase):

    def assert_invalid(self, instance, schema, fragment=None):
        errors = schema_lite.validate(instance, schema)
        self.assertTrue(errors, "expected validation to fail")
        if fragment:
            self.assertTrue(any(fragment in error for error in errors), errors)

    def test_type_supports_all_used_primitive_types_and_unions(self):
        values = {
            "object": {}, "array": [], "string": "x", "boolean": True,
            "null": None, "integer": 1, "number": 1.5,
        }
        for name, value in values.items():
            with self.subTest(name=name):
                self.assertEqual(schema_lite.validate(value, {"type": name}), [])
        self.assertEqual(schema_lite.validate(None, {"type": ["string", "null"]}), [])
        self.assert_invalid(True, {"type": "integer"}, "expected type")

    def test_enum_const_pattern_and_min_length(self):
        self.assert_invalid("no", {"enum": ["yes"]}, "not one of")
        self.assert_invalid("no", {"const": "yes"}, "expected")
        self.assert_invalid("abc", {"pattern": "^[0-9]+$"}, "does not match")
        self.assert_invalid("", {"minLength": 1}, "minLength")

    def test_date_time_format_requires_a_real_rfc3339_timestamp(self):
        schema = {"type": "string", "format": "date-time"}
        for value in ("2026-09-17T12:34:56Z", "2026-09-17t12:34:56.12+03:00"):
            self.assertEqual(schema_lite.validate(value, schema), [])
        for value in ("2026-09-17", "2026-13-17T12:34:56Z", "2026-09-17T12:34:56"):
            self.assert_invalid(value, schema, "RFC 3339 date-time")

    def test_numeric_bounds(self):
        self.assert_invalid(-1, {"minimum": 0}, "below minimum")
        self.assert_invalid(2, {"maximum": 1}, "above maximum")

    def test_array_size_uniqueness_items_and_contains(self):
        self.assert_invalid([], {"minItems": 1}, "minItems")
        self.assert_invalid([1, 2], {"maxItems": 1}, "maxItems")
        self.assert_invalid([1, 1], {"uniqueItems": True}, "not unique")
        self.assert_invalid([1, "x"], {"items": {"type": "integer"}}, "expected type")
        self.assert_invalid([1, 2], {"contains": {"const": 3}}, "contains")
        self.assertEqual(schema_lite.validate([1, 3], {"contains": {"const": 3}}), [])

    def test_object_required_properties_and_additional_properties(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        self.assert_invalid({}, schema, "missing required")
        self.assert_invalid({"name": 1}, schema, "expected type")
        self.assert_invalid({"name": "ok", "extra": True}, schema, "unexpected property")
        self.assertEqual(schema_lite.validate({"name": "ok"}, schema), [])

    def test_local_and_sibling_refs_and_defs(self):
        local = {"$defs": {"word": {"type": "string"}}, "$ref": "#/$defs/word"}
        self.assert_invalid(1, local, "expected type")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "child.json").write_text(
                json.dumps({"type": "integer"}), encoding="utf-8")
            schema = {"$ref": "child.json"}
            self.assertEqual(schema_lite.validate(1, schema, base_dir=base), [])
            errors = schema_lite.validate("1", schema, base_dir=base)
            self.assertTrue(any("expected type" in error for error in errors), errors)

    def test_all_of_and_any_of(self):
        self.assert_invalid(3, {"allOf": [{"minimum": 1}, {"maximum": 2}]}, "maximum")
        schema = {"anyOf": [{"const": "yes"}, {"const": "no"}]}
        self.assertEqual(schema_lite.validate("yes", schema), [])
        self.assert_invalid("maybe", schema, "anyOf")

    def test_if_then_else_selects_exactly_one_branch(self):
        schema = {
            "if": {"properties": {"mode": {"const": "strict"}}},
            "then": {"required": ["limit"]},
            "else": {"required": ["reason"]},
        }
        self.assert_invalid({"mode": "strict"}, schema, "limit")
        self.assert_invalid({"mode": "relaxed"}, schema, "reason")
        self.assertEqual(schema_lite.validate({"mode": "strict", "limit": 1}, schema), [])
        self.assertEqual(schema_lite.validate({"mode": "relaxed", "reason": "ok"}, schema), [])

    def test_unknown_keywords_are_ignored(self):
        self.assertEqual(schema_lite.validate("anything", {"futureKeyword": False}), [])


if __name__ == "__main__":
    unittest.main()
