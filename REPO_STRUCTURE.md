# Repo Structure

```text
codex-in-the-loop-ci-gatekeeper/
├── AGENTS.md
├── DEMO-COMMANDS.md
├── PLAYBOOK.md
├── README.md
├── REPO_STRUCTURE.md
├── ci_gatekeeper_reviewer.prompt
├── ci_gatekeeper_clarifier.prompt
├── install_git_hooks.sh
├── apply_patch.sh
├── .githooks/
│   └── pre-commit
├── user_store.py
├── ci_config.json
├── ci_loop.py
├── context_builder.sh
├── user_registry.py
├── delivery_window.py
├── orders.py
├── pricing.py
├── requirements.txt
├── run_codex.sh
├── output/
├── demo_scenarios/
│   ├── scenario_1_integration_bug.md
│   ├── scenario_2_wrong_fix_path.md
│   ├── scenario_3_refactor_bug.md
│   └── scenario_4_low_confidence.md
├── test_scenarios/
│   ├── refactor_contract_drift_bug.json
│   ├── write_path_canonicalization_bug.json
│   └── write_path_key_helper_bug.json
└── tests/
    ├── test_scenario_1_integration_bug.py
    ├── test_scenario_2_wrong_fix_path.py
    ├── test_scenario_3_refactor_bug.py
    └── test_scenario_4_low_confidence.py
```

## Key Files

- `ci_loop.py`: main CI gatekeeper loop, failure-driven context generation, repo-delta enrichment, scenario-registry lookup, clarification gating, reviewable scenario proposal write-back, patch application, and validation
- `ci_config.json`: repo-local default model configuration
- `ci_gatekeeper_reviewer.prompt`: shared repair-review prompt consumed by both backend paths
- `ci_gatekeeper_clarifier.prompt`: shared interactive-clarification reverse-prompting template for optioned Q&A rounds
- `install_git_hooks.sh`: helper that points Git at the tracked hook directory
- `.githooks/pre-commit`: tracked Git hook that enforces the local Codex gate before commit
- `user_store.py`: scenario 1 buggy baseline
- `user_registry.py`: scenario 2 buggy baseline
- `orders.py` and `pricing.py`: scenario 3 buggy baseline
- `delivery_window.py`: scenario 4 low-confidence clarification baseline
- `output/`: generated failure-driven `context.txt`, optional `clarification_request.json` and `scenario_proposal.json`, backend raw artifacts such as `response.json` or `response.md`, and `patch.diff`
- `demo_scenarios/`: operator-facing demo scenario notes
- `test_scenarios/`: machine-usable recurring scenario registry used for automated scenario matching and explicit proposal approval
- `tests/`: scenario-specific validators used by the loop
