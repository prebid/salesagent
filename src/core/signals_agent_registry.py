"""Signals Agent Registry for upstream signals discovery integration.

This module provides:
1. Signals agent registry (tenant-specific agents)
2. Dynamic signals discovery over the guarded MCP seam
3. Multi-agent support for different signals providers

Architecture:
- No default agent (tenant-specific only)
- Tenant agents: Configured in signals_agents database table
- Signals resolution: Query agents via ``call_operator_mcp_tool``, handle responses

Schema Version: AdCP v2.2.0
- Uses signal_spec (not brief from v1)
- Uses deliver_to.platforms as array of strings ["all"] (not single string "all")
- Supports custom auth headers via auth_header parameter

Security:
- Auth credentials stored in database (tenant-specific)
- Custom auth headers supported (e.g., Authorization, x-api-key)
- Bearer token format: "Bearer {token}"
- Token format: "{token}"

Transport and signing, in one sentence: EVERY dial leaves through the SSRF-guarded
MCP seam, and every dial a tenant's posture says may carry an RFC 9421 signature
carries one. Those used to be mutually exclusive here -- the only way to sign an
outbound agent call was to build an ``adcp.ADCPMultiAgentClient``, which owns its own
httpx stack and dials un-pinned (``adcp==6.6.0`` exposes no transport injection
point), so a signed call was necessarily an unguarded one. PR #1802 moved the dial
onto :func:`~src.core.utils.operator_mcp.call_operator_mcp_tool`, and
:func:`~src.core.utils.mcp_client.call_mcp_tool`'s ``sign=`` hook then made the
signature computable INSIDE that seam, over the exact bytes httpx is about to
transmit, on every JSON-RPC message and every retry. Neither property is now bought
with the other; see :func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant`
-- the ONE home of that gate, shared with ``CreativeAgentRegistry`` -- for which
tenants sign.
"""

import logging
from dataclasses import dataclass
from typing import Any

from adcp.types import GetSignalsResponse as LibraryGetSignalsResponse
from pydantic import ValidationError

from src.core.database.models import SignalsAgent as DBSignalsAgent
from src.core.exceptions import AdCPConfigurationError

# The ONE home of the outbound request-signing posture gate, shared with
# CreativeAgentRegistry. Both registries used to re-derive it locally and they
# had already drifted apart: one folded an unreadable private half into "does
# not sign" and dialled unsigned, the other raised. Two copies of a security
# gate mean a fix to one is a fix to one (CLAUDE.md DRY invariant), so the
# decision lives in adapter_helpers and nothing here re-asks it.
#
# Imported from the helper, not from the signing layer: ``src.core.signing``'s
# ``__init__`` re-exports nothing, so every consumer reaches a submodule by its
# dotted path and the signing layer's own ORM imports stay function-local.
from src.core.helpers.adapter_helpers import request_signer_for_tenant
from src.core.schemas import GetSignalsRequest
from src.core.utils.operator_mcp import ProbeResult, call_operator_mcp_tool, probe_failure

logger = logging.getLogger(__name__)


@dataclass
class SignalsAgent:
    """Represents a signals discovery agent that provides product enhancement via signals.

    Note: priority, max_signal_products, and fallback_to_database are configured per-product,
    not per-agent.

    ``tenant_id`` is carried on the DIAL CONFIG rather than threaded through
    :meth:`SignalsAgentRegistry._fetch_signals_operator` as a parameter, because it
    answers a question about the config and not about one call: which tenant's key signs
    a request to this agent. Every config production builds comes from
    :meth:`SignalsAgentRegistry.config_for`, which reads it off the stored row, so both
    entry points -- the discovery path and the operator's probe -- sign identically and a
    third one cannot be written that forgets to pass it.
    """

    agent_url: str
    name: str
    enabled: bool = True
    auth: dict[str, Any] | None = None  # Optional auth config for private agents
    auth_header: str | None = None  # HTTP header name for auth (e.g., "Authorization", "x-api-key")
    forward_promoted_offering: bool = True
    timeout: int = 30
    tenant_id: str | None = None  # Whose signing key signs the dial; None -> unsigned


class SignalsAgentRegistry:
    """Registry of signals discovery agents with dynamic discovery.

    Usage:
        registry = SignalsAgentRegistry()

        # Get signals from all agents
        signals = await registry.get_signals(
            brief="automotive targeting",
            tenant_id="tenant_123",
            promoted_offering="Tesla Model 3"
        )
    """

    def __init__(self):
        """Initialize registry."""
        pass  # No cache needed - the MCP seam owns connection lifetime per dial

    def _get_tenant_agents(self, tenant_id: str) -> list[SignalsAgent]:
        """Get list of signals agents for a tenant.

        Returns:
            List of SignalsAgent instances (tenant-specific only)
        """
        # Annotated because the list is now filled by extend() from a generator:
        # the old append-in-a-loop gave mypy an element type to infer, and extend
        # does not.
        agents: list[SignalsAgent] = []

        # Load tenant-specific agents from database
        from src.core.database.database_session import get_db_session
        from src.core.database.repositories.agent import SignalsAgentRepository

        with get_db_session() as session:
            db_agents = SignalsAgentRepository(session, tenant_id).get_enabled()

            agents.extend(self.config_for(db_agent) for db_agent in db_agents)

        # Sort by name for consistent ordering
        agents.sort(key=lambda a: a.name)
        return [a for a in agents if a.enabled]

    async def _fetch_signals_operator(self, agent: SignalsAgent, brief: str) -> list[dict[str, Any]]:
        """Fetch signals from an OPERATOR-configured signals agent, through the guarded MCP seam.

        Routes through ``call_operator_mcp_tool`` — a real MCP handshake, IP-pinned,
        redirect-refusing — rather than ``adcp.ADCPMultiAgentClient``, whose own
        httpx stack no egress policy of ours could reach (adcp 6.6.0 exposes no
        transport injection point; upstream adcp-client-python#1004). Closes the
        gap tracked by salesagent-4n88. Signals agents are ALWAYS
        operator-configured (tenant DB rows) — there is no counterparty-supplied
        signals URL, so every call here takes this path.

        The signature rides that same seam rather than beside it: ``sign=`` is applied
        as an httpx request hook INSIDE the pinned transport, so the bytes signed are
        the bytes transmitted and a signed dial keeps every guard an unsigned one has
        (#1291 C3). It is resolved BEFORE the dial, so a signing-capable tenant whose
        material cannot be loaded raises here with nothing sent, rather than degrading
        to an unsigned request (see
        :func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant`).

        Spec grounding for signing THIS dial: pinned AdCP 3.1.1,
        ``docs/building/by-layer/L1/security.mdx`` §Request signing (the
        ``adcp/request-signing/v1`` profile). :1043 restricts the operation a
        signature may be attributed to to AdCP protocol operation names;
        ``get_signals`` -- the only operation this module dials -- is one
        (``sdk_operation_names()`` cross-check), which is why signing this dial is
        legal at all. Graded by the conformance storyboard only for the VERIFY
        direction; the emit direction here is ungraded.

        The MCP protocol path only ever returns COMPLETED or FAILED (never
        SUBMITTED — that status exists in the adcp SDK's abstraction for other
        protocols, not for a synchronous MCP tool call), so there is no webhook/
        async branch to preserve here.

        Args:
            agent: SignalsAgent to query
            brief: Search brief/query (mapped onto AdCP's ``signal_spec``)

        Returns:
            List of signal dicts from the agent

        Raises:
            AdCPConfigurationError: the tenant is signing-capable but its key material
                cannot be loaded (raised before the dial), egress policy refuses the
                configured endpoint, the seam rejects us during the handshake, or the
                agent answers with nothing parseable.
        """
        import time

        start_time = time.time()

        sign = request_signer_for_tenant(tenant_id=agent.tenant_id)

        request = GetSignalsRequest(signal_spec=brief)
        args = request.model_dump(mode="json", exclude_none=True)

        logger.info(f"[TIMING] Calling agent {agent.name} ({'signed' if sign else 'unsigned'}), brief: {brief[:50]}...")
        payload = await call_operator_mcp_tool(
            agent.agent_url,
            "get_signals",
            args,
            label=f"signals agent {agent.name}",
            auth=agent.auth,
            auth_header=agent.auth_header,
            timeout=agent.timeout,
            sign=sign,
        )

        if not payload:
            # An empty payload means neither structured_content nor a TextContent
            # block carried anything parseable. GetSignalsResponse.model_validate({})
            # would otherwise validate CLEANLY with signals=None — every field is
            # optional — silently producing signals=[] and masking a genuine
            # agent failure as "agent up, 0 signals" (salesagent-9eu class bug).
            raise AdCPConfigurationError()
        try:
            parsed = LibraryGetSignalsResponse.model_validate(payload)
        except ValidationError as e:
            raise AdCPConfigurationError(internal_detail=e) from e

        signals = parsed.signals or []
        total_duration = time.time() - start_time
        logger.info(f"[TIMING] Got {len(signals)} signals in {total_duration:.2f}s")
        return [signal if isinstance(signal, dict) else signal.model_dump(mode="json") for signal in signals]

    async def get_signals(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Get signals from all registered agents for a tenant.

        Args:
            brief: Search brief/query
            tenant_id: Tenant identifier
            principal_id: Optional principal identifier
            principal_data: Optional principal information

        Returns:
            List of all signal objects across all agents
        """
        agents = self._get_tenant_agents(tenant_id)
        all_signals: list[dict[str, Any]] = []

        logger.info(f"get_signals: Found {len(agents)} agents for tenant {tenant_id}")

        if not agents:
            return all_signals

        for agent in agents:
            logger.info(f"get_signals: Fetching from {agent.agent_url}")
            try:
                signals = await self._fetch_signals_operator(agent, brief=brief)
                logger.info(f"get_signals: Got {len(signals)} signals from {agent.agent_url}")
                all_signals.extend(signals)
            except Exception as e:
                # Log error but continue with other agents (graceful degradation)
                logger.error(f"Failed to fetch signals from {agent.agent_url}: {e}", exc_info=True)
                continue

        logger.info(f"get_signals: Returning {len(all_signals)} total signals")
        return all_signals

    @staticmethod
    def config_for(db_agent: DBSignalsAgent) -> SignalsAgent:
        """The ONE place a stored signals-agent row becomes a dial config.

        Mirrors :meth:`CreativeAgentRegistry.config_for` for the same reason: a
        probe that rebuilds this mapping by hand dials with a different config
        than production, so a passing probe proves nothing about the path that
        runs. The stored ``timeout`` is read here rather than hard-coded to 30,
        which is what the hand-built version discarded.

        ``tenant_id`` is read here for the same reason and with an extra one: it
        selects the signing key, so a config built without it would dial UNSIGNED
        while the production config for the same row dialled signed — the probe
        would then be green on a path nobody runs. Reading it here makes that
        divergence unrepresentable.
        """
        auth = None
        if db_agent.auth_type and db_agent.auth_credentials:
            auth = {"type": db_agent.auth_type, "credentials": db_agent.auth_credentials}
        return SignalsAgent(
            agent_url=db_agent.agent_url,
            name=db_agent.name,
            enabled=db_agent.enabled,
            auth=auth,
            auth_header=db_agent.auth_header,
            # Was passed by _get_tenant_agents' inline mapping and omitted here, so
            # the two disagreed for any row where an operator turned it off: this
            # function fell back to the field default (True). Nothing in src/ reads
            # the field yet, so no dial behaviour differed -- the inline block was
            # the ONLY carrier of the stored value, and dropping it without adding
            # it here would have lost the column's meaning entirely.
            forward_promoted_offering=db_agent.forward_promoted_offering,
            timeout=db_agent.timeout,
            tenant_id=db_agent.tenant_id,
        )

    async def probe_agent(self, db_agent: DBSignalsAgent) -> ProbeResult:
        """Dial a stored signals agent exactly as production dials it.

        The public entry point for the operator's test-connection button, with
        the same shape as the creative registry's: the route holds a row, and
        everything between that row and the dial belongs to this class.

        "Exactly as production" now includes the signature: the config comes from
        :meth:`config_for`, so the probe signs with the same key on the same
        posture. An operator whose signing key is broken therefore learns it from
        this button — :func:`~src.core.utils.operator_mcp.probe_failure` renders the
        raise as a failed probe — instead of the probe passing unsigned while every
        real dial fails.
        """
        agent = self.config_for(db_agent)
        try:
            signals = await self._fetch_signals_operator(agent, brief="test")
        except Exception as exc:  # noqa: BLE001 - an operator probe reports every failure, it never 500s
            return probe_failure(exc, logger=logger)

        return ProbeResult(
            ok=True,
            message="Successfully connected to signals agent",
            count=len(signals),
        )


# Global registry instance
_registry: SignalsAgentRegistry | None = None


def get_signals_agent_registry() -> SignalsAgentRegistry:
    """Get the global signals agent registry instance."""
    global _registry
    if _registry is None:
        _registry = SignalsAgentRegistry()
    return _registry
