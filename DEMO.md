# Codex CI Gatekeeper Demo Walkthrough

This file is the single-screen walkthrough for the live demo. 

## Core Point

`python3 ci_loop.py test --scenario ...` does not fix anything.

It only shows the broken baseline.

`python3 ci_loop.py run-all --max-retries 2` is the actual gatekeeper loop:

1. build context
2. call the model
3. generate a patch
4. apply the patch
5. rerun validation
6. accept or reject the change

The point of the demo is not "the model wrote code."

The point is "the loop decides whether the change is safe."

## Architecture

```text
        +------------------+
        |   Codebase       |
        +--------+---------+
                 |
                 v
        +------------------+
        | Context Builder  |
        +--------+---------+
                 |
                 v
        +------------------+
        |     Codex        |
        |  (Diff Gen)      |
        +--------+---------+
                 |
                 v
        +------------------+
        |   Patch (Diff)   |
        +--------+---------+
                 |
                 v
        +------------------+
        | Validation Layer |
        | Tests / Lint     |
        +--------+---------+
                 |
        +--------+---------+
        |                  |
        v                  v
      Accept             Reject
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
```

Run the full gatekeeper loop:

```bash
python3 ci_loop.py run-all --max-retries 2
```

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

## What The Artifacts Mean

- `context.txt`: what the model saw
- `response.json`: raw API/model output
- `patch.diff`: the concrete change rendered locally and applied for validation

## One-Line Summary Per Scenario

- Scenario 1: write path and read path disagree, so the correct fix is normalize on write
- Scenario 2: the helper that generates storage keys is wrong, so the correct fix is repair the write-path helper, not the read path
- Scenario 3: a refactor changed a contract, so the correct fix is update the caller, not roll back the callee

## Overall Summary

The model proposes a change.

The loop decides whether the change deserves to live.
