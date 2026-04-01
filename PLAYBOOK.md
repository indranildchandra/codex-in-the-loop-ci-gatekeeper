# Codex CI Gatekeeper Playbook

This repo is intentionally small. The point is not scale. The point is control.

Each scenario starts with a failing system behavior. The loop builds context, asks the model for a change, renders a patch, validates it, and either accepts or rejects it.

The repository is intentionally kept in a buggy baseline state for the demo. The CI loop is supposed to fix those bugs and rerun the tests to confirm whether the fix actually worked.

This repo supports two intended operating modes:

- `codex` is the default local development path, where the loop acts like a pre-commit or pre-push quality gate
- `openai_responses_api` is the backup remote CI path, where Jenkins or a similar system runs the loop after commits on UAT or prod-tagged branches

In both modes, `ci_loop.py` is the gatekeeper that decides whether the generated patch is acceptable.

## Core Principles

- `zero-trust`: do not rely on engineers remembering to run a review sub-agent before pushing code. If the guard is optional, it will eventually be skipped.
- `tooling-enforced local review`: the repo now includes a tracked Git pre-commit hook path that runs the local Codex backend before the commit completes.
- `model council`: using a non-Anthropic-family reviewer against Anthropic-family generated code is a deliberate design choice to reduce inherent model bias and broaden what gets caught.

## Setup

- The demo runner loads `OPENAI_API_KEY` from `.env` automatically for the backup route.
- The default backend is stored in `ci_config.json` and currently set to `codex`.
- The `codex` path does not require an OpenAI API key.
- Live patch generation on the backup route requires network access to the OpenAI Responses API and a valid OpenAI API key.
- The repo default model is stored in `ci_config.json` and currently set to `gpt-4.1`.
- `CI_LOOP_BACKEND` overrides the repo config for a given environment.
- `OPENAI_MODEL` overrides the repo config for a given environment.
- `--backend` overrides the backend for a specific command invocation.
- `--model` overrides both for a specific command invocation.

Backend precedence:

1. `--backend`
2. `CI_LOOP_BACKEND`
3. `ci_config.json`
4. built-in fallback: `codex`

Model precedence:

1. `--model`
2. `OPENAI_MODEL`
3. `ci_config.json`
4. built-in fallback: `gpt-4.1`

How to change it:

- Edit `ci_config.json` to change the repo default backend or model.
- Set `CI_LOOP_BACKEND` in CI if you want an environment-specific backend override.
- Set `OPENAI_MODEL` in CI if you want an environment-specific override.
- Pass `--model` if you want a one-off override for a single run.

Important architecture note:

The default path now runs through `codex`, so the repo can be used without any OpenAI API key dependency when you stay on the local backend. The backup path still sends HTTP requests to the OpenAI Responses API.

Backend runtime requirements:

- `codex`: requires a working authenticated Codex CLI session plus available Codex usage quota
- `openai_responses_api`: requires `OPENAI_API_KEY` and network access

Install the local Git hook:

```bash
./install_git_hooks.sh
```

That sets `core.hooksPath=.githooks` and enables the tracked pre-commit hook at `.githooks/pre-commit`.

Hook control:

- repo-level switch: `ci_config.json` -> `git_hooks.pre_commit_enabled`
- one-off local bypass: `SKIP_CI_GATEKEEPER_PRE_COMMIT=1`

## Operating Modes

### Local development mode: `codex`

Use this backend when you want the loop to run close to the developer before code is committed.

```text
Local code change
        ↓
Pre-commit or pre-push style trigger
        ↓
ci_loop.py run-all --backend codex
        ↓
context.txt
        ↓
codex exec
        ↓
response.md
        ↓
patch.diff
        ↓
tests / local checks
        ↓
accept or reject before commit
```

Recommended command:

```bash
python3 ci_loop.py run-all --max-retries 1
```

Tracked hook:

```bash
.githooks/pre-commit
```

Shared review prompt:

```bash
code_review.prompt
```

### Remote CI mode: `openai_responses_api`

Use this backend when the loop is running as a deployment gate in Jenkins or another remote pipeline.

```text
Remote commit on UAT/prod-tagged branch
        ↓
Jenkins post-commit build
        ↓
ci_loop.py run-all --backend openai_responses_api
        ↓
context.txt
        ↓
OpenAI Responses API
        ↓
response.json
        ↓
patch.diff
        ↓
tests / lint / static checks
        ↓
mark build success, failure, or retry
```

Recommended command:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2
```

## List Scenarios

```bash
python3 ci_loop.py list-scenarios
```

Current scenarios:

- `scenario_1_integration_bug`
- `scenario_2_wrong_fix_path`
- `scenario_3_refactor_bug`

## Baseline Validation

Run each scenario directly:

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

Expected results:

- scenario 1 fails because the write path and read path disagree
- scenario 2 fails because `user_registry.py` computes the storage key incorrectly on write
- scenario 3 fails because the pricing contract changed and the caller did not

Those failures are expected. Do not "clean up" the repo before the demo; the broken baseline is the input to the loop.

## Automated Demo

Run one scenario end-to-end:

```bash
python3 ci_loop.py run --scenario scenario_1_integration_bug
```

Run the full scenario sweep:

```bash
python3 ci_loop.py run-all --max-retries 1
```

Run the local developer path explicitly:

```bash
python3 ci_loop.py run-all --backend codex --max-retries 1
```

Run the remote CI path explicitly:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2
```

What happens:

1. scenario-specific context is built
2. the configured backend is called with that context and prompt
3. the backend writes a raw artifact such as `response.json` or `response.md`
4. `patch.diff` is rendered locally from the backend output
5. the patch is applied with `patch`
6. only the selected scenario test target is validated
7. the change is accepted or the files are restored

Verified current behavior:

- scenario 1 produces a patch for `user_store.py`
- scenario 2 produces a patch for `user_registry.py`
- scenario 3 produces a patch for `orders.py`
- after each accepted run, the repo is restored to the intentionally failing baseline
- the full sweep passes on both `codex` and `openai_responses_api`

## Artifact Guide

Each scenario produces stable input/output artifacts under `output/<scenario>/`:

- `context.txt`: the repo snapshot and test context sent to the model
- `response.json`: the raw Responses API payload returned by OpenAI for the `openai_responses_api` backend
- `response.md`: the raw backend log for the `codex` backend
- `patch.diff`: the reviewable unified diff rendered locally from backend output

How to use them:

1. Read `context.txt` to understand the exact input state.
2. Read the backend-specific raw artifact to inspect generator output.
3. Read `patch.diff` to inspect the concrete code change.
4. Apply and validate the patch with the CLI commands if you want to replay the flow locally.

## Build Artifacts

Artifacts are persisted under scenario-specific output directories:

- `output/scenario_1_integration_bug/`
- `output/scenario_2_wrong_fix_path/`
- `output/scenario_3_refactor_bug/`

These output artifacts are meant to be commit-safe fallback material for live demos, so you can still show the flow if connectivity fails.

Build context only:

```bash
python3 ci_loop.py build-context --scenario scenario_2_wrong_fix_path
```

Generate a patch without applying it:

```bash
python3 ci_loop.py generate-patch --scenario scenario_2_wrong_fix_path
```

Apply and validate later:

```bash
python3 ci_loop.py apply --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
```

## Manual Low-Level Path

If you want to show the raw Responses API call, build the scenario context first:

```bash
python3 ci_loop.py build-context --scenario scenario_3_refactor_bug
```

Then call the API manually:

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

Then extract and apply:

```bash
python3 ci_loop.py extract-patch --scenario scenario_3_refactor_bug
python3 ci_loop.py apply --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

## Design Notes

- Scenario markdown files are not part of the default automated context.
- Scenario 2 is intentionally separate from scenario 1 and now targets `user_registry.py`, so it demonstrates the same architectural lesson through a distinct code change.
- Scenario 3 is now live in code, not just documented in a markdown note.
- The automated path is more reliable because it asks for structured edits and renders diffs locally.
- The repo now supports two generation transports: OpenAI Responses API for remote CI and a Codex CLI worker path for local developer-time validation.
