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
- When ADCP_TESTING=true, returns the checked-in reference formats instead of calling
  external services
- This avoids timeouts in CI when external creative agents are unreachable
"""

import copy
import logging
import os
import uuid as _uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# FIXME(#1388): ListCreativeFormatsRequest has a local subclass; import from src.core.schemas (Pattern #7/#4).
from adcp import ListCreativeFormatsRequest
from adcp.types import AssetContentType as AssetType
from adcp.types import (
    BrandReference,
    FormatReferenceStructuredObject,
)
from adcp.types import (
    BuildCreativeRequest as _BuildCreativeRequestConcrete,
)
from adcp.types import Error as AdCPResponseError

# The RootModel variant is what BuildCreativeRequest.creative_manifest is
# annotated with (adcp.types.CreativeManifest is a different, non-root class).
from adcp.types.generated_poc.core.creative_manifest import CreativeManifest
from pydantic import BaseModel, ConfigDict, ValidationError

from src.core.adcp_schema_tree import AdCPSchemaTreeError, load_schema, sibling_ref
from src.core.database.models import CreativeAgent as DBCreativeAgent
from src.core.exceptions import (
    AdCPConfigurationError,
    AdCPValidationError,
)
from src.core.format_cache import load_reference_formats
from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error
from src.core.schema_helpers import to_brand_reference
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

logger = logging.getLogger(__name__)


_ASSET_UNION_REF = "core/assets/asset-union.json"


def _known_asset_types() -> frozenset[str]:
    """The ``asset_type`` discriminators the PINNED AdCP spec defines.

    Read from ``core/assets/asset-union.json`` — the spec's own canonical union
    of asset variants — in the schema tree the installed SDK bundles for the
    pinned version (:mod:`src.core.adcp_schema_tree`, and see
    docs/adcp-spec-version.md). Each union arm's ``$ref`` is followed to the
    variant schema, whose ``properties.asset_type.const`` IS the discriminator.

    Why the schema and not an SDK enum: ``AssetContentType`` is the
    response-level CONTENT-type enum (15 members at adcp 6.6.0 / AdCP 3.1.1),
    not the discriminator vocabulary. It omits five types the pinned spec
    defines with their own schemas — ``zip``, ``card``, ``pixel_tracker``,
    ``vast_tracker``, ``daast_tracker`` — so deriving from it (even unioned with
    a hand-listed ``{"zip", "card"}``) classified three first-class 3.1.1 asset
    types as post-pin additive growth, i.e. exactly the silent drop the tolerant
    ingestion below exists to prevent for ``url``. The hand-listed supplement
    also had to be edited on every pin bump, which is what deriving is for.

    An annotation-walk over ``Format.assets`` is not an option: the
    ``Annotated[…, Discriminator]`` shape the SDK uses collects nothing.
    """
    discriminators: set[str] = set()
    for arm in load_schema(_ASSET_UNION_REF).get("oneOf") or []:
        ref = arm.get("$ref") if isinstance(arm, dict) else None
        if not isinstance(ref, str):
            continue
        variant = load_schema(sibling_ref(_ASSET_UNION_REF, ref))
        const = ((variant.get("properties") or {}).get("asset_type") or {}).get("const")
        if isinstance(const, str):
            discriminators.add(const)
    if not discriminators:
        raise AdCPSchemaTreeError(
            f"{_ASSET_UNION_REF} in the pinned SDK schema tree yielded no asset_type "
            "discriminators — the spec's asset-union layout changed; update _known_asset_types()."
        )
    return frozenset(discriminators)


_KNOWN_ASSET_TYPES = _known_asset_types()
_SCHEMA_VALIDATION_FAILURE_MARKERS = (
    "doesn't match expected schema",
    "does not match expected schema",
    "validation error",
    "validationerror",
    "failed to validate",
    # adcp >= 6.6 phrasing: "Schema validation failed for <tool>: ... oneOf
    # composition failed" (seen live when the pinned reference agent serves
    # post-pin additive asset_types, e.g. pixel_tracker).
    "schema validation failed",
)


class GenerativeOutputFormat(BaseModel):
    """The ``output_format`` leaf of a generative build's ``creative_output``."""

    model_config = ConfigDict(extra="allow")

    url: str | None = None


class GenerativeCreativeOutput(BaseModel):
    """The creative a generative build produced."""

    model_config = ConfigDict(extra="allow")

    assets: dict[str, Any] | None = None
    output_format: GenerativeOutputFormat | None = None


class GenerativeBuildResult(BaseModel):
    """A creative agent's ``build_creative`` response, as this adapter exposes it.

    ``build_creative`` is the one creative-agent call whose response callers act
    on field by field, so it crosses this boundary as a MODEL, not a dict: the
    business layer reads ``result.status`` / ``result.creative_output.assets``
    instead of chaining ``.get()`` on an untyped payload, and a renamed field is
    a validation failure here rather than a silent ``None`` three layers up.

    ``extra="allow"`` because this vocabulary (``status`` / ``context_id`` /
    ``creative_output``) is the reference agent's, NOT the pinned spec's:
    ``media-buy/build-creative-response.json @ 3.1.1`` defines
    ``creative_manifest`` + ``build_variant_id`` instead. Extra keys are
    preserved so the full response is still what gets persisted, and reconciling
    the two vocabularies is tracked in #2143.
    """

    model_config = ConfigDict(extra="allow")

    status: str = "draft"
    context_id: str | None = None
    creative_output: GenerativeCreativeOutput | None = None


def _render_creative_manifest(
    format_id: FormatId, assets: Mapping[str, Any] | None, *, url: str | None = None
) -> CreativeManifest:
    """Render the AdCP ``CreativeManifest`` a creative-agent call carries.

    The single renderer for both agent calls (``preview_creative`` /
    ``build_creative``), so the manifest is built once, in the adapter that owns
    the wire contract, from domain values the business layer already has.

    ``model_validate`` (not ``model_construct``) so the manifest's own field
    validators run — a malformed asset slot is rejected here, before a request
    goes out, rather than by the agent.

    A rejection is re-raised as :class:`AdCPValidationError`
    (``VALIDATION_ERROR`` / ``correctable`` per ``enums/error-code.json @
    3.1.1``), because the assets are BUYER input: a bare
    ``pydantic.ValidationError`` is not an ``AdCPError``, so the sync path's
    failure ladder would report it as "creative agent unreachable … retry
    recommended" (``transient``) — a retry hint for an error only the buyer can
    fix, and one that no retry will fix.

    ``url`` is deliberately NOT an AdCP ``CreativeManifest`` field: it is an
    extra key the static ``preview_creative`` path adds so the agent can render
    an already-hosted media URL, and it rides through on the model's
    ``extra`` allowance. The generative path never sets it.
    """
    payload: dict[str, Any] = {
        "format_id": {"agent_url": canonical_agent_url(str(format_id.agent_url)), "id": format_id.id},
        "assets": dict(assets or {}),
    }
    if url:
        payload["url"] = url
    try:
        return CreativeManifest.model_validate(payload)
    except ValidationError as exc:
        raise AdCPValidationError(
            f"Creative assets do not satisfy the AdCP creative manifest for format "
            f"{format_id.id!r}: {exc.error_count()} validation error(s). "
            f"First: {exc.errors()[0].get('loc')} — {exc.errors()[0].get('msg')}",
            field="assets",
        ) from exc


def _as_format_dict(fmt: Any) -> dict[str, Any]:
    """Normalize a format item (pydantic model or plain dict) to a dict.

    The adcp client may return parsed library models or raw dicts depending on
    the response shape; the tolerant validator needs one uniform input.
    """
    if isinstance(fmt, dict):
        return fmt
    model_dump = getattr(fmt, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    # Terminal by CLASS, not by a hand-typed kwarg: AdCPError no longer accepts
    # `recovery=` (it derives from the code), and an agent returning an item shape
    # we cannot read is a seller-side deployment problem — CONFIGURATION_ERROR is
    # pinned terminal, which is the classification this raise always wanted.
    raise AdCPConfigurationError(f"Unexpected format item type from creative agent: {type(fmt)!r}")


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

    Strategy (value-agnostic, robust to adcp's many-armed discriminated union):
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
            asset["asset_type"] = "image"  # known sentinel arm
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


def _testing_build_result(format_id: FormatId, assets: Mapping[str, Any] | None) -> GenerativeBuildResult:
    """The build response served in testing mode (ADCP_TESTING=true).

    Sibling of :func:`_get_reference_formats`: the same convention, for the same
    reason — under ``ADCP_TESTING`` the registry must not make external HTTP
    calls (CI has no reachable creative agent, and the live one rate-limits), so
    every registry method serves checked-in/derived data instead. ``get_format``
    and ``list_all_formats_with_errors`` already had this branch;
    ``build_creative`` was the only one without, which is why a generative sync
    could not be exercised against the live test stack at all.

    The response is DERIVED from the request (not a fixture blob): the output
    format is the generative format's own declared output, so it is consistent
    with whatever the reference catalog says today, and the buyer's input assets
    are echoed back as the produced creative's assets.
    """
    output_format_id = format_id.id
    for fmt in _get_reference_formats():
        if fmt.format_id.id == format_id.id and fmt.output_format_ids:
            output_format_id = fmt.output_format_ids[0].id
            break
    return GenerativeBuildResult(
        status="draft",
        context_id=f"adcp-testing-{format_id.id}",
        creative_output=GenerativeCreativeOutput(
            assets={name: _as_json_value(asset) for name, asset in (assets or {}).items()},
            output_format=GenerativeOutputFormat(url=f"https://creative-agent.adcp-testing.invalid/{output_format_id}"),
        ),
    )


def _as_json_value(value: Any) -> Any:
    """JSON-safe view of an asset value (model or dict) for a testing-mode echo."""
    return value.model_dump(mode="json", exclude_none=True) if isinstance(value, BaseModel) else value


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
    configured = os.environ.get("CREATIVE_AGENT_URL")
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

    Read at call time (not import) so test stacks that set the env after
    import still take effect.
    """
    configured = os.environ.get("CREATIVE_AGENT_URL")
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

    # Default creative agent (always available)
    # Note: agent_url is the base URL for the creative agent (e.g., https://creative.adcontextprotocol.org)
    # The MCP server endpoint (/mcp) is appended by the MCP client when connecting
    # Reads CREATIVE_AGENT_URL env var so CI can point at a containerized agent.
    DEFAULT_AGENT = CreativeAgent(
        agent_url=os.environ.get("CREATIVE_AGENT_URL", "https://creative.adcontextprotocol.org"),
        name="AdCP Standard Creative Agent",
        enabled=True,
        priority=1,
    )

    def __init__(self):
        """Initialize registry with empty cache."""
        self._format_cache: dict[str, CachedFormats] = {}  # Key: normalized agent_url

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
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAgent as CreativeAgentModel

        with get_db_session() as session:
            stmt = select(CreativeAgentModel).filter_by(tenant_id=tenant_id, enabled=True)
            db_agents = session.scalars(stmt).all()

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

        Returns:
            List of Format objects from the agent
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

        payload = await call_operator_mcp_tool(
            _connection_agent_url(agent.agent_url),
            "list_creative_formats",
            args,
            label=f"creative agent {agent.name}",
            auth=agent.auth,
            auth_header=agent.auth_header,
            timeout=agent.timeout,
        )
        return _validate_formats_tolerant(payload.get("formats", []), logger)

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
        """
        agent = self.config_for(db_agent)
        try:
            formats = await self._fetch_formats_operator(agent)
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
        """
        import json

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

        raise AdCPValidationError(
            f"No parseable result in MCP response from {agent.agent_url}",
            field=field,
        )

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
        raise AdCPValidationError("No text content in MCP tool result", field=field)

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

        # In testing mode (ADCP_TESTING=true), serve the checked-in reference formats
        # to avoid external HTTP calls (and to match the e2e server by construction).
        if os.environ.get("ADCP_TESTING", "").lower() == "true":
            return _get_reference_formats()

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
        formats = await self._fetch_formats_operator(agent, **filter_kwargs)

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
        # In testing mode (ADCP_TESTING=true), serve the checked-in reference formats
        # to avoid external HTTP calls (and to match the e2e server by construction).
        if os.environ.get("ADCP_TESTING", "").lower() == "true":
            logger.info("list_all_formats: Using reference formats (ADCP_TESTING=true)")
            return FormatFetchResult(formats=_get_reference_formats(), errors=[])

        agents = self._get_tenant_agents(tenant_id)
        all_formats: list[Format] = []
        errors: list[AdCPResponseError] = []

        logger.info(f"list_all_formats: Found {len(agents)} agents for tenant {tenant_id}")

        for agent in agents:
            logger.info(f"list_all_formats: Fetching from {agent.agent_url}")
            try:
                # Delegates to get_formats_for_agent for the cache-check + fetch +
                # cache-store logic (DRY — this loop used to reimplement it inline).
                filter_kwargs = {field: locals()[field] for field in _FORMAT_FILTER_FIELDS}
                formats = await self.get_formats_for_agent(
                    agent, force_refresh=force_refresh, type_filter=type_filter, **filter_kwargs
                )

                logger.info(f"list_all_formats: Got {len(formats)} formats from {agent.agent_url}")
                all_formats.extend(formats)
            except Exception as e:
                logger.error(f"Failed to fetch formats from {agent.agent_url}: {e}", exc_info=True)
                errors.append(
                    AdCPResponseError(
                        code="AGENT_UNREACHABLE",
                        message=f"Creative agent at {agent.agent_url} is unreachable: {e}",
                    )
                )
                continue

        logger.info(f"list_all_formats: Returning {len(all_formats)} formats, {len(errors)} errors")
        return FormatFetchResult(formats=all_formats, errors=errors)

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
        self, format_id: FormatId, assets: Mapping[str, Any] | None = None, *, url: str | None = None
    ) -> dict[str, Any]:
        """Generate preview renderings for a creative using the creative agent.

        Takes DOMAIN values and renders the AdCP wire objects itself (see
        :func:`_render_creative_manifest`) — the manifest is protocol framing and
        belongs to this adapter, not to the business layer that calls it.

        Args:
            format_id: Federation identity of the creative's format
                (``{agent_url, id}``). Its ``agent_url`` is the format's canonical
                identity; the transport connection may be rerouted to a pinned
                container by ``_connection_agent_url``.
            assets: Validated asset slot map keyed by the format's asset_ids
                (``{"main_image": {"asset_type": "image", "url": "https://…"}}``).
            url: Existing media URL for a static creative, if any. Carried as a
                non-AdCP extra key on the manifest so the agent can render an
                already-hosted asset (see :func:`_render_creative_manifest`).

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
        manifest_payload = _render_creative_manifest(format_id, assets, url=url).model_dump(
            mode="json", exclude_none=True
        )

        # Use custom MCP client for non-standard tools (preview_creative not in AdCP spec)
        # Dial through the ONE guarded seam (#1802): IP-pinned, redirect-refusing,
        # and it owns both error mappings. The manifest and the tool's identity
        # argument are still rendered here, from one value — see below.
        return await call_operator_mcp_tool(
            _connection_agent_url(str(format_id.agent_url)),
            "preview_creative",
            {
                # The pinned reference agent's schema takes format_id as the
                # federation-identity OBJECT {agent_url, id} — the live public
                # host tolerated a bare string, which masked this mismatch
                # until connections were pinned in-network.
                # The identity keeps the CANONICAL agent_url, not the
                # connection alias, and is READ BACK OUT of the serialized
                # manifest rather than rendered a second time: one request
                # carrying two spellings of the same agent_url (a hand-built
                # canonical string next to a pydantic-serialized AnyUrl, which
                # adds the trailing slash for a path-less URL) is exactly the
                # drift core/format-id.json's canonicalization MUST exists to
                # stop. The trailing-slash form is verified tolerated by the
                # pinned reference agent (probe 2026-07-13).
                "format_id": manifest_payload["format_id"],
                "creative_manifest": manifest_payload,
            },
            label="the creative agent",
        )

    async def build_creative(
        self,
        format_id: FormatId,
        message: str,
        *,
        brand: dict[str, Any] | BrandReference | str | None = None,
        assets: Mapping[str, Any] | None = None,
    ) -> GenerativeBuildResult | None:
        """Build a creative using AI generation via the creative agent.

        Uses ``ADCPMultiAgentClient`` + ``BuildCreativeRequest`` per AdCP 3.1.
        ``idempotency_key`` is generated automatically (required on every AdCP
        task request in 3.1).

        Like :meth:`preview_creative`, this takes DOMAIN values and renders the
        AdCP wire objects itself: the request identity and the manifest identity
        are both derived from the single ``format_id`` argument, so one request
        cannot carry two spellings of the same agent_url.

        Args:
            format_id: Federation identity of the generative format
                (``{agent_url, id}``, e.g. ``display_300x250_generative``).
            message: Creative brief or refinement instructions.
            brand: Optional brand value (str, dict, or Pydantic model) forwarded
                to the creative agent as a ``BrandRef``-shaped object.
            assets: Validated asset slot map for the generation inputs. Buyer
                inputs the pre-3.1 call spelled as a separate
                ``promoted_offerings`` argument travel here, in their own asset
                slot — ``media-buy/build-creative-request.json @ 3.1.1`` has no
                ``promoted_offerings`` property, and its ``creative_manifest``
                is documented as carrying "any required input assets".

        Returns:
            The agent's :class:`GenerativeBuildResult` (``status``,
            ``context_id``, ``creative_output``), typed so callers read
            attributes instead of indexing a dict — or ``None`` when the agent
            returned no payload at all, which callers treat as "nothing to
            store" rather than as a build with default fields.

        Note:
            AdCP 3.1's ``build_creative`` has no ``finalize`` field — the closest
            spec concept is ``quality`` (``"draft"`` | ``"production"``, output
            fidelity, not a finalize/commit action). A previous version of this
            method accepted a ``finalize: bool`` parameter that rode through via
            ``extra="allow"`` and was always ``False`` in practice (callers read
            ``getattr(creative, "approved", False)``, but ``CreativeAsset`` has no
            ``approved`` field) — i.e. it was dead code, not a working feature.
            It was removed rather than silently reinterpreted as ``quality``,
            since the caller-visible semantics (draft vs. production fidelity)
            differ from a boolean finalize/commit flag and need an explicit
            product decision before wiring.

            Refinement (``context_id`` on the pre-3.1 call) is likewise NOT wired:
            the pinned request schema's refinement handle is
            ``refine_from_build_variant_id``, sourced from a response
            ``build_variant_id`` that the reference agent does not emit today (it
            returns the non-spec ``context_id`` this method still surfaces). Passing
            a ``context_id`` as a ``build_variant_id`` would be an invented mapping,
            so the round-trip stays unwired and tracked in #2143 rather than being
            plumbed to a parameter nothing reads.
        """
        agent_url = canonical_agent_url(str(format_id.agent_url))

        # In testing mode (ADCP_TESTING=true), serve a derived build result to
        # avoid external HTTP calls — same branch get_format and
        # list_all_formats_with_errors carry (see _testing_build_result).
        if os.environ.get("ADCP_TESTING", "").lower() == "true":
            logger.info("build_creative: serving the testing-mode build result (ADCP_TESTING=true)")
            return _testing_build_result(format_id, assets)

        # Resolve brand to a typed BrandReference for the request.
        # to_brand_reference() is the single str/dict/model → BrandReference
        # converter used everywhere in the codebase (also used by
        # create_get_products_request and the create_media_buy boundary) —
        # it normalizes scheme/path/case for strings internally.
        brand_typed: BrandReference | None = to_brand_reference(brand)

        # The request is BUILT as the pinned SDK model — idempotency_key (required
        # on every 3.1 task request), the structured target_format_id, the
        # validated CreativeManifest — and then SENT through the one guarded MCP
        # seam (#1802) rather than through ADCPMultiAgentClient, whose own httpx
        # stack no egress policy of ours can reach (adcp 6.6.0 exposes no
        # transport injection point). Typing the payload and guarding the dial are
        # separate concerns; this keeps both instead of trading one for the other.
        request = _BuildCreativeRequestConcrete(
            message=message,
            target_format_id=FormatReferenceStructuredObject(
                agent_url=agent_url,
                id=format_id.id,
            ),
            idempotency_key=str(_uuid.uuid4()),
            creative_manifest=_render_creative_manifest(format_id, assets),
            brand=brand_typed,
        )

        # call_operator_mcp_tool owns both error mappings, so an outbound refusal or
        # an MCP failure already arrives as the internal typed AdCPError taxonomy
        # (SERVICE_UNAVAILABLE/transient for a dial failure, CONFIGURATION_ERROR/
        # terminal for an operator-endpoint refusal) that _failed_from_agent_error
        # reads recovery and code off.
        payload = await call_operator_mcp_tool(
            _connection_agent_url(agent_url),
            "build_creative",
            request.model_dump(mode="json", exclude_none=True),
            label="the creative agent",
        )
        if not payload:
            return None
        return GenerativeBuildResult.model_validate(payload)


# Global registry instance
_registry: CreativeAgentRegistry | None = None


def get_creative_agent_registry() -> CreativeAgentRegistry:
    """Get the global creative agent registry instance."""
    global _registry
    if _registry is None:
        _registry = CreativeAgentRegistry()
    return _registry
