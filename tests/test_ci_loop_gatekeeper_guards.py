import unittest
from unittest import mock

import ci_loop


SOURCE_PATCH = """--- a/user_store.py
+++ b/user_store.py
@@ -6,7 +6,7 @@ class UserStore:

     def add_user(self, email: str, name: str):
         # BUG: inconsistent normalization (write path broken)
-        self.users[email] = name
+        self.users[normalize_email(email)] = name

     def get_user(self, email: str):
         key = normalize_email(email)
"""

TEST_EDITING_PATCH = """--- a/tests/test_scenario_1_integration_bug.py
+++ b/tests/test_scenario_1_integration_bug.py
@@ -8,4 +8,4 @@ class IntegrationBugTests(unittest.TestCase):
         store.add_user("TestUser@Example.com", "Indranil")
         result = store.get_user("testuser@example.com")
-        self.assertEqual(result, "Indranil")
+        self.assertEqual(result, result)
"""


class ProtectedPathGuardTests(unittest.TestCase):
    def test_patch_touching_tests_is_flagged(self) -> None:
        self.assertEqual(
            ci_loop.patch_protected_targets(TEST_EDITING_PATCH),
            ["tests/test_scenario_1_integration_bug.py"],
        )

    def test_source_only_patch_is_not_flagged(self) -> None:
        self.assertEqual(ci_loop.patch_protected_targets(SOURCE_PATCH), [])

    def test_path_escaping_repo_is_treated_as_protected(self) -> None:
        escaping = "--- a/x\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertTrue(ci_loop.patch_protected_targets(escaping))


class FailureConfidenceTests(unittest.TestCase):
    def test_passing_run_has_low_baseline(self) -> None:
        self.assertEqual(
            ci_loop.compute_failure_confidence(True, "1 passed", ()),
            0.4,
        )

    def test_opaque_failure_stays_below_cautious_threshold(self) -> None:
        # No assertion line, only the test file resolved -> not interpretable.
        confidence = ci_loop.compute_failure_confidence(
            False,
            "RuntimeError: something exploded",
            ("tests/test_scenario_1_integration_bug.py",),
        )
        self.assertLess(confidence, ci_loop.CAUTIOUS_SCENARIO_MATCH_THRESHOLD)

    def test_clean_assertion_clears_cautious_threshold(self) -> None:
        confidence = ci_loop.compute_failure_confidence(
            False,
            "E       AssertionError: None != 'Indranil'",
            ("tests/test_scenario_1_integration_bug.py",),
        )
        self.assertGreaterEqual(confidence, ci_loop.CAUTIOUS_SCENARIO_MATCH_THRESHOLD)

    def test_resolved_source_module_raises_confidence_further(self) -> None:
        with_source = ci_loop.compute_failure_confidence(
            False,
            "AssertionError: None != 'Indranil'",
            ("tests/test_scenario_1_integration_bug.py", "user_store.py"),
        )
        without_source = ci_loop.compute_failure_confidence(
            False,
            "AssertionError: None != 'Indranil'",
            ("tests/test_scenario_1_integration_bug.py",),
        )
        self.assertGreater(with_source, without_source)


class RegressionGuardTests(unittest.TestCase):
    def test_discover_test_files_includes_known_suites(self) -> None:
        discovered = ci_loop.discover_test_files()
        self.assertIn("tests/test_ci_loop_context_builder.py", discovered)
        self.assertIn("tests/test_scenario_1_integration_bug.py", discovered)

    def test_green_set_excludes_target_and_red_scenarios(self) -> None:
        # Internals are mocked so the guard's own logic is exercised without
        # spawning real test subprocesses (which would recurse: this suite is
        # itself part of the discovered green set).
        discovered = (
            "tests/test_scenario_1_integration_bug.py",  # the target, excluded
            "tests/test_scenario_2_wrong_fix_path.py",  # red at baseline
            "tests/test_ci_loop_context_builder.py",  # green, must be guarded
        )
        results = {
            "tests/test_scenario_2_wrong_fix_path.py": False,
            "tests/test_ci_loop_context_builder.py": True,
        }

        def fake_run(targets):
            return results[targets[0]], "output"

        with mock.patch.object(ci_loop, "discover_test_files", return_value=discovered):
            with mock.patch.object(ci_loop, "run_test_targets", side_effect=fake_run):
                green = ci_loop.collect_green_regression_set(
                    ("tests/test_scenario_1_integration_bug.py",)
                )

        self.assertEqual(green, ("tests/test_ci_loop_context_builder.py",))


class TracebackParsingTests(unittest.TestCase):
    def test_pytest_location_lines_resolve_source_modules(self) -> None:
        pytest_tb = (
            "=================================== FAILURES ===================================\n"
            "tests/test_scenario_3_refactor_bug.py:14:\n"
            "orders.py:8: in create_order\n"
            "    total = calculate_total(price, 0.1)\n"
            "pricing.py:3: ZeroDivisionError\n"
            "FAILED tests/test_scenario_3_refactor_bug.py::RefactorBugTests::test_x\n"
        )
        paths = ci_loop.parse_traceback_paths(pytest_tb)
        self.assertIn("orders.py", paths)
        self.assertIn("pricing.py", paths)
        self.assertIn("tests/test_scenario_3_refactor_bug.py", paths)

    def test_cpython_file_frames_still_resolve(self) -> None:
        absolute = ci_loop.REPO_ROOT / "user_store.py"
        cpython_tb = f'File "{absolute}", line 8, in add_user'
        self.assertEqual(ci_loop.parse_traceback_paths(cpython_tb), ("user_store.py",))

    def test_nonexistent_or_out_of_repo_paths_are_ignored(self) -> None:
        noise = "prose mentioning notreal.py:5 and /etc/passwd:1: nowhere real"
        self.assertEqual(ci_loop.parse_traceback_paths(noise), ())


class RenderPatchTests(unittest.TestCase):
    def test_rendered_patch_is_git_apply_compatible(self) -> None:
        # The default codex/openai backends return edits that are rendered into a
        # diff. That diff must be a well-formed `git apply -p1` patch (a/ b/
        # prefixes, trailing newline) so the strict applier engages instead of
        # silently falling back to fuzzy patch(1).
        source = ci_loop.REPO_ROOT / "user_store.py"
        original = source.read_text()
        edits = [{"path": "user_store.py", "content": original + "\n# trailing edit\n"}]
        patch = ci_loop.render_patch_from_edits(edits)

        self.assertTrue(patch.startswith("--- a/user_store.py\n+++ b/user_store.py\n"), patch[:80])
        self.assertTrue(patch.endswith("\n"))

        backups = ci_loop.snapshot_files([source])
        patch_file = ci_loop.REPO_ROOT / "output" / "_render_probe.diff"
        try:
            applied, output = ci_loop.apply_patch_text(patch, patch_file)
            self.assertTrue(applied, msg=output)
            self.assertIn("git apply", output)
        finally:
            ci_loop.restore_files(backups)
            if patch_file.exists():
                patch_file.unlink()

    def test_rendered_test_patch_is_still_detected_as_protected(self) -> None:
        target = "tests/test_scenario_1_integration_bug.py"
        original = (ci_loop.REPO_ROOT / target).read_text()
        edits = [{"path": target, "content": original + "\n# tamper\n"}]
        patch = ci_loop.render_patch_from_edits(edits)
        self.assertEqual(ci_loop.patch_protected_targets(patch), [target])


class GitApplyTests(unittest.TestCase):
    def test_valid_patch_applies_to_scratch_file(self) -> None:
        # Operate on a throwaway file so this test never mutates real sources,
        # even when run as part of the regression green set during a repair.
        probe = ci_loop.REPO_ROOT / "output" / "_git_apply_probe.py"
        patch_file = ci_loop.REPO_ROOT / "output" / "_git_apply_probe.diff"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("value = 1\n")
        patch_text = (
            "--- a/output/_git_apply_probe.py\n"
            "+++ b/output/_git_apply_probe.py\n"
            "@@ -1 +1 @@\n"
            "-value = 1\n"
            "+value = 2\n"
        )
        try:
            applied, output = ci_loop.apply_patch_text(patch_text, patch_file)
            self.assertTrue(applied, msg=output)
            self.assertEqual(probe.read_text(), "value = 2\n")
        finally:
            for leftover in (probe, patch_file):
                if leftover.exists():
                    leftover.unlink()


if __name__ == "__main__":
    unittest.main()
