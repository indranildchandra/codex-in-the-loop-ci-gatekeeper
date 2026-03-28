# Demo Commands

Run these commands in order during the live demo.

## 0. Prerequisites

- Ensure `.env` contains a valid `OPENAI_API_KEY`.
- The repo default model lives in `ci_config.json` and is currently `gpt-4.1`.
- The live sweep calls the OpenAI Responses API, so network access must be available.
- This demo is meant to represent a CI gate such as a Jenkins job triggered after a remote commit.
- The baseline is intentionally broken before the run. Failing tests in step 2 are expected.

## 1. List the scenarios

```bash
python3 ci_loop.py list-scenarios
```

Expected:

- Three scenarios are listed.
- `scenario_2_wrong_fix_path` now targets `directory.py`, not `app.py`.

## 2. Show the intentionally buggy baseline

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

Expected:

- All three commands fail.
- Scenario 1 fails in `app.py`.
- Scenario 2 fails in `directory.py`.
- Scenario 3 fails in `orders.py`.

## 3. Run the full live sweep

```bash
python3 ci_loop.py run-all --max-retries 2
```

Expected:

- Each scenario generates `context.txt`, `response.json`, and `patch.diff` under `output/<scenario>/`.
- The generated patches target `app.py`, `directory.py`, and `orders.py` respectively.
- Each scenario passes validation after the generated patch is applied.
- The repo is restored to the intentionally broken baseline after each accepted run.

## 4. Show the saved artifacts

```bash
cat output/scenario_1_integration_bug/patch.diff
cat output/scenario_2_wrong_fix_path/patch.diff
cat output/scenario_3_refactor_bug/patch.diff
```

Expected:

- Scenario 1 shows a write-path normalization fix in `app.py`.
- Scenario 2 shows a storage-key fix in `directory.py`.
- Scenario 3 shows the tax-call fix in `orders.py`.

## 5. Offline fallback if network fails

```bash
cat output/scenario_1_integration_bug/context.txt
cat output/scenario_1_integration_bug/response.json
cat output/scenario_2_wrong_fix_path/context.txt
cat output/scenario_2_wrong_fix_path/response.json
cat output/scenario_3_refactor_bug/context.txt
cat output/scenario_3_refactor_bug/response.json
```

Expected:

- The saved artifacts let you walk through the previous successful run without making a live API call.
- If you changed scenario code or prompts recently, regenerate the artifacts before the demo so they stay current.
