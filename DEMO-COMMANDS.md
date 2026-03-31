# Demo Commands

Run these commands in order during the live demo.

## 0. Prerequisites

- The repo default backend lives in `ci_config.json` and is currently `codex`.
- The `codex` path does not require `OPENAI_API_KEY`.
- The OpenAI backup route does require `OPENAI_API_KEY`.
- The repo default model lives in `ci_config.json` and is currently `gpt-4.1`.
- The local `codex` backend is the default development path and is best explained as a pre-commit or pre-push quality gate.
- The `openai_responses_api` backend is the backup CI path and is best explained as a Jenkins post-commit gate on UAT or prod-tagged branches.
- The baseline is intentionally broken before the run. Failing tests in step 2 are expected.

## 1. List the scenarios

```bash
python3 ci_loop.py list-scenarios
```

Expected:

- Three scenarios are listed.

## 2. Show the intentionally buggy baseline

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

Expected:

- All three commands fail.
- Scenario 1 fails in `user_store.py`.
- Scenario 2 fails in `user_registry.py`.
- Scenario 3 fails in `orders.py`.

## 3. Run the remote CI sweep

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2
```

Expected:

- Each scenario generates `context.txt`, `response.json`, and `patch.diff` under `output/<scenario>/`.
- The generated patches target `user_store.py`, `user_registry.py`, and `orders.py` respectively.
- Each scenario passes validation after the generated patch is applied.
- The repo is restored to the intentionally broken baseline after each accepted run.

## 4. Manually inspect the local default backend on one scenario

```bash
python3 ci_loop.py generate-patch --scenario scenario_1_integration_bug
cat output/scenario_1_integration_bug/context.txt
glow output/scenario_1_integration_bug/response.md || cat output/scenario_1_integration_bug/response.md
glow output/scenario_1_integration_bug/patch.diff || cat output/scenario_1_integration_bug/patch.diff
```

Expected:

- `context.txt` shows the exact prompt input state
- `response.md` shows the raw Codex output
- `patch.diff` shows the concrete repair against `user_store.py`
- No OpenAI API key is needed for this path

## 5. Run the local developer sweep

```bash
python3 ci_loop.py run-all --max-retries 1
```

Expected:

- Each scenario generates `context.txt`, `response.md`, and `patch.diff` under `output/<scenario>/`.
- The generated patches still target `user_store.py`, `user_registry.py`, and `orders.py`.
- Each scenario passes validation after the generated patch is applied.
- This is the clean demo path for "run before commit."

## 6. Manually inspect the OpenAI backup backend on one scenario

```bash
python3 ci_loop.py generate-patch --scenario scenario_1_integration_bug --backend openai_responses_api
cat output/scenario_1_integration_bug/context.txt
cat output/scenario_1_integration_bug/response.json | jq .
glow output/scenario_1_integration_bug/patch.diff || cat output/scenario_1_integration_bug/patch.diff
```

Expected:

- `context.txt` shows the exact prompt input state
- `response.json` shows the raw OpenAI Responses API output
- `patch.diff` shows the concrete repair against `user_store.py`
- This path requires `OPENAI_API_KEY`

## 7. Show the saved artifacts

```bash
cat output/scenario_1_integration_bug/patch.diff
cat output/scenario_2_wrong_fix_path/patch.diff
cat output/scenario_3_refactor_bug/patch.diff
```

Expected:

- Scenario 1 shows a write-path normalization fix in `user_store.py`.
- Scenario 2 shows a storage-key fix in `user_registry.py`.
- Scenario 3 shows the tax-call fix in `orders.py`.
