# Scenario: Refactor-Induced Contract Drift

- pricing module changed tax_rate semantics
- orders module still uses old format

Possible Fixes:

1. Adjust calculation logic again (incorrect)
2. Fix caller to match new contract (correct)

Lesson:

- AI often patches locally instead of fixing system boundaries
- Refactors require system-wide consistency
