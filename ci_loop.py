from __future__ import annotations

import argparse
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
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_BACKEND = "codex"
OPENAI_BACKUP_BACKEND = "openai_responses_api"
MAX_RETRIES = 3


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    summary: str
    context_files: tuple[str, ...]
    test_targets: tuple[str, ...]
    base_prompt: str
    constrained_prompt: str


@dataclass(frozen=True)
class BackendResult:
    backend: str
    raw_artifact_name: str
    raw_artifact_payload: str
    edits: tuple[dict, ...] = ()
    patch_text: str | None = None
    error_message: str | None = None


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
}


def scenario_choices() -> list[str]:
    return list(SCENARIOS.keys())


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


def configured_raw_artifact_name(backend: str) -> str:
    normalized_backend = normalize_backend_name(backend)
    config = load_repo_config()
    raw_artifact_name = config.get("raw_artifact_name")
    if isinstance(raw_artifact_name, str) and raw_artifact_name.strip():
        return raw_artifact_name.strip()
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


def run_tests(scenario_name: str) -> tuple[bool, str]:
    scenario = get_scenario(scenario_name)
    code, stdout, stderr = run_command(
        validation_command(scenario.test_targets),
        extra_env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
    return code == 0, output or "(no output)"


def build_context(scenario_name: str) -> str:
    scenario = get_scenario(scenario_name)
    sections: list[str] = []
    for relative_path in scenario.context_files:
        path = REPO_ROOT / relative_path
        if not path.exists():
            continue
        sections.append(f"# FILE: {relative_path}\n{path.read_text()}")
    return "\n\n".join(sections)


def write_context_file(scenario_name: str, destination: Path | None = None) -> Path:
    target = destination or scenario_output_path(scenario_name, "context.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_context(scenario_name))
    return target


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

    payload = {
        "model": resolved_model,
        "instructions": (
            "You are a senior engineer. "
            "Return only JSON matching this schema: "
            '{"edits":[{"path":"relative/path.py","content":"full updated file contents"}]}. '
            "Only include files that need to change. "
            "Do not include markdown fences or commentary."
        ),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_text", "text": context},
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

    codex_prompt = "\n\n".join(
        [
            "You are a strict senior engineer inside a CI gatekeeper worker.",
            "Find the smallest invariant-preserving fix that makes the failing test pass.",
            "Prefer repairing the real contract or write path over adding a workaround on the read path.",
            "Do not change tests, do not add unrelated refactors, and do not widen the fix beyond the minimum files needed.",
            "Think about what could break if you fix the wrong layer, and reject tempting band-aids that only hide the symptom.",
            "Return only strict JSON matching the required schema.",
            "Do not include markdown fences, prose, or commentary.",
            "Do not apply changes directly to the repository.",
            f"Scenario instructions: {prompt}",
            "Repository context follows.",
            context,
        ]
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

    last_output = ""
    for strip_level in ("0", "1"):
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
) -> tuple[str, str]:
    output_dir = ensure_output_dir(scenario_name)
    context_path = write_context_file(scenario_name, output_dir / "context.txt")
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
) -> int:
    scenario = get_scenario(scenario_name)
    print(f"\n--- CI LOOP START ({scenario.name}) ---\n")
    ensure_output_dir(scenario_name)
    accepted_backups: dict[Path, str | None] | None = None

    for attempt in range(1, max_retries + 1):
        prompt = scenario.base_prompt if attempt == 1 else scenario.constrained_prompt
        print(f"--- Attempt {attempt} ---")
        print(f"Prompt: {prompt}\n")

        try:
            patch_text, _ = generate_patch(scenario_name=scenario_name, prompt=prompt, model=model, backend=backend)
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
            print("Tests passed. Change accepted.\n")
            accepted_backups = backups
            break

        restore_files(backups)
        print("Tests failed. Repository restored for the next attempt.\n")
        time.sleep(1)
    else:
        print("All attempts failed. Manual intervention required.\n")
        return 1

    if accepted_backups is not None:
        restore_files(accepted_backups)
        print("Repository restored to baseline after successful run.\n")

    return 0


def run_all(max_retries: int = MAX_RETRIES, model: str | None = None, backend: str | None = None) -> int:
    print("\n=== RUNNING ALL SCENARIOS ===\n")
    results: list[tuple[str, int]] = []

    for scenario_name in scenario_choices():
        result = run_demo(scenario_name=scenario_name, max_retries=max_retries, model=model, backend=backend)
        results.append((scenario_name, result))

    print("=== SCENARIO SUMMARY ===")
    for scenario_name, result in results:
        status = "passed" if result == 0 else "failed"
        print(f"- {scenario_name}: {status}")

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

    context_parser = subparsers.add_parser("build-context", help="Write a scenario context file under output/<scenario>/context.txt.")
    add_scenario_arg(context_parser)
    context_parser.add_argument("--output", default=None, help="Optional custom path for the built context.")

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

    apply_parser = subparsers.add_parser("apply", help="Apply an existing unified diff.")
    add_scenario_arg(apply_parser)
    apply_parser.add_argument("patch_file", nargs="?", default=None, help="Patch file to apply.")

    run_parser = subparsers.add_parser("run", help="Run the end-to-end demo loop for a scenario.")
    add_scenario_arg(run_parser)
    add_backend_arg(run_parser)
    run_parser.add_argument("--model", default=None, help="Optional model override. Defaults to OPENAI_MODEL, then ci_config.json, then gpt-4.1.")
    run_parser.add_argument("--max-retries", type=int, default=MAX_RETRIES, help="Number of attempts before giving up.")

    run_all_parser = subparsers.add_parser("run-all", help="Run the full scenario suite.")
    add_backend_arg(run_all_parser)
    run_all_parser.add_argument("--model", default=None, help="Optional model override. Defaults to OPENAI_MODEL, then ci_config.json, then gpt-4.1.")
    run_all_parser.add_argument("--max-retries", type=int, default=MAX_RETRIES, help="Number of attempts per scenario before giving up.")

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
            if args.verbose:
                print(f"  context: {', '.join(scenario.context_files)}")
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
        print(f"Context prepared in {output_path.relative_to(REPO_ROOT)}")
        return 0

    if command == "generate-patch":
        scenario = get_scenario(scenario_name)
        prompt = args.prompt or scenario.constrained_prompt
        patch_text, raw_artifact_name = generate_patch(
            scenario_name=scenario_name,
            prompt=prompt,
            model=args.model,
            backend=args.backend,
        )
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

    if command == "apply":
        patch_path = Path(args.patch_file) if args.patch_file else scenario_output_path(scenario_name, "patch.diff")
        if not patch_path.is_absolute():
            patch_path = REPO_ROOT / patch_path
        if not patch_path.exists():
            print(f"Patch file not found: {patch_path}")
            return 1
        applied, output = apply_patch_text(patch_path.read_text(), patch_path)
        print(output or "Patch applied.")
        return 0 if applied else 1

    if command == "run":
        return run_demo(
            scenario_name=scenario_name,
            max_retries=args.max_retries,
            model=args.model,
            backend=args.backend,
        )

    if command == "run-all":
        return run_all(max_retries=args.max_retries, model=args.model, backend=args.backend)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
