# Repo Structure

```text
codex-in-the-loop-ci-gatekeeper/
├── AGENTS.md
├── DEMO-COMMANDS.md
├── PLAYBOOK.md
├── README.md
├── REPO_STRUCTURE.md
├── apply_patch.sh
├── app.py
├── ci_config.json
├── ci_loop.py
├── context_builder.sh
├── directory.py
├── orders.py
├── pricing.py
├── requirements.txt
├── run_codex.sh
├── audit/
├── docs/
├── output/
├── scenarios/
│   ├── scenario_1_integration_bug.md
│   ├── scenario_2_wrong_fix_path.md
│   └── scenario_3_refactor_bug.md
├── tasks/
└── tests/
    ├── test_scenario_1_integration_bug.py
    ├── test_scenario_2_wrong_fix_path.py
    └── test_scenario_3_refactor_bug.py
```

## Key Files

- `ci_loop.py`: main CI gatekeeper loop, artifact generation, patch application, and validation
- `ci_config.json`: repo-local default model configuration
- `app.py`: scenario 1 buggy baseline
- `directory.py`: scenario 2 buggy baseline
- `orders.py` and `pricing.py`: scenario 3 buggy baseline
- `output/`: generated `context.txt`, `response.json`, and `patch.diff` artifacts
- `scenarios/`: operator-facing scenario notes
- `tests/`: scenario-specific validators used by the loop
