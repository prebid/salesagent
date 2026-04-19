"""L0-02 guard: the A2A agent-card JSON body is frozen.

The A2A SDK exposes agent metadata at two top-level URLs:

* ``/.well-known/agent-card.json`` — the standardised A2A discovery URL.
* ``/agent.json`` — a legacy alias (see ``src/app.py`` ``_replace_routes``).

Downstream A2A clients read these bodies to discover our capabilities,
security schemes, and skills. Any silent drift in the body (capabilities
removed, schema renamed, skills list reordered unexpectedly) breaks
clients — this test captures the current bytes so L1+ refactors must
knowingly update the fixture.

Dynamic fields: the ``url`` field is generated per-request from the
``Host`` header (see ``_create_dynamic_agent_card``). Under TestClient
it is always ``https://testserver/a2a``; we pin the request host and
then substitute the URL to a stable placeholder before hashing so the
fixture is robust against future local-dev host changes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tests.migration._fixtures import canonical_json_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "a2a-agent-card.json"
STABLE_URL = "__stable_url__"


def _normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``card`` with host-sensitive fields replaced."""
    normalized = copy.deepcopy(card)
    if "url" in normalized:
        normalized["url"] = STABLE_URL
    # additionalInterfaces may embed per-host URLs too
    interfaces = normalized.get("additionalInterfaces")
    if isinstance(interfaces, list):
        for interface in interfaces:
            if isinstance(interface, dict) and "url" in interface:
                interface["url"] = STABLE_URL
    return normalized


def _read_expected() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestA2aAgentCardSnapshot:
    """The /.well-known/agent-card.json body is frozen against a checked-in baseline."""

    def test_well_known_agent_card_matches_baseline(self, boot_client):
        response = boot_client.get("/.well-known/agent-card.json")
        assert response.status_code == 200, f"expected 200 for /.well-known/agent-card.json, got {response.status_code}"
        assert response.headers.get("content-type", "").startswith(
            "application/json"
        ), f"expected JSON, got content-type={response.headers.get('content-type')}"
        observed = _normalize_card(response.json())
        expected = _read_expected()

        assert canonical_json_bytes(observed) == canonical_json_bytes(expected), (
            "A2A agent card body drift detected.\n"
            f"  expected keys: {sorted(expected.keys())}\n"
            f"  observed keys: {sorted(observed.keys())}\n"
            f"If intentional, regenerate {FIXTURE.name} with the new snapshot."
        )

    def test_agent_json_alias_behaviour_is_pinned(self, boot_client):
        """The ``/agent.json`` alias is currently a 404 (SDK route not registered).

        This is a KNOWN PRE-EXISTING BEHAVIOUR — ``src/app.py`` logs the
        warning ``expected SDK routes not found for paths: ['/agent.json']``
        at startup. We pin the observed status so that any future fix
        (intentionally making it 200) is a deliberate change that updates
        this test in the same PR.
        """
        response = boot_client.get("/agent.json")
        assert response.status_code == 404, (
            "The /agent.json alias started returning a non-404 status. "
            "If this is an intentional fix to the pre-existing SDK route registration, "
            "update this test accordingly."
        )

    def test_planted_drift_is_detected(self, boot_client):
        """Meta-test: a mutated card must fail the comparison."""
        observed = _normalize_card(boot_client.get("/.well-known/agent-card.json").json())
        expected = _read_expected()

        mutated = dict(observed)
        mutated["name"] = "mutated-agent-name"
        assert canonical_json_bytes(mutated) != canonical_json_bytes(
            observed
        ), "planted mutation did not alter canonical bytes — meta-test setup is wrong"
        assert canonical_json_bytes(mutated) != canonical_json_bytes(
            expected
        ), "planted mutation was NOT detected — comparison is broken"
