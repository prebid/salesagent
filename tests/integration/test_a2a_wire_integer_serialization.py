"""Regression test: A2A wire coerces integer fields to floats.

Core Invariant: an AdCP-spec integer field (e.g.
``adcp.idempotency.replay_ttl_seconds``, typed ``integer`` in
get-adcp-capabilities-response.json) must round-trip as a JSON integer on
the real A2A wire, not a float.

Root cause: ``_dict_to_value`` (src/a2a_server/adcp_a2a_server.py) parses the
skill's response dict into a ``google.protobuf.Value`` via
``json_format.Parse``. ``google.protobuf.Value``/``Struct`` (the well-known
types backing A2A's ``Part.data``) have no integer variant -- every JSON
number is stored as ``number_value`` (a double). ``extract_data_from_artifact``
(tests/utils/a2a_helpers.py) reads the artifact back with
``json_format.MessageToJson`` + ``json.loads``, which is the same conversion
production's own wire path (``a2a`` SDK's ``jsonrpc_dispatcher.MessageToDict``)
performs -- so this in-process capture IS the real wire, not a harness
approximation. The result: any originally-int field placed in a DataPart
comes back as a Python ``float`` (``86400`` -> ``86400.0``) on every A2A
skill response, not just capabilities.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.harness.capabilities import CapabilitiesEnv


@pytest.mark.requires_db
class TestA2AHttpRouteIntegerRestoration:
    """The real /a2a HTTP route (src/app.py) must also emit integers, not just
    the in-process harness capture -- proves src/app.py's ASGI-level
    integer-restoration wrapper (_restore_a2a_wire_integers) is correctly
    wired into the actual production route, not just correct in isolation.
    """

    def test_real_a2a_get_adcp_capabilities_over_http_returns_integer_replay_ttl(self, integration_db):
        from src.app import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "data", "data": {"skill": "get_adcp_capabilities", "parameters": {}}}],
                        "messageId": "test-msg-1",
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body

        artifacts = body["result"]["artifacts"]
        data = next(
            part["data"]
            for artifact in artifacts
            for part in artifact["parts"]
            if "data" in part and "idempotency" in part.get("data", {}).get("adcp", {})
        )
        replay_ttl_seconds = data["adcp"]["idempotency"]["replay_ttl_seconds"]
        assert isinstance(replay_ttl_seconds, int), (
            f"expected a JSON integer on the real /a2a HTTP wire, got {replay_ttl_seconds!r} "
            f"({type(replay_ttl_seconds).__name__})"
        )
        assert replay_ttl_seconds == 86400


@pytest.mark.requires_db
class TestA2AWireIntegerSerialization:
    """AdCP integer-typed fields must stay integers on the real A2A wire."""

    def test_replay_ttl_seconds_is_an_integer_on_the_a2a_wire(self, integration_db):
        """adcp.idempotency.replay_ttl_seconds is `type: integer`
        in the pinned v3.1.1 get-adcp-capabilities-response.json schema. The real
        A2A wire (captured via extract_data_from_artifact -> MessageToJson, the
        same conversion the production jsonrpc_dispatcher performs) must not
        silently widen it to a JSON float.
        """
        with CapabilitiesEnv() as env:
            env.setup_default_data()

            env.call_a2a()

            wire = env._last_wire_response
            assert wire is not None, "A2A dispatch did not capture a wire response"
            replay_ttl_seconds = wire["adcp"]["idempotency"]["replay_ttl_seconds"]
            assert isinstance(replay_ttl_seconds, int), (
                "adcp.idempotency.replay_ttl_seconds must be a JSON integer on the "
                f"A2A wire (schema type: integer); got {replay_ttl_seconds!r} "
                f"({type(replay_ttl_seconds).__name__}) -- the protobuf Struct/Value "
                "round-trip (_dict_to_value -> MessageToJson) widened it to a double."
            )
            assert replay_ttl_seconds == 86400
