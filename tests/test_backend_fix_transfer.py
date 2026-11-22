"""The backend performance-fix harness: fixed selection rules and localization scoring.

Nothing here touches the network. `gh`, `git fetch` and the stack detector are replaced by
fakes, so the tests pin the rules, not GitHub's current search results.
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

import backend_fix_transfer as bft  # noqa: E402

PATCH = """diff --git a/internal/store/orders.go b/internal/store/orders.go
index 1111111..2222222 100644
--- a/internal/store/orders.go
+++ b/internal/store/orders.go
@@ -40,7 +40,9 @@ func (s *Store) ListOrders(ctx context.Context) ([]Order, error) {
-	for _, o := range orders {
-		s.loadCustomer(ctx, &o)
-	}
+	ids := customerIDs(orders)
+	customers, err := s.loadCustomers(ctx, ids)
+	if err != nil {
+		return nil, err
+	}
diff --git a/internal/store/orders_test.go b/internal/store/orders_test.go
--- a/internal/store/orders_test.go
+++ b/internal/store/orders_test.go
@@ -1,3 +1,4 @@
+// test
diff --git a/docs/perf.md b/docs/perf.md
--- a/docs/perf.md
+++ b/docs/perf.md
@@ -1 +1,2 @@
+note
"""


def pr(number, title="perf: batch customer lookups in order listing", patch=PATCH, base="b" * 40):
    return {"number": number, "title": title, "body": "", "base_commit": base,
            "merge_commit": "m" * 40, "patch": patch}


class SourceFileRules(unittest.TestCase):

    def test_source_files_across_backend_languages(self):
        for path in ("internal/store/orders.go", "app/services/orders.py",
                     "src/main/java/com/x/OrderService.java", "src/orders/service.ts",
                     "app/models/order.rb", "src/handlers.rs", "src/Orders/OrderService.cs",
                     "lib/Orders.php"):
            with self.subTest(path=path):
                self.assertTrue(bft.is_source(path))

    def test_tests_docs_generated_vendored_and_migrations_never_count(self):
        for path in ("internal/store/orders_test.go", "tests/test_orders.py",
                     "src/orders/service.spec.ts", "src/test/java/com/x/OrderServiceTest.java",
                     "spec/models/order_spec.rb", "docs/perf.md", "vendor/lib/x.go",
                     "node_modules/a/index.js", "db/migrations/0001_init.sql",
                     "api/orders.pb.go", "web/dist/app.min.js", "types/index.d.ts",
                     "package-lock.json", "go.sum"):
            with self.subTest(path=path):
                self.assertFalse(bft.is_source(path))

    def test_changed_regions_keep_only_source_hunks(self):
        self.assertEqual(bft.changed_regions(PATCH), {"internal/store/orders.go": [(40, 46)]})
        self.assertEqual(bft.changed_lines(PATCH), 8)
        self.assertIn("ListOrders", bft.hunk_contexts(PATCH))


class Selection(unittest.TestCase):

    def test_title_must_state_a_performance_change(self):
        self.assertIsNone(bft.eligible(pr(1)))
        self.assertIn("does not state", bft.eligible(pr(1, title="fix: order totals")))
        for title in ("Revert \"perf: faster listing\"", "perf: faster tests",
                      "chore: bump perf deps", "docs: performance guide"):
            with self.subTest(title=title):
                self.assertIn("revert, test", bft.eligible(pr(1, title=title)))

    def test_source_file_and_size_limits(self):
        no_source = "diff --git a/docs/a.md b/docs/a.md\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertIn("0 source files", bft.eligible(pr(1, patch=no_source)))
        many = "".join("diff --git a/s%d.go b/s%d.go\n@@ -1 +1 @@\n-a\n+b\n" % (i, i)
                       for i in range(4))
        self.assertIn("4 source files", bft.eligible(pr(1, patch=many)))
        big = "diff --git a/s.go b/s.go\n@@ -1,500 +1,500 @@\n" + "+x\n" * 401
        self.assertIn("more than 400", bft.eligible(pr(1, patch=big)))

    def test_one_pr_per_repo_in_hash_order_and_exclusions_applied(self):
        snapshot = {"repositories": [
            {"full_name": "acme/shop", "license": "mit",
             "pull_requests": [pr(n) for n in (5, 9, 12)]},
            {"full_name": "acme/blog", "license": "mit", "pull_requests": [pr(3)]},
            {"full_name": "gothinkster/old", "license": "mit", "pull_requests": [pr(1)]},
        ]}
        chosen, reasons = bft.select(snapshot, excluded_repos=["GoThinkster/old"])
        self.assertEqual([c["repo"] for c in chosen],
                         sorted(["acme/shop", "acme/blog"], key=bft._sha))
        shop = next(c for c in chosen if c["repo"] == "acme/shop")
        first = min((5, 9, 12), key=lambda n: bft._sha("acme/shop#%d" % n))
        self.assertEqual(shop["number"], first)
        self.assertEqual(shop["source_files"], ["internal/store/orders.go"])
        self.assertTrue(any(r.get("excluded") == "used by an earlier case" for r in reasons))

    def test_selection_does_not_depend_on_snapshot_order(self):
        repos = [{"full_name": "o/r%d" % i, "pull_requests": [pr(i)]} for i in range(6)]
        forward, _ = bft.select({"repositories": repos})
        backward, _ = bft.select({"repositories": list(reversed(repos))})
        self.assertEqual(forward, backward)


class Symptom(unittest.TestCase):

    def test_neutral_symptom_passes(self):
        self.assertEqual(bft.symptom_leaks(
            "Listing orders takes several seconds when an account has many orders.", PATCH), [])

    def test_symptom_naming_the_changed_file_or_function_is_rejected(self):
        for symptom in ("orders.go is slow.", "Something in store/orders.go is slow.",
                        "internal/store/orders.go takes seconds."):
            with self.subTest(symptom=symptom):
                self.assertTrue(any("changed file" in leak
                                    for leak in bft.symptom_leaks(symptom, PATCH)))
        leaks = bft.symptom_leaks("ListOrders is slow with many orders.", PATCH)
        self.assertTrue(any("ListOrders" in leak for leak in leaks))


class Verify(unittest.TestCase):

    def detection(self, framework=True, data=True):
        detected = {"runtime": [{"signal": "go"}]}
        if framework:
            detected["framework"] = [{"signal": "rest"}]
        if data:
            detected["datastore"] = [{"signal": "postgres"}]
        return {"detected": detected}

    def test_backend_needs_a_web_framework_and_a_datastore_or_cache(self):
        self.assertTrue(bft.routes_backend(self.detection()))
        self.assertFalse(bft.routes_backend(self.detection(framework=False)))
        self.assertFalse(bft.routes_backend(self.detection(data=False)))
        cache_only = {"detected": {"framework": [{"signal": "grpc"}],
                                   "cache": [{"signal": "redis"}]}}
        self.assertTrue(bft.routes_backend(cache_only))

    def test_verify_stops_at_the_requested_number_and_records_failures(self):
        candidates = [{"repo": "o/r%d" % i, "base_commit": "c" * 40} for i in range(5)]
        detections = {"o__r0": self.detection(data=False)}

        def fetcher(repo, commit, destination):
            destination.mkdir(parents=True)
            (destination / "main.go").write_text("package main\n")
            return destination

        def detect(path):
            return detections.get(Path(path).name, self.detection())

        with tempfile.TemporaryDirectory() as work:
            passed, records = bft.verify(candidates, work, 2, detect=detect, fetcher=fetcher)
        self.assertEqual([p["repo"] for p in passed], ["o/r1", "o/r2"])
        self.assertEqual(len(records), 3)
        self.assertIn("no web framework", records[0]["excluded"])
        self.assertEqual(records[1]["review_files"], 1)

    def test_an_unfetchable_base_commit_is_recorded_not_fatal(self):
        candidates = [{"repo": "o/gone", "base_commit": "c" * 40},
                      {"repo": "o/ok", "base_commit": "c" * 40}]

        def fetcher(repo, commit, destination):
            if repo == "o/gone":
                raise bft.FetchError("not our ref")
            destination.mkdir(parents=True)
            (destination / "main.go").write_text("package main\n")
            return destination

        with tempfile.TemporaryDirectory() as work:
            passed, records = bft.verify(candidates, work, 5, detect=lambda p: self.detection(),
                                         fetcher=fetcher)
        self.assertEqual([p["repo"] for p in passed], ["o/ok"])
        self.assertEqual(records[0]["excluded"], "base commit could not be fetched")


class ServiceClassification(unittest.TestCase):

    def test_packet_holds_only_readme_excerpt_and_top_level_listing(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = Path(work) / "acme__shop"
            (checkout / ".git").mkdir(parents=True)
            (checkout / "cmd").mkdir()
            (checkout / "README.md").write_text("# Shop\n" + "x" * 9000)
            (checkout / "docker-compose.yml").write_text("services: {}\n")
            packet = bft.packets([{"repo": "acme/shop"},
                                  {"repo": "acme/lib", "excluded": "detector"}], work)
        self.assertEqual(len(packet), 1)
        self.assertEqual(set(packet[0]), {"repo", "question", "top_level", "readme_excerpt"})
        self.assertEqual(packet[0]["top_level"], ["README.md", "cmd/", "docker-compose.yml"])
        self.assertEqual(len(packet[0]["readme_excerpt"]), bft.README_CHARS)
        self.assertIn("library, framework", packet[0]["question"])

    def test_accept_keeps_services_in_order_and_requires_every_decision(self):
        verified = [{"repo": "o/a"}, {"repo": "o/b", "excluded": "detector"},
                    {"repo": "o/c"}, {"repo": "o/d"}, {"repo": "o/e"}]
        decisions = [{"repo": "o/a", "service": False, "reason": "a framework"},
                     {"repo": "o/c", "service": True, "reason": "self-hosted server"},
                     {"repo": "o/d", "service": True, "reason": "API service"},
                     {"repo": "o/e", "service": True, "reason": "API service"}]
        accepted, rejected = bft.accept(verified, decisions, 2)
        self.assertEqual([r["repo"] for r in accepted], ["o/c", "o/d"])
        self.assertEqual(rejected[0]["classifier_reason"], "a framework")
        with self.assertRaises(ValueError):
            bft.accept(verified, decisions[1:], 2)


class FixCheck(unittest.TestCase):

    def candidates(self):
        snapshot = {"repositories": [
            {"full_name": "acme/shop", "pull_requests": [pr(n) for n in (5, 9, 12)]},
            {"full_name": "acme/blog", "pull_requests": [pr(3)]},
        ]}
        chosen, _ = bft.select(snapshot, all_prs=True)
        return chosen

    def test_select_all_ranks_every_eligible_pr_and_records_its_paths(self):
        chosen = self.candidates()
        shop = [c for c in chosen if c["repo"] == "acme/shop"]
        self.assertEqual([c["rank"] for c in shop], [0, 1, 2])
        self.assertEqual([c["number"] for c in shop],
                         sorted((5, 9, 12), key=lambda n: bft._sha("acme/shop#%d" % n)))
        self.assertEqual(shop[0]["changed_paths"], ["internal/store/orders.go",
                                                    "internal/store/orders_test.go",
                                                    "docs/perf.md"])
        default, _ = bft.select({"repositories": [
            {"full_name": "acme/shop", "pull_requests": [pr(n) for n in (5, 9, 12)]}]})
        self.assertEqual(len(default), 1)

    def test_fix_packets_cover_only_service_repos_and_carry_no_diff(self):
        packets = bft.fix_packets(self.candidates(), ["acme/shop"])
        self.assertEqual({p["repo"] for p in packets}, {"acme/shop"})
        self.assertEqual(set(packets[0]), {"repo", "number", "question", "title", "body",
                                           "changed_paths"})
        self.assertIn("server-side", packets[0]["question"])

    def test_accept_fixes_moves_to_the_next_pr_when_the_first_is_not_a_fix(self):
        chosen = self.candidates()
        shop = [c for c in chosen if c["repo"] == "acme/shop"]
        services = [{"repo": "acme/shop", "review_files": 40}, {"repo": "acme/blog"}]
        decisions = [{"repo": "acme/shop", "number": shop[0]["number"],
                      "server_side_fix": False, "reason": "frontend"},
                     {"repo": "acme/shop", "number": shop[1]["number"],
                      "server_side_fix": True, "reason": "batches queries"},
                     {"repo": "acme/shop", "number": shop[2]["number"],
                      "server_side_fix": True, "reason": "x"},
                     {"repo": "acme/blog", "number": 3, "server_side_fix": False,
                      "reason": "docs"}]
        accepted, rejected = bft.accept_fixes(services, chosen, decisions, 10)
        self.assertEqual([(a["repo"], a["number"]) for a in accepted],
                         [("acme/shop", shop[1]["number"])])
        self.assertEqual(accepted[0]["review_files"], 40)
        self.assertEqual(len(rejected), 2)
        with self.assertRaises(ValueError):
            bft.accept_fixes(services, chosen, decisions[1:], 10)

    def test_pool_searches_service_topics(self):
        queries = []

        def gh(args, accept=None):
            queries.append([a for a in args if a.startswith("q=")][0])
            return json.dumps({"items": [{"full_name": "acme/svc", "size": 10,
                                          "license": {"spdx_id": "MIT"}}]})

        original = bft.time.sleep
        bft.time.sleep = lambda seconds: None
        try:
            pool = bft.repository_pool(gh)
        finally:
            bft.time.sleep = original
        self.assertEqual(len(queries), len(bft.POOL_TOPICS) * len(bft.LANGUAGES))
        self.assertTrue(all("topic:" in q for q in queries))
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["topic"], bft.POOL_TOPICS[0])


class Collect(unittest.TestCase):

    def test_collect_snapshots_prs_with_their_base_commit_and_diff(self):
        calls = []

        def gh(args, accept=None):
            calls.append(args[0])
            if args[0] == "search/repositories":
                page = [a for a in args if a.startswith("page=")][0]
                items = [] if page != "page=1" else [
                    {"full_name": "acme/shop", "license": {"spdx_id": "MIT"},
                     "stargazers_count": 900, "size": 5000},
                    {"full_name": "acme/huge", "license": {"spdx_id": "MIT"},
                     "size": bft.MAX_REPO_KB + 1},
                    {"full_name": "acme/closed", "license": {"spdx_id": "NOASSERTION"}}]
                return json.dumps({"items": items})
            if args[0] == "search/issues":
                return json.dumps({"items": [{"number": 7, "title": "perf: faster listing"},
                                             {"number": 8, "title": "fix typo"}]})
            if args[0].endswith("/pulls/7"):
                return json.dumps({"merge_commit_sha": "m" * 40, "body": "slow",
                                   "merged_at": "2026-05-01T00:00:00Z"})
            if "/commits/" in args[0]:
                return json.dumps({"parents": [{"sha": "p" * 40}]})
            if "/compare/" in args[0]:
                self.assertEqual(accept, "application/vnd.github.diff")
                return PATCH
            raise AssertionError(args)

        original = bft.time.sleep
        bft.time.sleep = lambda seconds: None
        try:
            snapshot = bft.collect(5, gh=gh, log=io.StringIO())
        finally:
            bft.time.sleep = original
        self.assertEqual([r["full_name"] for r in snapshot["repositories"]], ["acme/shop"])
        prs = snapshot["repositories"][0]["pull_requests"]
        self.assertEqual([p["number"] for p in prs], [7])
        self.assertEqual(prs[0]["base_commit"], "p" * 40)
        self.assertEqual(snapshot["rules"]["max_source_files"], 3)
        self.assertEqual(snapshot["pool"], ["acme/shop"])

    def test_batches_extend_the_pool_in_its_fixed_order(self):
        names = ["o/r%d" % i for i in range(5)]

        def gh(args, accept=None):
            if args[0] == "search/repositories":
                page = [a for a in args if a.startswith("page=")][0]
                return json.dumps({"items": [] if page != "page=1" else [
                    {"full_name": n, "license": {"spdx_id": "MIT"}, "size": 1} for n in names]})
            return json.dumps({"items": []})

        original = bft.time.sleep
        bft.time.sleep = lambda seconds: None
        try:
            first = bft.collect(2, gh=gh, log=io.StringIO())
            second = bft.collect(2, gh=gh, log=io.StringIO(), offset=2)
        finally:
            bft.time.sleep = original
        ordered = sorted(names, key=bft._sha)
        self.assertEqual(first["pool"], ordered)
        self.assertEqual([r["full_name"] for r in first["repositories"] + second["repositories"]],
                         ordered[:4])

    def test_a_frozen_pool_is_reused_without_searching_again(self):
        pool = [{"full_name": "o/a", "license": "mit"}, {"full_name": "o/b", "license": "mit"}]

        def gh(args, accept=None):
            self.assertNotEqual(args[0], "search/repositories")
            return json.dumps({"items": []})

        original = bft.time.sleep
        bft.time.sleep = lambda seconds: None
        try:
            snapshot = bft.collect(5, gh=gh, log=io.StringIO(), pool=pool)
        finally:
            bft.time.sleep = original
        self.assertEqual(snapshot["pool"], ["o/a", "o/b"])
        self.assertEqual(snapshot["pool_sha256"],
                         bft._sha(json.dumps(pool, sort_keys=True)))

    def test_transient_network_errors_are_retried(self):
        outcomes = [subprocess_result(1, "dial tcp: connectex: timed out"),
                    subprocess_result(0, "", stdout="{}")]
        original_run, original_sleep = bft.subprocess.run, bft.time.sleep
        bft.subprocess.run = lambda *a, **k: outcomes.pop(0)
        bft.time.sleep = lambda seconds: None
        try:
            self.assertEqual(bft._gh(["rate_limit"]), "{}")
        finally:
            bft.subprocess.run, bft.time.sleep = original_run, original_sleep

    def test_other_errors_fail_at_once(self):
        original_run = bft.subprocess.run
        bft.subprocess.run = lambda *a, **k: subprocess_result(1, "HTTP 404: Not Found")
        try:
            with self.assertRaises(RuntimeError):
                bft._gh(["repos/o/missing"])
        finally:
            bft.subprocess.run = original_run


def subprocess_result(code, stderr, stdout=""):
    class Result:
        returncode = code
    Result.stderr, Result.stdout = stderr, stdout
    return Result()


class Score(unittest.TestCase):

    def test_file_hunk_and_top_finding_hits(self):
        review = {"findings": [
            {"id": "PERF-002", "priority": "P2",
             "location": {"file": "internal/store/orders.go", "line": 44}},
            {"id": "PERF-001", "priority": "P1",
             "location": {"file": "./cmd/server/main.go", "line": 10}},
        ]}
        result = bft.localize(PATCH, review)
        self.assertTrue(result["file_hit"])
        self.assertTrue(result["hunk_hit"])
        self.assertEqual(result["top_finding"], "PERF-001")
        self.assertFalse(result["top_finding_file_hit"])

    def test_line_outside_the_widened_hunk_is_a_file_hit_only(self):
        review = {"findings": [{"id": "PERF-001", "location": {
            "file": "C:/work/repo/internal/store/orders.go", "line": 200}}]}
        result = bft.localize(PATCH, review)
        self.assertTrue(result["file_hit"])
        self.assertFalse(result["hunk_hit"])


if __name__ == "__main__":
    unittest.main()
