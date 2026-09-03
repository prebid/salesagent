"""Operator-registered creative/signals agents dial through the guarded MCP seam (GH #1802).

``CreativeAgentRegistry`` and ``SignalsAgentRegistry`` used to build an
``adcp.ADCPMultiAgentClient`` (``_build_adcp_client``) to query OPERATOR-configured
agents — the tenant's own ``creative_agents`` / ``signals_agents`` rows, plus the
built-in default creative agent. adcp 6.6.0 exposes no client/transport/factory
injection point on ``ADCPMultiAgentClient`` (verified: its ``__init__`` takes
only ``agents``, ``webhook_url_template``, ``webhook_secret``, ``on_activity``,
``handlers``, ``signing``, ``adcp_version``), so that dial reached none of this
application's egress policy — address validation, IP pinning, redirect refusal
— at all (upstream ask: adcp-client-python#1004, open, blocked on a maintainer
design call as of this writing).

Fixed by routing the operator dial through
``src.core.utils.mcp_client.call_mcp_tool`` — the same guarded fastmcp seam
already used in this file for ``preview_creative``/``build_creative``, and
already proven redirect-refusing by ``tests/integration/test_mcp_client_egress.py``.
All four operator call sites reach it through one shared function,
``src.core.utils.operator_mcp.call_operator_mcp_tool``, which owns the dial,
the payload extraction and both seam-error mappings.
``_fetch_formats_operator``/``_fetch_signals_operator`` are now the only
operator-agent fetch paths; ``ADCPMultiAgentClient`` is gone from both
registries (confirmed by ``tests/unit/test_ruff_egress_bans.py``'s closed
exemption set, which no longer lists either file).

Same shape as ``tests/integration/test_mcp_client_egress.py``: an operator
agent answers ``302 -> <metadata stand-in>``, and the metadata stand-in's real
hit count over a real socket is the grade — not whether the call raised. A
pinned transport also refuses a wrong-host connect for its own reasons, so an
"assert the call failed" test would stay green even under the regression it
exists to catch (a followed redirect also ends in failure, just later). Only
the hit count on the redirect target distinguishes "refused" from "followed".
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import (
    RECOVERY_BY_WIRE_CODE,
    AdCPConfigurationError,
    AdCPError,
    AdCPServiceUnavailableError,
    build_two_layer_error_envelope,
)
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry
from src.core.utils.mcp_client import MCPConnectionError
from tests.helpers import assert_envelope_shape
from tests.helpers.local_http_origin import run_local_origin
from tests.integration.property_list_helpers import allow_local_origin
from tests.integration.test_outbound_http import fast_backoff

pytestmark = [pytest.mark.integration]


def _assert_wire_pair(exc: AdCPError, code: str, *, pinned_recovery: str) -> None:
    """Assert the buyer-visible envelope is *code* paired with its pinned recovery.

    Asserts on the two-layer WIRE envelope, not on the exception's attributes:
    the envelope is what a buyer receives, and reading the attributes would grade
    the object this test already constructed the expectation from.

    The expected recovery is read from ``RECOVERY_BY_WIRE_CODE`` rather than
    written as a literal, so this cannot drift from the pin — if a spec bump
    reclassified the code, this test would follow it instead of asserting
    yesterday's answer. *pinned_recovery* pins the derivation itself once per
    call site, so it cannot silently return the wrong thing.
    """
    expected = RECOVERY_BY_WIRE_CODE[code]
    assert expected == pinned_recovery, (
        f"the pinned enumMetadata classifies {code} as {expected!r}, not {pinned_recovery!r} — "
        "the premise this assertion is built on no longer holds"
    )
    assert_envelope_shape(build_two_layer_error_envelope(exc), code, recovery=expected)


def _assert_terminal_by_code(exc: AdCPConfigurationError) -> None:
    """CONFIGURATION_ERROR / terminal — the operator-fault pair this suite is about."""
    _assert_wire_pair(exc, "CONFIGURATION_ERROR", pinned_recovery="terminal")


def _assert_transient_by_code(exc: AdCPServiceUnavailableError) -> None:
    """SERVICE_UNAVAILABLE / transient — what a status-less seam failure maps to.

    ``raise_mapped_mcp_error``'s no-recoverable-status arm — delegated to
    ``adcp_error_for_status``'s ``status is None`` branch
    (``src/core/helpers/outbound_error_mapping.py``) — is the one an
    ``MCPCompatibilityError`` reaches: nothing HTTP is wrapped beneath it. The
    pinned 3.1.1 ``enums/error-code.json`` classifies SERVICE_UNAVAILABLE as
    ``transient``; INTERNAL_ERROR — what an unclassified ``AttributeError``
    becomes two frames later — would misgrade an agent's bad answer as the
    seller's own bug.
    """
    _assert_wire_pair(exc, "SERVICE_UNAVAILABLE", pinned_recovery="transient")


_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"


@contextlib.contextmanager
def _stub_mcp_tool_result(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: Any = None,
    text: str | None = None,
):
    """Make the guarded seam hand a registry a successful tool result it must interpret.

    The raises under test sit AFTER a successful handshake and tool call, so
    reaching them over a real socket would mean speaking enough MCP to satisfy
    fastmcp — a fixture that would grade the protocol library, not the
    classification. The seam itself is graded over real sockets by the sibling
    classes in this file; here it is stubbed at exactly one point
    (``call_mcp_tool``, as ``call_operator_mcp_tool`` imports it) so the
    assertion is about what the registry does with an answer it cannot use.

    Stubbing the dial rather than ``call_operator_mcp_tool`` itself is what
    makes these tests grade anything: ``extract_tool_payload`` and BOTH error
    mappings live inside that function, and they are precisely the code under
    test here. One patch point now serves every operator path — creative and
    signals alike funnel through this single seam function, so the caller no
    longer picks a registry module.

    *payload* becomes ``structured_content``; *text* instead becomes a legacy
    ``TextContent`` block, which is the only way to reach the ``json.loads``
    branch of ``extract_tool_payload``.
    """
    content = [MagicMock(text=text)] if text is not None else []
    result = MagicMock(structured_content=payload, content=content)
    monkeypatch.setattr(_SEAM_DIAL, AsyncMock(return_value=result))
    yield


@contextlib.contextmanager
def _stub_mcp_client_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception):
    """Make the guarded seam fail the way it reports a status-bearing failure."""
    monkeypatch.setattr(_SEAM_DIAL, AsyncMock(side_effect=exc))
    yield


class TestOperatorCreativeAgentRedirectIsRefused:
    """The guarded MCP seam refuses a redirect the OPERATOR creative-agent path is served."""

    @pytest.mark.timeout(30)
    async def test_operator_creative_agent_redirect_to_metadata_is_not_followed(self, local_origin_tls, monkeypatch):
        """An operator ``CreativeAgent`` answering ``302 -> metadata stand-in`` never reaches it.

        ``local_origin_tls`` stands in for a tenant-configured creative agent (an
        operator agent, not a buyer-supplied URL). ``_fetch_formats_operator``
        dials through ``call_operator_mcp_tool`` onto ``call_mcp_tool``, which
        pins the connection and sets
        ``follow_redirects=False`` — the redirect is never followed
        (``metadata_standin.hits == 0``), matching the fixed MCP-seam behavior
        in ``test_mcp_client_egress.py``.
        """
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)

        with run_local_origin() as metadata_standin:
            metadata_standin.close_without_responding()
            local_origin_tls.redirect_to(metadata_standin.base_url, status=302)

            agent = CreativeAgent(agent_url=local_origin_tls.base_url, name="operator-creative-agent")
            registry = CreativeAgentRegistry()

            with pytest.raises(Exception):  # noqa: B017 - a 302 is not a valid MCP handshake either way
                await registry._fetch_formats_operator(agent)

            assert local_origin_tls.hits >= 1, "the operator agent_url was never dialed — the test graded nothing"
            assert metadata_standin.hits == 0, (
                f"the redirect to the metadata stand-in was followed unguarded: {metadata_standin.requests}"
            )


class TestOperatorSignalsAgentRedirectIsRefused:
    """The guarded MCP seam refuses a redirect the OPERATOR signals-agent path is served."""

    @pytest.mark.timeout(30)
    async def test_operator_signals_agent_redirect_to_metadata_is_not_followed(self, local_origin_tls, monkeypatch):
        """A tenant-configured ``SignalsAgent`` answering ``302 -> metadata stand-in`` never reaches it."""
        allow_local_origin(monkeypatch)
        fast_backoff(monkeypatch)

        with run_local_origin() as metadata_standin:
            metadata_standin.close_without_responding()
            local_origin_tls.redirect_to(metadata_standin.base_url, status=302)

            agent = SignalsAgent(agent_url=local_origin_tls.base_url, name="operator-signals-agent")
            registry = SignalsAgentRegistry()

            with pytest.raises(Exception):  # noqa: B017 - a 302 is not a valid MCP handshake either way
                await registry._fetch_signals_operator(agent, brief="test brief")

            assert local_origin_tls.hits >= 1, "the operator agent_url was never dialed — the test graded nothing"
            assert metadata_standin.hits == 0, (
                f"the redirect to the metadata stand-in was followed unguarded: {metadata_standin.requests}"
            )


class TestOperatorAgentFailureIsClassifiedTerminalByCode:
    """An operator-configured agent that fails reaches the buyer as CONFIGURATION_ERROR.

    The address of a signals or creative agent is OPERATOR configuration — a
    tenant row this deployment wrote, never something the buyer sent. So every
    way that dial can fail means the same thing to a buyer: nothing they can
    change will help, and a human at the seller has to look. The pinned 3.1.1
    ``enums/error-code.json`` gives that exactly one code —
    ``CONFIGURATION_ERROR``, whose ``enumMetadata`` recovery is ``terminal``
    ("surface to a human at the seller ... MUST NOT auto-retry").

    These grade that the intent is expressed by CHOOSING that code. The sites
    used to say it by hand-typing ``recovery="terminal"`` onto
    ``AdCPAdapterError``, whose wire code is ``SERVICE_UNAVAILABLE`` — which the
    same pinned table classifies ``transient``. A buyer classifying by code was
    told to retry forever; a buyer classifying by recovery was told to stop. The
    pair is asserted as one tuple here because that contradiction is precisely
    what a single-value assertion lets back in.
    """

    async def test_a_rejected_handshake_is_configuration_error(self, monkeypatch):
        """A terminal 4xx reported by the guarded seam -> CONFIGURATION_ERROR / terminal.

        Grades ``raise_mapped_mcp_error``'s terminal-4xx arm — delegated to
        ``adcp_error_for_status``'s ``400 <= status < 500`` branch
        (``src/core/helpers/outbound_error_mapping.py``) — on the signals path:
        "the endpoint this deployment is configured to use rejected us" is a
        deployment fault, not a buyer fault, and not a transient one — a 404 will
        be a 404 on the next attempt too.

        The failure is injected as the seam's OWN contract rather than by serving
        a 404 over a socket: fastmcp's handshake surfaces a plain "Session
        terminated" with no HTTP status attached, so a real 404 never reaches the
        status-bearing arm at all (verified — it lands in the unreachable arm and
        raises SERVICE_UNAVAILABLE). What the mapper actually keys on is a
        ``httpx.HTTPStatusError`` chained beneath the seam's exception, which is
        what ``wrapped_failure`` exists to recover; that chain is
        what is built here.
        """
        import httpx

        response = httpx.Response(404, request=httpx.Request("POST", "https://signals.operator.test/mcp"))
        wrapped = httpx.HTTPStatusError("404 Not Found", request=response.request, response=response)
        seam_error = MCPConnectionError("Failed to connect to MCP agent after 3 attempts")
        seam_error.__cause__ = wrapped

        agent = SignalsAgent(agent_url="https://signals.operator.test", name="operator-signals-agent")

        with _stub_mcp_client_raising(monkeypatch, seam_error):
            with pytest.raises(AdCPConfigurationError) as excinfo:
                await SignalsAgentRegistry()._fetch_signals_operator(agent, brief="test brief")

        _assert_terminal_by_code(excinfo.value)

    async def test_an_unparseable_payload_is_configuration_error(self, monkeypatch):
        """A handshake that succeeds but carries nothing parseable -> CONFIGURATION_ERROR / terminal.

        Grades ``signals_agent_registry``'s empty-payload raise. The agent is up
        and answering, so this is not a transient outage: it is answering with
        something that is not a ``get_signals`` response, which only the operator
        who registered it can resolve.
        """
        agent = SignalsAgent(agent_url="https://signals.operator.test", name="operator-signals-agent")

        with _stub_mcp_tool_result(monkeypatch, payload={}):
            with pytest.raises(AdCPConfigurationError) as excinfo:
                await SignalsAgentRegistry()._fetch_signals_operator(agent, brief="test brief")

        _assert_terminal_by_code(excinfo.value)

    async def test_a_schema_invalid_payload_is_configuration_error(self, monkeypatch):
        """A payload that parses as JSON but not as a GetSignalsResponse -> CONFIGURATION_ERROR / terminal.

        The sibling of the empty-payload case: the agent answered with structure
        the pinned response schema rejects. Same operator fault, same code — the
        two must not diverge, which is why both are graded.
        """
        agent = SignalsAgent(agent_url="https://signals.operator.test", name="operator-signals-agent")

        with _stub_mcp_tool_result(monkeypatch, payload={"signals": "not-a-list"}):
            with pytest.raises(AdCPConfigurationError) as excinfo:
                await SignalsAgentRegistry()._fetch_signals_operator(agent, brief="test brief")

        _assert_terminal_by_code(excinfo.value)


_OPERATOR_FETCHES: dict[str, Any] = {
    "creative-format-fetch": lambda: CreativeAgentRegistry()._fetch_formats_operator(
        CreativeAgent(agent_url="https://creative.operator.test", name="operator-creative-agent")
    ),
    "signals-fetch": lambda: SignalsAgentRegistry()._fetch_signals_operator(
        SignalsAgent(agent_url="https://signals.operator.test", name="operator-signals-agent"),
        brief="test brief",
    ),
}


class TestAPayloadThatIsNotAJSONObjectIsClassified:
    """An agent answering with something that is not a JSON object reaches the buyer CLASSIFIED.

    ``extract_tool_payload`` is annotated ``-> dict[str, Any]`` but returns
    whatever ``structured_content`` or ``json.loads`` produced. A creative agent
    answering with a JSON ARRAY therefore reaches ``payload.get("formats", [])``
    as a ``list``, and the ``AttributeError`` that follows is nobody's classified
    error — it surfaces two frames later as INTERNAL_ERROR, which the pinned
    3.1.1 taxonomy reserves for the seller's OWN bug. The origin's malformed
    answer is not the seller's bug.

    Both non-object shapes are graded, because both are the same fault class and
    the fix must not cover only one: ``structured_content`` carrying a JSON array
    (the isinstance hole), and a ``TextContent`` block whose ``.text`` is not
    valid JSON at all (``json.loads`` raising ``JSONDecodeError``, a bare
    ``ValueError`` that no ``except`` arm on either path catches).
    """

    @pytest.mark.parametrize("path", list(_OPERATOR_FETCHES), ids=list(_OPERATOR_FETCHES))
    async def test_a_json_array_payload_is_service_unavailable(self, monkeypatch, path):
        """``structured_content`` is a list, not an object -> SERVICE_UNAVAILABLE / transient."""
        call = _OPERATOR_FETCHES[path]

        with _stub_mcp_tool_result(monkeypatch, payload=[{"format_id": "display_300x250"}]):
            with pytest.raises(AdCPServiceUnavailableError) as excinfo:
                await call()

        _assert_transient_by_code(excinfo.value)

    @pytest.mark.parametrize("path", list(_OPERATOR_FETCHES), ids=list(_OPERATOR_FETCHES))
    async def test_a_text_block_that_is_not_json_is_service_unavailable(self, monkeypatch, path):
        """A ``TextContent`` block that will not parse -> SERVICE_UNAVAILABLE / transient.

        The sibling hole of the array case: ``json.loads`` raises
        ``JSONDecodeError`` from inside ``extract_tool_payload``, so the
        annotation is untrue on this return path too, and the raise is equally
        unclassified.
        """
        call = _OPERATOR_FETCHES[path]

        with _stub_mcp_tool_result(monkeypatch, text="not json{"):
            with pytest.raises(AdCPServiceUnavailableError) as excinfo:
                await call()

        _assert_transient_by_code(excinfo.value)


_CREATIVE_AGENT_TOOLS: dict[str, Any] = {
    "preview_creative": lambda registry: registry.preview_creative(
        agent_url="https://creative.operator.test",
        format_id="display_300x250",
        creative_manifest={"creative_id": "c123", "name": "Banner Ad", "assets": {}},
    ),
    "build_creative": lambda registry: registry.build_creative(
        agent_url="https://creative.operator.test",
        format_id="display_300x250_generative",
        message="a creative brief",
        gemini_api_key="test-gemini-key",
    ),
}


class TestPreviewAndBuildDoNotLeakAnUnclassifiedSeamFailure:
    """``preview_creative``/``build_creative`` classify a seam failure at the REGISTRY boundary.

    Deliberately graded here rather than at the wire. All four callers of these
    two methods live in ``src/core/tools/creatives/_processing.py`` inside a
    ``try`` whose blanket ``except Exception`` already collapses today's
    unclassified failure into a per-item ``SERVICE_UNAVAILABLE`` result — and
    would collapse the classified one into the same place. No wire-level
    envelope test can distinguish before from after, so an envelope test here
    would be a green mark that grades nothing. What IS observable, and is the
    whole obligation for these two methods, is that the registry no longer hands
    its callers a raw ``AttributeError``/``JSONDecodeError``: it raises this
    application's own taxonomy, with the pair a buyer would be told.
    """

    @pytest.mark.parametrize("tool", list(_CREATIVE_AGENT_TOOLS), ids=list(_CREATIVE_AGENT_TOOLS))
    async def test_a_non_object_payload_raises_a_classified_error(self, monkeypatch, tool):
        """A JSON array from the creative agent -> SERVICE_UNAVAILABLE / transient, not a raw builtin."""
        call = _CREATIVE_AGENT_TOOLS[tool]

        with _stub_mcp_tool_result(monkeypatch, payload=["not", "an", "object"]):
            with pytest.raises(AdCPServiceUnavailableError) as excinfo:
                await call(CreativeAgentRegistry())

        _assert_transient_by_code(excinfo.value)

    @pytest.mark.parametrize("tool", list(_CREATIVE_AGENT_TOOLS), ids=list(_CREATIVE_AGENT_TOOLS))
    async def test_an_unparseable_text_block_raises_a_classified_error(self, monkeypatch, tool):
        """A ``TextContent`` block that will not parse -> SERVICE_UNAVAILABLE / transient."""
        call = _CREATIVE_AGENT_TOOLS[tool]

        with _stub_mcp_tool_result(monkeypatch, text="not json{"):
            with pytest.raises(AdCPServiceUnavailableError) as excinfo:
                await call(CreativeAgentRegistry())

        _assert_transient_by_code(excinfo.value)
