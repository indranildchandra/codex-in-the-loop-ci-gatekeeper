# Demo Commands

Run these commands in order during the live demo.

## 1. List the scenarios

```bash
python3 ci_loop.py list-scenarios
```

## 2. Show the intentionally buggy baseline

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

## 3. Run the full live sweep

```bash
python3 ci_loop.py run-all --max-retries 2
```

## 4. Show the saved artifacts

```bash
cat output/scenario_1_integration_bug/patch.diff
cat output/scenario_2_wrong_fix_path/patch.diff
cat output/scenario_3_refactor_bug/patch.diff
```

## 5. Offline fallback if network fails

```bash
cat output/scenario_1_integration_bug/context.txt
cat output/scenario_1_integration_bug/response.json
cat output/scenario_2_wrong_fix_path/context.txt
cat output/scenario_2_wrong_fix_path/response.json
cat output/scenario_3_refactor_bug/context.txt
cat output/scenario_3_refactor_bug/response.json
```
