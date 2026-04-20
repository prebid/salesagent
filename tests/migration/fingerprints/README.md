# Golden-Fingerprint Capture Infrastructure (L0-21)

This directory holds the L0-21 golden-fingerprint baselines used by L1+
Flask→FastAPI router ports. Each baseline is a JSON file produced by
`save_fingerprint()` from `tests/migration/_fingerprint.py`.

## Purpose

When a Flask blueprint is ported to a FastAPI router at L1+, the port
must produce byte-identical (or schema-identical, or status-identical)
responses to the Flask original. The helper in
`tests/migration/_fingerprint.py` provides the comparison primitive:

```python
from fastapi.testclient import TestClient
from tests.migration._fingerprint import (
    capture_fingerprint, load_fingerprint, assert_matches,
)

def test_fastapi_port_matches_flask_baseline(fastapi_client):
    observed = capture_fingerprint(fastapi_client, "GET", "/api/v1/capabilities")
    expected = load_fingerprint("api_v1_capabilities")
    assert_matches(observed, expected, strictness="byte")
```

## Strictness levels

| Level | What must match | When to use |
|-------|----------------|-------------|
| `byte` | `status_code` + `content_type` + `body_schema` + `body_sha256` | Deterministic bodies: static config, version strings, capability descriptors |
| `schema` | `status_code` + `content_type` + `body_schema` (top-level keys only) | Bodies with timestamps, per-request IDs, DB-generated surrogates |
| `status_only` | `status_code` + `content_type` | Pings, redirects, bodies that are entirely dynamic |

## Baselines in this directory

The 5 Category-1 AdCP wire fingerprints captured at L0-21 entry. These
are the endpoints whose JSON shape is an external AdCP contract —
their bytes must survive the Flask removal at L2 byte-identically.

| Baseline | Endpoint | Strictness | Notes |
|----------|----------|-----------|-------|
| `health.json` | `GET /health` | `byte` | MCP liveness probe; body is `{"service": "mcp", "status": "healthy"}` |
| `health_config.json` | `GET /health/config` | `byte` | MCP runtime config echo — static post-boot |
| `api_v1_capabilities.json` | `GET /api/v1/capabilities` | `byte` | AdCP capability descriptor |
| `api_v1_products_no_auth.json` | `POST /api/v1/products` (no auth) | `schema` | 400 error envelope — message text is an impl detail |
| `api_v1_media_buys_no_auth.json` | `POST /api/v1/media-buys` (no auth) | `schema` | 400 error envelope — message text is an impl detail |

## Relationship to L0-02

L0-02 previously captured these same 5 endpoints with a bespoke
fingerprint shape at `tests/integration/fixtures/rest_wire/*.json`.
L0-21's baselines use the richer `ResponseFingerprint` shape (adds
`body_sha256`, `headers_of_interest`, structured `body_schema`). The
two sets coexist intentionally:

* **L0-02 fixtures** are consumed by
  `tests/integration/test_rest_response_wire.py` as a dedicated
  byte-parity test with a custom comparator.
* **L0-21 baselines** are the shared comparison primitive for the
  ~14 L1+ router-port Red/Green cycles (each port compares its new
  FastAPI response against its baseline via `assert_matches`).

L0-21 does NOT duplicate L0-02's bytes — the two shapes differ and
serve different consumers. If an L1+ port finds it more convenient to
compare against an L0-02 fixture (because the existing
`_fingerprint_response()` helper in `test_rest_response_wire.py` is
already doing the right thing for that endpoint), the port is free to
do so; L0-21 is additive, not replacing.

## Adding a new baseline

1. Capture the baseline against the pre-port Flask code via
   `capture_fingerprint(boot_client, METHOD, PATH, ...)` in a one-off
   script or the `/capture-fixtures` skill.
2. `save_fingerprint("<endpoint_slug>", fp)` writes
   `baselines/<endpoint_slug>.json`.
3. Commit the JSON alongside the consumer test.
4. In the L1+ Red/Green cycle, load and compare:
   ```python
   expected = load_fingerprint("<endpoint_slug>")
   assert_matches(observed, expected, strictness="byte")
   ```

## Why `baselines/` has `__init__.py`

Empty `__init__.py` marker so the directory is importable as a Python
package (some tooling discovers baselines by walking packages, and the
structural-guard allowlists in `check_code_duplication.py` treat
non-package dirs specially). The marker does not export anything.
