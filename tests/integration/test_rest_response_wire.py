"""L0-02 guard: focused REST response-wire snapshots for 5 endpoints.

Not a full OpenAPI snapshot — a byte-focused check on the human-facing
response contract for the 5 most-frequently-hit REST surfaces. Each
fixture captures:

    {
        "status": <int>,
        "content_type": <str>,                 # only the media type, no params
        "body_keys": sorted list or null,      # None when body is not a JSON object
        "body": <parsed JSON>                  # only for deterministic, non-dynamic bodies
    }

Dynamic bodies (timestamps, per-request IDs) store ``body: null`` and
only assert the top-level key set.

See ``.claude/notes/flask-to-fastapi/L0-implementation-plan.md`` §L0-02.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rest_wire"


# Each probe is (fixture_filename, method, path, body_or_None).
# Static-body endpoints set ``pin_body=True`` so we compare the full JSON.
_PROBES: list[dict[str, Any]] = [
    {
        "fixture": "health.json",
        "method": "GET",
        "path": "/health",
        "pin_body": True,
    },
    {
        "fixture": "health_config.json",
        "method": "GET",
        "path": "/health/config",
        "pin_body": True,
    },
    {
        "fixture": "api_v1_capabilities.json",
        "method": "GET",
        "path": "/api/v1/capabilities",
        "pin_body": True,
    },
    {
        "fixture": "api_v1_products_no_auth.json",
        "method": "POST",
        "path": "/api/v1/products",
        "json": {"brief": "test"},
        "pin_body": False,  # message text is implementation detail
    },
    {
        "fixture": "api_v1_media_buys_no_auth.json",
        "method": "POST",
        "path": "/api/v1/media-buys",
        "json": {"buyer_ref": "x"},
        "pin_body": False,
    },
]


def _fingerprint_response(response, *, pin_body: bool) -> dict[str, Any]:
    body_keys: list[str] | None
    body: Any
    try:
        parsed = response.json()
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        body_keys = sorted(parsed.keys())
    else:
        body_keys = None

    if pin_body:
        body = parsed
    else:
        body = None  # drifty — only compare the key set.

    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", "").split(";")[0].strip(),
        "body_keys": body_keys,
        "body": body,
    }


def _read_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.integration
class TestRestResponseWire:
    """Representative REST endpoints match their wire-level fingerprints."""

    @pytest.mark.parametrize("probe", _PROBES, ids=[p["fixture"] for p in _PROBES])
    def test_probe_matches_fixture(self, boot_client, probe):
        response = boot_client.request(probe["method"], probe["path"], json=probe.get("json"))
        observed = _fingerprint_response(response, pin_body=probe["pin_body"])
        expected = _read_fixture(probe["fixture"])

        assert observed == expected, (
            f"{probe['method']} {probe['path']} response drifted.\n"
            f"  expected: {expected}\n"
            f"  observed: {observed}\n"
            f"If intentional, regenerate {FIXTURE_DIR.name}/{probe['fixture']}."
        )

    def test_planted_drift_is_detected_on_status(self, boot_client):
        """Meta-test: mutating the observed status must break equality with the fixture."""
        probe = _PROBES[0]
        response = boot_client.request(probe["method"], probe["path"], json=probe.get("json"))
        observed = _fingerprint_response(response, pin_body=probe["pin_body"])
        expected = _read_fixture(probe["fixture"])
        assert observed == expected, "baseline already drifted — meta-test cannot run"

        mutated = dict(observed)
        mutated["status"] = 999
        assert mutated != expected, "mutating status did not break equality — comparison is broken"

    def test_planted_drift_is_detected_on_body_keys(self, boot_client):
        """Meta-test: mutating body_keys must break equality."""
        probe = _PROBES[3]  # /api/v1/products — body_keys compared, body null
        response = boot_client.request(probe["method"], probe["path"], json=probe.get("json"))
        observed = _fingerprint_response(response, pin_body=probe["pin_body"])
        expected = _read_fixture(probe["fixture"])
        assert observed == expected, "baseline already drifted — meta-test cannot run"

        mutated = dict(observed)
        mutated["body_keys"] = [*(observed["body_keys"] or []), "__injected_key__"]
        assert mutated != expected, "mutating body_keys did not break equality — comparison is broken"
