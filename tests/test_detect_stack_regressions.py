"""Regression tests for detect_stack.py, specified in docs/evaluation.md §4.

Each test fixes a real bug found during behavioral evaluation against public repositories
(docs/evaluation.md §3.3) and asserts it stays fixed. These are not synthetic worst-case
inputs invented for coverage — the corpus in each test reproduces the actual collision text
found in a real lockfile, CI workflow, or compose file during that evaluation.

Run with: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "backend-performance-review"
REGISTRY = str(SKILL / "registry.yaml")

sys.path.insert(0, str(SKILL / "scripts"))
import detect_stack as detect  # noqa: E402


def matched_signals(corpus, entries, kind=None):
    detected, _, _, _ = detect.detect(corpus, entries)
    if kind is not None:
        return {record["signal"] for record in detected.get(kind, [])}
    return {record["signal"] for records in detected.values() for record in records}


class QuoteUnescapingTests(unittest.TestCase):
    """Regression for: a quoted match token with an escaped inner quote parsed to the
    literal backslash-quote characters instead of unescaping — the node signal's
    `"\\"node\\":"` token parsed to `\\"node\\":` (four literal characters, backslashes
    included) and so never matched anything in a real file, silently dead since v0.1.0.
    """

    def test_strip_quotes_unescapes_inner_quote(self):
        self.assertEqual(detect._strip_quotes('"\\"node\\":"'), '"node":')

    def test_parse_inline_list_unescapes_inner_quote(self):
        self.assertEqual(detect._parse_inline_list('["\\"node\\":"]'), ['"node":'])

    def test_node_signal_matches_its_own_quoted_token_against_real_json(self):
        entries, warnings = detect.parse_registry(REGISTRY)
        self.assertEqual(warnings, [])
        node_entry = next(e for e in entries if e["signal"] == "node")
        corpus = '{\n  "engines": {\n    "node": ">=18"\n  }\n}\n'
        matched = [m for m in node_entry["match"] if m and m.lower() in corpus.lower()]
        self.assertIn('"node":', matched)


class LockfileHashCollisionTests(unittest.TestCase):
    """Regression for: short match tokens (`rq`, `koa`, bare `gin`, bare `echo`) firing on
    substrings inside base64-encoded lockfile hashes, ordinary English words, and shell
    commands, rather than on the actual dependency they were meant to detect.
    """

    def setUp(self):
        self.entries, warnings = detect.parse_registry(REGISTRY)
        self.assertEqual(warnings, [])

    def test_gosum_hash_containing_rq_does_not_trigger_task_queue(self):
        # The actual collision found in gothinkster/golang-gin-realworld-example-app's
        # go.sum (docs/evaluation.md §3.1): a bare "rq" token matched inside this hash.
        corpus = "github.com/gomodule/redigo v1.8.9 h1:hxrqLVvrK65+Vbma1backReeH2WgSZ2FE=\n"
        self.assertNotIn("task-queue", matched_signals(corpus, self.entries))

    def test_gosum_hash_containing_koa_does_not_trigger_rest(self):
        # The actual collision found in the same repository's go.sum (§3.1): "koa" matched
        # case-insensitively inside a hash fragment unrelated to the Koa.js framework.
        corpus = "github.com/some/module v0.3.1 h1:aBcDeFgHiJkOaLmNoPqRsTuVwXyZ0123456789A=\n"
        self.assertNotIn("rest", matched_signals(corpus, self.entries))

    def test_logging_word_does_not_trigger_gin_detection(self):
        # The actual collision found in fastapi/full-stack-fastapi-template's
        # compose.override.yml (§3.2): "gin" matched inside the English word "logging".
        corpus = "services:\n  backend:\n    logging:\n      driver: json-file\n"
        self.assertNotIn("rest", matched_signals(corpus, self.entries))

    def test_shell_echo_commands_do_not_trigger_echo_framework_detection(self):
        # The actual collision found in the same repository's CI workflow YAML (§3.2):
        # "echo" matched inside ordinary shell `echo` commands, unrelated to the Echo
        # Go framework.
        corpus = "steps:\n  - run: echo \"Building image\"\n  - run: echo Done\n"
        self.assertNotIn("rest", matched_signals(corpus, self.entries))

    def test_fully_qualified_go_module_paths_still_match(self):
        # The fix must narrow, not remove, real detection: a genuine Gin/Echo dependency
        # declared by its fully-qualified module path must still be caught.
        corpus = "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n\tgithub.com/labstack/echo/v4 v4.11.1\n)\n"
        self.assertIn("rest", matched_signals(corpus, self.entries))

    def test_django_rq_and_python_rq_still_match_task_queue(self):
        # Same narrowing principle for the task-queue signal's rq tokens.
        corpus = "django-rq==2.10.1\npython-rq==1.15\n"
        self.assertIn("task-queue", matched_signals(corpus, self.entries))

    def test_koa_dependency_declaration_still_matches_rest(self):
        # Same narrowing principle for the quoted "koa": token.
        corpus = '{\n  "dependencies": {\n    "koa": "^2.14.2"\n  }\n}\n'
        self.assertIn("rest", matched_signals(corpus, self.entries))


if __name__ == "__main__":
    unittest.main()
