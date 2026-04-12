---
name: capture-fixtures
lifecycle: migration
description: >
  Capture golden response fingerprints from Flask blueprint routes for migration
  parity testing. Writes JSON fixtures to tests/migration/fixtures/fingerprints/.
args: <blueprint-name>
---

# Capture Golden Response Fixtures

## Args

`/capture-fixtures accounts` — blueprint name without `.py`.

## Protocol

> **Run this BEFORE `/port-blueprint`.** Fixtures must be captured from the live Flask blueprint so `/port-blueprint` can validate parity after conversion. If the blueprint has already been deleted, fixtures cannot be captured.

### Step 1: Ensure test stack is running

```bash
make test-stack-up 2>/dev/null || true
source .test-stack.env 2>/dev/null || true
curl -sf http://localhost:8000/admin/login > /dev/null || { echo "STOP: test stack not running"; exit 1; }
```

### Step 2: Read blueprint route inventory

```bash
grep -n "@.*\.route" src/admin/blueprints/{name}.py
```

For each route, record: path, methods, auth requirements.

### Step 3: Capture fingerprints

For each route, use the Flask test client (needs session auth):

```python
from src.admin.app import create_app
app = create_app()[0]
with app.test_client() as client:
    client.post("/admin/test-login", data={"password": "test123"})
    resp = client.get("/tenant/default/accounts/")
    fingerprint = {
        "status_code": resp.status_code,
        "content_type": resp.content_type,
        "header_keys": sorted(set(k for k, v in resp.headers)),
        "body_type": "json" if resp.is_json else "html" if "text/html" in resp.content_type else "other",
    }
```

### Step 4: Write fixtures

```bash
mkdir -p tests/migration/fixtures/fingerprints
```

Output: `tests/migration/fixtures/fingerprints/{name}.json`

### Step 5: Validate completeness

Every route in the blueprint must have a fingerprint. Count routes in blueprint source and compare to fixture entries.

## Hard rules

1. Capture EVERY route — not just GET routes (POST form submissions too)
2. Validate completeness against the blueprint route count
3. Include both status code AND content-type (catches HTML→JSON regression)
4. If a route returns 302/403 after test-login, it may require elevated auth context. Record the status code as-is and add `"note": "requires elevated auth"` to the fixture entry

**Note:** The fingerprint comparison test (`tests/migration/test_response_fingerprints.py`) is a separate Phase -1 deliverable. This skill only captures the golden data.

## See Also

- `/port-blueprint` — port the blueprint AFTER capturing fixtures
- `/test-router` — write integration tests that compare against these fixtures
