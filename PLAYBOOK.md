# Codex CI Gatekeeper Playbook

This repo is intentionally small. The point is not scale. The point is control.

Each scenario starts with a failing system behavior. The loop builds context, asks the model for a change, renders a patch, validates it, and either accepts or rejects it.

The repository is intentionally kept in a buggy baseline state for the demo. The CI loop is supposed to fix those bugs and rerun the tests to confirm whether the fix actually worked.

## Setup

The demo runner loads `OPENAI_API_KEY` from `.env` automatically.

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
- scenario 2 fails because keys are not normalized on write
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
2. the model is asked for structured edits
3. `patch.diff` is rendered locally
4. the patch is applied with `patch`
5. only the selected scenario test target is validated
6. the change is accepted or the files are restored

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
- Scenario 2 is intentionally separate from scenario 1 even though it touches the same code, because it demonstrates a different validator and a different failure mode.
- Scenario 3 is now live in code, not just documented in a markdown note.
- The automated path is more reliable because it asks for structured edits and renders diffs locally.
