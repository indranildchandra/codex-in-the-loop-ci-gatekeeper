# Codex CI Gatekeeper Demo Walkthrough

This file is the single-screen walkthrough for the live demo. 

## Core Point

`python3 ci_loop.py test --scenario ...` does not fix anything.

It only shows the broken baseline.

`python3 ci_loop.py run-all --max-retries 2 --dryRun` is the actual gatekeeper loop for the default local path:

1. build context
2. call the model
3. generate a patch
4. apply the patch
5. rerun validation
6. accept or reject the change

The point of the demo is not "the model wrote code."

The point is "the loop decides whether the change is safe."

By default, `run-all` executes only the gating scenarios (1 to 3).  
Use `--include-non-gating` to include scenario 4, then choose clarification policy: fail-closed (default) or `interactive`.

## Two Operating Modes

```text
Local developer path
    code change
        ↓
    ci_loop.py run-all
        ↓
    context.txt
        ↓
    codex exec
        ↓
    response.md
        ↓
    patch.diff
        ↓
    tests
        ↓
    accept or reject before commit
```

```text
Remote CI path
    remote commit on UAT/prod-tagged branch
        ↓
    Jenkins post-commit trigger
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
    tests / CI checks
        ↓
    mark build success, failure, or retry
```

## Demo Flow

List scenarios:

```bash
python3 ci_loop.py list-scenarios
```

Show the broken baseline:

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_4_low_confidence
```

Run the full gatekeeper loop:

```bash
python3 ci_loop.py run-all --max-retries 2 --dryRun
```

Run the local Codex path explicitly:

```bash
python3 ci_loop.py run-all --backend codex --max-retries 2 --dryRun
```

Run the remote CI path explicitly:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2 --dryRun
```

Show low-confidence artifact generation explicitly:

```bash
python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence
cat output/scenario_4_low_confidence/clarification_request.json | jq .
cat output/scenario_4_low_confidence/scenario_proposal.json | jq .
```

Run all 4 scenarios in one full interactive sweep:

```bash
python3 ci_loop.py run-all --include-non-gating --clarification-policy interactive --max-retries 2 --dryRun
```

## Manual Test Path

Yes, you can test both backends manually now.

Use this sequence if you want to prove the flow step by step instead of running the full sweep immediately.

### 1. Prove the broken baseline

```bash
python3 ci_loop.py test --scenario scenario_1_integration_bug
python3 ci_loop.py test --scenario scenario_2_wrong_fix_path
python3 ci_loop.py test --scenario scenario_3_refactor_bug
python3 ci_loop.py test --scenario scenario_4_low_confidence
```

Expected:

- all four fail
- that confirms the repo is still in the intentionally broken demo state

### 2. Manually test the local `codex` backend

Generate one patch:

```bash
python3 ci_loop.py generate-patch --scenario scenario_1_integration_bug
```

Inspect the local-backend artifacts:

```bash
cat output/scenario_1_integration_bug/context.txt
glow output/scenario_1_integration_bug/response.md || cat output/scenario_1_integration_bug/response.md
glow output/scenario_1_integration_bug/patch.diff || cat output/scenario_1_integration_bug/patch.diff
```

Run the full local backend:

```bash
python3 ci_loop.py run-all --backend codex --max-retries 2 --dryRun
```

What to say:

- this is the local developer path
- it is suitable for a pre-commit or pre-push style guard
- the raw backend artifact is `response.md`

### 3. Manually test the remote `openai_responses_api` backend

Generate one patch:

```bash
python3 ci_loop.py generate-patch --scenario scenario_1_integration_bug --backend openai_responses_api
```

Inspect the remote-backend artifacts:

```bash
cat output/scenario_1_integration_bug/context.txt
cat output/scenario_1_integration_bug/response.json | jq .
glow output/scenario_1_integration_bug/patch.diff || cat output/scenario_1_integration_bug/patch.diff
```

Run the full remote backend:

```bash
python3 ci_loop.py run-all --backend openai_responses_api --max-retries 2 --dryRun
```

What to say:

- this is the CI pipeline path
- it is suitable for Jenkins or another remote post-commit gate
- the raw backend artifact is `response.json`

Terminal viewing tip:

- use `jq` for `response.json`
- use `glow <file>.md` if you have `glow` installed
- otherwise use `cat <file>` as the portable fallback
- `patch.diff` also looks better through `glow`, so the demo commands use it there too

### 4. What "working" means

For both backends, working means:

- the baseline test fails before generation
- the backend produces a patch for the correct target file
- the scenario test passes after patch application
- with `--dryRun`, the repo is restored to the intentionally broken baseline after the accepted run

Default runtime behavior:

- without `--dryRun`, accepted fixes remain in the working tree
- with `--dryRun`, accepted fixes are reverted after validation

## Default Path

The default backend is now `codex`, so the manual path above does not require `OPENAI_API_KEY`.

If you want to force the OpenAI backup route, pass `--backend openai_responses_api` and provide `OPENAI_API_KEY`.

## Scenario 1: Write/Read Path Inconsistency

### Bug

- `user_store.py` writes the raw email into `self.users`
- `get_user()` reads using `normalize_email()`
- write path and read path disagree

### Broken Code

```python
from utils import normalize_email

class UserStore:
    def __init__(self):
        self.users = {}

    def add_user(self, email: str, name: str):
        # BUG: inconsistent normalization (write path broken)
        self.users[email] = name

    def get_user(self, email: str):
        key = normalize_email(email)
        return self.users.get(key)
```

### What `test` Shows

- lookup fails even though the user was added
- symptom: `None` instead of the stored name

### Failing Test Shape

```python
store.add_user("TestUser@Example.com", "Indranil")
result = store.get_user("testuser@example.com")
assert result == "Indranil"
```

### Right Fix From `run-all`

- normalize on write in `add_user()`
- this restores the system invariant: stored keys and lookup keys use the same format

### Correct Patch Shape

```python
def add_user(self, email: str, name: str):
    key = normalize_email(email)
    self.users[key] = name
```

### Summary

The model is not being rewarded for changing code anywhere. It is being rewarded only if the invariant is restored and the validation passes.

## Scenario 2: Misleading Local Fix

### Bug

- `user_registry.py` has a broken `storage_key()` helper
- it returns the raw email instead of the canonical normalized key
- this is a tempting wrong-fix-path scenario

### Broken Code

```python
from utils import normalize_email

class UserRegistry:
    def __init__(self):
        self.users = {}

    def storage_key(self, email: str) -> str:
        # BUG: write path uses the raw identifier instead of the canonical key.
        return email

    def add_user(self, email: str, name: str):
        key = self.storage_key(email)
        self.users[key] = name

    def get_user(self, email: str):
        key = normalize_email(email)
        return self.users.get(key)
```

### What `test` Shows

- the stored dict key is wrong
- the bug is in write-path key generation, not in read-path lookup

### Failing Test Shape

```python
store.add_user("TestUser@Example.com", "Indranil")
assert "testuser@example.com" in store.users
assert "TestUser@Example.com" not in store.users
```

### Wrong Fix

- patching `get_user()`
- adding a local workaround on read
- that may hide the symptom, but it does not fix the storage invariant

### Right Fix From `run-all`

- fix `storage_key()` so writes use `normalize_email(email)`
- this is the systemic fix, not a read-path band-aid

### Correct Patch Shape

```python
def storage_key(self, email: str) -> str:
    return normalize_email(email)
```

### Summary

This is the good demo case for “passing behavior locally is not enough.” The read path is tempting, but the actual invariant lives in the write path.

## Scenario 3: Refactor-Induced Contract Drift

### Bug

- `pricing.py` now expects tax as a percentage
- `orders.py` still calls it with `0.1`
- caller and callee drifted after a refactor

### Broken Code

```python
from pricing import calculate_total

class OrderService:
    def __init__(self):
        self.orders = []

    def create_order(self, price):
        total = calculate_total(price, 0.1)
        order = {
            "price": price,
            "total": total,
        }
        self.orders.append(order)
        return order
```

### Expected Contract

```python
def calculate_total(price, tax_percent):
    return price + (price * tax_percent / 100)
```

### What `test` Shows

- total is computed as `100.1` instead of `110`

### Failing Test Shape

```python
service = OrderService()
order = service.create_order(100)
assert order["total"] == 110
```

### Wrong Fix

- changing `pricing.py` back
- weakening the contract
- undoing the refactor instead of fixing the caller

### Right Fix From `run-all`

- update `orders.py` to pass `10` instead of `0.1`
- preserve the new pricing contract and repair the caller

### Correct Patch Shape

```python
def create_order(self, price):
    total = calculate_total(price, 10)
```

### Summary

This is not a syntax bug. It is a refactor contract bug. The right fix is not “make the old behavior work again.” The right fix is “repair the caller to match the new contract.”

## Scenario 4: Low-Confidence Clarification Gate

### Purpose

- this scenario is intentionally *not* mapped in seeded `test_scenarios/`
- it exists to demo the stop-and-review path
- by default it is excluded from `run-all`, but it can be included via `--include-non-gating`

### Bug

- `delivery_window.py` floors partial delivery windows
- for `501` km it returns `1`, but the expected contract is round-up to `2`

### What `test` Shows

- baseline test fails on scenario 4
- this creates a failure signature that has no high-confidence registry match

### What `plan-clarification` Shows

```bash
python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence
cat output/scenario_4_low_confidence/clarification_request.json | jq .
cat output/scenario_4_low_confidence/scenario_proposal.json | jq .
```

- `clarification_request.json` is generated to force operator review
- `scenario_proposal.json` is drafted as a candidate recurring scenario record
- the loop stops before backend generation for this path until intent is clarified

### Full Interactive Run-All Demo

Use this when you want to show the production-style operator flow in one command.

```bash
python3 ci_loop.py run-all --include-non-gating --clarification-policy interactive --max-retries 2 --dryRun
```

What happens live:

- scenarios 1 to 3 execute and validate normally
- scenario 4 pauses and asks clarification questions in terminal
- scenario 4 presents recommended answer options (`1/2/3`) per question and still allows free-text answers
- operator can type `edit`/`e` to do another Q&A pass before proceeding
- operator can type `yes`/`y` to continue after answering
- the interactive clarification trace is captured in `clarification_dialog.json`
- the loop proceeds to patch generation and validation for scenario 4

Inspect captured interactive answers:

```bash
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

### Forced Heuristics Fallback Demo

To demonstrate the fallback logic deterministically, force heuristic option generation in interactive mode:

```bash
python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --clarifier-option-source heuristic --max-retries 1 --dryRun
cat output/scenario_4_low_confidence/clarification_dialog.json | jq .
```

What to call out:

- question flow is still interactive and supports `edit`/`e` loops
- options are generated from heuristic rules (not backend clarifier responses)
- `clarification_dialog.json` shows `dialog_backend: "heuristic"` and per-question `option_source: "heuristic"`
- `response_thread_ids` is empty because backend clarifier calls are intentionally bypassed

### Fail-Closed Run-All Demo

Use this to show the default policy.

```bash
python3 ci_loop.py run-all --include-non-gating --max-retries 2 --dryRun
```

What happens live:

- scenarios 1 to 3 execute and validate normally
- scenario 4 writes clarification artifacts and exits fail-closed

## What The Artifacts Mean

- `context.txt`: the failure-driven input snapshot sent to the backend for that scenario. It includes normalized failure facts, raw failure output, relevant code context, and scenario-memory enrichment when available.
- `response.json`: raw output from the `openai_responses_api` backend.
- `response.md`: raw output/log from the `codex` backend.
- `patch.diff`: the concrete unified diff rendered from backend output and used for apply/validate.
- `clarification_request.json`: generated when confidence is low and policy is fail-closed; defines the questions that must be resolved before generation proceeds.
- `scenario_proposal.json`: generated in low-confidence paths as a draft recurring scenario record for later approval into `test_scenarios/`.
- `clarification_dialog.json`: generated in interactive clarification mode; captures questions, suggested options, chosen answers, answer edits, backend source (`backend` or `heuristic`), and response-thread ids when applicable.

## One-Line Summary Per Scenario

- Scenario 1: write path and read path disagree, so the correct fix is normalize on write
- Scenario 2: the helper that generates storage keys is wrong, so the correct fix is repair the write-path helper, not the read path
- Scenario 3: a refactor changed a contract, so the correct fix is update the caller, not roll back the callee
- Scenario 4: when failure classification is low confidence, the loop should stop and ask for clarification before generating a patch

## Overall Summary

The model proposes a change.

The loop decides whether the change deserves to live.

## Quick Demo Reference (Command -> Artifact -> Screenshot)

| Step | Command | Artifact | Screenshot |
| --- | --- | --- | --- |
| Build scenario 4 context | `python3 ci_loop.py build-context --scenario scenario_4_low_confidence` | [output/scenario_4_low_confidence/context.txt](output/scenario_4_low_confidence/context.txt) | [context screenshot](demo_screenshots/scenario_4_low_confidence-context-txt.png) |
| Generate patch with codex | `python3 ci_loop.py generate-patch --scenario scenario_4_low_confidence --backend codex` | [output/scenario_4_low_confidence/response.md](output/scenario_4_low_confidence/response.md) | [codex response screenshot](demo_screenshots/scenario_4_low_confidence-response-md.png) |
| Generate patch with openai responses | `python3 ci_loop.py generate-patch --scenario scenario_4_low_confidence --backend openai_responses_api` | [output/scenario_4_low_confidence/response.json](output/scenario_4_low_confidence/response.json) | [responses API screenshot](demo_screenshots/scenario_4_low_confidence-response-json.png) |
| Inspect rendered diff | `cat output/scenario_4_low_confidence/patch.diff` | [output/scenario_4_low_confidence/patch.diff](output/scenario_4_low_confidence/patch.diff) | [patch screenshot](demo_screenshots/scenario_4_low_confidence-patch-diff.png) |
| Plan clarification artifacts | `python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence` | [output/scenario_4_low_confidence/clarification_request.json](output/scenario_4_low_confidence/clarification_request.json) | [clarification request screenshot](demo_screenshots/scenario_4_low_confidence-clarification_request-json.png) |
| Plan clarification artifacts | `python3 ci_loop.py plan-clarification --scenario scenario_4_low_confidence` | [output/scenario_4_low_confidence/scenario_proposal.json](output/scenario_4_low_confidence/scenario_proposal.json) | [scenario proposal screenshot](demo_screenshots/scenario_4_low_confidence-scenario_proposal-json.png) |
| Interactive clarification trace | `python3 ci_loop.py run --scenario scenario_4_low_confidence --clarification-policy interactive --max-retries 1 --dryRun` | [output/scenario_4_low_confidence/clarification_dialog.json](output/scenario_4_low_confidence/clarification_dialog.json) | [clarification dialog screenshot](demo_screenshots/scenario_4_low_confidence-clarification_dialog-json.png) |
