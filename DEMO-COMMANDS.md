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
- Use `--dryRun` for demo runs so accepted fixes are reverted and baseline remains intact between steps.
- Interactive clarification works in a real terminal and also with piped stdin for scripted demos.

## 1. List the scenarios

```bash
python3 ci_loop.py list-scenarios
```

Expected:

- Four scenarios are listed.

## 2. Show the intentionally buggy baseline

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_4_low_confidence
```

Expected:

- All four commands fail.
- Scenario 1 fails in `user_store.py`.
- Scenario 2 fails in `user_registry.py`.
- Scenario 3 fails in `orders.py`.
- Scenario 4 fails in `delivery_window.py`.

## 3. Run the remote CI sweep

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2 --dryRun
```

Expected:

- Each gating scenario generates `context.txt`, `response.json`, and `patch.diff` under `output/<scenario>/`.
- The generated patches target `user_store.py`, `user_registry.py`, and `orders.py` respectively.
- Each failing gating scenario passes validation after the generated patch is applied.
- If a gating scenario is already green, it is skipped without entering clarification.
- The repo is restored to the intentionally broken baseline after each accepted run.
- `scenario_4_low_confidence` is intentionally excluded from `run-all`.

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
python3 ci_loop.py run-all --max-retries 2 --dryRun
```

Expected:

- Each gating scenario generates `context.txt`, `response.md`, and `patch.diff` under `output/<scenario>/`.
- The generated patches still target `user_store.py`, `user_registry.py`, and `orders.py`.
- Each failing gating scenario passes validation after the generated patch is applied.
- If a gating scenario is already green, it is skipped without entering clarification.
- This is the clean demo path for "run before commit."
- `scenario_4_low_confidence` is intentionally excluded from `run-all`.

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

## 8. Demo the low-confidence exception path, using default codex backend

```bash
python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence
cat output/scenario_4_low_confidence/clarification_request.json | jq .
cat output/scenario_4_low_confidence/scenario_proposal.json | jq .
cat output/scenario_4_low_confidence/context.txt
```

Expected:

- The command reports clarification is required.
- `clarification_request.json` is written with targeted contract questions.
- `scenario_proposal.json` is written as a reviewable candidate for `test_scenarios/`.

## 9. Full 4-scenario flow (fail-closed first, then interactive)

Fail-closed behavior (default policy = fail, default backend = codex):

```bash
python3 ci_loop.py run-all --include-non-gating --max-retries 2 --dryRun
```

Expected:

- Scenarios 1 to 3 run as before and pass.
- Scenario 4 halts on clarification and exits fail-closed.
- `clarification_request.json` and `scenario_proposal.json` are written.

Interactive continue behavior:

```bash
python3 ci_loop.py run-all --include-non-gating --clarification-policy interactive --max-retries 2 --dryRun
```

Expected:

- Scenario 4 asks clarification questions in terminal.
- You can run this directly in a terminal, or pipe answers for scripted demos.
- Scenario 4 shows reverse-prompted options (`1/2/3`) for each question; you can pick an option or type a custom answer.
- You can type `edit`/`e` after a round to revise all answers in another pass.
- You can type `yes`/`y` to continue after answering.
- Full clarification trace is saved to `clarification_dialog.json`.
- Scenario 4 then generates and validates a fix in the same sweep.
- You can inspect the captured interactive answers:
  `cat output/scenario_4_low_confidence/clarification_dialog.json | jq .`

Validate the generated patch

```bash
cat output/scenario_4_low_confidence/context.txt
glow output/scenario_4_low_confidence/response.md || cat output/scenario_4_low_confidence/response.md
glow output/scenario_4_low_confidence/patch.diff || cat output/scenario_4_low_confidence/patch.diff
```

## 10. Forced heuristics fallback demo (deterministic)

Use this to show exactly how fallback options behave even when backend connectivity is fine.

```bash
python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --clarifier-option-source heuristic --max-retries 1 --dryRun
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

Expected:

- Interactive questions still appear.
- Option source is forced to heuristic for every question.
- `clarification_dialog.json` shows:
  - `dialog_backend: "heuristic"`
  - each question entry with `"option_source": "heuristic"`
  - empty `response_thread_ids` because backend clarifier calls are bypassed.
