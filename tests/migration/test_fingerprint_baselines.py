"""L0-21: verify the 5 Category-1 AdCP baselines match the live surface.

These baselines are captured at L0-21 entry against the pre-L2 Flask
surface and shipped as JSON under ``tests/migration/fingerprints/baselines/``.
L1+ router-port Red/Green cycles load them via
:func:`tests.migration._fingerprint.load_fingerprint` and compare new
FastAPI responses against them with :func:`assert_matches`.

This test file re-captures each baseline on every CI run and asserts
the live response still matches the committed JSON at the declared
strictness. If a baseline drifts BEFORE the L1+ port lands, something
unrelated changed the wire shape and the drift must be investigated.

Baselines captured (5 Category-1 endpoints — the AdCP-surface
endpoints whose JSON shape is an external contract per
``flask-to-fastapi-adcp-safety.md``):

  * ``health`` — ``GET /health`` (MCP liveness; byte-stable)
  * ``health_config`` — ``GET /health/config`` (MCP runtime config;
    byte-stable)
  * ``api_v1_capabilities`` — ``GET /api/v1/capabilities`` (AdCP
    capabilities descriptor; byte-stable)
  * ``api_v1_products_no_auth`` — ``POST /api/v1/products`` without
    auth (AdCP error envelope, schema-only; message text is an impl
    detail)
  * ``api_v1_media_buys_no_auth`` — ``POST /api/v1/media-buys``
    without auth (AdCP error envelope, schema-only)

All 5 run against the pre-existing session-scoped ``boot_client``
fixture (``tests/conftest_db.py``). No Docker, no live services.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.migration._fingerprint import (
    ResponseFingerprint,
    assert_matches,
    capture_fingerprint,
    load_fingerprint,
)

# (baseline_name, method, path, kwargs, strictness)
_BASELINES: list[dict[str, Any]] = [
    {
        "name": "health",
        "method": "GET",
        "path": "/health",
        "kwargs": {},
        "strictness": "byte",
    },
    {
        "name": "health_config",
        "method": "GET",
        "path": "/health/config",
        "kwargs": {},
        "strictness": "byte",
    },
    {
        "name": "api_v1_capabilities",
        "method": "GET",
        "path": "/api/v1/capabilities",
        "kwargs": {},
        "strictness": "byte",
    },
    {
        "name": "api_v1_products_no_auth",
        "method": "POST",
        "path": "/api/v1/products",
        "kwargs": {"json": {"brief": "test"}},
        "strictness": "schema",
    },
    {
        "name": "api_v1_media_buys_no_auth",
        "method": "POST",
        "path": "/api/v1/media-buys",
        "kwargs": {"json": {"buyer_ref": "x"}},
        "strictness": "schema",
    },
]


@pytest.mark.integration
class TestCategory1Baselines:
    """The 5 Category-1 AdCP fingerprints match their committed baselines."""

    @pytest.mark.parametrize("spec", _BASELINES, ids=[b["name"] for b in _BASELINES])
    def test_live_fingerprint_matches_baseline(self, boot_client, spec):
        observed = capture_fingerprint(boot_client, spec["method"], spec["path"], **spec["kwargs"])
        expected = load_fingerprint(spec["name"])
        assert_matches(observed, expected, strictness=spec["strictness"])

    def test_all_baselines_loaded_without_error(self):
        """Every committed baseline deserializes into a ResponseFingerprint."""
        for spec in _BASELINES:
            fp = load_fingerprint(spec["name"])
            assert isinstance(fp, ResponseFingerprint)
            assert fp.status_code in range(100, 600)
            assert fp.content_type, f"baseline {spec['name']} missing content_type"
