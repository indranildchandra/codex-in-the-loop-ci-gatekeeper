from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "output"
CONFIG_PATH = REPO_ROOT / "ci_config.json"
REVIEWER_PROMPT_PATH = REPO_ROOT / "ci_gatekeeper_reviewer.prompt"
CLARIFIER_PROMPT_PATH = REPO_ROOT / "ci_gatekeeper_clarifier.prompt"
TEST_SCENARIOS_DIR = REPO_ROOT / "test_scenarios"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_BACKEND = "codex"
OPENAI_BACKUP_BACKEND = "openai_responses_api"
MAX_RETRIES = 3
MAX_CLARIFICATION_PASSES = 3
AUTO_SCENARIO_MATCH_THRESHOLD = 0.8
CAUTIOUS_SCENARIO_MATCH_THRESHOLD = 0.5

# Paths the repair loop must never let a generated patch touch. Tests are the
# executable contract; if the model can edit them it can manufacture a green run.
PROTECTED_PATH_PREFIXES = ("tests/",)


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    summary: str
    context_files: tuple[str, ...]
    test_targets: tuple[str, ...]
    base_prompt: str
    constrained_prompt: str
    include_in_run_all: bool = True


@dataclass(frozen=True)
class BackendResult:
    backend: str
    raw_artifact_name: str
    raw_artifact_payload: str
    edits: tuple[dict, ...] = ()
    patch_text: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class FailureRecord:
    failed_tests: tuple[str, ...]
    failure_summary: str
    failure_output: str
    likely_modules: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class RepoDelta:
    source: str
    changed_files: tuple[str, ...]
    diff_text: str


@dataclass(frozen=True)
class TestScenarioRecord:
    scenario_id: str
    title: str
    origin: str
    failure_signatures: tuple[str, ...]
    affected_tests: tuple[str, ...]
    likely_code_areas: tuple[str, ...]
    invariants: tuple[str, ...]
    accepted_fix_patterns: tuple[str, ...]
    rejected_fix_patterns: tuple[str, ...]
    review_required: bool


@dataclass(frozen=True)
class ScenarioMatch:
    matched: bool
    source: str
    scenario_id: str | None
    confidence: float
    accepted_fix_patterns: tuple[str, ...]
    rejected_fix_patterns: tuple[str, ...]
    invariants: tuple[str, ...]
    record: TestScenarioRecord | None = None


@dataclass(frozen=True)
class ScenarioClarificationPlan:
    required: bool
    reason: str
    confidence_band: str
    questions: tuple[str, ...]
    candidate_match: ScenarioMatch | None = None


@dataclass(frozen=True)
class ScenarioProposal:
    scenario_id: str
    title: str
    origin: str
    failure_signatures: tuple[str, ...]
    affected_tests: tuple[str, ...]
    likely_code_areas: tuple[str, ...]
    invariants: tuple[str, ...]
    accepted_fix_patterns: tuple[str, ...]
    rejected_fix_patterns: tuple[str, ...]
    review_required: bool
    proposal_reason: str
    confidence: float


@dataclass(frozen=True)
class FailureAnalysis:
    failure_record: FailureRecord
    context_files: tuple[str, ...]
    repo_delta: RepoDelta | None
    scenario_candidate: ScenarioMatch | None
    scenario_match: ScenarioMatch | None
    clarification_plan: ScenarioClarificationPlan
    scenario_proposal: ScenarioProposal | None


class ClarificationRequiredError(RuntimeError):
    """Raised when repair generation should stop until a user reviews low-confidence artifacts."""


SCENARIOS = {
    "scenario_1_integration_bug": Scenario(
        name="scenario_1_integration_bug",
        title="Write/Read Path Inconsistency",
        summary="The write path stores raw email while the read path normalizes it.",
        context_files=(
            "user_store.py",
            "utils.py",
            "tests/test_scenario_1_integration_bug.py",
        ),
        test_targets=("tests/test_scenario_1_integration_bug.py",),
        base_prompt="Fix the failing tests in tests/test_scenario_1_integration_bug.py.",
        constrained_prompt=(
            "Fix the failing tests in tests/test_scenario_1_integration_bug.py. "
            "Do not modify tests. "
            "Do not change API contracts. "
            "Preserve the read path. "
            "Only edit the minimum code needed, preferably in user_store.py. "
            "Ensure consistent normalization across write and read paths."
        ),
    ),
    "scenario_2_wrong_fix_path": Scenario(
        name="scenario_2_wrong_fix_path",
        title="Misleading Local Fix",
        summary="The tempting fix is to patch the read path, but the correct fix is to repair the write-path key helper.",
        context_files=(
            "user_registry.py",
            "utils.py",
            "tests/test_scenario_2_wrong_fix_path.py",
        ),
        test_targets=("tests/test_scenario_2_wrong_fix_path.py",),
        base_prompt="Fix the failing tests in tests/test_scenario_2_wrong_fix_path.py.",
        constrained_prompt=(
            "Fix the failing tests in tests/test_scenario_2_wrong_fix_path.py. "
            "Do not modify tests. "
            "Do not change API contracts. "
            "Do not patch around the bug in get_user. "
            "Only edit the minimum code needed, preferably in user_registry.py. "
            "Preserve the normalized-read behavior and repair the write-path key helper so the invariant holds."
        ),
    ),
    "scenario_3_refactor_bug": Scenario(
        name="scenario_3_refactor_bug",
        title="Refactor-Induced Contract Drift",
        summary="pricing.calculate_total now expects percentage tax, but orders still calls it with 0.1.",
        context_files=(
            "orders.py",
            "pricing.py",
            "tests/test_scenario_3_refactor_bug.py",
        ),
        test_targets=("tests/test_scenario_3_refactor_bug.py",),
        base_prompt="Fix the failing tests in tests/test_scenario_3_refactor_bug.py.",
        constrained_prompt=(
            "Fix the failing tests in tests/test_scenario_3_refactor_bug.py. "
            "Do not modify tests. "
            "Do not change the pricing.calculate_total contract. "
            "Only edit the minimum code needed, preferably in orders.py. "
            "Preserve the refactored percentage-based tax semantics."
        ),
    ),
    "scenario_4_low_confidence": Scenario(
        name="scenario_4_low_confidence",
        title="Low-Confidence Clarification Demo",
        summary="A deliberately unmapped failure to demonstrate clarification_request and scenario_proposal artifacts.",
        context_files=(
            "delivery_window.py",
            "tests/test_scenario_4_low_confidence.py",
        ),
        test_targets=("tests/test_scenario_4_low_confidence.py",),
        base_prompt="Fix the failing tests in tests/test_scenario_4_low_confidence.py.",
        constrained_prompt=(
            "Fix the failing tests in tests/test_scenario_4_low_confidence.py. "
            "Do not modify tests. "
            "Do not change API contracts. "
            "Only edit the minimum code needed."
        ),
        include_in_run_all=False,
    ),
}


def scenario_choices(include_non_gating: bool = True) -> list[str]:
    if include_non_gating:
        return list(SCENARIOS.keys())
    return [name for name, scenario in SCENARIOS.items() if scenario.include_in_run_all]


def get_scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise RuntimeError(f"Unknown scenario: {name}")
    return SCENARIOS[name]


def load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def load_repo_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    raw = json.loads(CONFIG_PATH.read_text())
    return raw if isinstance(raw, dict) else {}


def load_code_review_prompt_template() -> str:
    if not REVIEWER_PROMPT_PATH.exists():
        raise RuntimeError(f"Prompt template not found: {REVIEWER_PROMPT_PATH}")
    return REVIEWER_PROMPT_PATH.read_text().strip()


def load_clarifier_prompt_template() -> str:
    if not CLARIFIER_PROMPT_PATH.exists():
        raise RuntimeError(f"QnA prompt template not found: {CLARIFIER_PROMPT_PATH}")
    return CLARIFIER_PROMPT_PATH.read_text().strip()


def render_runtime_qna_guidance(plan: ScenarioClarificationPlan) -> str:
    template = load_clarifier_prompt_template()
    return template.format(
        confidence_band=plan.confidence_band,
        reason=plan.reason,
    )


def clarification_option_sets(
    plan: ScenarioClarificationPlan,
    analysis: FailureAnalysis,
) -> tuple[tuple[str, str, str], ...]:
    return tuple(clarification_option_set_for_question(question, analysis) for question in plan.questions)


def resolve_answer(raw_answer: str, options: tuple[str, ...]) -> str:
    if raw_answer.isdigit():
        selected_index = int(raw_answer) - 1
        if 0 <= selected_index < len(options):
            return options[selected_index]
    return raw_answer


def request_clarification_options_via_openai(
    *,
    question: str,
    analysis: FailureAnalysis,
    scenario_name: str,
    model: str | None = None,
    previous_response_id: str | None = None,
) -> tuple[tuple[str, str, str] | None, str | None, str | None]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None, None

    resolved_model = model or configured_model()
    settings = backend_settings("openai_responses_api")
    endpoint = settings.get("endpoint", "https://api.openai.com/v1/responses")
    timeout_seconds = settings.get("timeout_seconds", 60)
    context_paths = "\n".join(f"- {path}" for path in analysis.context_files)
    clarifier_prompt = render_runtime_qna_guidance(analysis.clarification_plan)
    instruction = (
        f"{clarifier_prompt}\n\n"
        "Generate exactly three concise answer options for the operator.\n"
        "Constraints:\n"
        "- Option 1 must be recommended and conservative.\n"
        "- Option 2 should represent a risky/contract-changing path.\n"
        "- Option 3 should represent uncertainty/escalation.\n"
        "- Keep each option one sentence.\n"
        "- Return strict JSON only with schema: "
        '{"options":["...","...","..."],"recommended_index":1}.\n\n'
        f"Scenario: {scenario_name}\n"
        f"Failure summary: {analysis.failure_record.failure_summary}\n"
        f"Likely modules:\n{context_paths}\n"
        f"Question: {question}"
    )

    payload: dict[str, object] = {
        "model": resolved_model,
        "instructions": "Return strict JSON only. Do not include markdown fences, prose, or commentary.",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                ],
            }
        ],
    }
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    request = urllib.request.Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, None, f"openai_clarifier_error: {exc}"

    response_id = raw_response.get("id")
    if not isinstance(response_id, str):
        response_id = None

    payload_text = normalize_json_text(extract_output_text(raw_response))
    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError:
        return None, response_id, payload_text

    options = parsed.get("options")
    if (
        not isinstance(options, list)
        or len(options) != 3
        or not all(isinstance(item, str) and item.strip() for item in options)
    ):
        return None, response_id, payload_text

    return (options[0], options[1], options[2]), response_id, payload_text


def request_clarification_options_via_codex(
    *,
    question: str,
    analysis: FailureAnalysis,
    scenario_name: str,
    model: str | None = None,
    dialog_context: str = "",
) -> tuple[tuple[str, str, str] | None, str | None]:
    settings = backend_settings("codex")
    command = settings.get("command", ["codex", "exec"])
    timeout_seconds = int(settings.get("timeout_seconds", 120))
    sandbox_mode = str(settings.get("sandbox", "read-only"))
    resolved_model = model or configured_codex_model()
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return None, None

    schema = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "recommended_index": {"type": "integer"},
        },
        "required": ["options", "recommended_index"],
        "additionalProperties": False,
    }

    context_paths = "\n".join(f"- {path}" for path in analysis.context_files)
    clarifier_prompt = render_runtime_qna_guidance(analysis.clarification_plan)
    worker_prompt = (
        f"{clarifier_prompt}\n\n"
        "Generate exactly three concise answer options for the operator.\n"
        "Constraints:\n"
        "- Option 1 must be recommended and conservative.\n"
        "- Option 2 should represent a risky/contract-changing path.\n"
        "- Option 3 should represent uncertainty/escalation.\n"
        "- Keep each option one sentence.\n"
        "- Return strict JSON only with fields options and recommended_index.\n\n"
        f"Scenario: {scenario_name}\n"
        f"Failure summary: {analysis.failure_record.failure_summary}\n"
        f"Likely modules:\n{context_paths}\n"
        f"Prior dialog context:\n{dialog_context or '(none)'}\n\n"
        f"Question: {question}"
    )

    with tempfile.TemporaryDirectory(prefix="codex-clarifier-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        last_message_path = temp_path / "last_message.json"
        schema_path.write_text(json.dumps(schema, indent=2))

        exec_args = [
            *command,
            "--json",
            "--ephemeral",
            "--sandbox",
            sandbox_mode,
            "-C",
            str(REPO_ROOT),
            "--output-schema",
            str(schema_path),
            "-o",
            str(last_message_path),
        ]
        if resolved_model:
            exec_args.extend(["-m", resolved_model])
        exec_args.append("-")

        try:
            completed = subprocess.run(
                exec_args,
                input=worker_prompt,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except Exception as exc:
            return None, f"codex_clarifier_error: {exc}"

        last_message = last_message_path.read_text().strip() if last_message_path.exists() else ""
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        raw_log = "\n".join(
            [
                "# Codex Clarifier Log",
                "",
                f"- exit_code: {completed.returncode}",
                "",
                "## Stdout",
                markdown_fence(stdout or "(no stdout)", "json"),
                "",
                "## Stderr",
                markdown_fence(stderr or "(no stderr)", "text"),
                "",
                "## Last Message",
                markdown_fence(last_message or "(no last message captured)", "json"),
            ]
        )

    if completed.returncode != 0 or not last_message:
        return None, raw_log

    try:
        payload = json.loads(normalize_json_text(last_message))
    except json.JSONDecodeError:
        return None, raw_log

    options = payload.get("options")
    if (
        not isinstance(options, list)
        or len(options) != 3
        or not all(isinstance(item, str) and item.strip() for item in options)
    ):
        return None, raw_log

    return (options[0], options[1], options[2]), raw_log


def clarification_option_set_for_question(
    question: str,
    analysis: FailureAnalysis,
) -> tuple[str, str, str]:
    candidate_sources = tuple(
        path for path in analysis.context_files if path.endswith(".py") and not path.startswith("tests/")
    )
    primary_surface = candidate_sources[0] if candidate_sources else "the most likely source file from context"

    normalized = question.lower()
    if "intended contract" in normalized:
        return (
            "Keep the current failing test contract as intended behavior. (Recommended)",
            "Treat the contract as changed and stop for explicit test update approval.",
            "Unsure about intended contract; stop and escalate to the owner.",
        )

    if "public api" in normalized:
        return (
            "Preserve public API shape and behavior outside this failure. (Recommended)",
            "Allow API contract changes and stop for explicit approval.",
            "Unsure about API constraints; stop and escalate to the owner.",
        )

    if "source file" in normalized or "repair surface" in normalized or "contract should be treated" in normalized:
        return (
            f"Use {primary_surface} as the primary repair surface. (Recommended)",
            "Use the failing test target as repair surface and stop for approval before changing tests.",
            "Unsure about repair surface; stop and escalate to the owner.",
        )

    return (
        "Use the smallest code change that preserves existing tests and contracts. (Recommended)",
        "Broaden scope with contract changes and stop for explicit approval.",
        "Unsure; stop and escalate to the owner.",
    )


def clarification_dialog_payload(
    questions: tuple[str, ...],
    answers: tuple[str, ...],
    rounds: tuple[dict[str, object], ...] = (),
    dialog_backend: str | None = None,
    response_thread: tuple[str, ...] = (),
) -> str:
    answer_pairs = []
    for question, answer in zip(questions, answers):
        answer_pairs.append(
            {
                "question": question,
                "answer": answer,
            }
        )
    return json.dumps(
        {
        "question_count": len(questions),
        "answers": answer_pairs,
        "dialog_backend": dialog_backend,
        "response_thread_ids": list(response_thread),
        "rounds": list(rounds),
        },
        indent=2,
    )


def collect_runtime_clarification_answers(
    plan: ScenarioClarificationPlan,
    analysis: FailureAnalysis,
    *,
    backend: str | None = None,
    model: str | None = None,
    scenario_name: str = "unknown_scenario",
    clarifier_option_source: str = "backend",
) -> dict[str, object] | None:
    normalized_backend = normalize_backend_name(backend or configured_backend())
    force_heuristic = clarifier_option_source == "heuristic"
    use_openai_clarifier = (not force_heuristic) and normalized_backend == "openai_responses_api"
    use_codex_clarifier = (not force_heuristic) and normalized_backend == "codex"
    prior_answers: tuple[str, ...] | None = None
    response_thread_ids: list[str] = []
    previous_response_id: str | None = None
    dialog_context = ""
    rounds: list[dict[str, object]] = []

    for pass_number in range(1, MAX_CLARIFICATION_PASSES + 1):
        print(f"\nClarification round {pass_number}/{MAX_CLARIFICATION_PASSES}")
        print("Pick an option number or type a custom answer.")
        print("Leave any answer empty to stop.")
        answers: list[str] = []
        round_questions: list[dict[str, object]] = []

        for index, question in enumerate(plan.questions, start=1):
            options = clarification_option_set_for_question(question, analysis)
            option_source = "heuristic"
            backend_payload: str | None = None
            backend_trace_id: str | None = None
            previous_response_input: str | None = None
            if use_openai_clarifier:
                previous_response_input = previous_response_id
                generated, response_id, raw_payload = request_clarification_options_via_openai(
                    question=question,
                    analysis=analysis,
                    scenario_name=scenario_name,
                    model=model,
                    previous_response_id=previous_response_id,
                )
                backend_payload = raw_payload
                if response_id:
                    response_thread_ids.append(response_id)
                    previous_response_id = response_id
                    backend_trace_id = response_id
                if generated is not None:
                    options = generated
                    option_source = "openai_responses_api"
            elif use_codex_clarifier:
                generated, raw_payload = request_clarification_options_via_codex(
                    question=question,
                    analysis=analysis,
                    scenario_name=scenario_name,
                    model=model,
                    dialog_context=dialog_context,
                )
                backend_payload = raw_payload
                if generated is not None:
                    options = generated
                    option_source = "codex"
            print(f"\nQuestion {index}: {question}")
            for option_index, option in enumerate(options, start=1):
                print(f"  {option_index}. {option}")
            if prior_answers is not None and index - 1 < len(prior_answers):
                print(f"  previous answer: {prior_answers[index - 1]}")
            try:
                raw_response = input("Select [1-3] or type custom answer:\n> ").strip()
            except EOFError:
                return None
            if not raw_response:
                return None
            resolved = resolve_answer(raw_response, options)
            answers.append(resolved)
            round_questions.append(
                {
                    "question": question,
                    "options": list(options),
                    "option_source": option_source,
                    "selected_input": raw_response,
                    "resolved_answer": resolved,
                    "backend_payload": backend_payload,
                    "backend_trace_id": backend_trace_id,
                    "previous_response_id_input": previous_response_input,
                }
            )

        try:
            confirmed = input(
                "\nType 'yes'/'y' to proceed, 'edit'/'e' to revise answers, or anything else to stop:\n> "
            ).strip().lower()
        except EOFError:
            return None
        rounds.append(
            {
                "round": pass_number,
                "questions": round_questions,
                "decision": confirmed,
            }
        )
        dialog_context = json.dumps(rounds, indent=2)
        if confirmed in {"yes", "y"}:
            return {
                "answers": tuple(answers),
                "rounds": tuple(rounds),
                "dialog_backend": (
                    "openai_responses_api"
                    if use_openai_clarifier
                    else "codex" if use_codex_clarifier else "heuristic"
                ),
                "response_thread_ids": tuple(response_thread_ids),
            }
        if confirmed not in {"edit", "e"}:
            return None

        prior_answers = tuple(answers)

    return None


def build_prompt_with_runtime_clarifications(
    prompt: str,
    questions: tuple[str, ...],
    answers: tuple[str, ...],
) -> str:
    qna_lines = []
    for index, (question, answer) in enumerate(zip(questions, answers), start=1):
        qna_lines.append(f"{index}. Question: {question}")
        qna_lines.append(f"   Operator answer: {answer}")
    qna_block = "\n".join(qna_lines)
    return (
        f"{prompt}\n\n"
        "Runtime clarification from the operator (authoritative for this run):\n"
        f"{qna_block}\n\n"
        "Use these clarifications to resolve ambiguity while keeping tests unchanged."
    )


def configured_model() -> str:
    env_model = os.getenv("OPENAI_MODEL")
    if env_model:
        return env_model

    config = load_repo_config()
    model = config.get("openai_model")
    if isinstance(model, str) and model.strip():
        return model.strip()

    return DEFAULT_MODEL


def configured_codex_model() -> str | None:
    env_model = os.getenv("CODEX_MODEL")
    if env_model:
        return env_model

    settings = backend_settings("codex")
    model = settings.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()

    return None


def configured_backend() -> str:
    env_backend = os.getenv("CI_LOOP_BACKEND")
    if env_backend:
        return normalize_backend_name(env_backend)

    config = load_repo_config()
    backend = config.get("backend")
    if isinstance(backend, str) and backend.strip():
        normalized_backend = normalize_backend_name(backend.strip())
        if normalized_backend in {DEFAULT_BACKEND, OPENAI_BACKUP_BACKEND}:
            return normalized_backend
        return DEFAULT_BACKEND

    return DEFAULT_BACKEND


def backend_settings(backend: str) -> dict:
    normalized_backend = normalize_backend_name(backend)
    config = load_repo_config()
    settings = config.get("backend_settings", {})
    if not isinstance(settings, dict):
        return {}
    backend_config = settings.get(normalized_backend, {})
    return backend_config if isinstance(backend_config, dict) else {}


def pre_commit_hook_enabled() -> bool:
    if os.getenv("SKIP_CI_GATEKEEPER_PRE_COMMIT") == "1":
        return False

    config = load_repo_config()
    hook_settings = config.get("git_hooks", {})
    if not isinstance(hook_settings, dict):
        return True

    enabled = hook_settings.get("pre_commit_enabled")
    if isinstance(enabled, bool):
        return enabled

    return True


def configured_raw_artifact_name(backend: str) -> str:
    normalized_backend = normalize_backend_name(backend)
    if normalized_backend == "openai_responses_api":
        return "response.json"
    if normalized_backend == "codex":
        return "response.md"
    return "backend_response.txt"


def normalize_backend_name(backend: str) -> str:
    return backend


def resolve_repo_relative_path(relative_path: str) -> Path:
    candidate = (REPO_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"Edit path escapes the repo root: {relative_path}") from exc
    return candidate


def markdown_fence(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"


def build_code_review_prompt(
    scenario_instructions: str,
    context: str,
    response_contract: str,
) -> str:
    template = load_code_review_prompt_template()
    return template.format(
        response_contract=response_contract,
        scenario_instructions=scenario_instructions,
        context=context,
    )


def extract_codex_exec_error(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error" and isinstance(event.get("message"), str):
            return event["message"]
        if event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
    return None


def ensure_output_dir(scenario_name: str) -> Path:
    path = OUTPUT_DIR / scenario_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def scenario_output_path(scenario_name: str, filename: str) -> Path:
    return ensure_output_dir(scenario_name) / filename


def run_command(args: list[str], extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    repo_pythonpath = str(REPO_ROOT)
    env["PYTHONPATH"] = repo_pythonpath if not existing_pythonpath else f"{repo_pythonpath}:{existing_pythonpath}"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def unittest_modules(targets: tuple[str, ...]) -> list[str]:
    modules: list[str] = []
    for target in targets:
        module = target.replace("/", ".")
        if module.endswith(".py"):
            module = module[:-3]
        modules.append(module)
    return modules


def validation_command(targets: tuple[str, ...]) -> list[str]:
    if shutil.which("pytest"):
        return ["pytest", "-q", *targets]
    return [sys.executable, "-m", "unittest", "-q", *unittest_modules(targets)]


def run_test_targets(targets: tuple[str, ...]) -> tuple[bool, str]:
    if not targets:
        return True, "(no tests)"
    code, stdout, stderr = run_command(
        validation_command(targets),
        extra_env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
    return code == 0, output or "(no output)"


def run_tests(scenario_name: str) -> tuple[bool, str]:
    scenario = get_scenario(scenario_name)
    return run_test_targets(scenario.test_targets)


def discover_test_files() -> tuple[str, ...]:
    tests_dir = REPO_ROOT / "tests"
    if not tests_dir.is_dir():
        return ()
    discovered = sorted(
        repo_relative_path(path)
        for path in tests_dir.glob("test_*.py")
        if path.is_file()
    )
    return tuple(path for path in discovered if path is not None)


def collect_green_regression_set(exclude: tuple[str, ...]) -> tuple[str, ...]:
    """Return test files that currently pass, excluding the scenario's own targets.

    These are the tests the repair must not break. The scenario under repair is
    red at baseline by design, so it is excluded; the remaining green tests form
    the regression guard that an accepted patch has to keep green.
    """
    excluded = set(exclude)
    green: list[str] = []
    for test_file in discover_test_files():
        if test_file in excluded:
            continue
        passed, _ = run_test_targets((test_file,))
        if passed:
            green.append(test_file)
    return tuple(green)


def repo_relative_path(path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return None


def display_path(path: Path) -> str:
    relative = repo_relative_path(path)
    return relative if relative is not None else str(path)


def summarize_failure_output(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("AssertionError:"):
            return stripped

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED") or stripped.startswith("ERROR"):
            return stripped

    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped

    return "No failure output captured."


def parse_traceback_paths(output: str) -> tuple[str, ...]:
    discovered: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r'File "([^"]+)"', output):
        raw_path = match.group(1)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (REPO_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        relative = repo_relative_path(candidate)
        if relative and relative not in seen and candidate.exists():
            seen.add(relative)
            discovered.append(relative)

    return tuple(discovered)


def compute_failure_confidence(
    tests_passed: bool,
    failure_output: str,
    likely_modules: tuple[str, ...],
) -> float:
    """Estimate how interpretable a failure is, not just whether one occurred.

    The score feeds clarification gating: an opaque failure (no clean assertion,
    no resolvable non-test source) scores below the cautious threshold so the loop
    stops and asks rather than guessing the intended contract.
    """
    if tests_passed:
        # No actionable failure signal to reason about.
        return 0.4

    score = 0.3

    # Match a clean assertion line in either the unittest ("AssertionError: ...")
    # or pytest ("E   AssertionError: ...") traceback format.
    has_assertion = any(
        re.match(r"^\s*(E\s+)?AssertionError\b", line) for line in failure_output.splitlines()
    )
    if has_assertion:
        score += 0.35

    resolved_source = any(
        not module.startswith(PROTECTED_PATH_PREFIXES) for module in likely_modules
    )
    if resolved_source:
        score += 0.2

    return round(min(score, 0.9), 2)


def build_failure_record(scenario_name: str) -> FailureRecord:
    scenario = get_scenario(scenario_name)
    tests_passed, failure_output = run_tests(scenario_name)
    likely_modules: list[str] = []
    seen: set[str] = set()

    def add(relative_path: str) -> None:
        if relative_path in seen:
            return
        path = REPO_ROOT / relative_path
        if not path.exists():
            return
        seen.add(relative_path)
        likely_modules.append(relative_path)

    for test_target in scenario.test_targets:
        add(test_target)

    for relative_path in parse_traceback_paths(failure_output):
        add(relative_path)

    likely_modules_tuple = tuple(likely_modules)
    confidence = compute_failure_confidence(tests_passed, failure_output, likely_modules_tuple)
    failed_tests = scenario.test_targets if not tests_passed else ()

    return FailureRecord(
        failed_tests=failed_tests,
        failure_summary=summarize_failure_output(failure_output),
        failure_output=failure_output,
        likely_modules=likely_modules_tuple,
        confidence=confidence,
    )


def run_git_command(args: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def git_is_available() -> bool:
    code, stdout, _ = run_git_command(["rev-parse", "--is-inside-work-tree"])
    return code == 0 and stdout.strip() == "true"


def is_delta_candidate_path(relative_path: str) -> bool:
    if not relative_path.endswith(".py"):
        return False
    disallowed_prefixes = ("output/", "demo_scenarios/", "docs/", ".githooks/")
    return not relative_path.startswith(disallowed_prefixes)


def git_changed_paths(*git_args: str) -> tuple[str, ...]:
    code, stdout, _ = run_git_command(["diff", "--name-only", *git_args, "--"])
    if code != 0:
        return ()

    changed_paths: list[str] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        relative_path = line.strip()
        if not relative_path or relative_path in seen:
            continue
        path = REPO_ROOT / relative_path
        if not path.exists():
            continue
        if not is_delta_candidate_path(relative_path):
            continue
        seen.add(relative_path)
        changed_paths.append(relative_path)
    return tuple(changed_paths)


def imported_python_paths(relative_paths: tuple[str, ...]) -> set[str]:
    imported: set[str] = set()
    for relative_path in relative_paths:
        imported.update(extract_local_import_paths(relative_path))
    return imported


def prioritize_delta_paths(changed_paths: tuple[str, ...], context_files: tuple[str, ...], limit: int = 5) -> tuple[str, ...]:
    context_set = set(context_files)
    imported_by_context = imported_python_paths(context_files)
    prioritized: list[tuple[int, str]] = []

    for relative_path in changed_paths:
        imports_from_changed = set(extract_local_import_paths(relative_path))
        score = 0

        if relative_path in context_set and relative_path.startswith("tests/"):
            score += 220
        elif relative_path in context_set:
            score += 160
        elif relative_path in imported_by_context:
            score += 80
        if imports_from_changed & context_set:
            score += 60
        if imports_from_changed & imported_by_context:
            score += 40
        if score > 0 and relative_path.startswith("tests/"):
            score += 10

        if score > 0:
            prioritized.append((score, relative_path))

    prioritized.sort(key=lambda item: (-item[0], item[1]))
    return tuple(path for _, path in prioritized[:limit])


def git_diff_text(compare_args: tuple[str, ...], changed_files: tuple[str, ...], max_characters: int = 4000) -> str:
    if not changed_files:
        return ""

    code, stdout, _ = run_git_command(["diff", "--unified=3", *compare_args, "--", *changed_files])
    if code != 0:
        return ""

    diff_text = stdout.strip()
    if len(diff_text) <= max_characters:
        return diff_text

    truncated = diff_text[:max_characters].rstrip()
    return f"{truncated}\n\n... diff truncated ..."


def local_module_path(module_name: str) -> str | None:
    module_root = module_name.split(".", 1)[0]
    module_path = REPO_ROOT / f"{module_root}.py"
    if module_path.exists():
        return module_path.relative_to(REPO_ROOT).as_posix()

    package_path = REPO_ROOT / module_root / "__init__.py"
    if package_path.exists():
        return package_path.relative_to(REPO_ROOT).as_posix()

    return None


def extract_local_import_paths(relative_path: str) -> tuple[str, ...]:
    path = REPO_ROOT / relative_path
    if not path.exists() or path.suffix != ".py":
        return ()

    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return ()

    discovered: list[str] = []
    seen: set[str] = set()

    def add(module_name: str) -> None:
        module_path = local_module_path(module_name)
        if module_path and module_path not in seen:
            seen.add(module_path)
            discovered.append(module_path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            add(node.module)

    return tuple(discovered)


def resolve_context_file_paths(scenario_name: str, failure_record: FailureRecord, max_depth: int = 3) -> tuple[str, ...]:
    scenario = get_scenario(scenario_name)
    ordered_paths: list[str] = []
    seen: set[str] = set()

    def add(relative_path: str) -> None:
        if relative_path in seen:
            return
        path = REPO_ROOT / relative_path
        if not path.exists():
            return
        seen.add(relative_path)
        ordered_paths.append(relative_path)

    queue: list[tuple[str, int]] = []

    for relative_path in failure_record.failed_tests:
        add(relative_path)
        queue.append((relative_path, 0))

    for relative_path in failure_record.likely_modules:
        add(relative_path)
        queue.append((relative_path, 0))

    while queue:
        relative_path, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for imported_path in extract_local_import_paths(relative_path):
            if imported_path in seen:
                continue
            add(imported_path)
            queue.append((imported_path, depth + 1))

    for relative_path in scenario.context_files:
        add(relative_path)

    return tuple(ordered_paths)


def collect_recent_repo_delta(context_files: tuple[str, ...]) -> RepoDelta | None:
    if not git_is_available():
        return None

    working_tree_changes = git_changed_paths("HEAD")
    prioritized_working_tree = prioritize_delta_paths(working_tree_changes, context_files)
    if prioritized_working_tree:
        return RepoDelta(
            source="working_tree",
            changed_files=prioritized_working_tree,
            diff_text=git_diff_text(("HEAD",), prioritized_working_tree),
        )

    code, _, _ = run_git_command(["rev-parse", "--verify", "HEAD~1"])
    if code != 0:
        return None

    previous_commit_changes = git_changed_paths("HEAD~1", "HEAD")
    prioritized_previous_commit = prioritize_delta_paths(previous_commit_changes, context_files)
    if prioritized_previous_commit:
        return RepoDelta(
            source="previous_commit",
            changed_files=prioritized_previous_commit,
            diff_text=git_diff_text(("HEAD~1", "HEAD"), prioritized_previous_commit),
        )

    return None


def load_test_scenario_registry() -> tuple[TestScenarioRecord, ...]:
    if not TEST_SCENARIOS_DIR.exists():
        return ()

    records: list[TestScenarioRecord] = []
    for path in sorted(TEST_SCENARIOS_DIR.glob("*.json")):
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            continue

        records.append(
            TestScenarioRecord(
                scenario_id=str(raw.get("id", path.stem)),
                title=str(raw.get("title", path.stem)),
                origin=str(raw.get("origin", "unknown")),
                failure_signatures=tuple(str(item) for item in raw.get("failure_signatures", []) if isinstance(item, str)),
                affected_tests=tuple(str(item) for item in raw.get("affected_tests", []) if isinstance(item, str)),
                likely_code_areas=tuple(str(item) for item in raw.get("likely_code_areas", []) if isinstance(item, str)),
                invariants=tuple(str(item) for item in raw.get("invariants", []) if isinstance(item, str)),
                accepted_fix_patterns=tuple(str(item) for item in raw.get("accepted_fix_patterns", []) if isinstance(item, str)),
                rejected_fix_patterns=tuple(str(item) for item in raw.get("rejected_fix_patterns", []) if isinstance(item, str)),
                review_required=bool(raw.get("review_required", True)),
            )
        )

    return tuple(records)


def confidence_band(score: float) -> str:
    if score >= AUTO_SCENARIO_MATCH_THRESHOLD:
        return "high"
    if score >= CAUTIOUS_SCENARIO_MATCH_THRESHOLD:
        return "medium"
    return "low"


def score_test_scenario_record(
    record: TestScenarioRecord,
    failure_record: FailureRecord,
    context_files: tuple[str, ...],
) -> float:
    score = 0.0
    failed_tests = set(failure_record.failed_tests)
    context_set = set(context_files)

    affected_test_overlap = failed_tests & set(record.affected_tests)
    likely_code_overlap = context_set & set(record.likely_code_areas)
    failure_text = f"{failure_record.failure_summary}\n{failure_record.failure_output}".lower()
    matching_signatures = [signature for signature in record.failure_signatures if signature.lower() in failure_text]

    if affected_test_overlap:
        score += 0.7
    if likely_code_overlap:
        score += 0.2
    if matching_signatures:
        score += 0.1

    return min(score, 1.0)


def find_best_test_scenario_candidate(
    failure_record: FailureRecord,
    context_files: tuple[str, ...],
) -> ScenarioMatch | None:
    best_record: TestScenarioRecord | None = None
    best_score = 0.0

    for record in load_test_scenario_registry():
        score = score_test_scenario_record(record, failure_record, context_files)
        if score > best_score:
            best_score = score
            best_record = record

    if best_record is None or best_score <= 0.0:
        return None

    return ScenarioMatch(
        matched=best_score >= AUTO_SCENARIO_MATCH_THRESHOLD,
        source="test_scenarios",
        scenario_id=best_record.scenario_id,
        confidence=best_score,
        accepted_fix_patterns=best_record.accepted_fix_patterns,
        rejected_fix_patterns=best_record.rejected_fix_patterns,
        invariants=best_record.invariants,
        record=best_record,
    )


def lookup_test_scenario_match(
    failure_record: FailureRecord,
    context_files: tuple[str, ...],
    confidence_threshold: float = AUTO_SCENARIO_MATCH_THRESHOLD,
) -> ScenarioMatch | None:
    candidate = find_best_test_scenario_candidate(failure_record, context_files)
    if candidate is None or candidate.confidence < confidence_threshold:
        return None
    return candidate


def dedupe_preserve_order(items: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def plan_scenario_clarification(
    failure_record: FailureRecord,
    scenario_candidate: ScenarioMatch | None,
) -> ScenarioClarificationPlan:
    candidate_band = confidence_band(scenario_candidate.confidence) if scenario_candidate is not None else "low"
    reason = ""
    required = False

    if not failure_record.failed_tests:
        required = False
        reason = "No failing tests were detected for this scenario run, so clarification is not required."
    elif failure_record.confidence < CAUTIOUS_SCENARIO_MATCH_THRESHOLD:
        required = True
        reason = "The failing test signal is not strong enough to infer the intended contract safely."
    elif scenario_candidate is None or scenario_candidate.confidence < CAUTIOUS_SCENARIO_MATCH_THRESHOLD:
        required = True
        reason = "No existing test_scenarios record matched this failure with sufficient confidence."
    elif scenario_candidate.confidence < AUTO_SCENARIO_MATCH_THRESHOLD:
        reason = "A partial scenario candidate was found, so its guidance should be reviewed cautiously before relying on it."
    else:
        reason = "A high-confidence scenario record matched this failure; no clarification is required."

    questions = [
        "Is the current failing test asserting the intended contract, or should the contract change instead?",
        "Should the fix preserve the current public API shape and behavior outside the failing scenario?",
    ]
    if scenario_candidate is not None and scenario_candidate.record is not None:
        questions.append(
            f"Should the fix preserve this invariant: {scenario_candidate.record.invariants[0] if scenario_candidate.record.invariants else scenario_candidate.record.title}?"
        )
    else:
        questions.append("Which source file or contract should be treated as the most likely repair surface?")

    return ScenarioClarificationPlan(
        required=required,
        reason=reason,
        confidence_band=candidate_band,
        questions=tuple(questions),
        candidate_match=scenario_candidate,
    )


def scenario_title_from_summary(summary: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", summary)
    if not words:
        return "Proposed failure scenario"
    return " ".join(word.capitalize() for word in words[:8])


def proposed_scenario_id(failure_record: FailureRecord) -> str:
    if failure_record.failed_tests:
        stem = Path(failure_record.failed_tests[0]).stem
        stem = re.sub(r"^test_", "", stem)
        slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
        if slug:
            return f"{slug}_bug"

    summary_slug = re.sub(r"[^a-z0-9]+", "_", failure_record.failure_summary.lower()).strip("_")
    if summary_slug:
        return f"{summary_slug[:48]}_bug"

    return "proposed_failure_bug"


def build_scenario_proposal(
    failure_record: FailureRecord,
    context_files: tuple[str, ...],
    scenario_candidate: ScenarioMatch | None,
    clarification_plan: ScenarioClarificationPlan,
) -> ScenarioProposal | None:
    if scenario_candidate is not None and scenario_candidate.confidence >= AUTO_SCENARIO_MATCH_THRESHOLD:
        return None

    likely_code_areas = tuple(
        path
        for path in context_files
        if path.endswith(".py") and not path.startswith("tests/")
    )[:3]
    failure_signatures = [failure_record.failure_summary]
    for line in failure_record.failure_output.splitlines():
        stripped = line.strip()
        if stripped and stripped not in failure_signatures:
            failure_signatures.append(stripped)
        if len(failure_signatures) >= 3:
            break

    accepted_fix_patterns = (
        scenario_candidate.accepted_fix_patterns
        if scenario_candidate is not None and scenario_candidate.accepted_fix_patterns
        else ("confirm the intended contract before approving this scenario",)
    )
    rejected_fix_patterns = list(scenario_candidate.rejected_fix_patterns if scenario_candidate is not None else ())
    rejected_fix_patterns.append("modify tests without explicit approval")

    invariants = (
        scenario_candidate.invariants
        if scenario_candidate is not None and scenario_candidate.invariants
        else ("confirm the intended invariant before persisting this scenario",)
    )

    return ScenarioProposal(
        scenario_id=proposed_scenario_id(failure_record),
        title=(
            scenario_candidate.record.title
            if scenario_candidate is not None and scenario_candidate.record is not None
            else scenario_title_from_summary(failure_record.failure_summary)
        ),
        origin="auto-proposed",
        failure_signatures=dedupe_preserve_order(failure_signatures),
        affected_tests=dedupe_preserve_order(list(failure_record.failed_tests)),
        likely_code_areas=dedupe_preserve_order(list(likely_code_areas)),
        invariants=dedupe_preserve_order(list(invariants)),
        accepted_fix_patterns=dedupe_preserve_order(list(accepted_fix_patterns)),
        rejected_fix_patterns=dedupe_preserve_order(rejected_fix_patterns),
        review_required=True,
        proposal_reason=clarification_plan.reason,
        confidence=scenario_candidate.confidence if scenario_candidate is not None else 0.0,
    )


def analyze_failure(scenario_name: str) -> FailureAnalysis:
    failure_record = build_failure_record(scenario_name)
    context_files = resolve_context_file_paths(scenario_name, failure_record)
    repo_delta = collect_recent_repo_delta(context_files)
    scenario_candidate = find_best_test_scenario_candidate(failure_record, context_files)
    scenario_match = lookup_test_scenario_match(failure_record, context_files)
    clarification_plan = plan_scenario_clarification(failure_record, scenario_candidate)
    scenario_proposal = build_scenario_proposal(
        failure_record=failure_record,
        context_files=context_files,
        scenario_candidate=scenario_candidate,
        clarification_plan=clarification_plan,
    )
    return FailureAnalysis(
        failure_record=failure_record,
        context_files=context_files,
        repo_delta=repo_delta,
        scenario_candidate=scenario_candidate,
        scenario_match=scenario_match,
        clarification_plan=clarification_plan,
        scenario_proposal=scenario_proposal,
    )


def build_context_from_analysis(analysis: FailureAnalysis) -> str:
    failure_record = analysis.failure_record
    sections = [
        "# FAILURE_RECORD\n"
        + json.dumps(
            {
                "failed_tests": list(failure_record.failed_tests),
                "failure_summary": failure_record.failure_summary,
                "likely_modules": list(failure_record.likely_modules),
                "confidence": failure_record.confidence,
            },
            indent=2,
        ),
        "# FAILURE_OUTPUT\n" + failure_record.failure_output,
    ]

    if analysis.repo_delta is not None:
        sections.append(
            "# RECENT_REPO_DELTA\n"
            + json.dumps(
                {
                    "source": analysis.repo_delta.source,
                    "changed_files": list(analysis.repo_delta.changed_files),
                },
                indent=2,
            )
        )
        if analysis.repo_delta.diff_text:
            sections.append("# RECENT_REPO_DELTA_DIFF\n" + analysis.repo_delta.diff_text)

    if analysis.scenario_match is not None and analysis.scenario_match.record is not None:
        sections.append(
            "# SCENARIO_MATCH\n"
            + json.dumps(
                {
                    "matched": analysis.scenario_match.matched,
                    "source": analysis.scenario_match.source,
                    "scenario_id": analysis.scenario_match.scenario_id,
                    "confidence": analysis.scenario_match.confidence,
                    "accepted_fix_patterns": list(analysis.scenario_match.accepted_fix_patterns),
                    "rejected_fix_patterns": list(analysis.scenario_match.rejected_fix_patterns),
                    "invariants": list(analysis.scenario_match.invariants),
                },
                indent=2,
            )
        )
        sections.append(
            "# TEST_SCENARIO_RECORD\n"
            + json.dumps(
                {
                    "id": analysis.scenario_match.record.scenario_id,
                    "title": analysis.scenario_match.record.title,
                    "origin": analysis.scenario_match.record.origin,
                    "failure_signatures": list(analysis.scenario_match.record.failure_signatures),
                    "affected_tests": list(analysis.scenario_match.record.affected_tests),
                    "likely_code_areas": list(analysis.scenario_match.record.likely_code_areas),
                    "invariants": list(analysis.scenario_match.record.invariants),
                    "accepted_fix_patterns": list(analysis.scenario_match.record.accepted_fix_patterns),
                    "rejected_fix_patterns": list(analysis.scenario_match.record.rejected_fix_patterns),
                    "review_required": analysis.scenario_match.record.review_required,
                },
                indent=2,
            )
        )

    if (
        analysis.scenario_match is None
        and analysis.scenario_candidate is not None
        and analysis.scenario_candidate.record is not None
        and analysis.scenario_candidate.confidence >= CAUTIOUS_SCENARIO_MATCH_THRESHOLD
    ):
        sections.append(
            "# SCENARIO_CANDIDATE\n"
            + json.dumps(
                {
                    "matched": False,
                    "source": analysis.scenario_candidate.source,
                    "scenario_id": analysis.scenario_candidate.scenario_id,
                    "confidence": analysis.scenario_candidate.confidence,
                    "confidence_band": confidence_band(analysis.scenario_candidate.confidence),
                    "accepted_fix_patterns": list(analysis.scenario_candidate.accepted_fix_patterns),
                    "rejected_fix_patterns": list(analysis.scenario_candidate.rejected_fix_patterns),
                    "invariants": list(analysis.scenario_candidate.invariants),
                    "note": analysis.clarification_plan.reason,
                },
                indent=2,
            )
        )

    if analysis.clarification_plan.required:
        sections.append(
            "# CLARIFICATION_REQUEST\n"
            + json.dumps(
                {
                    "required": analysis.clarification_plan.required,
                    "reason": analysis.clarification_plan.reason,
                    "confidence_band": analysis.clarification_plan.confidence_band,
                    "questions": list(analysis.clarification_plan.questions),
                    "candidate_scenario_id": (
                        analysis.clarification_plan.candidate_match.scenario_id
                        if analysis.clarification_plan.candidate_match is not None
                        else None
                    ),
                },
                indent=2,
            )
        )

    for relative_path in analysis.context_files:
        path = REPO_ROOT / relative_path
        if not path.exists():
            continue
        sections.append(f"# FILE: {relative_path}\n{path.read_text()}")

    return "\n\n".join(sections)


def build_context(scenario_name: str) -> str:
    return build_context_from_analysis(analyze_failure(scenario_name))


def optional_output_payload(path: Path, payload: str | None) -> None:
    if payload is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)


def scenario_proposal_payload(proposal: ScenarioProposal) -> str:
    return json.dumps(
        {
            "id": proposal.scenario_id,
            "title": proposal.title,
            "origin": proposal.origin,
            "failure_signatures": list(proposal.failure_signatures),
            "affected_tests": list(proposal.affected_tests),
            "likely_code_areas": list(proposal.likely_code_areas),
            "invariants": list(proposal.invariants),
            "accepted_fix_patterns": list(proposal.accepted_fix_patterns),
            "rejected_fix_patterns": list(proposal.rejected_fix_patterns),
            "review_required": proposal.review_required,
            "proposal_reason": proposal.proposal_reason,
            "confidence": proposal.confidence,
        },
        indent=2,
    )


def clarification_request_payload(plan: ScenarioClarificationPlan) -> str:
    return json.dumps(
        {
            "required": plan.required,
            "reason": plan.reason,
            "confidence_band": plan.confidence_band,
            "questions": list(plan.questions),
            "candidate_scenario_id": plan.candidate_match.scenario_id if plan.candidate_match is not None else None,
            "candidate_confidence": plan.candidate_match.confidence if plan.candidate_match is not None else None,
        },
        indent=2,
    )


def write_analysis_artifacts(
    scenario_name: str,
    analysis: FailureAnalysis,
    destination_dir: Path | None = None,
) -> dict[str, Path]:
    output_dir = destination_dir or ensure_output_dir(scenario_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    context_path = output_dir / "context.txt"
    context_path.write_text(build_context_from_analysis(analysis))
    written["context"] = context_path

    clarification_path = output_dir / "clarification_request.json"
    proposal_path = output_dir / "scenario_proposal.json"

    if analysis.clarification_plan.required:
        optional_output_payload(clarification_path, clarification_request_payload(analysis.clarification_plan))
        written["clarification_request"] = clarification_path
    else:
        optional_output_payload(clarification_path, None)

    if analysis.scenario_proposal is not None:
        optional_output_payload(proposal_path, scenario_proposal_payload(analysis.scenario_proposal))
        written["scenario_proposal"] = proposal_path
    else:
        optional_output_payload(proposal_path, None)

    return written


def write_context_file(scenario_name: str, destination: Path | None = None) -> Path:
    target = destination or scenario_output_path(scenario_name, "context.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    analysis = analyze_failure(scenario_name)
    if target.name == "context.txt" and target.parent == ensure_output_dir(scenario_name):
        write_analysis_artifacts(scenario_name, analysis, target.parent)
    else:
        target.write_text(build_context_from_analysis(analysis))
    return target


def ensure_clarification_not_required(
    scenario_name: str,
    analysis: FailureAnalysis,
    allow_low_confidence: bool = False,
) -> None:
    if allow_low_confidence:
        return

    if not analysis.clarification_plan.required:
        return

    clarification_path = scenario_output_path(scenario_name, "clarification_request.json")
    proposal_path = scenario_output_path(scenario_name, "scenario_proposal.json")
    proposal_suffix = ""
    if proposal_path.exists():
        proposal_suffix = f" and {display_path(proposal_path)}"

    raise ClarificationRequiredError(
        f"Clarification required before repair generation. Review {display_path(clarification_path)}{proposal_suffix}."
    )


def approve_scenario_proposal(proposal_file: Path, force: bool = False) -> Path:
    if not proposal_file.exists():
        raise RuntimeError(f"Scenario proposal file not found: {proposal_file}")

    raw = json.loads(proposal_file.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError("Scenario proposal file must contain a JSON object.")

    scenario_id = str(raw.get("id", "")).strip()
    if not scenario_id:
        raise RuntimeError("Scenario proposal file is missing a non-empty 'id'.")

    target_path = TEST_SCENARIOS_DIR / f"{scenario_id}.json"
    if target_path.exists() and not force:
        raise RuntimeError(
            f"Scenario record already exists: {display_path(target_path)}. Use --force to replace it."
        )

    payload = {
        "id": scenario_id,
        "title": str(raw.get("title", scenario_id)),
        "origin": str(raw.get("origin", "approved-auto-proposed")),
        "failure_signatures": [str(item) for item in raw.get("failure_signatures", []) if isinstance(item, str)],
        "affected_tests": [str(item) for item in raw.get("affected_tests", []) if isinstance(item, str)],
        "likely_code_areas": [str(item) for item in raw.get("likely_code_areas", []) if isinstance(item, str)],
        "invariants": [str(item) for item in raw.get("invariants", []) if isinstance(item, str)],
        "accepted_fix_patterns": [str(item) for item in raw.get("accepted_fix_patterns", []) if isinstance(item, str)],
        "rejected_fix_patterns": [str(item) for item in raw.get("rejected_fix_patterns", []) if isinstance(item, str)],
        "review_required": bool(raw.get("review_required", True)),
    }
    TEST_SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, indent=2) + "\n")
    return target_path


def extract_output_text(response: dict) -> str:
    fragments: list[str] = []

    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                fragments.append(content["text"])

    if fragments:
        return "\n".join(fragments).strip()

    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()

    return ""


def request_edit_plan_via_openai(prompt: str, context: str, model: str | None = None) -> BackendResult:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env or export it in your shell.")

    resolved_model = model or configured_model()
    settings = backend_settings("openai_responses_api")
    endpoint = settings.get("endpoint", "https://api.openai.com/v1/responses")
    timeout_seconds = settings.get("timeout_seconds", 60)
    response_contract = (
        "Return only JSON matching this schema: "
        '{"edits":[{"path":"relative/path.py","content":"full updated file contents"}]}. '
        "Only include files that need to change."
    )
    review_prompt = build_code_review_prompt(
        scenario_instructions=prompt,
        context=context,
        response_contract=response_contract,
    )

    payload = {
        "model": resolved_model,
        "instructions": "Return only strict JSON. Do not include markdown fences, prose, or commentary.",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": review_prompt},
                ],
            }
        ],
    }

    request = urllib.request.Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API request failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc

    payload_text = normalize_json_text(extract_output_text(raw_response))
    payload_json = json.loads(payload_text)
    edits = payload_json.get("edits", [])
    if not isinstance(edits, list):
        raise RuntimeError("OpenAI backend returned an invalid edits payload.")

    return BackendResult(
        backend="openai_responses_api",
        raw_artifact_name=configured_raw_artifact_name("openai_responses_api"),
        raw_artifact_payload=json.dumps(raw_response, indent=2),
        edits=tuple(edits),
    )


def request_edit_plan_via_codex(prompt: str, context: str, model: str | None = None) -> BackendResult:
    resolved_model = model or configured_codex_model()
    settings = backend_settings("codex")
    command = settings.get("command", ["codex", "exec"])
    timeout_seconds = int(settings.get("timeout_seconds", 120))
    sandbox_mode = str(settings.get("sandbox", "read-only"))
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise RuntimeError("backend_settings.codex.command must be a list of strings.")
    if not command or shutil.which(command[0]) is None:
        raise RuntimeError(
            f"The '{command[0] if command else 'codex'}' CLI was not found on PATH. "
            "The codex backend requires the Codex CLI to be installed and available, "
            "or switch to the openai_responses_api backend (set CI_LOOP_BACKEND or "
            "backend in ci_config.json) with OPENAI_API_KEY set."
        )

    schema = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["edits"],
        "additionalProperties": False,
    }

    codex_prompt = build_code_review_prompt(
        scenario_instructions=prompt,
        context=context,
        response_contract=(
            "Return only strict JSON matching the required schema. "
            "Do not include markdown fences, prose, or commentary."
        ),
    )

    with tempfile.TemporaryDirectory(prefix="codex-native-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        last_message_path = temp_path / "last_message.json"
        schema_path.write_text(json.dumps(schema, indent=2))

        exec_args = [
            *command,
            "--json",
            "--ephemeral",
            "--sandbox",
            sandbox_mode,
            "-C",
            str(REPO_ROOT),
            "--output-schema",
            str(schema_path),
            "-o",
            str(last_message_path),
        ]
        if resolved_model:
            exec_args.extend(["-m", resolved_model])
        exec_args.append("-")

        completed = subprocess.run(
            exec_args,
            input=codex_prompt,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        last_message = last_message_path.read_text().strip() if last_message_path.exists() else ""
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

    command_preview = " ".join(command)
    log_lines = [
        "# Codex Native Backend Log",
        "",
        "## Status",
        "",
        f"- exit_code: `{completed.returncode}`",
        f"- backend: `codex`",
        f"- model: `{resolved_model or 'backend-default'}`",
        f"- command: `{command_preview}`",
        f"- sandbox: `{sandbox_mode}`",
        "",
        "## Prompt Preview",
        "",
        markdown_fence(prompt, "text"),
        "",
        "## Context Size",
        "",
        f"- characters: `{len(context)}`",
        "",
        "## Stdout",
        "",
        markdown_fence(stdout or "(no stdout)", "json"),
        "",
        "## Stderr",
        "",
        markdown_fence(stderr or "(no stderr)", "text"),
        "",
        "## Last Message",
        "",
        markdown_fence(last_message or "(no last message captured)", "json"),
    ]
    raw_log = "\n".join(log_lines)
    codex_error = extract_codex_exec_error(stdout)

    if completed.returncode != 0:
        return BackendResult(
            backend="codex",
            raw_artifact_name=configured_raw_artifact_name("codex"),
            raw_artifact_payload=raw_log,
            error_message=codex_error or f"Codex native backend failed with exit code {completed.returncode}.",
        )

    if not last_message:
        return BackendResult(
            backend="codex",
            raw_artifact_name=configured_raw_artifact_name("codex"),
            raw_artifact_payload=raw_log,
            error_message="Codex native backend did not produce a final structured message.",
        )

    try:
        payload = json.loads(normalize_json_text(last_message))
    except json.JSONDecodeError as exc:
        return BackendResult(
            backend="codex",
            raw_artifact_name=configured_raw_artifact_name("codex"),
            raw_artifact_payload=raw_log,
            error_message=f"Codex native backend returned invalid JSON: {exc}",
        )

    edits = payload.get("edits", [])
    if not isinstance(edits, list):
        return BackendResult(
            backend="codex",
            raw_artifact_name=configured_raw_artifact_name("codex"),
            raw_artifact_payload=raw_log,
            error_message="Codex native backend returned an invalid edits payload.",
        )

    return BackendResult(
        backend="codex",
        raw_artifact_name=configured_raw_artifact_name("codex"),
        raw_artifact_payload=raw_log,
        edits=tuple(edits),
    )


def request_edit_plan(prompt: str, context: str, model: str | None = None, backend: str | None = None) -> BackendResult:
    resolved_backend = backend or configured_backend()
    resolved_backend = normalize_backend_name(resolved_backend)
    if resolved_backend == "openai_responses_api":
        return request_edit_plan_via_openai(prompt=prompt, context=context, model=model)
    if resolved_backend == "codex":
        return request_edit_plan_via_codex(prompt=prompt, context=context, model=model)
    raise RuntimeError(f"Unsupported backend: {resolved_backend}")


def normalize_patch_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    match = re.search(r"(?ms)^(diff --git .+|--- .+)$.*", text)
    return match.group(0).strip() if match else text


def normalize_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def patch_targets(patch_text: str) -> list[Path]:
    targets: list[Path] = []
    seen: set[Path] = set()

    for line in patch_text.splitlines():
        if not line.startswith("+++ "):
            continue
        candidate = line[4:].strip()
        if candidate == "/dev/null":
            continue
        if candidate.startswith("b/"):
            candidate = candidate[2:]
        path = REPO_ROOT / candidate
        if path not in seen:
            seen.add(path)
            targets.append(path)

    return targets


def patch_protected_targets(patch_text: str) -> list[str]:
    """Return repo-relative targets in the patch that fall under a protected path.

    A non-empty result means the patch tries to edit files the loop treats as an
    immutable contract (the test suite), and it must be rejected before apply.
    """
    protected: list[str] = []
    seen: set[str] = set()
    for path in patch_targets(patch_text):
        relative = repo_relative_path(path)
        if relative is None:
            # Path escapes the repo root entirely; treat as protected (never apply).
            relative = str(path)
        elif not relative.startswith(PROTECTED_PATH_PREFIXES):
            continue
        if relative not in seen:
            seen.add(relative)
            protected.append(relative)
    return protected


def snapshot_files(paths: list[Path]) -> dict[Path, str | None]:
    backups: dict[Path, str | None] = {}
    for path in paths:
        backups[path] = path.read_text() if path.exists() else None
    return backups


def restore_files(backups: dict[Path, str | None]) -> None:
    for path, content in backups.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def apply_patch_text(patch_text: str, patch_file: Path) -> tuple[bool, str]:
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(patch_text)

    attempts = 0
    last_output = ""

    # Prefer git apply: it refuses to apply with fuzz, so a hunk can never land
    # at the wrong offset and silently report success.
    if git_is_available():
        attempts += 1
        check_code, _, check_err = run_git_command(["apply", "--check", str(patch_file)])
        if check_code == 0:
            apply_code, apply_out, apply_err = run_git_command(["apply", str(patch_file)])
            combined = "\n".join(part for part in [apply_out.strip(), apply_err.strip()] if part)
            if apply_code == 0:
                return True, combined or "Patch applied cleanly with git apply."
            last_output = combined
        else:
            last_output = check_err.strip()

    # Fall back to patch(1). It applies with fuzz, so it is only used when the
    # strict git apply path is unavailable or the diff context is imperfect.
    if shutil.which("patch"):
        for strip_level in ("0", "1"):
            attempts += 1
            result = subprocess.run(
                ["patch", f"-p{strip_level}", "--forward", "--input", str(patch_file)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            combined = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
            if result.returncode == 0:
                return True, combined
            last_output = combined

    if attempts == 0:
        return False, (
            "No patch applier available: git apply requires a git work tree and "
            "the patch(1) binary was not found on PATH."
        )

    return False, last_output


def render_patch_from_edits(edits: list[dict]) -> str:
    patch_chunks: list[str] = []

    for edit in edits:
        relative_path = edit.get("path")
        content = edit.get("content")

        if not isinstance(relative_path, str) or not isinstance(content, str):
            raise RuntimeError("Each edit must contain string 'path' and 'content' fields.")

        path = resolve_repo_relative_path(relative_path)
        if not path.exists():
            raise RuntimeError(f"Edit path does not exist in the repo: {relative_path}")

        original_lines = [f"{line}\n" for line in path.read_text().splitlines()]
        updated_lines = [f"{line}\n" for line in content.splitlines()]

        diff = list(
            difflib.unified_diff(
                original_lines,
                updated_lines,
                fromfile=relative_path,
                tofile=relative_path,
            )
        )
        if diff:
            patch_chunks.append("".join(diff))

    return "".join(patch_chunks).strip()


def generate_patch(
    scenario_name: str,
    prompt: str,
    model: str | None = None,
    backend: str | None = None,
    write_response: bool = True,
    allow_low_confidence: bool = False,
) -> tuple[str, str]:
    output_dir = ensure_output_dir(scenario_name)
    analysis = analyze_failure(scenario_name)
    artifacts = write_analysis_artifacts(scenario_name, analysis, output_dir)
    ensure_clarification_not_required(
        scenario_name,
        analysis,
        allow_low_confidence=allow_low_confidence,
    )
    context_path = artifacts["context"]
    patch_output_path = output_dir / "patch.diff"
    if patch_output_path.exists():
        patch_output_path.unlink()
    result = request_edit_plan(prompt=prompt, context=context_path.read_text(), model=model, backend=backend)
    if write_response:
        (output_dir / result.raw_artifact_name).write_text(result.raw_artifact_payload)

    if result.error_message:
        raise RuntimeError(result.error_message)

    patch_text = result.patch_text or render_patch_from_edits(list(result.edits))
    if patch_text:
        patch_output_path.write_text(patch_text)
        return patch_text, result.raw_artifact_name

    return "", result.raw_artifact_name


def extract_patch_from_response(response_file: Path, output_file: Path | None = None) -> str:
    response = json.loads(response_file.read_text())
    patch_text = normalize_patch_text(extract_output_text(response))
    if patch_text and output_file is not None:
        output_file.write_text(patch_text)
    return patch_text


def run_demo(
    scenario_name: str,
    max_retries: int = MAX_RETRIES,
    model: str | None = None,
    backend: str | None = None,
    clarification_policy: str = "fail",
    clarifier_option_source: str = "backend",
    dry_run: bool = False,
) -> int:
    scenario = get_scenario(scenario_name)
    print(f"\n--- CI LOOP START ({scenario.name}) ---\n")
    if dry_run:
        print("Mode: --dryRun enabled. Accepted fixes will be reverted after validation.\n")
    else:
        print("Mode: live run. Accepted fixes will remain in the working tree.\n")
    ensure_output_dir(scenario_name)
    accepted_backups: dict[Path, str | None] | None = None
    baseline_passed, _ = run_tests(scenario_name)
    if baseline_passed:
        print("Validation pre-check passed; scenario is already green. Skipping repair generation.\n")
        if dry_run:
            print("Run complete (--dryRun): no persistent repository changes were made.\n")
        else:
            print("Run complete (live): repository state unchanged because scenario was already green.\n")
        return 0

    regression_guard = collect_green_regression_set(scenario.test_targets)
    if regression_guard:
        print(
            "Regression guard: an accepted fix must keep these currently-green tests green: "
            + ", ".join(regression_guard)
            + "\n"
        )

    for attempt in range(1, max_retries + 1):
        prompt = scenario.base_prompt if attempt == 1 else scenario.constrained_prompt
        print(f"--- Attempt {attempt} ---")
        print(f"Prompt: {prompt}\n")

        try:
            patch_text, _ = generate_patch(
                scenario_name=scenario_name,
                prompt=prompt,
                model=model,
                backend=backend,
            )
        except ClarificationRequiredError as exc:
            print(str(exc))
            if clarification_policy != "interactive":
                print("Stopping before repair generation.\n")
                if dry_run:
                    print("Run complete (--dryRun): no persistent repository changes were made.\n")
                else:
                    print("Run complete (live): no accepted fix was applied.\n")
                return 1

            analysis = analyze_failure(scenario_name)
            artifacts = write_analysis_artifacts(scenario_name, analysis)
            print("Runtime clarification is required before generation can continue.")
            print(f"Reason: {analysis.clarification_plan.reason}")
            print("Questions:")
            for index, question in enumerate(analysis.clarification_plan.questions, start=1):
                print(f"  {index}. {question}")
            print(
                "Generated artifacts: "
                + ", ".join(display_path(path) for path in artifacts.values())
            )
            answers = collect_runtime_clarification_answers(
                analysis.clarification_plan,
                analysis,
                backend=backend,
                model=model,
                scenario_name=scenario_name,
                clarifier_option_source=clarifier_option_source,
            )
            if not answers:
                print("Interactive clarification stopped by operator or unavailable stdin.\n")
                if dry_run:
                    print("Run complete (--dryRun): no persistent repository changes were made.\n")
                else:
                    print("Run complete (live): no accepted fix was applied.\n")
                return 1
            resolved_answers = tuple(str(item) for item in answers.get("answers", ()))
            rounds = tuple(
                item for item in answers.get("rounds", ()) if isinstance(item, dict)
            )
            dialog_backend = answers.get("dialog_backend")
            if not isinstance(dialog_backend, str):
                dialog_backend = "heuristic"
            response_thread = tuple(
                str(item) for item in answers.get("response_thread_ids", ()) if isinstance(item, str)
            )
            dialog_path = scenario_output_path(scenario_name, "clarification_dialog.json")
            dialog_payload = clarification_dialog_payload(
                analysis.clarification_plan.questions,
                resolved_answers,
                rounds=rounds,
                dialog_backend=dialog_backend,
                response_thread=response_thread,
            )
            dialog_path.write_text(dialog_payload)
            print(f"Captured clarification dialog trace in {display_path(dialog_path)}.")
            clarified_prompt = build_prompt_with_runtime_clarifications(
                prompt,
                analysis.clarification_plan.questions,
                resolved_answers,
            )

            try:
                patch_text, _ = generate_patch(
                    scenario_name=scenario_name,
                    prompt=clarified_prompt,
                    model=model,
                    backend=backend,
                    allow_low_confidence=True,
                )
            except Exception as retry_exc:
                print(f"Patch generation failed after clarification approval: {retry_exc}")
                print("Retrying...\n")
                time.sleep(1)
                continue
        except Exception as exc:
            print(f"Patch generation failed: {exc}")
            print("Retrying...\n")
            time.sleep(1)
            continue

        if not patch_text.strip():
            print("No patch generated. Retrying...\n")
            continue

        print("Generated Patch Preview:\n")
        print(patch_text[:800])
        print()

        protected = patch_protected_targets(patch_text)
        if protected:
            print(
                "Patch rejected: it would modify protected files that are treated as "
                "the immutable contract: " + ", ".join(protected)
            )
            print("Tests define acceptance; the loop will not let a fix edit them.\n")
            time.sleep(1)
            continue

        backups = snapshot_files(patch_targets(patch_text))
        applied, apply_output = apply_patch_text(patch_text, scenario_output_path(scenario_name, "patch.diff"))
        if not applied:
            print("Patch failed to apply:")
            print(apply_output or "(no patch output)")
            restore_files(backups)
            print()
            continue

        tests_passed, test_output = run_tests(scenario_name)
        print("Validation Output:\n")
        print(test_output)
        print()

        if tests_passed:
            if regression_guard:
                regression_ok, regression_output = run_test_targets(regression_guard)
                if not regression_ok:
                    print("Regression check failed: the fix broke previously-green tests:\n")
                    print(regression_output)
                    restore_files(backups)
                    print("\nChange rejected and repository restored for the next attempt.\n")
                    time.sleep(1)
                    continue
                print("Regression check passed: no previously-green test regressed.")
            print("Tests passed. Change accepted.\n")
            accepted_backups = backups
            break

        restore_files(backups)
        print("Tests failed. Repository restored for the next attempt.\n")
        time.sleep(1)
    else:
        print("All attempts failed. Manual intervention required.\n")
        if dry_run:
            print("Run complete (--dryRun): repository restored or unchanged after failed attempts.\n")
        else:
            print("Run complete (live): no accepted fix was kept.\n")
        return 1

    if accepted_backups is not None:
        if dry_run:
            restore_files(accepted_backups)
            print("Repository restored to baseline after successful run (--dryRun).\n")
        else:
            print("Change kept in working tree after successful run (dry-run disabled).\n")

    if dry_run:
        print("Run complete (--dryRun): accepted changes were reverted after validation.\n")
    else:
        print("Run complete (live): accepted changes remain in the working tree.\n")
    return 0


def run_all(
    max_retries: int = MAX_RETRIES,
    model: str | None = None,
    backend: str | None = None,
    include_non_gating: bool = False,
    clarification_policy: str = "fail",
    clarifier_option_source: str = "backend",
    dry_run: bool = False,
) -> int:
    suite_name = "FULL SCENARIO SUITE" if include_non_gating else "GATING SCENARIOS"
    print(f"\n=== RUNNING ALL {suite_name} ===\n")
    if dry_run:
        print("Mode: --dryRun enabled. Accepted fixes for each scenario will be reverted.\n")
    else:
        print("Mode: live run. Accepted fixes can persist in the working tree.\n")
    results: list[tuple[str, int]] = []

    for scenario_name in scenario_choices(include_non_gating=include_non_gating):
        result = run_demo(
            scenario_name=scenario_name,
            max_retries=max_retries,
            model=model,
            backend=backend,
            clarification_policy=clarification_policy,
            clarifier_option_source=clarifier_option_source,
            dry_run=dry_run,
        )
        results.append((scenario_name, result))

    print("=== SCENARIO SUMMARY ===")
    for scenario_name, result in results:
        status = "passed" if result == 0 else "failed"
        print(f"- {scenario_name}: {status}")

    if dry_run:
        print("\nRun-all complete (--dryRun): accepted scenario fixes were reverted after validation.")
    else:
        print("\nRun-all complete (live): accepted scenario fixes will remain in the working tree.")
    return 0 if all(result == 0 for _, result in results) else 1


def add_scenario_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scenario",
        default="scenario_1_integration_bug",
        choices=scenario_choices(),
        help="Which scenario to run.",
    )


def add_backend_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        default=None,
        help="Optional backend override. Defaults to CI_LOOP_BACKEND, then ci_config.json, then codex.",
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex-in-the-loop CI gatekeeper demo.")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list-scenarios", help="List the supported demo scenarios.")
    list_parser.add_argument("--verbose", action="store_true", help="Show scenario files and prompts.")

    test_parser = subparsers.add_parser("test", help="Run validation for a demo scenario.")
    add_scenario_arg(test_parser)

    context_parser = subparsers.add_parser("build-context", help="Write a failure-driven scenario context file under output/<scenario>/context.txt.")
    add_scenario_arg(context_parser)
    context_parser.add_argument("--output", default=None, help="Optional custom path for the built context.")

    clarification_parser = subparsers.add_parser(
        "plan-clarification",
        help="Write clarification_request.json and scenario_proposal.json when the current failure is not classified confidently.",
    )
    add_scenario_arg(clarification_parser)

    generate_parser = subparsers.add_parser("generate-patch", help="Call the configured backend and write output artifacts for a scenario.")
    add_scenario_arg(generate_parser)
    add_backend_arg(generate_parser)
    generate_parser.add_argument(
        "--prompt",
        default=None,
        help="Optional prompt override. Defaults to the scenario's constrained prompt.",
    )
    generate_parser.add_argument("--model", default=None, help="Optional model override. Defaults to OPENAI_MODEL, then ci_config.json, then gpt-4.1.")

    extract_parser = subparsers.add_parser("extract-patch", help="Extract output/<scenario>/patch.diff from an existing OpenAI response JSON file.")
    add_scenario_arg(extract_parser)
    extract_parser.add_argument("--response-file", default=None, help="OpenAI Responses API JSON file to parse.")
    extract_parser.add_argument("--output", default=None, help="Where to write the extracted patch.")

    approve_parser = subparsers.add_parser(
        "approve-scenario-proposal",
        help="Persist an approved scenario_proposal.json into test_scenarios/.",
    )
    add_scenario_arg(approve_parser)
    approve_parser.add_argument("--proposal-file", default=None, help="Proposal file to approve. Defaults to output/<scenario>/scenario_proposal.json.")
    approve_parser.add_argument("--force", action="store_true", help="Replace an existing test_scenarios record if it already exists.")

    apply_parser = subparsers.add_parser("apply", help="Apply an existing unified diff.")
    add_scenario_arg(apply_parser)
    apply_parser.add_argument("patch_file", nargs="?", default=None, help="Patch file to apply.")

    hook_parser = subparsers.add_parser("pre-commit-gate", help="Run the tracked local pre-commit gate.")
    hook_parser.add_argument("--max-retries", type=int, default=2, help="Number of attempts before failing the hook.")

    run_parser = subparsers.add_parser("run", help="Run the end-to-end demo loop for a scenario.")
    add_scenario_arg(run_parser)
    add_backend_arg(run_parser)
    run_parser.add_argument("--model", default=None, help="Optional model override. Defaults to OPENAI_MODEL, then ci_config.json, then gpt-4.1.")
    run_parser.add_argument("--max-retries", type=int, default=MAX_RETRIES, help="Number of attempts before giving up.")
    run_parser.add_argument(
        "--clarification-policy",
        default="fail",
        choices=("fail", "interactive"),
        help="How low-confidence clarifications are handled: fail (default) or interactive.",
    )
    run_parser.add_argument(
        "--clarifier-option-source",
        default="backend",
        choices=("backend", "heuristic"),
        help="Where clarification options come from in interactive mode: backend (default) or forced heuristic.",
    )
    run_parser.add_argument(
        "--dryRun",
        "-dryRun",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Restore files to baseline after accepted runs (demo-safe mode).",
    )

    run_all_parser = subparsers.add_parser("run-all", help="Run the full scenario suite.")
    add_backend_arg(run_all_parser)
    run_all_parser.add_argument("--model", default=None, help="Optional model override. Defaults to OPENAI_MODEL, then ci_config.json, then gpt-4.1.")
    run_all_parser.add_argument("--max-retries", type=int, default=MAX_RETRIES, help="Number of attempts per scenario before giving up.")
    run_all_parser.add_argument(
        "--include-non-gating",
        action="store_true",
        help="Include low-confidence demo scenarios (for example scenario_4_low_confidence).",
    )
    run_all_parser.add_argument(
        "--clarification-policy",
        default="fail",
        choices=("fail", "interactive"),
        help="How low-confidence clarifications are handled: fail (default) or interactive.",
    )
    run_all_parser.add_argument(
        "--clarifier-option-source",
        default="backend",
        choices=("backend", "heuristic"),
        help="Where clarification options come from in interactive mode: backend (default) or forced heuristic.",
    )
    run_all_parser.add_argument(
        "--dryRun",
        "-dryRun",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Restore files to baseline after accepted runs (demo-safe mode).",
    )

    return parser


def main() -> int:
    load_dotenv()
    parser = make_parser()
    args = parser.parse_args()
    command = args.command or "run"

    if command == "list-scenarios":
        for scenario in SCENARIOS.values():
            print(f"{scenario.name}: {scenario.title}")
            print(f"  {scenario.summary}")
            print(f"  participates in run-all: {'yes' if scenario.include_in_run_all else 'no'}")
            if args.verbose:
                print(f"  fallback context: {', '.join(scenario.context_files)}")
                print(f"  tests: {', '.join(scenario.test_targets)}")
        return 0

    scenario_name = getattr(args, "scenario", "scenario_1_integration_bug")

    if command == "test":
        tests_passed, output = run_tests(scenario_name)
        print(output)
        return 0 if tests_passed else 1

    if command == "build-context":
        output_path = Path(args.output) if args.output else scenario_output_path(scenario_name, "context.txt")
        if not output_path.is_absolute():
            output_path = REPO_ROOT / output_path
        write_context_file(scenario_name, output_path)
        message = [f"Context prepared in {output_path.relative_to(REPO_ROOT)}"]
        clarification_path = scenario_output_path(scenario_name, "clarification_request.json")
        proposal_path = scenario_output_path(scenario_name, "scenario_proposal.json")
        if clarification_path.exists():
            message.append(f"clarification request written in {clarification_path.relative_to(REPO_ROOT)}")
        if proposal_path.exists():
            message.append(f"scenario proposal written in {proposal_path.relative_to(REPO_ROOT)}")
        print(", ".join(message))
        return 0

    if command == "plan-clarification":
        analysis = analyze_failure(scenario_name)
        artifacts = write_analysis_artifacts(scenario_name, analysis)
        if analysis.clarification_plan.required:
            print(
                f"Clarification required. Review {artifacts['clarification_request'].relative_to(REPO_ROOT)}"
                + (
                    f" and {artifacts['scenario_proposal'].relative_to(REPO_ROOT)}."
                    if "scenario_proposal" in artifacts
                    else "."
                )
            )
        elif "scenario_proposal" in artifacts:
            print(f"No blocking clarification required. Review {artifacts['scenario_proposal'].relative_to(REPO_ROOT)} before approving any new scenario record.")
        else:
            print("No clarification or scenario proposal is needed for this failure.")
        return 0

    if command == "generate-patch":
        scenario = get_scenario(scenario_name)
        prompt = args.prompt or scenario.constrained_prompt
        try:
            patch_text, raw_artifact_name = generate_patch(
                scenario_name=scenario_name,
                prompt=prompt,
                model=args.model,
                backend=args.backend,
            )
        except RuntimeError as exc:
            print(str(exc))
            return 1
        if not patch_text:
            print("No patch text found in the model response.")
            return 1
        print(
            f"output/{scenario_name}/context.txt, "
            f"output/{scenario_name}/{raw_artifact_name}, and "
            f"output/{scenario_name}/patch.diff written."
        )
        return 0

    if command == "extract-patch":
        response_path = Path(args.response_file) if args.response_file else scenario_output_path(scenario_name, "response.json")
        output_path = Path(args.output) if args.output else scenario_output_path(scenario_name, "patch.diff")
        if not response_path.is_absolute():
            response_path = REPO_ROOT / response_path
        if not output_path.is_absolute():
            output_path = REPO_ROOT / output_path
        if not response_path.exists():
            print(f"Response file not found: {response_path}")
            return 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        patch_text = extract_patch_from_response(response_path, output_path)
        if not patch_text:
            print("No patch text found in the response file.")
            return 1
        print(f"Patch extracted to {output_path.relative_to(REPO_ROOT)}")
        return 0

    if command == "approve-scenario-proposal":
        proposal_path = Path(args.proposal_file) if args.proposal_file else scenario_output_path(scenario_name, "scenario_proposal.json")
        if not proposal_path.is_absolute():
            proposal_path = REPO_ROOT / proposal_path
        try:
            target_path = approve_scenario_proposal(proposal_path, force=args.force)
        except RuntimeError as exc:
            print(str(exc))
            return 1
        print(f"Scenario record written to {target_path.relative_to(REPO_ROOT)}")
        return 0

    if command == "apply":
        patch_path = Path(args.patch_file) if args.patch_file else scenario_output_path(scenario_name, "patch.diff")
        if not patch_path.is_absolute():
            patch_path = REPO_ROOT / patch_path
        if not patch_path.exists():
            print(f"Patch file not found: {patch_path}")
            return 1
        patch_text = patch_path.read_text()
        protected = patch_protected_targets(patch_text)
        if protected:
            print(
                "Patch rejected: it would modify protected files that are treated as "
                "the immutable contract: " + ", ".join(protected)
            )
            return 1
        applied, output = apply_patch_text(patch_text, patch_path)
        print(output or "Patch applied.")
        return 0 if applied else 1

    if command == "pre-commit-gate":
        if not pre_commit_hook_enabled():
            print("Pre-commit gate is disabled.")
            return 0
        return run_all(
            max_retries=args.max_retries,
            backend="codex",
            include_non_gating=False,
            clarification_policy="fail",
            clarifier_option_source="backend",
            dry_run=True,
        )

    if command == "run":
        return run_demo(
            scenario_name=scenario_name,
            max_retries=args.max_retries,
            model=args.model,
            backend=args.backend,
            clarification_policy=args.clarification_policy,
            clarifier_option_source=args.clarifier_option_source,
            dry_run=args.dry_run,
        )

    if command == "run-all":
        return run_all(
            max_retries=args.max_retries,
            model=args.model,
            backend=args.backend,
            include_non_gating=args.include_non_gating,
            clarification_policy=args.clarification_policy,
            clarifier_option_source=args.clarifier_option_source,
            dry_run=args.dry_run,
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
