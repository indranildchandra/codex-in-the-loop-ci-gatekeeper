# Scenario: Low-Confidence Clarification Gate

- This scenario intentionally does not map to the seeded `test_scenarios/` records.
- Its purpose is to demonstrate the confidence gate behavior.

Expected Behavior:

1. `plan-clarification` writes `clarification_request.json`.
2. `plan-clarification` also writes `scenario_proposal.json`.
3. The loop should stop before backend generation until the operator reviews artifacts.
