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

    def test_sqlite_jdbc_dependency_matches_sqlite_signal(self):
        # Regression for a false negative found during the independent blind pass against
        # gothinkster/spring-boot-realworld-example-app (docs/evaluation.md §3.13): the
        # sqlite signal's match list covered sqlite3/better-sqlite3/mattn-go-sqlite3/libsql
        # but not org.xerial:sqlite-jdbc, the dominant JVM driver for SQLite, nor its
        # jdbc:sqlite: connection-string scheme. The reviewing agent found the datastore
        # only by manually reading application.properties against methodology/discovery.md's
        # instruction to check connection-string schemes regardless of the accelerator's
        # output — an agent that trusted detect_stack.py's output alone would have missed
        # databases/relational.md entirely and, with it, two of that review's findings.
        corpus = (
            "dependencies {\n"
            "    implementation 'org.xerial:sqlite-jdbc:3.36.0.3'\n"
            "}\n"
        )
        self.assertIn("sqlite", matched_signals(corpus, self.entries))

    def test_jdbc_sqlite_connection_scheme_matches_sqlite_signal(self):
        corpus = "spring.datasource.url=jdbc:sqlite:dev.db\n"
        self.assertIn("sqlite", matched_signals(corpus, self.entries))


class WeakEvidenceProvenanceTests(unittest.TestCase):
    """Regression for: scan()/detect() flattened every file into one corpus string, so a
    match could never be traced back to the file it came from. Self-scanning this very
    repository reported ~30 spurious signals (Cassandra, Oracle, PHP, Kubernetes, ...) with
    no way to tell they were all matching inside registry.yaml itself, the one file in the
    repo that necessarily contains every match token in the whole system by construction.
    detect() now attributes each match to its source file and kind, and grades a signal
    "weak_evidence" when every match for it came from a non-manifest YAML file rather than
    a real dependency manifest, lockfile, or matching filename.
    """

    def setUp(self):
        self.entries, warnings = detect.parse_registry(REGISTRY)
        self.assertEqual(warnings, [])

    def _record(self, signal):
        detected, _, _, _ = detect.detect(self.records, self.entries)
        for records_for_kind in detected.values():
            for rec in records_for_kind:
                if rec["signal"] == signal:
                    return rec
        return None

    def test_match_only_inside_generic_yaml_is_flagged_weak(self):
        # A signal's own match token appearing only in an arbitrary YAML file (a k8s
        # values file, here) — not a manifest, not a matching filename — is exactly the
        # shape of the registry.yaml self-scan false positive.
        self.records = [("charts/values.yaml", "cassandra:\n  enabled: true\n", "yaml")]
        record = self._record("cassandra")
        self.assertIsNotNone(record)
        self.assertTrue(record["weak_evidence"])
        for token_entry in record["matched_on"]:
            self.assertTrue(token_entry.get("weak_evidence", True))

    def test_match_inside_real_manifest_is_not_flagged_weak(self):
        # The same token, found in a real dependency manifest instead, must not be
        # downgraded — the fix narrows evidence grading, it does not suppress detection.
        self.records = [("requirements.txt", "cassandra-driver==3.28.0\n", "manifest")]
        record = self._record("cassandra")
        self.assertIsNotNone(record)
        self.assertNotIn("weak_evidence", record)

    def test_self_scan_flags_registry_yaml_only_matches_as_weak(self):
        # The actual bug, reproduced end to end: scanning this skill's own directory (which
        # necessarily contains registry.yaml, itself packed with every match token in the
        # system) must not silently report Cassandra et al. as ordinary strong detections.
        records, _, _, _ = detect.scan(str(SKILL), 256 * 1024)
        detected, _, _, _ = detect.detect(records, self.entries)
        cassandra = next(
            rec for records_for_kind in detected.values() for rec in records_for_kind
            if rec["signal"] == "cassandra"
        )
        self.assertTrue(cassandra["weak_evidence"])
        matched_files = {
            f for token_entry in cassandra["matched_on"] for f in token_entry.get("files", [])
        }
        self.assertEqual(matched_files, {"registry.yaml"})

    def test_matched_on_attributes_files_for_a_real_manifest_match(self):
        # A genuine detection (postgres via a requirements.txt entry) must carry the exact
        # file it was found in, not just the bare fact that it matched somewhere.
        self.records = [
            ("app/requirements.txt", "psycopg2-binary==2.9.9\n", "manifest"),
            (".github/workflows/ci.yml", "runs-on: ubuntu-latest\n", "yaml"),
        ]
        record = self._record("postgres")
        self.assertIsNotNone(record)
        self.assertNotIn("weak_evidence", record)
        token_entry = next(t for t in record["matched_on"] if t["token"] == "psycopg2")
        self.assertEqual(token_entry["files"], ["app/requirements.txt"])
        self.assertFalse(token_entry["weak_evidence"])

    def test_legacy_flat_string_corpus_still_detects_without_grading(self):
        # detect() must keep accepting a plain string corpus (as every test above this
        # class does, and as any external caller predating this change would) — it simply
        # can't grade evidence it has no file attribution for, so weak_evidence is omitted
        # rather than guessed.
        corpus = "psycopg2==2.9.6\n"
        self.assertIn("postgres", matched_signals(corpus, self.entries))
        record = self._record_from_string("postgres", corpus)
        self.assertNotIn("weak_evidence", record)

    def _record_from_string(self, signal, corpus):
        detected, _, _, _ = detect.detect(corpus, self.entries)
        for records_for_kind in detected.values():
            for rec in records_for_kind:
                if rec["signal"] == signal:
                    return rec
        return None


if __name__ == "__main__":
    unittest.main()
