"""Creative Agent Registry for dynamic format discovery per AdCP v2.4.

This module provides:
1. Creative agent registry (system defaults + tenant-specific)
2. Dynamic format discovery via MCP
3. Format caching (in-memory with TTL)
4. Multi-agent support for DCO platforms, custom creative agents

Architecture:
- Default agent: https://creative.adcontextprotocol.org (always available)
- Tenant agents: Configured in creative_agents database table
- Format resolution: Query agents via MCP, cache results
- Preview generation: Delegate to creative agent
- Generative creative: Use agent's create_generative_creative tool

Testing:
- When ADCP_TESTING=true, returns mock formats instead of calling external services
- This avoids timeouts in CI when external creative agents are unreachable
"""

import copy
import logging
import typing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# FIXME(#1388): ListCreativeFormatsRequest has a local subclass; import from src.core.schemas (Pattern #7/#4).
from adcp import ListCreativeFormatsRequest
from adcp.types import AssetContentType as AssetType
from pydantic import ValidationError

from src.core.config import get_settings
from src.core.database.models import CreativeAgent as DBCreativeAgent
from src.core.errors.codes import AppErrorCode
from src.core.exceptions import AdCPConfigurationError, AdCPValidationError
from src.core.format_cache import load_reference_formats

# The ONE home of the outbound request-signing posture gate, shared with
# SignalsAgentRegistry. This module used to re-derive it locally, and its local
# copy had DRIFTED: it spelled the key-presence half
# ``signing_key_backed(repo, now=now).signs``, which folds an unreadable private
# half into ``False`` and dialled unsigned where the signals registry raised.
# Three copies of a security gate mean a fix to one is a fix to one (CLAUDE.md
# DRY invariant).
from src.core.helpers.adapter_helpers import request_signer_for_tenant
from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error
from src.core.schemas import Error as AdCPResponseError
from src.core.schemas import Format, FormatId, canonical_agent_url
from src.core.security.outbound_http import (
    OutboundError,
    UrlProvenance,
    asend,
    is_counterparty,
    refusal_field,
)
from src.core.utils.operator_mcp import ProbeResult, call_operator_mcp_tool, probe_failure

logger = logging.getLogger(__name__)


def _known_asset_types() -> frozenset[str]:
    """Asset-type Literals adcp's closed discriminated union models.

    Derived dynamically from the adcp schema (not hardcoded) so the tolerant
    ingestion stays correct across adcp version bumps — what counts as
    "additive" is always "not in the union the pinned library knows about".
    """
    literals: set[str] = set()
    assets_field = Format.model_fields["assets"].annotation
    # annotation shape: list[Union[ImageFormatAsset, VideoFormatAsset, ...]] | None
    for outer in typing.get_args(assets_field):
        for inner in typing.get_args(outer):  # the Union inside list[...]
            for branch in typing.get_args(inner):
                asset_type_field = getattr(branch, "model_fields", {}).get("asset_type")
                if asset_type_field is not None:
                    literals.update(typing.get_args(asset_type_field.annotation))
    return frozenset(literals)


_KNOWN_ASSET_TYPES = _known_asset_types()


def _unknown_asset_types(fmt_data: dict[str, Any]) -> set[str]:
    """Asset-type values in a format dict that adcp's closed union does not model."""
    unknown: set[str] = set()
    for asset in fmt_data.get("assets") or []:
        if isinstance(asset, dict):
            asset_type = asset.get("asset_type")
            if isinstance(asset_type, str) and asset_type not in _KNOWN_ASSET_TYPES:
                unknown.add(asset_type)
    return unknown


def _is_purely_additive_asset_type(fmt_data: dict[str, Any], unknown_types: set[str]) -> bool:
    """True iff the ONLY reason fmt_data fails validation is an unknown additive asset_type.

    Strategy (value-agnostic, robust to adcp's many-branched discriminated union):
    substitute every unknown asset_type with a known sentinel and re-validate. If
    it then validates cleanly, the format is well-formed apart from AdCP-additive
    enum growth → safe to drop. If it still fails, there is a genuine structural
    defect (or a mixed error) that must NOT be masked.
    """
    if not unknown_types:
        return False
    patched = copy.deepcopy(fmt_data)
    for asset in patched.get("assets") or []:
        if isinstance(asset, dict) and asset.get("asset_type") in unknown_types:
            asset["asset_type"] = "image"  # known sentinel branch
    try:
        Format.model_validate(patched)
    except ValidationError:
        return False
    return True


def _validate_formats_tolerant(format_dicts: list[dict[str, Any]], logger: logging.Logger) -> list[Format]:
    """Validate formats independently; tolerate ONLY AdCP-additive asset_type growth.

    Postel / asymmetric strictness: strict on what we emit (adcp Literal untouched),
    liberal on peer responses. A format whose sole defect is an unrecognized additive
    asset_type is DROPPED (never mis-represented — we never model a creative we
    cannot fully understand), aggregated into ONE structured WARNING. Any other
    ValidationError still fails LOUD so real contract breakage is not masked.
    """
    validated: list[Format] = []
    skipped_count = 0
    skipped_asset_types: set[str] = set()
    for fmt_data in format_dicts:
        try:
            validated.append(Format.model_validate(fmt_data))
        except ValidationError:
            unknown_types = _unknown_asset_types(fmt_data)
            if unknown_types and _is_purely_additive_asset_type(fmt_data, unknown_types):
                skipped_count += 1
                skipped_asset_types.update(unknown_types)
                continue
            raise
    if skipped_count:
        logger.warning(
            "Skipped %d creative format(s) using unsupported additive asset_type(s) %s "
            "(not modeled by the pinned adcp schema); returning the %d compatible format(s).",
            skipped_count,
            sorted(skipped_asset_types),
            len(validated),
        )
    return validated


@dataclass
class FormatFetchResult:
    """Result from list_all_formats_with_errors() — formats + per-agent errors.

    Decision: docs/design/error-propagation-in-format-discovery.md
    """

    formats: list[Format]
    errors: list[AdCPResponseError]
    #: NON-WIRE. The agent_urls whose fetch failed, in the SAME ORDER as
    #: ``errors`` -- entry i produced errors[i]. Never serialized: it exists so
    #: the boundary can decide the CODE, which it can only do by comparing the
    #: failed agent against what the REQUEST referenced, and the registry has no
    #: access to the request. The advisory itself deliberately does not name the
    #: agent (every wire field is client-facing -- see the comment at the append
    #: site), so this is the only channel that carries the correlation.
    #:
    #: Per list_creative_formats.mdx:654, a format_id REFERENCING an unavailable
    #: agent must be REFERENCE_NOT_FOUND with error.field naming the typed
    #: parameter, not the AGENT_UNREACHABLE advisory that covers the
    #: seller-aggregation branch (salesagent-3dawm.16).
    failed_agent_urls: list[str] = field(default_factory=list)


def _get_reference_formats() -> list[Format]:
    """Return the reference formats served in testing mode (ADCP_TESTING=true).

    These are loaded from the checked-in fixture
    (tests/fixtures/creative_formats/reference_formats.json) captured from the
    pinned reference creative agent (pin: ADCP_PIN in
    scripts/creative-agent-stack.sh). Reading from the fixture
    — rather than a hand-maintained list — is what keeps the in-process harness
    and the e2e server serving identical formats by construction, with no risk of
    silent drift from the real agent.

    Refresh the fixture with `make creative-formats-refresh` when the pin or the
    agent's catalog changes. See salesagent issue #1418.
    """
    return list(load_reference_formats())


# Canonical URL of the AdCP standard creative agent. This is the FEDERATION
# IDENTITY half of a format reference — (agent_url, id) — and must stay stable
# across deployments; it is NOT necessarily where connections go (see
# _connection_agent_url).
PUBLIC_DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def _is_operator_agent(agent_url: str) -> bool:
    """Whether *agent_url* names an agent THIS DEPLOYMENT stands behind.

    A buyer naming the seller's own default agent — the overwhelmingly common
    case — is not choosing a destination; they are echoing back the one we
    published. So the testing short-circuit still applies to it, exactly as
    before, and CI does not start dialling on ordinary traffic.

    Learned the hard way: bypassing the short-circuit for EVERY buyer-supplied
    url made the e2e stack fetch for real on routine update_media_buy traffic,
    and an unrelated scenario failed on the seam's delivery error. The narrow
    rule keeps the short-circuit where it was designed to help while still
    letting a genuinely foreign destination reach the seam and be refused.
    """
    known = {canonical_agent_url(PUBLIC_DEFAULT_AGENT_URL)}
    configured = get_settings().integrations.creative_agent_url
    if configured:
        known.add(canonical_agent_url(configured))
    return canonical_agent_url(agent_url) in known


def _connection_agent_url(agent_url: str) -> str:
    """Resolve the TRANSPORT url for an agent reference.

    When CREATIVE_AGENT_URL is set (test/CI stacks run a pinned container
    serving the standard catalog), references to the PUBLIC default agent
    connect there instead of the live public host — which rate-limits under
    CI load and must never be a test dependency (the catalog also drifts).
    Only the connection reroutes: cache keys and format_id federation
    identity stay on the canonical url. Non-default agents are untouched.
    """
    configured = get_settings().integrations.creative_agent_url
    if not configured:
        return agent_url
    if canonical_agent_url(agent_url) != canonical_agent_url(PUBLIC_DEFAULT_AGENT_URL):
        return agent_url
    if canonical_agent_url(configured) == canonical_agent_url(agent_url):
        return agent_url
    return configured


# The format-discovery filter parameter names shared by every layer that
# forwards them (this module's own internal calls, format_resolver.py,
# admin/blueprints/products.py). Named once so a call site building the
# forwarding kwargs can do it from this tuple + locals() instead of writing
# out "max_width=max_width, max_height=max_height, ..." again — the literal
# repetition is exactly what pylint's R0801 (and CLAUDE.md's DRY invariant)
# flag as duplicated code once it appears at more than one call site.
_FORMAT_FILTER_FIELDS = (
    "max_width",
    "max_height",
    "min_width",
    "min_height",
    "is_responsive",
    "asset_types",
    "name_search",
)


@dataclass
class CreativeAgent:
    """Represents a creative agent that provides format definitions and creative services."""

    agent_url: str
    name: str
    enabled: bool = True
    priority: int = 1  # Lower = higher priority in search results
    auth: dict[str, Any] | None = None  # Optional auth config for private agents
    auth_header: str | None = None  # Optional custom auth header name
    timeout: int = 30  # Request timeout in seconds


@dataclass
class CachedFormats:
    """Cached format list from a creative agent."""

    formats: list[Format]
    fetched_at: datetime
    ttl_seconds: int = 3600  # 1 hour default

    def is_expired(self) -> bool:
        """Check if cache has expired."""
        return datetime.now(UTC) > self.fetched_at + timedelta(seconds=self.ttl_seconds)


class CreativeAgentRegistry:
    """Registry of creative agents with dynamic format discovery and caching.

    Usage:
        registry = CreativeAgentRegistry()

        # Get all formats from all agents
        formats = await registry.list_all_formats(tenant_id="tenant_123")

        # Search formats across agents
        results = await registry.search_formats(query="300x250", tenant_id="tenant_123")

        # Get specific format
        fmt = await registry.get_format(
            agent_url="https://creative.adcontextprotocol.org",
            format_id="display_300x250_image"
        )
    """

    def __init__(self):
        """Initialize registry with empty cache and the deployment's default agent."""
        self._format_cache: dict[str, CachedFormats] = {}  # Key: normalized agent_url
        # Default creative agent (always available). agent_url is the base URL; the MCP
        # endpoint (/mcp) is appended by the client. CREATIVE_AGENT_URL lets CI point it at
        # a containerized agent.
        self.DEFAULT_AGENT = CreativeAgent(
            agent_url=get_settings().integrations.creative_agent_url or PUBLIC_DEFAULT_AGENT_URL,
            name="AdCP Standard Creative Agent",
            enabled=True,
            priority=1,
        )

    def _operator_formats_override(self) -> list[Format] | None:
        """What this registry serves for an OPERATOR agent instead of dialling it, or None.

        The live registry dials; :class:`ReferenceFormatsRegistry` answers with the
        checked-in reference catalog. A counterparty URL never reaches this: it is judged
        by the egress seam before the override is consulted.
        """
        return None

    @staticmethod
    def _cache_key(agent_url: str) -> str:
        """Canonicalize agent URL for consistent cache keys (RFC 3986).

        Delegates to ``schemas.canonical_agent_url`` so the format cache key and the
        format_id federation identity (``format_id_identity``) share one
        canonicalization (DRY) — a reference and its cached catalog can never
        disagree over trailing-slash/case/default-port noise.
        """
        return canonical_agent_url(agent_url)

    def _get_tenant_agents(self, tenant_id: str | None) -> list[CreativeAgent]:
        """Get list of creative agents for a tenant.

        Returns:
            List of CreativeAgent instances (default + tenant-specific)
        """
        agents = [self.DEFAULT_AGENT]

        if not tenant_id:
            return agents

        # Load tenant-specific agents from database
        from src.core.database.database_session import get_db_session
        from src.core.database.repositories.agent import CreativeAgentRepository

        with get_db_session() as session:
            db_agents = CreativeAgentRepository(session, tenant_id).get_enabled()

            # config_for, not a second mapping: the inline block here also wrote an
            # auth["header"] key with ZERO readers in src/, while auth_header was
            # already carried as its own named field on both sides. Adopting the one
            # mapping deletes the dead key for free.
            agents.extend(self.config_for(db_agent) for db_agent in db_agents)

        # Sort by priority (lower number = higher priority)
        agents.sort(key=lambda a: a.priority)
        return [a for a in agents if a.enabled]

    async def _fetch_formats_operator(
        self,
        agent: CreativeAgent,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Format]:
        """Fetch format list from an OPERATOR-configured creative agent, through the guarded MCP seam.

        Routes through ``call_operator_mcp_tool`` — a real MCP handshake, IP-pinned,
        redirect-refusing — rather than ``adcp.ADCPMultiAgentClient``, whose own
        httpx stack no egress policy of ours could reach (adcp 6.6.0 exposes no
        transport injection point; upstream adcp-client-python#1004).
        ``_connection_agent_url`` applies HERE
        (the pinned-container alias for the public default agent); a
        counterparty-supplied ``agent_url`` never reaches this method — see
        ``_fetch_formats_raw_mcp`` for that path, unchanged by this migration.

        The dial is SSRF-guarded and SIGNED, and those are no longer alternatives:
        ``sign=`` computes the RFC 9421 signature inside the seam's own pinned
        transport, over the exact bytes, target URI and headers httpx is about to
        transmit, on every JSON-RPC message and every retry. Before that parameter
        the only way to sign was to hand key material to an SDK client that dialled
        for itself, so "signed" and "guarded" could not both hold (#1291 C3 vs
        GH #1802); :func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant`
        decides WHETHER this tenant signs and
        raises rather than downgrade, so this call site states what it wants signed
        and never how.

        Spec grounding for signing THIS dial: pinned AdCP 3.1.1,
        ``docs/building/by-layer/L1/security.mdx`` §Request signing (the
        ``adcp/request-signing/v1`` profile). :1043 restricts the operation a
        signature may be attributed to to names "defined by the AdCP protocol
        spec"; ``list_creative_formats`` is one — it has a task reference at
        ``creative/task-reference/list_creative_formats.mdx`` and appears in
        ``sdk_operation_names()`` (the cross-check leg). That is what makes signing
        this dial legal at all. Note the predicate is NOT
        ``src.core.signing.vocabulary.is_adcp_operation``: that one answers "does
        THIS SELLER implement it", which is the inbound question. Graded by the
        conformance storyboard only for the VERIFY direction; the emit direction
        here is ungraded.

        Args:
            agent: CreativeAgent to query (operator-configured — a tenant DB row
                or the built-in default agent, never a buyer-supplied URL)
            max_width: Maximum width in pixels (inclusive)
            max_height: Maximum height in pixels (inclusive)
            min_width: Minimum width in pixels (inclusive)
            min_height: Minimum height in pixels (inclusive)
            is_responsive: Filter for responsive formats
            asset_types: Filter by asset types
            name_search: Search by name
            tenant_id: Whose signing posture governs this dial. ``None`` — a caller
                with no tenant in scope — dials unsigned, exactly as before.

        Returns:
            List of Format objects from the agent

        Raises:
            AdCPConfigurationError: the tenant is signing-capable but its key material
                cannot be loaded (raised before the dial), egress policy refuses the
                configured endpoint, the seam rejects us during the handshake, or the
                agent answers with nothing parseable.
        """
        typed_asset_types: list[AssetType] | None = None
        if asset_types:
            typed_asset_types = [AssetType(at) for at in asset_types]

        request = ListCreativeFormatsRequest(
            max_width=max_width,
            max_height=max_height,
            min_width=min_width,
            min_height=min_height,
            is_responsive=is_responsive,
            asset_types=typed_asset_types,
            name_search=name_search,
        )
        args = request.model_dump(mode="json", exclude_none=True)

        # Resolved BEFORE the dial: the signing session is opened, read and closed
        # by request_signer_for_tenant, so no pooled connection is parked on an
        # agent's latency.
        payload = await call_operator_mcp_tool(
            _connection_agent_url(agent.agent_url),
            "list_creative_formats",
            args,
            label=f"creative agent {agent.name}",
            auth=agent.auth,
            auth_header=agent.auth_header,
            timeout=agent.timeout,
            sign=request_signer_for_tenant(tenant_id=tenant_id),
        )
        if "formats" not in payload:
            # Nothing parseable came back: ``extract_tool_payload`` answers ``{}``
            # when neither structured_content nor a TextContent block carried a JSON
            # object, and an agent that answered with an object lacking ``formats``
            # did not answer ``list_creative_formats``. Reading ``.get("formats", [])``
            # over either turns a dead agent into "agent up, 0 formats" — the exact
            # bug prebid/salesagent#1136 filed and tests/unit/test_silent_empty_format_bug.py
            # guards. Mirrors the signals twin's ``if not payload`` raise, including its
            # vocabulary: post-ADR-010 an AdCPSalesAgentError takes NO ``message``, and
            # neither ``agent.name`` nor ``agent.agent_url`` may be interpolated into
            # buyer-facing text anyway (transport-errors.mdx § Security Considerations --
            # the same reason ``_parse_mcp_tool_result`` below raises bare). The
            # operator-readable sentence is the log line; nothing on ``ConfigurationDetails``
            # names "the agent answered the wrong shape", so ``details`` stays unset
            # rather than being filled with a field that means something else
            # (``mcp_tool_payload._require_json_object`` made the same call).
            logger.error(
                "list_creative_formats: no parseable payload from operator agent %s (%s)",
                agent.name,
                agent.agent_url,
            )
            raise AdCPConfigurationError()
        return _validate_formats_tolerant(payload["formats"], logger)

    @staticmethod
    def config_for(db_agent: DBCreativeAgent) -> CreativeAgent:
        """The ONE place a stored creative-agent row becomes a dial config.

        The admin's test-connection route used to rebuild this mapping by hand
        and, doing so, dropped ``auth_header`` and ``timeout`` -- so the operator's
        probe dialled with different auth and a different timeout than production
        did, and a probe that passed proved nothing about the path that runs. A
        second hand-written mapping is what made that possible; there is now one.
        """
        auth = None
        if db_agent.auth_type and db_agent.auth_credentials:
            auth = {"type": db_agent.auth_type, "credentials": db_agent.auth_credentials}
        return CreativeAgent(
            agent_url=db_agent.agent_url,
            name=db_agent.name,
            enabled=db_agent.enabled,
            priority=db_agent.priority,
            auth=auth,
            auth_header=db_agent.auth_header,
            timeout=db_agent.timeout,
        )

    async def probe_agent(self, db_agent: DBCreativeAgent) -> ProbeResult:
        """Dial a stored creative agent exactly as production dials it.

        The public entry point for the operator's test-connection button. It
        exists so the admin route has no reason to reach into a private fetch
        method: the route holds a database row, and everything between that row
        and the dial -- the config mapping, the operator provenance, both error
        vocabularies -- is this class's business, not a blueprint's.

        "Exactly as production dials it" now includes the SIGNATURE: the row carries
        its own ``tenant_id``, so the probe signs with the same key the same tenant's
        production fetch signs with. A probe that dialled unsigned against a
        counterparty enforcing RFC 9421 would pass while production failed -- the
        same class of lie that dropping ``auth_header`` and ``timeout`` produced.
        """
        agent = self.config_for(db_agent)
        try:
            formats = await self._fetch_formats_operator(agent, tenant_id=db_agent.tenant_id)
        except Exception as exc:  # noqa: BLE001 - an operator probe reports every failure, it never 500s
            return probe_failure(exc, logger=logger)

        if not formats:
            return ProbeResult(ok=False, message="Agent returned no formats")

        return ProbeResult(
            ok=True,
            message=f"Successfully connected to '{agent.name}'",
            count=len(formats),
            samples=tuple(f.name for f in formats[:5]),
        )

    async def _fetch_formats_raw_mcp(self, agent: CreativeAgent, *, provenance: UrlProvenance) -> list[Format]:
        """Fetch formats through the EGRESS SEAM, as a raw MCP tools/call.

        Its one caller today is every fetch of a COUNTERPARTY-SUPPLIED
        ``agent_url`` (always a :class:`CounterpartyUrl`), because the SDK client
        dials through its own httpx stack that no policy of ours can reach —
        adcp 6.6.0 exposes no transport knob (upstream adcp-client-python#1004).
        Measured: a buyer URL sent down the SDK path put 3 real requests on the
        wire against a destination egress policy forbids. Here the seam owns
        address, TLS, redirect and retry, so the request either goes to an
        allowed destination or never leaves. ``provenance`` is required — not
        optional — because it is the only thing that decides how a refusal is
        reported: :func:`raise_mapped_outbound_error` re-raises a
        ``CounterpartyUrl`` refusal unchanged (the seam's own
        ``OutboundRequestBlocked`` is already VALIDATION_ERROR / correctable,
        naming the field when there is one) and classifies an
        ``OperatorEndpoint`` refusal as CONFIGURATION_ERROR / terminal — the
        buyer did not choose that address and cannot fix it.

        The adcp SDK 3.6.0 requires structuredContent in MCP responses, but some
        creative agents return TextContent with JSON. This method calls the MCP
        endpoint directly via HTTP and parses the JSON response.

        Deliberately UNSIGNED, and that is a posture, not an oversight. Two reasons,
        either sufficient: no tenant is in scope on this path, and ``asend``'s
        ``sign=`` hook is a :class:`~src.core.security.outbound_http.SignAttempt`
        — ``(method, url, body)``, with no ``headers`` — while the RFC 9421 request
        profile must cover the request's REAL headers or produce a signature no
        verifier can reconstruct (``adcp.signing.verifier._check_components``).
        Adapting one to the other would mean signing headers that never ship, which
        is worse than sending none.
        """
        import json

        # The DESTINATION is the URL the operator registered, not its canonical identity
        # form. `canonical_agent_url` answers "are these the same agent?" -- it applies the
        # spec's comparison algorithm, which among other things renders an empty path as
        # "/" (step 5), so building a path onto its output yields `https://agent//mcp`.
        # Identity and destination are different questions and this one is the destination.
        agent_url = str(agent.agent_url).rstrip("/")
        # MCP endpoint may be at /mcp (as per adcp SDK fallback behavior)
        mcp_url = f"{agent_url}/mcp" if not agent_url.endswith("/mcp") else agent_url

        # Build headers with auth credentials if configured
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if agent.auth:
            auth_header = agent.auth_header or "x-adcp-auth"
            auth_token = agent.auth.get("credentials")
            if auth_token:
                headers[auth_header] = auth_token

        # One call, no retry loop: the seam owns attempts, backoff (BR-RULE-029
        # plus any Retry-After the agent asks for), address and TLS policy, and
        # what counts as retryable. Nothing is validated before this call — the
        # seam refuses or it sends, and a pre-check would only add a TOCTOU
        # window and a second copy of a decision it already owns.
        try:
            result = await asend(
                mcp_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "list_creative_formats", "arguments": {}},
                    "id": 1,
                },
                headers=headers,
                timeout=agent.timeout,
                provenance=provenance,
            )
        except OutboundError as exc:
            raise_mapped_outbound_error(exc, provenance=provenance, logger=logger)

        field = refusal_field(provenance)

        # Parse SSE or JSON response
        content_type = result.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in result.text.split("\n"):
                if line.startswith("data: "):
                    event_data = json.loads(line[6:])
                    if "result" in event_data:
                        return self._parse_mcp_tool_result(event_data["result"], logger, field=field)
        else:
            data = result.json()
            if "result" in data:
                return self._parse_mcp_tool_result(data["result"], logger, field=field)

        # The endpoint stays OFF the wire: ``agent.agent_url`` is a URL this seller
        # does not publish (transport-errors.mdx § Security Considerations).
        # ``field`` is what the buyer needs.
        raise AdCPValidationError(field=field)

    def _parse_mcp_tool_result(self, result: dict, logger: Any, *, field: str | None = None) -> list[Format]:
        """Parse formats from an MCP tools/call result.

        ``field`` names the BUYER input that supplied this agent_url, when there is
        one, so a refusal can say which of up to 100 creatives to fix.
        """
        import json

        content_list = result.get("content", [])
        for item in content_list:
            if item.get("type") == "text" and item.get("text"):
                data = json.loads(item["text"])
                formats_list = data.get("formats", [])
                formats = _validate_formats_tolerant(formats_list, logger)
                logger.info(f"_fetch_formats_raw_mcp: Parsed {len(formats)} formats from TextContent")
                return formats
        raise AdCPValidationError(field=field)

    async def get_formats_for_agent(
        self,
        agent: CreativeAgent,
        force_refresh: bool = False,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
        provenance: UrlProvenance | None = None,
        tenant_id: str | None = None,
    ) -> list[Format]:
        """Get formats from agent with caching.

        Args:
            agent: CreativeAgent to query
            force_refresh: Skip cache and fetch fresh data
            max_width: Maximum width in pixels (inclusive)
            max_height: Maximum height in pixels (inclusive)
            min_width: Minimum width in pixels (inclusive)
            min_height: Minimum height in pixels (inclusive)
            is_responsive: Filter for responsive formats
            asset_types: Filter by asset types
            name_search: Search by name
            type_filter: Filter by format type (display, video, audio)
            provenance: Whose URL ``agent.agent_url`` is. A :class:`CounterpartyUrl`
                (even with no ``field``) routes through the egress seam; anything
                else (an :class:`OperatorEndpoint`, or ``None``) is operator
                configuration and is eligible for the testing short-circuit below.
            tenant_id: Whose signing posture governs the OPERATOR dial below.
                Forwarded, never re-derived: which tenant is asking is the caller's
                fact, not something a cache-keyed-by-agent-url could recover.

        Returns:
            List of Format objects
        """
        # A COUNTERPARTY-supplied agent_url is judged before anything else, and is
        # NOT eligible for the testing short-circuit below.
        #
        # That short-circuit exists to avoid dialling the OPERATOR's default agent
        # from CI (docstring: "avoids timeouts when external creative agents are
        # unreachable"). Applied to a counterparty URL it does something quite
        # different: it makes us skip the only egress decision on the path, so a
        # buyer could name any destination and every test environment — including
        # the e2e stack, which also sets ADCP_TESTING=true — would return reference
        # formats and grade nothing. The security-relevant path would be the one
        # path no test ever exercises. Possession of a ``CounterpartyUrl`` is the
        # test — not whether it happens to carry a field — so a stored creative's
        # agent_url (``CounterpartyUrl(field=None)``, no canonical request path)
        # still takes this branch instead of falling through to the short-circuit.
        if is_counterparty(provenance) and not _is_operator_agent(agent.agent_url):
            # Straight to the seam: it refuses or it sends. Nothing is validated
            # first — a pre-check would add a TOCTOU window and a second copy of a
            # decision the seam already owns. The SDK client is not usable here at
            # all, since it dials through an httpx stack no policy of ours can
            # reach (upstream adcp-client-python#1004).
            return self._cache_formats(
                agent, await self._fetch_formats_raw_mcp(agent, provenance=provenance), has_filters=False
            )

        if (reference := self._operator_formats_override()) is not None:
            return reference

        # Check cache - only use cache if no filtering parameters provided
        has_filters = any(
            [
                max_width is not None,
                max_height is not None,
                min_width is not None,
                min_height is not None,
                is_responsive is not None,
                asset_types is not None,
                name_search is not None,
                type_filter is not None,
            ]
        )

        cache_key = self._cache_key(agent.agent_url)
        cached = self._format_cache.get(cache_key)
        if cached and not cached.is_expired() and not force_refresh and not has_filters:
            return cached.formats

        # Fetch from agent
        filter_kwargs = {field: locals()[field] for field in _FORMAT_FILTER_FIELDS}
        formats = await self._fetch_formats_operator(agent, tenant_id=tenant_id, **filter_kwargs)

        return self._cache_formats(agent, formats, has_filters)

    def _cache_formats(self, agent: CreativeAgent, formats: list[Format], has_filters: bool) -> list[Format]:
        """Cache a full result set and return it; a filtered one is returned uncached.

        Only the unfiltered fetch may be cached — a filtered result is a subset,
        and storing it under the agent's key would serve that subset to the next
        caller who asked for everything.
        """
        if not has_filters:
            self._format_cache[self._cache_key(agent.agent_url)] = CachedFormats(
                formats=formats, fetched_at=datetime.now(UTC), ttl_seconds=3600
            )
        return formats

    async def list_all_formats(
        self,
        tenant_id: str | None = None,
        force_refresh: bool = False,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
    ) -> list[Format]:
        """List all formats from all registered agents.

        Backward-compatible wrapper — returns only formats, discards errors.
        For error visibility, use list_all_formats_with_errors() instead.
        """
        result = await self.list_all_formats_with_errors(
            tenant_id=tenant_id,
            force_refresh=force_refresh,
            max_width=max_width,
            max_height=max_height,
            min_width=min_width,
            min_height=min_height,
            is_responsive=is_responsive,
            asset_types=asset_types,
            name_search=name_search,
            type_filter=type_filter,
        )
        return result.formats

    async def list_all_formats_with_errors(
        self,
        tenant_id: str | None = None,
        force_refresh: bool = False,
        max_width: int | None = None,
        max_height: int | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        is_responsive: bool | None = None,
        asset_types: list[str] | None = None,
        name_search: str | None = None,
        type_filter: str | None = None,
    ) -> FormatFetchResult:
        """List all formats from all registered agents, with per-agent error reporting.

        Decision: docs/design/error-propagation-in-format-discovery.md

        Returns FormatFetchResult with:
        - formats: Formats from all healthy agents
        - errors: One AdCP error per failed agent (code + message)

        When all agents succeed, errors is empty.
        When some agents fail, returns partial results + errors for failed agents.
        """
        if (reference := self._operator_formats_override()) is not None:
            return FormatFetchResult(formats=reference, errors=[])

        agents = self._get_tenant_agents(tenant_id)
        all_formats: list[Format] = []
        errors: list[AdCPResponseError] = []
        failed_agent_urls: list[str] = []

        logger.info(f"list_all_formats: Found {len(agents)} agents for tenant {tenant_id}")

        for agent in agents:
            logger.info(f"list_all_formats: Fetching from {agent.agent_url}")
            try:
                # Delegates to get_formats_for_agent for the cache-check + fetch +
                # cache-store logic (DRY — this loop used to reimplement it inline).
                filter_kwargs = {field: locals()[field] for field in _FORMAT_FILTER_FIELDS}
                formats = await self.get_formats_for_agent(
                    agent,
                    force_refresh=force_refresh,
                    type_filter=type_filter,
                    tenant_id=tenant_id,
                    **filter_kwargs,
                )

                logger.info(f"list_all_formats: Got {len(formats)} formats from {agent.agent_url}")
                all_formats.extend(formats)
            except Exception as e:
                logger.error(f"Failed to fetch formats from {agent.agent_url}: {e}", exc_info=True)
                # errors[] on a SUCCESS payload is still buyer-facing — the
                # storyboard grades a typed error code "via either adcp_error
                # (envelope) or errors[] (payload)", and transport-errors.mdx
                # § Security Considerations opens with "Every field is
                # client-facing". So neither ``e`` nor the seller-configured
                # ``agent.agent_url`` is interpolated. ``adcp.types.Error`` is
                # an SDK model with no internal_detail slot; the raw cause is
                # captured by the ``logger.error(..., exc_info=True)`` above.
                errors.append(
                    # field="formats" names the response section this advisory
                    # degrades, which the open-vocabulary permission depends on:
                    # a receiver decodes an unpublished code by reading recovery
                    # (derived from the code by CODE_TABLE) and locating what it
                    # affects. message/suggestion/recovery are NOT passed -- there
                    # is no parameter for them (salesagent-3dawm.14).
                    AdCPResponseError.of(AppErrorCode.AGENT_UNREACHABLE, field="formats")
                )
                # Kept parallel to ``errors`` and NOT serialized -- see the field
                # note on FormatFetchResult. Appended in the same breath as the
                # advisory so the two cannot drift out of correspondence.
                failed_agent_urls.append(agent.agent_url)
                continue

        logger.info(f"list_all_formats: Returning {len(all_formats)} formats, {len(errors)} errors")
        return FormatFetchResult(formats=all_formats, errors=errors, failed_agent_urls=failed_agent_urls)

    async def search_formats(
        self, query: str, tenant_id: str | None = None, type_filter: str | None = None
    ) -> list[Format]:
        """Search formats across all agents.

        Args:
            query: Search query (matches format_id, name, description)
            tenant_id: Optional tenant ID for tenant-specific agents
            type_filter: Optional format type filter (display, video, etc.)

        Returns:
            List of matching Format objects
        """
        all_formats = await self.list_all_formats(tenant_id)
        query_lower = query.lower()

        results = []
        for fmt in all_formats:
            # Match query against format_id, name, or description
            # format_id is a FormatId object, so we need to access .id
            format_id_str = fmt.format_id.id if isinstance(fmt.format_id, FormatId) else str(fmt.format_id)
            if (
                query_lower in format_id_str.lower()
                or query_lower in fmt.name.lower()
                or (fmt.description and query_lower in fmt.description.lower())
            ):
                results.append(fmt)

        return results

    async def get_format(
        self, agent_url: str, format_id: str, *, provenance: UrlProvenance | None = None
    ) -> Format | None:
        """Get a specific format from an agent.

        ``agent_url`` here is an arbitrary URL handed in by a caller, not one of
        this tenant's registered agents. Which path dials depends on
        ``provenance``: a :class:`CounterpartyUrl` (a counterparty supplied the
        URL) goes through the egress seam (``asend``); anything else (an
        :class:`OperatorEndpoint`, or ``None`` — e.g. the registered-agent-gated
        caller in ``media_buy_create.py``) rides the SDK client path, which
        dials un-pinned until adcp grows a transport injection point (GH #1589).

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID to retrieve
            provenance: Whose URL this is. Construct a ``CounterpartyUrl``,
                optionally naming the request-document path it arrived on (e.g.
                ``creatives[0].format_id.agent_url``, or ``None`` when there is
                no canonical path), so a refusal is reported as the buyer's
                correctable error instead of a seller misconfiguration.

        Returns:
            Format object or None if not found

        Note:
            No ``tenant_id`` is threaded here yet, so this call dials unsigned --
            unchanged from before request signing existed. Its caller
            (``media_buy_create.py``) has one in scope; wiring it is a follow-up,
            not a downgrade introduced here.
        """
        # Find agent
        agent = CreativeAgent(agent_url=agent_url, name="Unknown", enabled=True)
        formats = await self.get_formats_for_agent(agent, provenance=provenance)

        # Find matching format
        for fmt in formats:
            # fmt.format_id is a FormatId object with .id attribute, format_id parameter is a string
            if fmt.format_id.id == format_id:
                return fmt

        return None

    async def preview_creative(
        self,
        agent_url: str,
        format_id: str,
        creative_manifest: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate preview renderings for a creative using the creative agent.

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID for the creative
            creative_manifest: Complete creative manifest with all required assets.
                Assets MUST be a dictionary keyed by asset_id from format's asset_requirements.
                Example: {
                    "creative_id": "c123",
                    "name": "Banner Ad",
                    "format_id": "display_300x250",
                    "assets": {
                        "main_image": {"asset_type": "image", "url": "https://..."},
                        "logo": {"asset_type": "image", "url": "https://..."}
                    }
                }
            tenant_id: Whose signing posture governs this dial (see
                :func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant`).
                ``None`` dials unsigned, as before.

        Returns:
            Preview response containing array of preview variants with preview_url.
            Example: {
                "previews": [{
                    "name": "Default",
                    "renders": [{
                        "preview_url": "https://...",
                        "dimensions": {"width": 300, "height": 250}
                    }]
                }]
            }
        """
        # ``preview_creative`` IS an AdCP protocol operation at the pinned 3.1.1 --
        # ``creative/task-reference/preview_creative.mdx``, and a member of
        # ``sdk_operation_names()`` -- so security.mdx:1043 permits attributing a
        # signature to it. That check is what licenses ``sign=`` here, and it is the
        # reason the comment this replaced ("non-standard tool ... not in AdCP spec")
        # could not be left standing above a signed dial. The predicate is NOT
        # ``vocabulary.is_adcp_operation``: that answers "does this seller SERVE it",
        # which is False for every tool we only ever CALL, ``get_signals`` included.
        return await call_operator_mcp_tool(
            _connection_agent_url(agent_url),
            "preview_creative",
            {
                # The pinned reference agent's schema takes format_id as the
                # federation-identity OBJECT {agent_url, id} — the live public
                # host tolerated a bare string, which masked this mismatch
                # until connections were pinned in-network.
                # The identity keeps the CANONICAL agent_url, not the
                # connection alias. AnyUrl serialization yields the
                # trailing-slash form for path-less URLs — verified tolerated
                # by the pinned reference agent (probe 2026-07-13).
                "format_id": FormatId(agent_url=agent_url, id=format_id).model_dump(mode="json"),
                "creative_manifest": creative_manifest,
            },
            label="the creative agent",
            sign=request_signer_for_tenant(tenant_id=tenant_id),
        )

    async def build_creative(
        self,
        agent_url: str,
        format_id: str,
        message: str,
        gemini_api_key: str,
        promoted_offerings: dict[str, Any] | None = None,
        context_id: str | None = None,
        finalize: bool = False,
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a creative using AI generation via the creative agent.

        This calls the creative agent's build_creative tool which requires the user's
        Gemini API key (the creative agent doesn't pay for API calls).

        Args:
            agent_url: URL of the creative agent
            format_id: Format ID (must be generative type like display_300x250_generative)
            message: Creative brief or refinement instructions
            gemini_api_key: User's Gemini API key (REQUIRED)
            promoted_offerings: Brand and product information for AI generation
            context_id: Session ID for iterative refinement (optional)
            finalize: Set to true to finalize the creative (default: False)
            tenant_id: Whose signing posture governs this dial (see
                :func:`~src.core.helpers.adapter_helpers.request_signer_for_tenant`).
                ``None`` dials unsigned, as before.

        Returns:
            Build response containing:
            - message: Status message
            - context_id: Session ID for refinement
            - status: "draft" or "finalized"
            - creative_output: Generated creative manifest with output_format
        """
        # ``build_creative`` IS an AdCP protocol operation at the pinned 3.1.1
        # (``creative/task-reference/build_creative.mdx``, and a member of
        # ``sdk_operation_names()``), so :1043 permits the signature below -- same
        # verification as ``preview_creative`` above, same reason the old
        # "not in AdCP spec" comment is gone.
        params = {
            "message": message,
            "format_id": format_id,
            "gemini_api_key": gemini_api_key,
            "finalize": finalize,
        }

        if promoted_offerings:
            params["promoted_offerings"] = promoted_offerings

        if context_id:
            params["context_id"] = context_id

        return await call_operator_mcp_tool(
            _connection_agent_url(agent_url),
            "build_creative",
            params,
            label="the creative agent",
            sign=request_signer_for_tenant(tenant_id=tenant_id),
        )


# Global registry instance
class ReferenceFormatsRegistry(CreativeAgentRegistry):
    """The registry a test deployment runs: operator agents answer from the checked-in
    reference catalog, so no test ever dials a creative agent and the in-process and e2e
    servers agree by construction. Counterparty URLs still go through the egress seam."""

    def _operator_formats_override(self) -> list[Format] | None:
        return _get_reference_formats()


_registry: CreativeAgentRegistry | None = None


def get_creative_agent_registry() -> CreativeAgentRegistry:
    """The process's creative agent registry, selected once from the settings."""
    global _registry
    if _registry is None:
        _registry = ReferenceFormatsRegistry() if get_settings().reference_formats_only else CreativeAgentRegistry()
    return _registry
