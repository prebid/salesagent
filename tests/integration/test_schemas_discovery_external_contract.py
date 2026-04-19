"""L0-02 guard: ``/schemas/adcp/v2.4/*`` is an external contract.

The ``/schemas/...`` endpoints (served by the Flask blueprint
``src.admin.routers.schemas`` and grafted into the root WSGI mount)
return JSON Schema documents that are consumed by EXTERNAL AdCP-protocol
validators. Any drift in:

* the set of URLs served,
* the HTTP status,
* the ``Content-Type`` header,
* the top-level JSON key set, OR
* the ``$id`` URL (external validators key off this byte-exact),

breaks downstream consumers silently. This test captures the current
state at L0. The test MUST stay green across the Flask-to-FastAPI
cutover at L2 — if it fails post-cutover, an external consumer breaks.

Cites: ``flask-to-fastapi-adcp-safety.md:348-351``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURE_DIR / "schemas-discovery-contract.json"


def _current_registry_names() -> list[str]:
    """Return the sorted list of schema names currently in the registry."""
    from src.core.schema_validation import create_schema_registry

    return sorted(create_schema_registry().keys())


def _build_current_contract(boot_client) -> dict[str, dict[str, Any]]:
    """Fetch every ``/schemas/...`` URL and summarise the contract-sensitive surface."""
    paths = ["/schemas/", "/schemas/adcp/", "/schemas/adcp/v2.4/"]
    paths += [f"/schemas/adcp/v2.4/{name}.json" for name in _current_registry_names()]

    contract: dict[str, dict[str, Any]] = {}
    for path in paths:
        response = boot_client.get(path)
        body: Any
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        body_id = body.get("$id") if isinstance(body, dict) else None
        contract[path] = {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", "").split(";")[0].strip(),
            "top_level_keys": sorted(body.keys()) if isinstance(body, dict) else None,
            "id": body_id,
        }
    return contract


def _read_expected() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.integration
class TestSchemasDiscoveryExternalContract:
    """Each /schemas/... URL's status + content-type + top-level keys + $id is frozen."""

    def test_contract_matches_baseline(self, boot_client):
        observed = _build_current_contract(boot_client)
        expected = _read_expected()

        observed_paths = sorted(observed.keys())
        expected_paths = sorted(expected.keys())
        assert observed_paths == expected_paths, (
            f"/schemas/ URL set changed — external validators depend on these URLs being stable.\n"
            f"  expected: {expected_paths}\n"
            f"  observed: {observed_paths}"
        )

        for path in expected_paths:
            assert observed[path] == expected[path], (
                f"Contract drift on {path}:\n"
                f"  expected: {expected[path]}\n"
                f"  observed: {observed[path]}\n"
                f"If this change is intentional, regenerate {FIXTURE.name} AND notify downstream "
                "AdCP-protocol validators — they depend on these bytes."
            )

    def test_planted_drift_detected_on_id(self, boot_client):
        """Meta-test: mutating the $id URL in the observed contract must fail."""
        observed = _build_current_contract(boot_client)
        expected = _read_expected()

        # Pick a schema URL that has a non-null $id.
        target = next(
            (p for p, row in observed.items() if row.get("id")),
            None,
        )
        assert target is not None, "no observed schema row has a non-null $id — setup is wrong"

        mutated = {k: dict(v) for k, v in observed.items()}
        mutated[target]["id"] = "https://evil.example/schemas/hijacked.json"

        # The observed contract should currently equal expected; after mutation, it must differ.
        assert observed == expected, "baseline already drifted — cannot run meta-test"
        assert mutated != expected, "mutating the $id did not break equality — comparison is broken"

    def test_planted_drift_detected_on_key_set(self, boot_client):
        """Meta-test: mutating the top_level_keys list must fail."""
        observed = _build_current_contract(boot_client)
        target = next(iter(observed.keys()))
        mutated = {k: dict(v) for k, v in observed.items()}
        original_keys = mutated[target]["top_level_keys"]
        mutated[target]["top_level_keys"] = (list(original_keys) if original_keys else []) + ["__extra__"]
        assert (
            mutated[target] != observed[target]
        ), "mutating top_level_keys did not change the row — meta-test setup is wrong"
