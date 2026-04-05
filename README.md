# Codex-in-the-loop CI Gatekeeper Demo

This repository shows how to put a coding model inside a controlled CI loop instead of treating generation as the end of the workflow.

The codebase is intentionally left in a buggy baseline state. That is part of the demo. The job of the CI loop is to inspect the broken state, build a constrained failure-driven context snapshot, call the configured backend for a candidate change, render a patch, rerun validation, and decide whether the change should be accepted.

This repo is designed around two operating modes:

- `codex` is the default local development path, where a developer runs the loop before commit as a pre-hook style quality gate
- `openai_responses_api` is the backup remote CI path, where Jenkins or another pipeline triggers the loop after a commit lands on a UAT or prod-tagged branch

In both cases, [ci_loop.py](ci_loop.py) is the gatekeeper. It is the component that decides whether a generated patch is valid enough to count as a pass.

## Explicit Assumption

This workflow is currently failure-driven and test-first.

- It assumes the target repository already has meaningful executable tests for the behavior you care about.
- In practice, that means functional/integration scenario tests are the primary signal for acceptance or rejection.
- Unit tests are useful supporting coverage, but unit-only coverage is usually not enough for this gate to reflect real system behavior.
- If no relevant tests exist, the loop can still generate patches, but acceptance quality and confidence drop because the validation signal is weak.
- This workflow is not designed as a general code-review agent. It is a code-fix gate for buggy implementations detected by failing pre-defined tests.
- For repositories that do not define tests before implementation, this workflow has limited practical value.

## Core Principles

- `zero-trust`: this is built as a workflow guard, not as an optional engineer habit. If review depends on someone remembering to trigger a sub-agent before push, it will eventually be skipped.
- `automation over memory`: the repo now includes a real tracked pre-commit gate, so the local review path can be enforced by tooling instead of process discipline.
- `model council`: a non-Anthropic-family reviewer checking Anthropic-family generated code is a deliberate choice to reduce same-model bias and improve output quality plus test coverage.

## Why This Repo Exists

Most coding-agent demos stop at "the model wrote some code."

That is not the hard part in production.

The harder part is deciding whether a generated change is safe to merge.

This repo focuses on that boundary:

- build context from the current repo state
- ask the model for a constrained change
- render and apply a patch
- run validations
- accept or reject the change

## Architecture

```text
        +------------------+
        |   Codebase       |
        +--------+---------+
                 |
                 v
        +------------------+
        | Context Builder  |
        +--------+---------+
                 |
                 v
        +------------------+
        |     Codex        |
        |  (Diff Gen)      |
        +--------+---------+
                 |
                 v
        +------------------+
        |   Patch (Diff)   |
        +--------+---------+
                 |
                 v
        +------------------+
        | Validation Layer |
        | Tests / Lint     |
        +--------+---------+
                 |
        +--------+---------+
        |                  |
        v                  v
      Accept             Reject
```

### Local Development Flow: `codex`

```text
Developer change
        ↓
Local hook or manual trigger
        ↓
python3 ci_loop.py run-all --backend codex
        ↓
Build Context (repo snapshot)
        ↓
Codex CLI worker (`codex exec`)
        ↓
response.md + patch.diff
        ↓
Validation Layer
    - Scenario test
    - Optional local lint/static checks
        ↓
Decision
    - Accept candidate patch
    - Reject and keep working tree at baseline
        ↓
Developer decides whether to commit
```

### Remote CI Flow: `openai_responses_api`

```text
Remote commit to UAT/prod-tagged branch
        ↓
Jenkins post-commit build trigger
        ↓
python3 ci_loop.py run-all --backend openai_responses_api
        ↓
Build Context (repo snapshot)
        ↓
OpenAI Responses API
        ↓
response.json + patch.diff
        ↓
Validation Layer
    - Scenario test
    - Optional lint/static/security checks
        ↓
Decision Layer
    - Accept
    - Reject
    - Retry
        ↓
Mark CI build success or failure
```

In this repository, backend selection is configurable. The shared loop is:

- `ci_loop.py` builds `context.txt` from the failing test run, the observed failure output, dynamically discovered local code dependencies, recent repo delta when that signal is relevant, and matched or candidate `test_scenarios/` knowledge depending on confidence
- `ci_loop.py` dispatches the scenario attempt through the configured backend
- the backend writes a raw artifact such as `response.json` or `response.md`
- the repo renders those edits into `patch.diff`
- the patch is applied and validated locally

If you want this demo to use a specific Codex-capable model on the OpenAI backup path, set `OPENAI_MODEL` accordingly. The Responses API is the mechanism for the backup route. The configured model behind that API is the reasoning engine.

## Model Configuration

The default backend and model are stored in [ci_config.json](ci_config.json):

```json
{
  "backend": "codex",
  "openai_model": "gpt-4.1"
}
```

Backend resolution order is:

1. `--backend` CLI flag
2. `CI_LOOP_BACKEND` environment variable
3. `ci_config.json`
4. built-in fallback: `codex`

Model resolution order is:

1. `--model` CLI flag
2. `OPENAI_MODEL` environment variable
3. `ci_config.json`
4. built-in fallback: `gpt-4.1`

How to change it:

- Edit [ci_config.json](ci_config.json) if you want to change the repo default backend or model.
- Set `CI_LOOP_BACKEND=...` if you want a per-environment backend override.
- Set `OPENAI_MODEL=...` if you want a per-environment override.
- Pass `--model ...` if you want a one-off run override.

Supported backends today:

- `codex`: implemented via non-interactive `codex exec` and used by default for local developer-time execution
- `openai_responses_api`: implemented as the backup route for remote CI execution

Backend-specific runtime requirements:

- `codex` requires a working authenticated Codex CLI session and available Codex usage quota
- `openai_responses_api` requires `OPENAI_API_KEY` plus network access

Optional local viewer:

```bash
brew install glow
```

`glow` is not required to run the repo, but it makes `response.md` and `patch.diff` much easier to present in a terminal demo.

## Install The Local Hook

Install the tracked Git hook:

```bash
./install_git_hooks.sh
```

That configures `core.hooksPath=.githooks` and enables the local pre-commit gate in `.githooks/pre-commit`.

Hook control options:

- default: enabled through `ci_config.json` via `git_hooks.pre_commit_enabled`
- temporary local bypass: run with `SKIP_CI_GATEKEEPER_PRE_COMMIT=1`

That gives you a stable repo-level switch plus a one-off escape hatch for local testing.

Important limit:

The default backend is now `codex`, so the repo can run without any OpenAI API key dependency when you use the Codex CLI path. The backup `openai_responses_api` route still calls `https://api.openai.com/v1/responses`, so it requires network access and an API key.

## Scenario Coverage

All scenario files under `demo_scenarios/` map to runnable demo flows:

- `scenario_1_integration_bug`
  Write/read inconsistency in [user_store.py](user_store.py)
- `scenario_2_wrong_fix_path`
  Tempting local fix vs systemic fix in [user_registry.py](user_registry.py)
- `scenario_3_refactor_bug`
  Contract drift between [orders.py](orders.py) and [pricing.py](pricing.py)
- `scenario_4_low_confidence`
  Deliberately unmapped failure to demonstrate clarification/proposal artifact generation using [delivery_window.py](delivery_window.py)

Machine-usable recurring scenario knowledge now lives separately under `test_scenarios/`. That registry is used for automated matching and prompt enrichment; `demo_scenarios/` remains human-facing demo documentation only.
The old `scenarios/` folder is removed and no longer used.

## Failure-Driven Phases

The runtime now implements five phases.

### Phase 1: Failure Intake

- run the failing scenario test first
- normalize that result into `failed_tests`, `failure_summary`, `failure_output`, and likely repo-local modules
- place that normalized failure record at the top of `context.txt`

### Phase 2: Repo Delta Enrichment

- inspect recent changed Python files from the working tree or previous commit
- keep only delta that overlaps the failing test context
- attach that bounded signal under `# RECENT_REPO_DELTA` and `# RECENT_REPO_DELTA_DIFF`

### Phase 3: Structured Scenario Memory

- load `test_scenarios/`
- attach high-confidence matches automatically under `# SCENARIO_MATCH` and `# TEST_SCENARIO_RECORD`
- attach medium-confidence candidates cautiously under `# SCENARIO_CANDIDATE`

### Phase 4: Clarification Gate

- if the failure cannot be classified confidently enough, stop before backend generation
- write `output/<scenario>/clarification_request.json`
- use that artifact to review the targeted contract questions before retrying repair generation

### Phase 5: Reviewable Scenario Write-Back

- when the failure looks new or only partially classified, auto-draft `output/<scenario>/scenario_proposal.json`
- do not persist that proposal silently
- approve it explicitly with:

```bash
python3 ci_loop.py approve-scenario-proposal --scenario scenario_1_integration_bug
```

## Core Commands

List scenarios:

```bash
python3 ci_loop.py list-scenarios
```

Check the intentionally failing baseline:

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_4_low_confidence
```

Run one scenario:

```bash
python3 ci_loop.py run --scenario scenario_1_integration_bug
```

Demo-safe run (revert accepted fixes after validation):

```bash
python3 ci_loop.py run --scenario scenario_1_integration_bug --dryRun
```

Run the full sweep:

```bash
python3 ci_loop.py run-all --max-retries 2
```

Demo-safe full sweep:

```bash
python3 ci_loop.py run-all --max-retries 2 --dryRun
```

Note: `run-all` executes only gating scenarios (1 to 3). `scenario_4_low_confidence` is excluded on purpose so pre-commit and CI gate flows stay green while still allowing a dedicated low-confidence demo.

Run all scenarios including scenario 4 (default fail-closed clarification policy):

```bash
python3 ci_loop.py run-all --include-non-gating --max-retries 2 --dryRun
```

Run all scenarios including scenario 4 with interactive clarification:

```bash
python3 ci_loop.py run-all --include-non-gating --clarification-policy interactive --max-retries 2 --dryRun
```

Run scenario 4 with forced heuristic clarification options:

```bash
python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --clarifier-option-source heuristic --max-retries 1 --dryRun
```

Clarification policy behavior:

- `fail` (default): writes clarification artifacts and exits before low-confidence generation
- `interactive`: prompts the operator with explicit clarification questions plus recommended options, supports `edit`/`e` rounds for answer refinement, accepts `yes`/`y` to continue, records the interactive trace in `clarification_dialog.json`, then proceeds using the resolved answers as runtime context. This works in a real terminal and also with piped stdin for scripted demos.
- The runtime log now prints concise mode banners and does not print the full clarifier prompt template.

Clarifier option source behavior (interactive mode only):

- `--clarifier-option-source backend` (default): options come from the configured backend clarifier
- `--clarifier-option-source heuristic`: backend clarifier calls are bypassed and deterministic heuristic options are used

Run explicitly with the local development backend:

```bash
python3 ci_loop.py run-all --max-retries 2
```

Run explicitly with the remote CI backend:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2
```

If you want to force the local path explicitly, you can still pass:

```bash
python3 ci_loop.py run-all --backend codex --max-retries 2
```

Generate context and any clarification artifacts without attempting a repair:

```bash
python3 ci_loop.py build-context --scenario scenario_2_wrong_fix_path
python3 ci_loop.py plan-clarification --scenario scenario_2_wrong_fix_path
python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence
```

Low-confidence artifact demo:

```bash
cat output/scenario_4_low_confidence/clarification_request.json | jq .
cat output/scenario_4_low_confidence/scenario_proposal.json | jq .
# interactive mode only:
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

Approve a reviewed proposal into `test_scenarios/`:

```bash
python3 ci_loop.py approve-scenario-proposal --scenario scenario_2_wrong_fix_path
```

With the default `codex` backend, `run` and `run-all` work without an `OPENAI_API_KEY` when the Codex CLI is available. The backup `openai_responses_api` route still requires a valid `OPENAI_API_KEY` in `.env` or the shell environment, plus network access to the OpenAI Responses API.

## Shared Prompt

The shared repair-review prompt lives in [ci_gatekeeper_reviewer.prompt](ci_gatekeeper_reviewer.prompt).
The shared interactive clarification template lives in [ci_gatekeeper_clarifier.prompt](ci_gatekeeper_clarifier.prompt).

Both backends use it:

- `codex` uses it as the worker prompt passed to `codex exec`
- `openai_responses_api` uses the same prompt body as the repair request sent through the Responses API

That keeps the repair stance in one file instead of duplicating prompt logic across backends.
The clarification template keeps reverse-prompting behavior consistent: recommended options first, explicit alternatives, and free-text fallback.

## Operating Intent

### Local `codex` backend

Use `codex` as the developer-side gate before code leaves the laptop. The intended pattern is:

1. A developer changes code locally.
2. A local hook or manual command runs `python3 ci_loop.py run-all --backend codex --max-retries 2`.
3. Codex proposes a minimal patch and the loop validates it immediately.
4. The developer only proceeds to commit if the generated repair path actually validates.

This is the right backend for a pre-commit or pre-push style workflow because it keeps the loop close to the developer, produces a readable `response.md` artifact for local inspection, and now has a tracked Git hook install path in this repo.

### Remote `openai_responses_api` backend

The intended deployment model for `openai_responses_api` is a CI job such as Jenkins. A typical productionized flow would be:

1. A commit lands on the remote repository.
2. Jenkins or another CI system triggers a build on a UAT branch, prod-tagged branch, or similar protected release path.
3. The build runs `python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2` or a scenario-specific command.
4. The loop generates a candidate fix, validates it, and decides pass or fail.
5. The CI job reports success only if the accepted change satisfies the validation layer.

In a real pipeline, you would usually add lint, static analysis, and security checks alongside the tests already shown here.

## Verification Status

The current repo state has been re-verified on both backends:

- Broken baseline checks fail for all four scenarios, which is the intended demo starting state.
- `python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2` passes end to end with `openai_responses_api`.
- `python3 ci_loop.py run-all --backend codex --max-retries 2` passes end to end with `codex`.

That means both the remote CI path and the local developer path are currently working in this repo.

## What The Loop Produces

Each scenario writes artifacts under `output/<scenario>/`:

- `context.txt`
- backend-specific raw artifact such as `response.json` or `response.md`
- `patch.diff`
- optional `clarification_request.json`
- optional `scenario_proposal.json`
- optional `clarification_dialog.json` (interactive mode only)

### Quick File References (Real Outputs)

Use these concrete files when demoing so people can inspect actual artifacts instead of imagining the flow:

- context snapshot: [output/scenario_4_low_confidence/context.txt](output/scenario_4_low_confidence/context.txt)
- codex raw output: [output/scenario_4_low_confidence/response.md](output/scenario_4_low_confidence/response.md)
- responses API raw output: [output/scenario_4_low_confidence/response.json](output/scenario_4_low_confidence/response.json)
- rendered patch: [output/scenario_4_low_confidence/patch.diff](output/scenario_4_low_confidence/patch.diff)
- low-confidence request artifact: [output/scenario_4_low_confidence/clarification_request.json](output/scenario_4_low_confidence/clarification_request.json)
- recurring-scenario draft artifact: [output/scenario_4_low_confidence/scenario_proposal.json](output/scenario_4_low_confidence/scenario_proposal.json)
- interactive clarification trace: [output/scenario_4_low_confidence/clarification_dialog.json](output/scenario_4_low_confidence/clarification_dialog.json)

### What Each Artifact Means

- `context.txt`: the failure-driven input snapshot sent to the model for that scenario. It includes the normalized failure record, raw failure output, dynamically discovered local code context, bounded recent repo delta when relevant, matched or candidate `test_scenarios/` knowledge when confidence warrants it, optional clarification metadata when the loop is blocked, and the static scenario fallback files.
- `response.json`: the raw OpenAI Responses API output for the `openai_responses_api` backend.
- `response.md`: the raw backend log for the implemented `codex` backend.
- `patch.diff`: the unified diff rendered locally from backend output. Use this to review, apply, or discuss the concrete code change.
- `clarification_request.json`: the confidence-gated question set the operator should review before allowing a low-confidence repair path.
- `scenario_proposal.json`: an auto-drafted `test_scenarios/` candidate that still requires explicit approval before it becomes durable repo knowledge.
- `clarification_dialog.json`: full interactive trace with suggested options, selected inputs, answer revisions, backend source, and response-thread ids (for `openai_responses_api`).
  The Responses API clarifier path chains `previous_response_id` across question turns so option generation retains conversation context.

### How To Use The Artifacts

1. Open `context.txt` to see the exact failure-driven input state.
2. Open the backend-specific raw artifact to inspect the generator output:
   `response.json` for `openai_responses_api`, `response.md` for `codex`.
3. Open `patch.diff` to inspect the exact code change the loop will apply.
4. Run `python3 ci_loop.py apply --scenario <scenario>` to apply `patch.diff`.
5. Run `python3 ci_loop.py test --scenario <scenario>` to validate the patched result.

Short version:

- `context.txt` = failure record + failure output + relevant code input + recent repo delta + matched or candidate scenario knowledge
- `response.json` or `response.md` = raw backend output, depending on the backend mode selecting in run-time
- `patch.diff` = concrete code change derived from that output
- `clarification_request.json` = stop-and-review signal for low-confidence failures
- `scenario_proposal.json` = reviewable recurring-scenario draft, not durable state yet
- `clarification_dialog.json` = detailed Q&A transcript for auditability and debugging

Verified current artifact targets:

- `scenario_1_integration_bug` -> `user_store.py`
- `scenario_2_wrong_fix_path` -> `user_registry.py`
- `scenario_3_refactor_bug` -> `orders.py`

These artifacts are intentionally kept in the repo so you have a fallback demo trail even if the live API call fails on stage.

## Manual Flow

Build context:

```bash
python3 ci_loop.py build-context --scenario scenario_3_refactor_bug
```

Generate and apply automatically:

```bash
python3 ci_loop.py generate-patch --scenario scenario_3_refactor_bug
python3 ci_loop.py apply --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

Or show the remote backup API call directly:

```bash
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-4.1",
    "input": [
      {
        "role": "system",
        "content": "You are a senior engineer. Maintain system invariants."
      },
      {
        "role": "user",
        "content": "Fix the failing test. Return only a diff."
      },
      {
        "role": "user",
        "content": "'"$(cat output/scenario_3_refactor_bug/context.txt | sed 's/"/\\"/g')"'"
      }
    ]
  }' | tee output/scenario_3_refactor_bug/response.json
```

```bash
python3 ci_loop.py extract-patch --scenario scenario_3_refactor_bug
python3 ci_loop.py apply --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

## Repo Summary

The Codex CLI is the default local path. The Responses API call is the backup route for CI or when you explicitly choose `openai_responses_api`.

The model is not the system. The loop is the system.

See [PLAYBOOK.md](PLAYBOOK.md) for the walkthrough of demo and [DEMO-COMMANDS.md](DEMO-COMMANDS.md) for the live sequence.
