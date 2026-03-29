# Codex-in-the-loop CI Gatekeeper Demo

This repository shows how to put a coding model inside a controlled CI loop instead of treating generation as the end of the workflow.

The codebase is intentionally left in a buggy baseline state. That is part of the demo. The job of the CI loop is to inspect the broken state, build a constrained context snapshot, call the OpenAI Responses API for a candidate change, render a patch, rerun validation, and decide whether the change should be accepted.

This is intended to be deployed into CI infrastructure such as Jenkins. In a real deployment, `ci_loop.py` would be triggered on each remote commit or pull-request build and would act as a post-commit gate for the build result: if the generated fix validates cleanly, the build can proceed; if it fails validation, the build is marked failed or retried according to pipeline policy.

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

### Codex CI Gatekeeper Architecture

```text
User / CI Trigger
        ↓
Build Context (repo snapshot)
        ↓
Codex (diff generation)
        ↓
Patch Output (diff)
        ↓
Validation Layer
    - Tests
    - Lint
    - Static checks
        ↓
Decision Layer
    - Accept
    - Reject
    - Retry
        ↓
Merge / Apply
```

In this repository, the transport layer is the OpenAI Responses API. The model selection now comes from repo config first and can be overridden by environment variables. The flow is:

- `ci_loop.py` builds `context.txt`
- `ci_loop.py` sends that context and prompt to `https://api.openai.com/v1/responses`
- the model returns structured edit data in `response.json`
- the repo renders those edits into `patch.diff`
- the patch is applied and validated locally

If you want this demo to use a specific Codex-capable model, set `OPENAI_MODEL` accordingly. The Responses API is the mechanism. The configured model behind that API is the reasoning engine.

## Model Configuration

The default model is stored in [ci_config.json](ci_config.json):

```json
{
  "openai_model": "gpt-4.1"
}
```

Model resolution order is:

1. `--model` CLI flag
2. `OPENAI_MODEL` environment variable
3. `ci_config.json`
4. built-in fallback: `gpt-4.1`

How to change it:

- Edit `ci_config.json` if you want to change the repo default.
- Set `OPENAI_MODEL=...` if you want a per-environment override.
- Pass `--model ...` if you want a one-off run override.

Important limit:

Changing the model value does not remove the OpenAI API dependency. This implementation still calls `https://api.openai.com/v1/responses`, so it still requires network access and an API key. To run without that dependency, you would need a different backend path in `ci_loop.py`, such as a Codex CLI integration or another local/provider-specific execution layer.

## Scenario Coverage

All scenario files under `scenarios/` map to runnable demo flows:

- `scenario_1_integration_bug`
  Write/read inconsistency in `user_store.py`
- `scenario_2_wrong_fix_path`
  Tempting local fix vs systemic fix in `user_registry.py`
- `scenario_3_refactor_bug`
  Contract drift between `orders.py` and `pricing.py`

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
```

Run one scenario:

```bash
python3 ci_loop.py run --scenario scenario_1_integration_bug
```

Run the full sweep:

```bash
python3 ci_loop.py run-all --max-retries 2
```

`run` and `run-all` require a valid `OPENAI_API_KEY` in `.env` or the shell environment, plus network access to the OpenAI Responses API. Without that key, the live generation part of the demo will not work.

## CI Deployment Intent

The intended deployment model is a CI job, not an interactive local script only. A typical productionized flow would be:

1. A commit lands on the remote repository.
2. Jenkins or another CI system triggers a build.
3. The build runs `python3 ci_loop.py run-all --max-retries 2` or a scenario-specific command.
4. The loop generates a candidate fix, validates it, and decides pass or fail.
5. The CI job reports success only if the accepted change satisfies the validation layer.

In a real pipeline, you would usually add lint, static analysis, and security checks alongside the tests already shown here.

## What The Loop Produces

Each scenario writes artifacts under `output/<scenario>/`:

- `context.txt`
- `response.json`
- `patch.diff`

### What Each Artifact Means

- `context.txt`: the exact source-and-test snapshot sent to the model for that scenario. Use this to inspect what the model saw.
- `response.json`: the raw OpenAI Responses API output. Use this to inspect the structured edits and API response payload.
- `patch.diff`: the unified diff rendered locally from the structured edits in `response.json`. Use this to review, apply, or discuss the concrete code change.

### How To Use The Artifacts

1. Open `context.txt` to see the exact input state.
2. Open `response.json` to inspect the raw model output from the API.
3. Open `patch.diff` to inspect the exact code change the loop will apply.
4. Run `python3 ci_loop.py apply --scenario <scenario>` to apply `patch.diff`.
5. Run `python3 ci_loop.py test --scenario <scenario>` to validate the patched result.

Short version:

- `context.txt` = input
- `response.json` = raw model/API output
- `patch.diff` = concrete code change derived from that output

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

Or show the lower-level API call directly:

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

python3 ci_loop.py extract-patch --scenario scenario_3_refactor_bug
python3 ci_loop.py apply --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

## Repo Summary

The Responses API call is how this repo talks to the model.

The model is not the system.

The loop is the system.

See `PLAYBOOK.md` for the walkthrough and `DEMO-COMMANDS.md` for the live sequence.
