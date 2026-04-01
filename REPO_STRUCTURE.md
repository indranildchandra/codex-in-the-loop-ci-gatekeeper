# Repo Structure

```text
codex-in-the-loop-ci-gatekeeper/
├── AGENTS.md
├── DEMO-COMMANDS.md
├── PLAYBOOK.md
├── README.md
├── REPO_STRUCTURE.md
├── code_review.prompt
├── install_git_hooks.sh
├── apply_patch.sh
├── .githooks/
│   └── pre-commit
├── user_store.py
├── ci_config.json
├── ci_loop.py
├── context_builder.sh
├── user_registry.py
├── orders.py
├── pricing.py
├── requirements.txt
├── run_codex.sh
├── output/
├── scenarios/
│   ├── scenario_1_integration_bug.md
│   ├── scenario_2_wrong_fix_path.md
│   └── scenario_3_refactor_bug.md
└── tests/
    ├── test_scenario_1_integration_bug.py
    ├── test_scenario_2_wrong_fix_path.py
    └── test_scenario_3_refactor_bug.py
```

## Key Files

- `ci_loop.py`: main CI gatekeeper loop, artifact generation, patch application, and validation
- `ci_config.json`: repo-local default model configuration
- `code_review.prompt`: shared repair-review prompt consumed by both backend paths
- `install_git_hooks.sh`: helper that points Git at the tracked hook directory
- `.githooks/pre-commit`: tracked Git hook that enforces the local Codex gate before commit
- `user_store.py`: scenario 1 buggy baseline
- `user_registry.py`: scenario 2 buggy baseline
- `orders.py` and `pricing.py`: scenario 3 buggy baseline
- `output/`: generated `context.txt`, backend raw artifacts such as `response.json` or `response.md`, and `patch.diff`
- `scenarios/`: operator-facing scenario notes
- `tests/`: scenario-specific validators used by the loop
