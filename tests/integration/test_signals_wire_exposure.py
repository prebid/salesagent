"""get_signals wire exposure — MCP tool, A2A skill, REST route.

TDD red for the get_signals exposure work (#1593 tracks the descoped activate_signal) (owner decision 2026-07-12: get_signals ONLY;
activate_signal descoped to its follow-up bead and must stay unregistered).

The full implementation stack already exists in src/core/tools/signals.py
(_get_signals_impl, MCP wrapper, get_signals_raw) — what is missing is
REGISTRATION on every transport. These tests pin the exposed surface:

- MCP: get_signals listed by tools/list and callable through the full
  FastMCP pipeline (currently NOT registered in src/core/main.py).
- A2A: "get_signals" dispatches through the skill table (currently absent
  from skill_handlers in adcp_a2a_server.py -> Unknown skill).
- REST: POST /api/v1/signals returns 200 with a typed GetSignalsResponse
  (route currently absent from src/routes/api_v1.py -> 404).

The IMPL-transport test is the control: it passes TODAY, proving the wire
failures are exposure-only, not business-logic gaps.

Spec grounding: pinned AdCP v3.1.1 signals schemas
(dist/schemas/3.1.1/signals/get-signals-{request,response}.json — all fields
optional) and the graded contract BR-UC-008-manage-audience-signals.feature.
"""

import asyncio

import pytest

from src.core.schemas import GetSignalsRequest, GetSignalsResponse
from tests.harness import SignalsEnv, Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _assert_sports_signal_response(response: GetSignalsResponse) -> None:
    """Shared payload contract: signal_spec='sports' selects exactly sports_content.

    Asserting on the FILTERED result (not just non-empty) proves the transport
    actually forwarded the request fields to _get_signals_impl — a wrapper that
    dropped the body/arguments would return all six catalog signals.
    """
    assert [s.signal_agent_segment_id for s in response.signals] == ["sports_content"]
    signal = response.signals[0]
    assert signal.name == "Sports Content Pages"
    assert signal.signal_type == "owned"
    assert signal.coverage_percentage == 95.0
    assert len(signal.pricing_options) == 1
    assert signal.pricing_options[0].model == "cpm"


class TestGetSignalsImplBaseline:
    """Control: the business logic works today — red tests below are exposure-only."""

    def test_impl_returns_filtered_signals(self, integration_db):
        with SignalsEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.IMPL, req=GetSignalsRequest(signal_spec="sports"))

            assert result.is_success, f"_get_signals_impl failed: {result.error!r}"
            _assert_sports_signal_response(result.payload)


class TestGetSignalsWireExposure:
    """get_signals must be reachable on every wire transport (#1593)."""

    def test_get_signals_listed_by_mcp_tools_list(self, integration_db):
        """tools/list (the MCP discovery wire) must advertise get_signals."""
        from fastmcp import Client

        from src.core.main import mcp

        async def _list_tools():
            async with Client(mcp) as client:
                return await client.list_tools()

        tool_names = [t.name for t in asyncio.run(_list_tools())]
        assert "get_signals" in tool_names, (
            f"get_signals is not registered as an MCP tool. Registered tools: {sorted(tool_names)}"
        )

    @pytest.mark.parametrize(
        "transport",
        [Transport.MCP, Transport.A2A, Transport.REST],
        ids=["mcp", "a2a", "rest"],
    )
    def test_get_signals_reachable_and_forwards_request(self, integration_db, transport):
        """Full-pipeline dispatch per transport returns the spec-shaped response.

        Expected red failures today:
        - MCP: Unknown tool 'get_signals' (not registered in src/core/main.py)
        - A2A: Unknown skill 'get_signals' (missing from skill_handlers)
        - REST: 404 Not Found (no POST /api/v1/signals route)
        """
        with SignalsEnv() as env:
            env.setup_default_data()

            result = env.call_via(transport, req=GetSignalsRequest(signal_spec="sports"))

            assert result.is_success, (
                f"get_signals not reachable via {transport}: {result.error!r} "
                f"(wire_error_envelope={result.wire_error_envelope!r})"
            )
            _assert_sports_signal_response(result.payload)
            # Real wire (not payload reconstruction): all three transports here
            # dispatch through the wire-capturing paths (_run_mcp_client,
            # _run_a2a_handler, REST HTTP body).
            assert result.wire_response is not None
            wire_signals = result.wire_response["signals"]
            assert [s["signal_agent_segment_id"] for s in wire_signals] == ["sports_content"]

    def test_rest_route_exists(self, integration_db):
        """POST /api/v1/signals must not 404 (route currently absent).

        Isolates the route-existence failure from response-shape failures:
        whatever else may be wrong, a 404 means the wire surface is missing.
        """
        with SignalsEnv() as env:
            env.setup_default_data()

            response = env._run_rest_request(env.REST_ENDPOINT, req=GetSignalsRequest(signal_spec="sports"))

            assert response.status_code == 200, (
                f"POST {env.REST_ENDPOINT} returned {response.status_code}: {response.text}"
            )
            _assert_sports_signal_response(GetSignalsResponse(**response.json()))


class TestGetSignalsBodyDeclaresAllSpecFields:
    """REST boundary ACCEPTS every declared v3.1.1 get-signals-request property.

    #1442: GetSignalsBody omitted ext / push_notification_config /
    if_pricing_version / if_wholesale_feed_version — on the dev/CI arm
    (extra="forbid", Pattern #7) a spec-valid request carrying any of them was
    rejected with INVALID_REQUEST; in production they were silently dropped.
    Spec: dist/schemas/3.1.1/signals/get-signals-request.json @ v3.1.1 declares
    16 properties including these four.
    """

    def test_spec_valid_request_with_all_declared_fields_accepted(self, integration_db):
        with SignalsEnv() as env:
            env.setup_default_data()

            response = env._run_rest_request(
                env.REST_ENDPOINT,
                req=GetSignalsRequest(
                    signal_spec="sports",
                    ext={"vendor": {"k": "v"}},
                    push_notification_config={"url": "https://buyer.example.com/hooks"},
                    if_pricing_version="abc123",
                    if_wholesale_feed_version="def456",
                ),
            )

            assert response.status_code == 200, (
                f"spec-valid request with declared optional fields rejected: {response.status_code}: {response.text}"
            )
            _assert_sports_signal_response(GetSignalsResponse(**response.json()))
