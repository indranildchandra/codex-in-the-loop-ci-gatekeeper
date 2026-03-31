# Repository Instructions

This repo is a small demo, but treat it like production demo infrastructure. Keep changes deliberate, minimal, and easy to explain live.

## Working Rules

- Plan before editing for any task with more than one obvious step.
- Before writing to `docs/plan.md`, `tasks/todo.md`, `tasks/tracker.md`, `tasks/lessons.md`, or `audit/changelog.md`, load the matching `aidlc-tracking` skill format and follow it exactly.
- Write intent to `docs/plan.md` before implementation starts.
- Keep `tasks/todo.md` current while working.
- After implementation, record the change in `audit/changelog.md` and append a task-complete entry to `tasks/tracker.md`.
- If a user correction exposes a reusable mistake pattern, prepend a lesson to `tasks/lessons.md`.

## Repo Priorities

- Preserve the intentionally buggy baseline unless the task is explicitly to change a scenario.
- Prefer the smallest change that keeps the demo narrative clearer.
- Verify with runnable commands, not inspection alone.
- Keep docs aligned with the actual CLI behavior and file layout.

## Scenario Design Guardrails

- Each scenario must represent a meaningfully distinct debugging or repair task.
- If two scenarios can legitimately produce the same minimal patch, either rename them to make that equivalence explicit or separate them into different modules/tests.
- Keep scenario markdown as operator guidance only; automated model context should come from live code and the scenario-specific test files.
- When adding a scenario, update `ci_loop.py`, the scenario markdown file, the scenario test, and the docs in the same change.

## Code And CLI Conventions

- `ci_loop.py` is the source of truth for scenario wiring, prompts, context files, and validation targets.
- Scenario tests should fail deterministically on baseline and pass after the intended fix.
- Prefer `python3 -m unittest` fallback compatibility when validating because `pytest` may not be installed.
- Saved artifacts under `output/<scenario>/` are commit-safe demo material, but they can become stale after scenario changes and should be regenerated when feasible.

## Editing Boundaries

- Do not silently rewrite the repo to a clean state; the broken state is part of the demo.
- Avoid broad refactors unless they clearly improve the teaching value or reliability of the loop.
- Do not change public CLI names or scenario identifiers unless the task requires it.
