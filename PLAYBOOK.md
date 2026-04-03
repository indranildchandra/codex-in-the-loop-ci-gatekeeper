# Codex CI Gatekeeper Playbook

This repo is intentionally small. The point is not scale. The point is control.

Each scenario starts with a failing system behavior. The loop builds failure-driven context, asks the model for a change, renders a patch, validates it, and either accepts or rejects it.

The repository is intentionally kept in a buggy baseline state for the demo. The CI loop is supposed to fix those bugs and rerun the tests to confirm whether the fix actually worked.

This repo supports two intended operating modes:

- `codex` is the default local development path, where the loop acts like a pre-commit or pre-push quality gate
- `openai_responses_api` is the backup remote CI path, where Jenkins or a similar system runs the loop after commits on UAT or prod-tagged branches

In both modes, `ci_loop.py` is the gatekeeper that decides whether the generated patch is acceptable.

## Explicit Assumption

This gate is failure-driven and depends on pre-existing tests.

- The intended deployment target is a repo that already follows a test-first discipline for important behaviors.
- Functional/integration scenario tests are the primary acceptance signal for this workflow.
- Unit tests are additive but generally not sufficient as the only gate signal for production-like confidence.
- If no relevant tests exist, the loop can still propose patches, but the accept/reject decision becomes materially less reliable.
- This workflow is not a generic code-review system. It is a code-fix loop for buggy implementations identified by failing pre-defined tests.
- In repos where tests are not defined before implementation, this loop is significantly less useful.

## Core Principles

- `zero-trust`: do not rely on engineers remembering to run a review sub-agent before pushing code. If the guard is optional, it will eventually be skipped.
- `tooling-enforced local review`: the repo now includes a tracked Git pre-commit hook path that runs the local Codex backend before the commit completes.
- `model council`: using a non-Anthropic-family reviewer against Anthropic-family generated code is a deliberate design choice to reduce inherent model bias and broaden what gets caught.

## Setup

- The demo runner loads `OPENAI_API_KEY` from `.env` automatically for the backup route.
- The default backend is stored in `ci_config.json` and currently set to `codex`.
- The `codex` path does not require an OpenAI API key.
- Live patch generation on the backup route requires network access to the OpenAI Responses API and a valid OpenAI API key.
- The repo default model is stored in `ci_config.json` and currently set to `gpt-4.1`.
- `CI_LOOP_BACKEND` overrides the repo config for a given environment.
- `OPENAI_MODEL` overrides the repo config for a given environment.
- `--backend` overrides the backend for a specific command invocation.
- `--model` overrides both for a specific command invocation.

Backend precedence:

1. `--backend`
2. `CI_LOOP_BACKEND`
3. `ci_config.json`
4. built-in fallback: `codex`

Model precedence:

1. `--model`
2. `OPENAI_MODEL`
3. `ci_config.json`
4. built-in fallback: `gpt-4.1`

How to change it:

- Edit `ci_config.json` to change the repo default backend or model.
- Set `CI_LOOP_BACKEND` in CI if you want an environment-specific backend override.
- Set `OPENAI_MODEL` in CI if you want an environment-specific override.
- Pass `--model` if you want a one-off override for a single run.

Important architecture note:

The default path now runs through `codex`, so the repo can be used without any OpenAI API key dependency when you stay on the local backend. The backup path still sends HTTP requests to the OpenAI Responses API.

Backend runtime requirements:

- `codex`: requires a working authenticated Codex CLI session plus available Codex usage quota
- `openai_responses_api`: requires `OPENAI_API_KEY` and network access

Install the local Git hook:

```bash
./install_git_hooks.sh
```

That sets `core.hooksPath=.githooks` and enables the tracked pre-commit hook at `.githooks/pre-commit`.

Hook control:

- repo-level switch: `ci_config.json` -> `git_hooks.pre_commit_enabled`
- one-off local bypass: `SKIP_CI_GATEKEEPER_PRE_COMMIT=1`

## Operational Phases

The runtime now implements five explicit phases.

### Phase 1: Failure Intake

- run the failing scenario test
- normalize the failure into a structured record
- build `context.txt` from observed failure instead of demo notes

### Phase 2: Repo Delta Enrichment

- inspect recent changed Python files from git
- keep only delta that overlaps the current failure context
- add bounded repo-delta sections to `context.txt`

### Phase 3: Scenario Registry Lookup

- load `test_scenarios/`
- auto-attach high-confidence records
- attach medium-confidence candidates cautiously instead of treating them as facts

### Phase 4: Clarification Gate

- if no record matches confidently enough, stop before backend generation
- write `clarification_request.json`
- require operator review before proceeding with a low-confidence repair

### Phase 5: Reviewable Scenario Write-Back

- auto-draft `scenario_proposal.json` when the failure is new or only partially classified
- persist nothing automatically
- write to `test_scenarios/` only through explicit approval

## Operating Modes

### Local development mode: `codex`

Use this backend when you want the loop to run close to the developer before code is committed.

```text
Local code change
        ↓
Pre-commit or pre-push style trigger
        ↓
ci_loop.py run-all --backend codex
        ↓
context.txt
        ↓
codex exec
        ↓
response.md
        ↓
patch.diff
        ↓
tests / local checks
        ↓
accept or reject before commit
```

Recommended command:

```bash
python3 ci_loop.py run-all --max-retries 2
```

Tracked hook:

```bash
.githooks/pre-commit
```

Shared review prompt:

```bash
ci_gatekeeper_reviewer.prompt
```

Shared clarification Q&A prompt:

```bash
ci_gatekeeper_clarifier.prompt
```

### Remote CI mode: `openai_responses_api`

Use this backend when the loop is running as a deployment gate in Jenkins or another remote pipeline.

```text
Remote commit on UAT/prod-tagged branch
        ↓
Jenkins post-commit build
        ↓
ci_loop.py run-all --backend openai_responses_api
        ↓
context.txt
        ↓
OpenAI Responses API
        ↓
response.json
        ↓
patch.diff
        ↓
tests / lint / static checks
        ↓
mark build success, failure, or retry
```

Recommended command:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2
```

## List Scenarios

```bash
python3 ci_loop.py list-scenarios
```

Current scenarios:

- `scenario_1_integration_bug`
- `scenario_2_wrong_fix_path`
- `scenario_3_refactor_bug`
- `scenario_4_low_confidence` (clarification/proposal artifact demo; excluded from `run-all`)

## Baseline Validation

Run each scenario directly:

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_4_low_confidence
```

Expected results:

- scenario 1 fails because the write path and read path disagree
- scenario 2 fails because `user_registry.py` computes the storage key incorrectly on write
- scenario 3 fails because the pricing contract changed and the caller did not

Those failures are expected. Do not "clean up" the repo before the demo; the broken baseline is the input to the loop.

## Automated Demo

Run one scenario end-to-end:

```bash
python3 ci_loop.py run --scenario scenario_1_integration_bug --dryRun
```

Run the full scenario sweep:

```bash
python3 ci_loop.py run-all --max-retries 2 --dryRun
```

`run-all` executes only gating scenarios (`scenario_1` to `scenario_3`). `scenario_4_low_confidence` is intentionally excluded so the commit gate remains deterministic while still allowing explicit low-confidence workflow demos.

Run the local developer path explicitly:

```bash
python3 ci_loop.py run-all --backend codex --max-retries 2 --dryRun
```

Run the remote CI path explicitly:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2 --dryRun
```

What happens:

1. scenario-specific failure-driven context is built from the failing test, failure output, local code dependencies, recent repo delta when relevant, and matched or candidate `test_scenarios/` knowledge based on confidence
2. the configured backend is called with that context and prompt
3. the backend writes a raw artifact such as `response.json` or `response.md`
4. `patch.diff` is rendered locally from the backend output
5. the patch is applied with `patch`
6. only the selected scenario test target is validated
7. the change is accepted (or restored when `--dryRun` is set)

Low-confidence exception:

- if clarification is required, the loop stops before step 2
- it writes `clarification_request.json`
- it can also draft `scenario_proposal.json`
- in `--clarification-policy interactive`, the operator interaction is captured in `clarification_dialog.json` and resolved answers are injected as runtime context before generation. This works in a real terminal and with piped stdin for scripted demos.
- interactive confirmation supports `yes`/`y` to continue and `edit`/`e` to revise answers.
- runtime logs are concise and do not print the full clarifier prompt template.
- in interactive mode, `--clarifier-option-source backend|heuristic` controls whether options are generated by backend clarifiers or deterministic heuristics
- the operator decides whether to clarify intent or approve a new recurring scenario record

Verified current behavior:

- scenario 1 produces a patch for `user_store.py`
- scenario 2 produces a patch for `user_registry.py`
- scenario 3 produces a patch for `orders.py`
- scenario 4 writes both `clarification_request.json` and `scenario_proposal.json` via `plan-clarification`
- with `--dryRun`, accepted runs restore the intentionally failing baseline
- without `--dryRun`, accepted runs remain in the working tree
- the full sweep passes on both `codex` and `openai_responses_api`
- structured recurring scenario knowledge comes from `test_scenarios/`, not `demo_scenarios/`

## Artifact Guide

Each scenario produces stable input/output artifacts under `output/<scenario>/`:

- `context.txt`: the normalized failure record, raw failure output, dynamically discovered local code context, bounded recent repo delta when relevant, matched or candidate `test_scenarios/` knowledge when confidence warrants it, optional clarification metadata when the run is blocked, and static scenario fallback files sent to the model
- `response.json`: the raw Responses API payload returned by OpenAI for the `openai_responses_api` backend
- `response.md`: the raw backend log for the `codex` backend
- `patch.diff`: the reviewable unified diff rendered locally from backend output
- `clarification_request.json`: the confidence-gated question set emitted when the loop should stop before generation
- `scenario_proposal.json`: an auto-drafted recurring scenario record that still requires explicit approval
- `clarification_dialog.json`: full interactive trace including suggested options, selected inputs, answer revisions, backend source, and any response-thread ids
  For `openai_responses_api`, the clarifier path threads `previous_response_id` across question turns to preserve conversation context.

How to use them:

1. Read `context.txt` to understand the exact input state.
2. Read the backend-specific raw artifact to inspect generator output.
3. Read `patch.diff` to inspect the concrete code change.
4. Apply and validate the patch with the CLI commands if you want to replay the flow locally.

## Build Artifacts

Artifacts are persisted under scenario-specific output directories:

- `output/scenario_1_integration_bug/`
- `output/scenario_2_wrong_fix_path/`
- `output/scenario_3_refactor_bug/`
- `output/scenario_4_low_confidence/`

These output artifacts are meant to be commit-safe fallback material for live demos, so you can still show the flow if connectivity fails.

Build context only:

```bash
python3 ci_loop.py build-context --scenario scenario_2_wrong_fix_path
```

Generate clarification and proposal artifacts only:

```bash
python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence
cat output/scenario_4_low_confidence/clarification_request.json | jq .
cat output/scenario_4_low_confidence/scenario_proposal.json | jq .
```

Interactive clarification demo:

```bash
python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --max-retries 1 --dryRun
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

Forced heuristic fallback demo:

```bash
python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --clarifier-option-source heuristic --max-retries 1 --dryRun
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

Approve a reviewed proposal into `test_scenarios/`:

```bash
python3 ci_loop.py approve-scenario-proposal --scenario scenario_2_wrong_fix_path
```

Generate a patch without applying it:

```bash
python3 ci_loop.py generate-patch --scenario scenario_2_wrong_fix_path
```

Apply and validate later:

```bash
python3 ci_loop.py apply --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
```

## Manual Low-Level Path

If you want to show the raw Responses API call, build the scenario context first:

```bash
python3 ci_loop.py build-context --scenario scenario_3_refactor_bug
```

Then call the API manually:

```bash
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-4.1",
    "input": [
      {
        "role": "system",
        "content": "You are a senior engineer. Maintain system invariants."
      },
      {
        "role": "user",
        "content": "Fix the failing test. Return only a diff."
      },
      {
        "role": "user",
        "content": "'"$(cat output/scenario_3_refactor_bug/context.txt | sed 's/"/\\"/g')"'"
      }
    ]
  }' | tee output/scenario_3_refactor_bug/response.json
```

Then extract and apply:

```bash
python3 ci_loop.py extract-patch --scenario scenario_3_refactor_bug
python3 ci_loop.py apply --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_3_refactor_bug
```

## Design Notes

- Scenario markdown files are not part of the default automated context.
- Scenario 2 is intentionally separate from scenario 1 and now targets `user_registry.py`, so it demonstrates the same architectural lesson through a distinct code change.
- Scenario 3 is now live in code, not just documented in a markdown note.
- The automated path is more reliable because it asks for structured edits and renders diffs locally.
- The repo now supports two generation transports: OpenAI Responses API for remote CI and a Codex CLI worker path for local developer-time validation.
