import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import ci_loop


class FailureDrivenContextTests(unittest.TestCase):
    def test_run_all_scenario_choices_excludes_low_confidence_demo_scenario(self) -> None:
        self.assertIn("scenario_4_low_confidence", ci_loop.scenario_choices())
        self.assertNotIn("scenario_4_low_confidence", ci_loop.scenario_choices(include_non_gating=False))

    def test_load_test_scenario_registry_returns_seeded_records(self) -> None:
        records = ci_loop.load_test_scenario_registry()
        record_ids = {record.scenario_id for record in records}

        self.assertIn("write_path_canonicalization_bug", record_ids)
        self.assertIn("write_path_key_helper_bug", record_ids)
        self.assertIn("refactor_contract_drift_bug", record_ids)

    def test_build_failure_record_captures_failed_test_and_output(self) -> None:
        record = ci_loop.build_failure_record("scenario_1_integration_bug")

        self.assertEqual(record.failed_tests, ("tests/test_scenario_1_integration_bug.py",))
        self.assertIn("tests/test_scenario_1_integration_bug.py", record.likely_modules)
        self.assertGreater(record.confidence, 0.5)
        self.assertIn("AssertionError", record.failure_output)

    def test_build_context_includes_failure_sections_and_dynamic_dependencies(self) -> None:
        context = ci_loop.build_context("scenario_3_refactor_bug")

        self.assertIn("# FAILURE_RECORD", context)
        self.assertIn("# FAILURE_OUTPUT", context)
        self.assertIn("# FILE: tests/test_scenario_3_refactor_bug.py", context)
        self.assertIn("# FILE: orders.py", context)
        self.assertIn("# FILE: pricing.py", context)
        self.assertNotIn("# FILE: demo_scenarios/", context)

    def test_context_resolution_keeps_static_fallback_paths(self) -> None:
        record = ci_loop.FailureRecord(
            failed_tests=(),
            failure_summary="synthetic",
            failure_output="synthetic",
            likely_modules=(),
            confidence=0.0,
        )

        context_files = ci_loop.resolve_context_file_paths("scenario_2_wrong_fix_path", record)

        self.assertIn("tests/test_scenario_2_wrong_fix_path.py", context_files)
        self.assertIn("user_registry.py", context_files)
        self.assertIn("utils.py", context_files)

    def test_prioritize_delta_paths_prefers_context_related_changes(self) -> None:
        changed_paths = (
            "ci_loop.py",
            "orders.py",
            "tests/test_scenario_3_refactor_bug.py",
            "tests/test_ci_loop_context_builder.py",
        )
        context_files = (
            "tests/test_scenario_3_refactor_bug.py",
            "orders.py",
            "pricing.py",
        )

        prioritized = ci_loop.prioritize_delta_paths(changed_paths, context_files)

        self.assertEqual(
            prioritized,
            ("tests/test_scenario_3_refactor_bug.py", "orders.py"),
        )

    def test_build_context_includes_recent_repo_delta_when_available(self) -> None:
        repo_delta = ci_loop.RepoDelta(
            source="working_tree",
            changed_files=("orders.py",),
            diff_text="diff --git a/orders.py b/orders.py\n",
        )

        with mock.patch("ci_loop.collect_recent_repo_delta", return_value=repo_delta):
            context = ci_loop.build_context("scenario_3_refactor_bug")

        self.assertIn("# RECENT_REPO_DELTA", context)
        self.assertIn('"source": "working_tree"', context)
        self.assertIn('"changed_files": [\n    "orders.py"\n  ]', context)
        self.assertIn("# RECENT_REPO_DELTA_DIFF", context)
        self.assertIn("diff --git a/orders.py b/orders.py", context)

    def test_lookup_test_scenario_match_prefers_exact_test_overlap(self) -> None:
        failure_record = ci_loop.FailureRecord(
            failed_tests=("tests/test_scenario_2_wrong_fix_path.py",),
            failure_summary="canonical storage key missing",
            failure_output="canonical storage key missing",
            likely_modules=("tests/test_scenario_2_wrong_fix_path.py",),
            confidence=0.85,
        )
        context_files = (
            "tests/test_scenario_2_wrong_fix_path.py",
            "user_registry.py",
            "utils.py",
        )

        match = ci_loop.lookup_test_scenario_match(failure_record, context_files)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.scenario_id, "write_path_key_helper_bug")
        self.assertGreaterEqual(match.confidence, 0.8)
        self.assertIn("repair the storage key helper", match.accepted_fix_patterns)

    def test_find_best_test_scenario_candidate_can_return_medium_confidence_match(self) -> None:
        failure_record = ci_loop.FailureRecord(
            failed_tests=("tests/test_scenario_2_wrong_fix_path.py",),
            failure_summary="storage helper issue",
            failure_output="storage helper issue",
            likely_modules=("tests/test_scenario_2_wrong_fix_path.py",),
            confidence=0.85,
        )

        candidate = ci_loop.find_best_test_scenario_candidate(
            failure_record,
            ("tests/test_scenario_2_wrong_fix_path.py",),
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.scenario_id, "write_path_key_helper_bug")
        self.assertGreaterEqual(candidate.confidence, 0.5)
        self.assertLess(candidate.confidence, 0.8)

    def test_build_context_includes_matched_test_scenario_record(self) -> None:
        record = next(
            item for item in ci_loop.load_test_scenario_registry() if item.scenario_id == "write_path_key_helper_bug"
        )
        analysis = ci_loop.FailureAnalysis(
            failure_record=ci_loop.FailureRecord(
                failed_tests=("tests/test_scenario_2_wrong_fix_path.py",),
                failure_summary="canonical storage key missing",
                failure_output="canonical storage key missing",
                likely_modules=("tests/test_scenario_2_wrong_fix_path.py",),
                confidence=0.9,
            ),
            context_files=("tests/test_scenario_2_wrong_fix_path.py", "user_registry.py", "utils.py"),
            repo_delta=None,
            scenario_candidate=None,
            scenario_match=ci_loop.ScenarioMatch(
                matched=True,
                source="test_scenarios",
                scenario_id=record.scenario_id,
                confidence=0.95,
                accepted_fix_patterns=record.accepted_fix_patterns,
                rejected_fix_patterns=record.rejected_fix_patterns,
                invariants=record.invariants,
                record=record,
            ),
            clarification_plan=ci_loop.ScenarioClarificationPlan(
                required=False,
                reason="Matched scenario with high confidence.",
                confidence_band="high",
                questions=(),
                candidate_match=None,
            ),
            scenario_proposal=None,
        )

        context = ci_loop.build_context_from_analysis(analysis)

        self.assertIn("# SCENARIO_MATCH", context)
        self.assertIn('"scenario_id": "write_path_key_helper_bug"', context)
        self.assertIn("# TEST_SCENARIO_RECORD", context)
        self.assertIn('"accepted_fix_patterns": [', context)
        self.assertIn('"rejected_fix_patterns": [', context)

    def test_build_context_includes_medium_confidence_candidate_without_blocking(self) -> None:
        record = ci_loop.load_test_scenario_registry()[1]
        analysis = ci_loop.FailureAnalysis(
            failure_record=ci_loop.FailureRecord(
                failed_tests=("tests/test_scenario_2_wrong_fix_path.py",),
                failure_summary="storage helper issue",
                failure_output="storage helper issue",
                likely_modules=("tests/test_scenario_2_wrong_fix_path.py",),
                confidence=0.85,
            ),
            context_files=("tests/test_scenario_2_wrong_fix_path.py", "user_registry.py"),
            repo_delta=None,
            scenario_candidate=ci_loop.ScenarioMatch(
                matched=False,
                source="test_scenarios",
                scenario_id=record.scenario_id,
                confidence=0.7,
                accepted_fix_patterns=record.accepted_fix_patterns,
                rejected_fix_patterns=record.rejected_fix_patterns,
                invariants=record.invariants,
                record=record,
            ),
            scenario_match=None,
            clarification_plan=ci_loop.ScenarioClarificationPlan(
                required=False,
                reason="A partial scenario candidate was found, so its guidance should be reviewed cautiously before relying on it.",
                confidence_band="medium",
                questions=(),
                candidate_match=None,
            ),
            scenario_proposal=None,
        )

        context = ci_loop.build_context_from_analysis(analysis)

        self.assertIn("# SCENARIO_CANDIDATE", context)
        self.assertIn('"confidence_band": "medium"', context)
        self.assertNotIn("# CLARIFICATION_REQUEST", context)

    def test_plan_scenario_clarification_requires_questions_for_unknown_failure(self) -> None:
        failure_record = ci_loop.FailureRecord(
            failed_tests=("tests/test_unknown_failure.py",),
            failure_summary="unexpected behavior in a new module",
            failure_output="unexpected behavior in a new module",
            likely_modules=("tests/test_unknown_failure.py",),
            confidence=0.85,
        )

        plan = ci_loop.plan_scenario_clarification(failure_record, None)

        self.assertTrue(plan.required)
        self.assertEqual(plan.confidence_band, "low")
        self.assertGreaterEqual(len(plan.questions), 3)

    def test_plan_scenario_clarification_not_required_when_no_failing_tests(self) -> None:
        failure_record = ci_loop.FailureRecord(
            failed_tests=(),
            failure_summary="all tests passed",
            failure_output="1 passed",
            likely_modules=("tests/test_scenario_2_wrong_fix_path.py",),
            confidence=0.4,
        )

        plan = ci_loop.plan_scenario_clarification(failure_record, None)

        self.assertFalse(plan.required)
        self.assertIn("No failing tests were detected", plan.reason)

    def test_generate_patch_blocks_when_clarification_is_required(self) -> None:
        analysis = ci_loop.FailureAnalysis(
            failure_record=ci_loop.FailureRecord(
                failed_tests=("tests/test_unknown_failure.py",),
                failure_summary="unexpected behavior in a new module",
                failure_output="unexpected behavior in a new module",
                likely_modules=("tests/test_unknown_failure.py",),
                confidence=0.85,
            ),
            context_files=("tests/test_unknown_failure.py",),
            repo_delta=None,
            scenario_candidate=None,
            scenario_match=None,
            clarification_plan=ci_loop.ScenarioClarificationPlan(
                required=True,
                reason="No existing test_scenarios record matched this failure with sufficient confidence.",
                confidence_band="low",
                questions=("Is the test correct?",),
                candidate_match=None,
            ),
            scenario_proposal=ci_loop.ScenarioProposal(
                scenario_id="unknown_failure_bug",
                title="Unknown Failure",
                origin="auto-proposed",
                failure_signatures=("unexpected behavior in a new module",),
                affected_tests=("tests/test_unknown_failure.py",),
                likely_code_areas=(),
                invariants=("confirm the intended invariant before persisting this scenario",),
                accepted_fix_patterns=("confirm the intended contract before approving this scenario",),
                rejected_fix_patterns=("modify tests without explicit approval",),
                review_required=True,
                proposal_reason="No existing test_scenarios record matched this failure with sufficient confidence.",
                confidence=0.0,
            ),
        )

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with mock.patch("ci_loop.analyze_failure", return_value=analysis):
                with mock.patch("ci_loop.ensure_output_dir", return_value=output_dir):
                    with mock.patch("ci_loop.request_edit_plan", side_effect=AssertionError("should not call backend")):
                        with self.assertRaises(ci_loop.ClarificationRequiredError):
                            ci_loop.generate_patch("scenario_1_integration_bug", "Fix it")

            self.assertTrue((output_dir / "clarification_request.json").exists())
            self.assertTrue((output_dir / "scenario_proposal.json").exists())

    def test_generate_patch_can_continue_when_low_confidence_is_explicitly_allowed(self) -> None:
        analysis = ci_loop.FailureAnalysis(
            failure_record=ci_loop.FailureRecord(
                failed_tests=("tests/test_unknown_failure.py",),
                failure_summary="unexpected behavior in a new module",
                failure_output="unexpected behavior in a new module",
                likely_modules=("tests/test_unknown_failure.py",),
                confidence=0.85,
            ),
            context_files=("tests/test_unknown_failure.py",),
            repo_delta=None,
            scenario_candidate=None,
            scenario_match=None,
            clarification_plan=ci_loop.ScenarioClarificationPlan(
                required=True,
                reason="No existing test_scenarios record matched this failure with sufficient confidence.",
                confidence_band="low",
                questions=("Is the test correct?",),
                candidate_match=None,
            ),
            scenario_proposal=ci_loop.ScenarioProposal(
                scenario_id="unknown_failure_bug",
                title="Unknown Failure",
                origin="auto-proposed",
                failure_signatures=("unexpected behavior in a new module",),
                affected_tests=("tests/test_unknown_failure.py",),
                likely_code_areas=(),
                invariants=("confirm the intended invariant before persisting this scenario",),
                accepted_fix_patterns=("confirm the intended contract before approving this scenario",),
                rejected_fix_patterns=("modify tests without explicit approval",),
                review_required=True,
                proposal_reason="No existing test_scenarios record matched this failure with sufficient confidence.",
                confidence=0.0,
            ),
        )

        passthrough_content = (ci_loop.REPO_ROOT / "user_store.py").read_text()
        backend_result = ci_loop.BackendResult(
            backend="codex",
            raw_artifact_name="response.md",
            raw_artifact_payload="synthetic backend output",
            edits=(
                {
                    "path": "user_store.py",
                    "content": passthrough_content,
                },
            ),
        )

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with mock.patch("ci_loop.analyze_failure", return_value=analysis):
                with mock.patch("ci_loop.ensure_output_dir", return_value=output_dir):
                    with mock.patch("ci_loop.request_edit_plan", return_value=backend_result) as backend_mock:
                        patch_text, artifact_name = ci_loop.generate_patch(
                            "scenario_1_integration_bug",
                            "Fix it",
                            allow_low_confidence=True,
                        )

            self.assertEqual(patch_text, "")
            self.assertEqual(artifact_name, "response.md")
            self.assertEqual(backend_mock.call_count, 1)
            self.assertTrue((output_dir / "clarification_request.json").exists())
            self.assertTrue((output_dir / "scenario_proposal.json").exists())

    def test_approve_scenario_proposal_writes_registry_file_and_refuses_overwrite(self) -> None:
        proposal = {
            "id": "synthetic_contract_bug",
            "title": "Synthetic Contract Bug",
            "origin": "auto-proposed",
            "failure_signatures": ["synthetic failure"],
            "affected_tests": ["tests/test_synthetic.py"],
            "likely_code_areas": ["synthetic.py"],
            "invariants": ["synthetic invariant"],
            "accepted_fix_patterns": ["repair the contract"],
            "rejected_fix_patterns": ["modify tests without explicit approval"],
            "review_required": True,
        }

        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            proposal_path = temp_root / "scenario_proposal.json"
            proposal_path.write_text(ci_loop.json.dumps(proposal, indent=2))
            registry_dir = temp_root / "test_scenarios"

            with mock.patch.object(ci_loop, "TEST_SCENARIOS_DIR", registry_dir):
                target = ci_loop.approve_scenario_proposal(proposal_path)
                self.assertTrue(target.exists())
                self.assertEqual(target.name, "synthetic_contract_bug.json")
                with self.assertRaises(RuntimeError):
                    ci_loop.approve_scenario_proposal(proposal_path)

    def test_low_confidence_scenario_writes_clarification_and_proposal_artifacts(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")

        self.assertTrue(analysis.clarification_plan.required)
        self.assertIsNotNone(analysis.scenario_proposal)

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            artifacts = ci_loop.write_analysis_artifacts("scenario_4_low_confidence", analysis, output_dir)

            self.assertIn("clarification_request", artifacts)
            self.assertIn("scenario_proposal", artifacts)
            self.assertTrue((output_dir / "clarification_request.json").exists())
            self.assertTrue((output_dir / "scenario_proposal.json").exists())

    def test_clarification_dialog_payload_contains_question_answer_pairs(self) -> None:
        payload = ci_loop.clarification_dialog_payload(
            ("Is the test contract correct?", "Which file owns the contract?"),
            ("Yes", "delivery_window.py"),
        )
        parsed = ci_loop.json.loads(payload)
        self.assertEqual(parsed["question_count"], 2)
        self.assertEqual(parsed["answers"][0]["question"], "Is the test contract correct?")
        self.assertEqual(parsed["answers"][0]["answer"], "Yes")
        self.assertIn("rounds", parsed)

    def test_build_prompt_with_runtime_clarifications_enriches_prompt(self) -> None:
        prompt = ci_loop.build_prompt_with_runtime_clarifications(
            "Fix failing tests.",
            ("What is the intended behavior?",),
            ("Round up partial windows.",),
        )
        self.assertIn("Fix failing tests.", prompt)
        self.assertIn("Runtime clarification from the operator", prompt)
        self.assertIn("Round up partial windows.", prompt)

    def test_clarification_option_sets_include_recommended_choices(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        options = ci_loop.clarification_option_sets(analysis.clarification_plan, analysis)
        self.assertEqual(len(options), len(analysis.clarification_plan.questions))
        self.assertTrue(all(len(option_group) == 3 for option_group in options))
        self.assertIn("(Recommended)", options[0][0])

    def test_collect_runtime_clarification_answers_supports_edit_roundtrip(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        responses = [
            "1",
            "1",
            "1",
            "edit",
            "Custom contract answer",
            "1",
            "1",
            "yes",
        ]
        with mock.patch("sys.stdin.isatty", return_value=True):
            with mock.patch("builtins.input", side_effect=responses):
                answers = ci_loop.collect_runtime_clarification_answers(
                    analysis.clarification_plan,
                    analysis,
                    backend="heuristic",
                )
        self.assertIsNotNone(answers)
        assert answers is not None
        resolved_answers = answers.get("answers")
        assert isinstance(resolved_answers, tuple)
        self.assertEqual(resolved_answers[0], "Custom contract answer")

    def test_collect_runtime_clarification_answers_threads_openai_previous_response_id(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        call_previous_ids: list[str | None] = []
        response_ids = iter(["resp_1", "resp_2", "resp_3"])

        def fake_openai(**kwargs):
            call_previous_ids.append(kwargs.get("previous_response_id"))
            next_id = next(response_ids)
            return (
                (
                    "Keep the current failing test contract as intended behavior. (Recommended)",
                    "Risky option",
                    "Escalate option",
                ),
                next_id,
                '{"options":["a","b","c"],"recommended_index":1}',
            )

        with mock.patch("sys.stdin.isatty", return_value=True):
            with mock.patch("builtins.input", side_effect=["1", "1", "1", "yes"]):
                with mock.patch("ci_loop.request_clarification_options_via_openai", side_effect=fake_openai):
                    answers = ci_loop.collect_runtime_clarification_answers(
                        analysis.clarification_plan,
                        analysis,
                        backend="openai_responses_api",
                        scenario_name="scenario_4_low_confidence",
                    )

        self.assertIsNotNone(answers)
        assert answers is not None
        self.assertEqual(call_previous_ids, [None, "resp_1", "resp_2"])
        self.assertEqual(answers.get("response_thread_ids"), ("resp_1", "resp_2", "resp_3"))
        self.assertEqual(answers.get("dialog_backend"), "openai_responses_api")

    def test_collect_runtime_clarification_answers_returns_none_on_empty_answer(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        with mock.patch("sys.stdin.isatty", return_value=True):
            with mock.patch("builtins.input", side_effect=[""]):
                answers = ci_loop.collect_runtime_clarification_answers(
                    analysis.clarification_plan,
                    analysis,
                    backend="heuristic",
                )
        self.assertIsNone(answers)

    def test_collect_runtime_clarification_answers_handles_eof_without_tty_guard(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        with mock.patch("builtins.input", side_effect=EOFError):
            answers = ci_loop.collect_runtime_clarification_answers(
                analysis.clarification_plan,
                analysis,
                backend="heuristic",
            )
        self.assertIsNone(answers)

    def test_collect_runtime_clarification_answers_can_force_heuristic_options(self) -> None:
        analysis = ci_loop.analyze_failure("scenario_4_low_confidence")
        with mock.patch("sys.stdin.isatty", return_value=True):
            with mock.patch("builtins.input", side_effect=["1", "1", "1", "yes"]):
                with mock.patch("ci_loop.request_clarification_options_via_openai") as openai_mock:
                    with mock.patch("ci_loop.request_clarification_options_via_codex") as codex_mock:
                        answers = ci_loop.collect_runtime_clarification_answers(
                            analysis.clarification_plan,
                            analysis,
                            backend="openai_responses_api",
                            clarifier_option_source="heuristic",
                        )
        self.assertIsNotNone(answers)
        assert answers is not None
        self.assertEqual(answers.get("dialog_backend"), "heuristic")
        self.assertEqual(answers.get("response_thread_ids"), ())
        openai_mock.assert_not_called()
        codex_mock.assert_not_called()

    def test_run_demo_short_circuits_when_baseline_is_already_green(self) -> None:
        with mock.patch("ci_loop.run_tests", return_value=(True, "1 passed")):
            with mock.patch("ci_loop.generate_patch", side_effect=AssertionError("should not generate patch")):
                rc = ci_loop.run_demo("scenario_1_integration_bug", max_retries=1)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
