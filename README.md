# Codex CI Gatekeeper Demo

This repository shows how to put a coding model inside a controlled CI loop instead of treating generation as the end of the workflow.

The codebase is intentionally left in a buggy baseline state. That is part of the demo. The job of the CI loop is to inspect the broken state, generate a candidate fix, apply the patch, rerun validation, and decide whether the change should be accepted.

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

## Scenario Coverage

All scenario files under `scenarios/` map to runnable demo flows:

- `scenario_1_integration_bug`
  Write/read inconsistency in `app.py`
- `scenario_2_wrong_fix_path`
  Tempting local fix vs systemic fix in `app.py`
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

## What The Loop Produces

Each scenario writes artifacts under `output/<scenario>/`:

- `context.txt`
- `response.json`
- `patch.diff`

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

## Public Repo Notes

- `.env` is ignored and should never be committed.
- The repo is supposed to look broken before the loop runs.
- `output/` is intentionally committed as demo evidence and fallback material.
- Python cache directories are ignored.

## Repo Summary

Codex is not the system.

The loop is the system.

See `PLAYBOOK.md` for the walkthrough and `DEMO-COMMANDS.md` for the live sequence.
