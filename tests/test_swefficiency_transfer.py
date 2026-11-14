"""Contracts for the SWE-fficiency transfer test's task selection and localization scoring.

SWE-fficiency tasks pair a repository snapshot and a slow workload with the patch an expert
wrote to speed it up. The transfer test asks whether a review points at the code the expert
changed. These tests pin how changed regions are read from a patch, how a review's findings
are matched to them, and how tasks are selected before any review exists.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

import swefficiency_transfer as transfer  # noqa: E402


PATCH = """diff --git a/pandas/core/groupby/ops.py b/pandas/core/groupby/ops.py
index 1111111..2222222 100644
--- a/pandas/core/groupby/ops.py
+++ b/pandas/core/groupby/ops.py
@@ -120,7 +120,9 @@ class BaseGrouper:
     def indices(self):
-        return slow(self)
+        if self._cache:
+            return self._cache
+        return fast(self)
@@ -400,3 +402,3 @@ def agg_series(obj, func):
-    out = [func(x) for x in obj]
+    out = func_vectorized(obj)
diff --git a/pandas/tests/groupby/test_ops.py b/pandas/tests/groupby/test_ops.py
--- a/pandas/tests/groupby/test_ops.py
+++ b/pandas/tests/groupby/test_ops.py
@@ -1,1 +1,2 @@
+import pytest
diff --git a/doc/source/whatsnew/v1.2.0.rst b/doc/source/whatsnew/v1.2.0.rst
--- a/doc/source/whatsnew/v1.2.0.rst
+++ b/doc/source/whatsnew/v1.2.0.rst
@@ -10,0 +11,1 @@
+- Faster groupby indices
diff --git a/asv_bench/benchmarks/groupby.py b/asv_bench/benchmarks/groupby.py
--- a/asv_bench/benchmarks/groupby.py
+++ b/asv_bench/benchmarks/groupby.py
@@ -5,0 +6,2 @@
+class Indices:
+    pass
"""


def review(*findings):
    return {"findings": [
        {"id": fid, "priority": priority, "location": location}
        for fid, priority, location in findings]}


class ChangedRegionTests(unittest.TestCase):

    def test_only_source_files_count_and_ranges_are_base_side(self):
        regions = transfer.changed_regions(PATCH)
        self.assertEqual(list(regions), ["pandas/core/groupby/ops.py"])
        self.assertEqual(regions["pandas/core/groupby/ops.py"], [(120, 126), (400, 402)])

    def test_a_pure_insertion_is_anchored_at_its_line(self):
        patch = ("diff --git a/x/mod.py b/x/mod.py\n--- a/x/mod.py\n+++ b/x/mod.py\n"
                 "@@ -50,0 +51,2 @@\n+a\n+b\n")
        self.assertEqual(transfer.changed_regions(patch), {"x/mod.py": [(50, 50)]})

    def test_new_and_deleted_files_are_handled(self):
        patch = ("diff --git a/x/new.py b/x/new.py\nnew file mode 100644\n--- /dev/null\n"
                 "+++ b/x/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
                 "diff --git a/x/old.py b/x/old.py\ndeleted file mode 100644\n--- a/x/old.py\n"
                 "+++ /dev/null\n@@ -1,3 +0,0 @@\n-a\n-b\n-c\n")
        regions = transfer.changed_regions(patch)
        self.assertEqual(regions["x/new.py"], [(0, 0)])
        self.assertEqual(regions["x/old.py"], [(1, 3)])

    def test_cython_and_c_sources_count(self):
        patch = ("diff --git a/pkg/_lib.pyx b/pkg/_lib.pyx\n--- a/pkg/_lib.pyx\n"
                 "+++ b/pkg/_lib.pyx\n@@ -10,2 +10,2 @@\n-a\n+b\n")
        self.assertIn("pkg/_lib.pyx", transfer.changed_regions(patch))


class LocalizationTests(unittest.TestCase):

    def test_a_finding_inside_a_changed_hunk_is_a_hunk_and_file_hit(self):
        result = transfer.localize(PATCH, review(
            ("PERF-001", "P1", {"file": "pandas/core/groupby/ops.py", "line": 122})))
        self.assertTrue(result["file_hit"])
        self.assertTrue(result["hunk_hit"])
        self.assertTrue(result["top_finding_file_hit"])
        self.assertEqual(result["hunk_hits"], ["PERF-001"])

    def test_the_window_widens_a_hunk_by_a_fixed_number_of_lines(self):
        near = review(("PERF-001", "P1", {"file": "pandas/core/groupby/ops.py", "line": 105}))
        far = review(("PERF-001", "P1", {"file": "pandas/core/groupby/ops.py", "line": 99}))
        self.assertTrue(transfer.localize(PATCH, near, window=15)["hunk_hit"])
        self.assertFalse(transfer.localize(PATCH, far, window=15)["hunk_hit"])
        self.assertTrue(transfer.localize(PATCH, far, window=15)["file_hit"])

    def test_test_docs_and_benchmark_files_never_count(self):
        result = transfer.localize(PATCH, review(
            ("PERF-001", "P1", {"file": "pandas/tests/groupby/test_ops.py", "line": 1}),
            ("PERF-002", "P2", {"file": "asv_bench/benchmarks/groupby.py", "line": 6})))
        self.assertFalse(result["file_hit"])

    def test_paths_are_normalized(self):
        for path in ("./pandas/core/groupby/ops.py", "pandas\\core\\groupby\\ops.py",
                     "/work/repo/pandas/core/groupby/ops.py"):
            with self.subTest(path=path):
                result = transfer.localize(PATCH, review(("PERF-001", "P1",
                                                          {"file": path, "line": 401})))
                self.assertTrue(result["hunk_hit"], path)

    def test_the_top_finding_is_the_highest_priority_then_first_listed(self):
        result = transfer.localize(PATCH, review(
            ("PERF-001", "P2", {"file": "pandas/core/groupby/ops.py", "line": 122}),
            ("PERF-002", "P1", {"file": "pandas/core/frame.py", "line": 10})))
        self.assertEqual(result["top_finding"], "PERF-002")
        self.assertFalse(result["top_finding_file_hit"])
        self.assertTrue(result["file_hit"])

    def test_a_finding_without_a_line_can_hit_the_file_but_not_a_hunk(self):
        result = transfer.localize(PATCH, review(
            ("PERF-001", "P1", {"file": "pandas/core/groupby/ops.py"})))
        self.assertTrue(result["file_hit"])
        self.assertFalse(result["hunk_hit"])

    def test_no_findings_is_a_miss_not_an_error(self):
        result = transfer.localize(PATCH, {"findings": []})
        self.assertFalse(result["file_hit"])
        self.assertIsNone(result["top_finding"])


class SelectionTests(unittest.TestCase):

    def rows(self):
        def row(instance, repo, files):
            patch = "".join(
                "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1,1 +1,1 @@\n-a\n+b\n"
                .format(name) for name in files)
            return {"instance_id": instance, "repo": repo, "patch": patch,
                    "base_commit": "a" * 40, "workload": "def workload(): pass"}
        return [row("pandas-dev__pandas-1", "pandas-dev/pandas", ["pandas/a.py"]),
                row("pandas-dev__pandas-2", "pandas-dev/pandas", ["pandas/b.py"]),
                row("pandas-dev__pandas-3", "pandas-dev/pandas",
                    ["pandas/a.py", "pandas/b.py", "pandas/c.py", "pandas/d.py"]),
                row("numpy__numpy-1", "numpy/numpy", ["numpy/core/x.py"])]

    def test_one_task_per_repository_chosen_by_hash_not_by_hand(self):
        selected = transfer.select(self.rows(), per_repo=1, max_source_files=3)
        self.assertEqual(sorted(r["repo"] for r in selected),
                         ["numpy/numpy", "pandas-dev/pandas"])
        again = transfer.select(list(reversed(self.rows())), per_repo=1, max_source_files=3)
        self.assertEqual([r["instance_id"] for r in selected],
                         [r["instance_id"] for r in again])

    def test_diffuse_patches_are_excluded(self):
        selected = transfer.select(self.rows(), per_repo=3, max_source_files=3)
        self.assertNotIn("pandas-dev__pandas-3", [r["instance_id"] for r in selected])


class CommandLineTests(unittest.TestCase):

    def test_cli_scores_a_patch_and_a_review(self):
        with tempfile.TemporaryDirectory() as directory:
            patch = Path(directory) / "expert.patch"
            patch.write_text(PATCH, encoding="utf-8")
            reviewed = Path(directory) / "review.json"
            reviewed.write_text(json.dumps(review(
                ("PERF-001", "P1", {"file": "pandas/core/groupby/ops.py", "line": 401}))),
                encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "benchmark" / "swefficiency_transfer.py"),
                 "score", "--patch", str(patch), "--review", str(reviewed)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["hunk_hit"])


if __name__ == "__main__":
    unittest.main()
