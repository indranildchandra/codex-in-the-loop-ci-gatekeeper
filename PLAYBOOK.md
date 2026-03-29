# Codex CI Gatekeeper Playbook

This repo is intentionally small. The point is not scale. The point is control.

Each scenario starts with a failing system behavior. The loop builds context, asks the model for a change, renders a patch, validates it, and either accepts or rejects it.

The repository is intentionally kept in a buggy baseline state for the demo. The CI loop is supposed to fix those bugs and rerun the tests to confirm whether the fix actually worked.

This is intended to model a CI deployment such as Jenkins. In a real setup, `ci_loop.py` would be triggered by each remote commit or pull-request build and would act like a post-commit gate that decides whether the build should be marked success, failure, or retry based on validation results.

## Setup

- The demo runner loads `OPENAI_API_KEY` from `.env` automatically.
- A valid OpenAI API key is required for the live generation path.
- Live patch generation also requires network access to the OpenAI Responses API.
- The repo default model is stored in `ci_config.json` and currently set to `gpt-4.1`.
- `OPENAI_MODEL` overrides the repo config for a given environment.
- `--model` overrides both for a specific command invocation.

Model precedence:

1. `--model`
2. `OPENAI_MODEL`
3. `ci_config.json`
4. built-in fallback: `gpt-4.1`

How to change it:

- Edit `ci_config.json` to change the repo default.
- Set `OPENAI_MODEL` in CI if you want an environment-specific override.
- Pass `--model` if you want a one-off override for a single run.

Important architecture note:

Changing the model value does not make this repo run "inside Codex" without OpenAI API dependency. The current implementation still sends HTTP requests to the OpenAI Responses API. To remove that dependency, `ci_loop.py` would need a separate backend implementation.

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
python3 ci_loop.py run-all --max-retries 2
```

What happens:

1. scenario-specific context is built
2. the OpenAI Responses API is called with that context and prompt
3. the model returns structured edits in `response.json`
4. `patch.diff` is rendered locally from those edits
5. the patch is applied with `patch`
6. only the selected scenario test target is validated
7. the change is accepted or the files are restored

Verified current behavior:

- scenario 1 produces a patch for `user_store.py`
- scenario 2 produces a patch for `user_registry.py`
- scenario 3 produces a patch for `orders.py`
- after each accepted run, the repo is restored to the intentionally failing baseline

## Artifact Guide

Each scenario produces three primary files under `output/<scenario>/`:

- `context.txt`: the repo snapshot and test context sent to the model
- `response.json`: the raw Responses API payload returned by OpenAI
- `patch.diff`: the reviewable unified diff rendered locally from `response.json`

How to use them:

1. Read `context.txt` to understand the exact input state.
2. Read `response.json` to inspect the raw API output.
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
- The word "Codex" in the repo name refers to the coding-agent role in the loop; the actual API transport in this implementation is the OpenAI Responses API.
