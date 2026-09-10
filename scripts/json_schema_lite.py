#!/usr/bin/env python3
"""A deliberately small JSON Schema validator, standard library only.

Why this exists rather than a dependency on `jsonschema`:

This repository's tooling is stdlib-only end to end. The bundled `detect_stack.py` must be
safe to run on an unfamiliar repository without installing anything (docs/architecture.md
§10), and the CI jobs that check it install no packages at all. Adding a dependency to the
invariants job alone would have been defensible, but it would make the one command a
contributor is told to run — `python scripts/check_repo_invariants.py` — fail on a clean
checkout until they pip-installed something. The same trade the registry reader makes
applies here: a small reader we control, against a format we also control.

The trade is explicit: this supports only the JSON Schema subset that
`schemas/*.schema.json` actually use. Extending those schemas may mean extending this
reader, exactly as `detect_stack.py` documents for its YAML subset. It is not, and should
not become, a general-purpose validator.

Supported: type, enum, const, pattern, minLength, minimum, maximum, minItems, maxItems,
uniqueItems, required, properties, additionalProperties, items, contains, $ref (local
pointers and sibling-file pointers), $defs, allOf, anyOf, if/then/else.

Deliberately ignored: format (advisory only — an invalid date-time is not worth a
dependency), and every keyword not listed above. Unknown keywords are ignored silently,
which is what the specification requires anyway.
"""

import json
import re
from pathlib import Path

__all__ = ["validate", "load_schema", "SchemaError"]


class SchemaError(Exception):
    """Raised for a defect in the schema itself, as opposed to in the instance."""


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value, name):
    if name == "integer":
        # bool is a subclass of int in Python; a boolean is not an integer here.
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES.get(name)
    if expected is None:
        raise SchemaError("unknown type keyword: %r" % name)
    return isinstance(value, expected)


class _Validator:
    def __init__(self, root, base_dir, filename):
        self.root = root
        self.base_dir = Path(base_dir) if base_dir else None
        self.filename = filename
        self._siblings = {}
        # Which document local "#/..." pointers currently resolve against. This changes
        # when a cross-file $ref is followed: once inside finding.schema.json, its own
        # "#/$defs/evidenceItem" must resolve there, not back in the review schema that
        # pointed at it.
        self._current_root = root

    # -- reference resolution ---------------------------------------------------

    def _sibling(self, name):
        """Load a schema file next to this one, for cross-file $refs."""
        if name in self._siblings:
            return self._siblings[name]
        if self.base_dir is None:
            raise SchemaError("cross-file $ref %r but no base directory given" % name)
        path = self.base_dir / name
        if not path.is_file():
            raise SchemaError("cross-file $ref %r does not resolve to a file" % name)
        with path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
        self._siblings[name] = loaded
        return loaded

    def _resolve(self, ref):
        """Return (node, document_the_node_came_from)."""
        if ref.startswith("#"):
            document, pointer = self._current_root, ref[1:]
        else:
            filename, _, fragment = ref.partition("#")
            document, pointer = self._sibling(filename), fragment
        node = document
        for token in [t for t in pointer.split("/") if t]:
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise SchemaError("$ref %r does not resolve" % ref)
            node = node[token]
        return node, document

    # -- validation -------------------------------------------------------------

    def check(self, instance, schema, path, errors):
        if not isinstance(schema, dict):
            raise SchemaError("schema node at %s is not an object" % (path or "$"))

        if "$ref" in schema:
            target, document = self._resolve(schema["$ref"])
            previous = self._current_root
            self._current_root = document
            try:
                self.check(instance, target, path, errors)
            finally:
                self._current_root = previous
            # Siblings of $ref are legal in 2020-12 and our schemas do not use them,
            # but continuing costs nothing and is more correct than returning here.

        if "type" in schema:
            declared = schema["type"]
            names = declared if isinstance(declared, list) else [declared]
            if not any(_is_type(instance, n) for n in names):
                errors.append("%s: expected type %s, got %s"
                              % (path or "$", "/".join(names),
                                 type(instance).__name__))
                # Every other keyword assumes the type held; stop this branch.
                return

        if "enum" in schema and instance not in schema["enum"]:
            errors.append("%s: %r is not one of %s"
                          % (path or "$", instance, schema["enum"]))

        if "const" in schema and instance != schema["const"]:
            errors.append("%s: expected %r" % (path or "$", schema["const"]))

        if isinstance(instance, str):
            pattern = schema.get("pattern")
            if pattern is not None and not re.search(pattern, instance):
                errors.append("%s: %r does not match /%s/" % (path or "$", instance, pattern))
            minimum_length = schema.get("minLength")
            if minimum_length is not None and len(instance) < minimum_length:
                errors.append("%s: shorter than minLength %d" % (path or "$", minimum_length))

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append("%s: %r below minimum %r"
                              % (path or "$", instance, schema["minimum"]))
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append("%s: %r above maximum %r"
                              % (path or "$", instance, schema["maximum"]))

        if isinstance(instance, list):
            self._check_array(instance, schema, path, errors)

        if isinstance(instance, dict):
            self._check_object(instance, schema, path, errors)

        for subschema in schema.get("allOf", []):
            self.check(instance, subschema, path, errors)

        if "anyOf" in schema:
            branch_errors = []
            for subschema in schema["anyOf"]:
                collected = []
                self.check(instance, subschema, path, collected)
                if not collected:
                    break
                branch_errors.extend(collected)
            else:
                errors.append("%s: matched no anyOf branch (%s)"
                              % (path or "$", "; ".join(branch_errors)))

        if "if" in schema:
            probe = []
            self.check(instance, schema["if"], path, probe)
            branch = "then" if not probe else "else"
            if branch in schema:
                self.check(instance, schema[branch], path, errors)

    def _check_array(self, instance, schema, path, errors):
        minimum_items = schema.get("minItems")
        if minimum_items is not None and len(instance) < minimum_items:
            errors.append("%s: has %d item(s), minItems is %d"
                          % (path or "$", len(instance), minimum_items))
        maximum_items = schema.get("maxItems")
        if maximum_items is not None and len(instance) > maximum_items:
            errors.append("%s: has %d item(s), maxItems is %d"
                          % (path or "$", len(instance), maximum_items))
        if schema.get("uniqueItems"):
            seen = [json.dumps(item, sort_keys=True) for item in instance]
            if len(set(seen)) != len(seen):
                errors.append("%s: items are not unique" % (path or "$"))
        if "items" in schema:
            for index, item in enumerate(instance):
                self.check(item, schema["items"], "%s[%d]" % (path or "$", index), errors)
        if "contains" in schema:
            for item in instance:
                probe = []
                self.check(item, schema["contains"], path, probe)
                if not probe:
                    break
            else:
                errors.append("%s: no item satisfies `contains`" % (path or "$"))

    def _check_object(self, instance, schema, path, errors):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required property %r" % (path or "$", key))

        properties = schema.get("properties", {})
        for key, value in instance.items():
            child = "%s.%s" % (path, key) if path else key
            if key in properties:
                self.check(value, properties[key], child, errors)
            elif schema.get("additionalProperties") is False:
                errors.append("%s: unexpected property %r" % (path or "$", key))


def load_schema(path):
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle), path.parent, path.name


def validate(instance, schema, base_dir=None, filename=None):
    """Return a list of human-readable error strings; empty means valid."""
    errors = []
    _Validator(schema, base_dir, filename).check(instance, schema, "", errors)
    return errors


def validate_file(instance, schema_path):
    schema, base_dir, filename = load_schema(schema_path)
    return validate(instance, schema, base_dir, filename)
