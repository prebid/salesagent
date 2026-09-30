"""Regression tests for : silent return [] masks agent failures.

A creative-agent fetch failure must surface as an error in
``FormatFetchResult.errors``, never as a silently empty format list — that
distinction is what lets ``products.py`` tell "agent up, genuinely 0 formats"
apart from "agent unreachable" and trigger graceful degradation accordingly.

Two of this file's original classes were retired, each for its own reason, and
neither because the invariant relaxed:

``TestFetchFormatsAnomalousStatusesMustRaise`` (anomalous ``status`` values on
a mocked adcp SDK response) was retired by salesagent-4n88: the OPERATOR agent
path no longer goes through ``adcp.ADCPMultiAgentClient`` at all — neither
``_fetch_formats_from_agent`` nor ``_build_adcp_client`` exists, so there is no
SDK-shaped ``status`` field left to be anomalous.

``TestRawMcpFallbackMustRaise`` was retired because its coverage MOVED, not
because its subject died: ``_fetch_formats_raw_mcp`` is still live, but it now
dials the egress seam (``asend``) rather than ``httpx.AsyncClient``, requires a
keyword-only ``provenance``, and raises ``AdCPValidationError`` (a sibling of
``AdCPAdapterError``, not a subclass). Both of its cases are graded — through
the seam, with the provenance and the exact refusal field — by
``test_creative_agent_fallback.py``:
``TestFetchFormatsRawMcpThroughTheSeam::test_a_response_with_no_result_is_a_correctable_buyer_error``
and ``TestParseMcpToolResult::test_empty_content_raises``.

What this file grades now is the invariant on the path that REPLACED them: the
operator dial ``_fetch_formats_operator``, which reads its answer out of the
guarded, RFC 9421-signed MCP seam (``call_operator_mcp_tool``). That dial's
``"formats" not in payload`` raise is the merged successor of every retired
"must raise, not return []" test above, and ``creative_agent_registry.py`` names
this file as its guard.

Bug: prebid/salesagent#1136
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import AdCPConfigurationError


@pytest.fixture
def registry():
    return CreativeAgentRegistry()


@pytest.fixture
def agent():
    return CreativeAgent(agent_url="https://creative.example.com", name="test-agent")


def _seam_answers(payload: dict):
    """Patch the guarded MCP seam, as ``_fetch_formats_operator`` imports it, to answer *payload*."""
    return patch(
        "src.core.creative_agent_registry.call_operator_mcp_tool",
        AsyncMock(return_value=payload),
    )


class TestOperatorDialMustRaiseOnUnparseablePayload:
    """``_fetch_formats_operator`` must raise when the payload carries no ``formats``.

    This is the live #1136 surface. ``call_operator_mcp_tool`` answers ``{}``
    when neither structuredContent nor a TextContent block carried a JSON
    object — a dead or wrong-answering agent — and an object that simply lacks
    ``formats`` did not answer ``list_creative_formats``. Reading
    ``.get("formats", [])`` over either would turn both into "agent up, 0
    formats", which ``products.py`` acts on by rejecting every submitted format
    ID. The gate must therefore be key PRESENCE, not truthiness.

    Nothing here mocks the signing gate: ``tenant_id=None`` is production's own
    "no tenant in scope, dial unsigned" answer, so the real
    ``request_signer_for_tenant`` runs and returns ``None`` before it ever
    opens a session — which is what keeps this a unit test.

    Graded on ``error_code``, not on the raise's text: post-ADR-010 an
    ``AdCPSalesAgentError`` takes no ``message``, so the sentence a reader
    would have matched on ("No parseable content in ... from <agent name>")
    does not exist. ``CODE_TABLE`` owns the message, and the operator-readable
    detail is the ``logger.error`` line above the raise — the raise site
    deliberately interpolates neither ``agent.name`` nor ``agent.agent_url``
    into buyer-facing text (transport-errors.mdx § Security Considerations).
    """

    @pytest.mark.asyncio
    async def test_empty_payload_raises(self, registry, agent):
        """Nothing parseable came back — must raise, not return []."""
        with _seam_answers({}):
            with pytest.raises(AdCPConfigurationError) as excinfo:
                await registry._fetch_formats_operator(agent, tenant_id=None)
        assert excinfo.value.error_code == "CONFIGURATION_ERROR"

    @pytest.mark.asyncio
    async def test_payload_without_formats_key_raises(self, registry, agent):
        """A non-empty object that answered something else — must raise, not return [].

        The case a truthiness gate (``if not payload``) would wave through: the
        payload is truthy, so only checking key presence catches it.
        """
        with _seam_answers({"some_other_tool_response": {"ok": True}}):
            with pytest.raises(AdCPConfigurationError) as excinfo:
                await registry._fetch_formats_operator(agent, tenant_id=None)
        assert excinfo.value.error_code == "CONFIGURATION_ERROR"

    @pytest.mark.asyncio
    async def test_genuinely_empty_format_list_is_not_an_error(self, registry, agent):
        """``{"formats": []}`` IS "agent up, genuinely 0 formats" — the one honest [].

        The counterpart assertion: the raise above must not be so broad that a
        healthy agent with no formats is reported as a failure. Without this,
        the gate could be tightened to ``if not payload.get("formats")`` and
        every test above would still pass.
        """
        with _seam_answers({"formats": []}):
            assert await registry._fetch_formats_operator(agent, tenant_id=None) == []


class TestListAllFormatsErrorPropagation:
    """End-to-end: a failed agent fetch must produce an error in FormatFetchResult.

    This is the critical integration point — when the operator fetch raises,
    list_all_formats_with_errors must record it as an error so that
    products.py can trigger graceful degradation.

    ``tenant_id=None`` throughout, and the value is deliberate rather than
    incidental: ``list_all_formats_with_errors`` now threads its tenant down to
    ``_fetch_formats_operator``'s ``sign=request_signer_for_tenant(...)``, whose
    posture gate opens a real session for any tenant that is not ``None``. In a
    unit run (``tests/conftest.py`` unsets ``DATABASE_URL``) that raises, and the
    loop's ``except Exception`` below would book it as the agent failure — so
    every assertion here would hold while the injected failure and the payload
    under test never reached the dial at all. ``None`` is production's own "no
    tenant in scope, dial unsigned" answer, returns before any session is
    opened, and leaves the recorded error attributable to the thing each test
    actually injects. ``_get_tenant_agents`` is stubbed either way, so the
    tenant value is not otherwise read.
    """

    @pytest.mark.asyncio
    async def test_operator_fetch_failure_produces_error(self, registry, agent, monkeypatch):
        """When the guarded MCP seam fails, the failure lands in FormatFetchResult.errors.

        Not the seam's business (asend/call_mcp_tool's own retry/backoff is
        graded elsewhere): what's graded here is that list_all_formats_with_errors
        turns a raised exception into a recorded error rather than an empty
        format list — a failed fetch must never look like "agent up, no formats".

        The failure is injected at the dial as ``call_operator_mcp_tool``
        imports it, so it travels the full production path — through that
        function's except branches (which do NOT catch a bare ``RuntimeError``) and
        out of ``_fetch_formats_operator`` — before the recording under test.
        """
        monkeypatch.delenv("ADCP_TESTING", raising=False)

        monkeypatch.setattr(registry, "_get_tenant_agents", lambda tenant_id=None: [agent])

        with patch("src.core.utils.operator_mcp.call_mcp_tool", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await registry.list_all_formats_with_errors(tenant_id=None)

        assert len(result.errors) > 0, (
            "FormatFetchResult.errors must be non-empty when the operator fetch fails. "
            "Silent return [] masks the failure as 'agent up, no formats'."
        )
        assert len(result.formats) == 0

    @pytest.mark.asyncio
    async def test_unparseable_payload_is_recorded_as_an_error_not_zero_formats(self, registry, agent, monkeypatch):
        """The #1136 shape end to end: an unparseable payload must reach ``.errors``.

        Closes the loop the two layers above only half-grade — the dial raises
        (``TestOperatorDialMustRaiseOnUnparseablePayload``) and a raise is
        recorded (test above) — by driving the ACTUAL payload a dead agent
        returns all the way to the FormatFetchResult products.py reads.
        """
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        monkeypatch.setattr(registry, "_get_tenant_agents", lambda tenant_id=None: [agent])

        seam = AsyncMock(return_value={})
        with patch("src.core.creative_agent_registry.call_operator_mcp_tool", seam):
            result = await registry.list_all_formats_with_errors(tenant_id=None)

        # The dial must actually have happened: without this, an exception raised
        # anywhere BEFORE it (the signing gate is evaluated as an argument to this
        # very call) would be booked by the loop's ``except Exception`` and every
        # assertion below would hold for a reason that has nothing to do with #1136.
        assert seam.await_count == 1
        assert len(result.formats) == 0
        assert len(result.errors) > 0, (
            "An unparseable list_creative_formats payload must be recorded as an error. "
            "FormatFetchResult(formats=[], errors=[]) is read by products.py as "
            "'agent up, genuinely 0 formats' and rejects every submitted format ID."
        )
