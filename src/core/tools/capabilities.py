"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple

from adcp.types import GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
from adcp.types.generated_poc.core.postal_area_support import (
    PostalAreaSupport,  # adcp 6.6: standalone GeoPostalAreas removed; capabilities use PostalAreaSupport
)
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.enums.specialism import AdcpSpecialism
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Account as AccountCapability,  # capability sub-object; distinct from the domain Account schema
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Adcp,
    Execution,
    GeoMetros,
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    SupportedProtocol,
    # FIXME(#1388): Targeting has a local subclass; import from src.core.schemas (Pattern #7/#4).
    Targeting,
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Idempotency3 as IdempotencyUnsupported,  # the honest supported=False variant (agent-wide)
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.auth import get_principal_object, require_identity
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.helpers import enum_value
from src.core.helpers.account_helpers import resolve_supported_billing
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import TenantLike
from src.core.tool_context import ToolContext
from src.core.tools._mcp import mcp_result
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)


# Mapping from adapter channel names to MediaChannel enum values
CHANNEL_MAPPING: dict[str, MediaChannel] = {
    "display": MediaChannel.display,
    "olv": MediaChannel.olv,
    "video": MediaChannel.olv,  # alias
    "social": MediaChannel.social,
    "search": MediaChannel.search,
    "ctv": MediaChannel.ctv,
    "linear_tv": MediaChannel.linear_tv,
    "radio": MediaChannel.radio,
    "streaming_audio": MediaChannel.streaming_audio,
    "audio": MediaChannel.streaming_audio,  # alias
    "podcast": MediaChannel.podcast,
    "dooh": MediaChannel.dooh,
    "ooh": MediaChannel.ooh,
    "print": MediaChannel.print,
    "cinema": MediaChannel.cinema,
    "email": MediaChannel.email,
    "gaming": MediaChannel.gaming,
    "retail_media": MediaChannel.retail_media,
    "influencer": MediaChannel.influencer,
    "affiliate": MediaChannel.affiliate,
    "product_placement": MediaChannel.product_placement,
}


def _build_account_capability(tenant: TenantLike | None) -> AccountCapability:
    """Build the `account` capability object with an HONEST sandbox declaration.

    sandbox=False (#1329 gap 13): this seller stores a per-account `sandbox` flag
    (a natural-key discriminator for sync_accounts + a list_accounts filter) but has
    NO behavioral isolation — a media buy under a sandbox account routes to the exact
    same live adapter path as production; `account.sandbox` is wholly disconnected
    from the `dry_run` testing hook (the only "no real spend" switch). The spec field
    mandates "Requests using a sandbox account perform no real platform calls or
    spend" (get-adcp-capabilities-response.json, AdCP 3.1.1); declaring `true`
    without that isolation is the same wire-honesty defect as `catalog_management=True`
    (fixed in PR #1276 R7-1). Declared False until behavioral sandbox isolation ships.
    The field is ungraded by the 3.1.1 storyboards, so there is no coverage cost to
    declaring it honestly. Mirrors the `catalog_management` / `property_list_filtering`
    honesty rationale on MediaBuyFeatures.

    require_operator_auth=False: accounts are buyer-declared via sync_accounts
    (brand + operator natural key, BR-RULE-056) — operators do not authenticate.

    supported_billing (required by the schema): the account-billable parties this
    seller accepts, resolved by ``resolve_supported_billing`` — the SAME resolver
    sync_accounts enforces against, so what is advertised here equals what sync_accounts
    accepts (#1329). A configured value with no account-billable party raises
    (loud) rather than silently substituting the default.

    required_for_products and account_financials default to False on the library type,
    and False is the honest value here: get_products is auth-optional and needs no
    account (required_for_products=False), and this seller exposes no account financial
    detail (account_financials=False). authorization_endpoint is left absent (no
    operator-auth endpoint, consistent with require_operator_auth=False).
    """
    return AccountCapability(
        supported_billing=resolve_supported_billing(tenant),
        sandbox=False,
        require_operator_auth=False,
    )


# The protocols this seller hosts (SSOT for both the emitted `supported_protocols` and the
# specialism gate's parent-protocol rule — the two must agree, so they read one constant).
_SUPPORTED_PROTOCOLS: list[SupportedProtocol] = [SupportedProtocol.media_buy]


class _SpecialismDecision(StrEnum):
    DECLARED = "declared"
    DECLINED = "declined"


# Two distinct sentinels for the ``requires_scenarios`` column (the reviewer's finding 1 ask —
# an empty tuple must never read as "no requirement"):
#   ``()``               — upstream genuinely lists NO required storyboards for this specialism.
#   ``NOT_TRANSCRIBED``  — upstream DOES list required storyboards, but they are not vendored
#                          here. The compliance bundle (``dist/compliance/3.1.1/``) is not
#                          shipped in the installed SDK (``adcp/_schemas/3.1/`` holds schemas +
#                          enums only), so a full scenario-id transcription would be a second
#                          unguarded hand-copy of the artifact — the exact drift finding 1 opens
#                          on. ``required_tools`` IS transcribed (short + load-bearing for the
#                          tool gate, verified ⊆ the live FastMCP registry); ``requires_scenarios``
#                          stays NOT_TRANSCRIBED and the audit gate DECLINES any row whose
#                          scenarios are not transcribed-and-backed. Promoting a row to DECLARED
#                          means transcribing its real ``requires_scenarios`` (per the cited
#                          ``specialisms/<id>/index.yaml @ v3.1.1``) and backing each id in the
#                          test layer's ``_BACKED_SPECIALISM_SCENARIOS``.
NOT_TRANSCRIBED: tuple[str, ...] | None = None


class _SpecialismAudit(NamedTuple):
    """One machine-checked row of the specialism audit (AdCP 3.1.1, #1329).

    The audit is DATA, not prose, so an inconsistent decision reddens ``test_specialism_audit_gate``
    (which walks EVERY row — not only the declared ones) instead of only a human re-reading a
    comment. Each specialism at ``dist/compliance/3.1.1/specialisms/{id}/`` is gated by:

    * ``parent_protocol`` — the ``SupportedProtocol`` enum member the specialism sits under
      (``None`` for ``signed_requests``, which is not a compliance specialism). Must be in
      ``_SUPPORTED_PROTOCOLS``.
    * ``required_tools`` — the exact tool list from ``dist/compliance/3.1.1/index.json @ v3.1.1``
      (adcp==6.6.0), transcribed verbatim. Every entry must be a live FastMCP-registered tool.
    * ``requires_scenarios`` — see the sentinel note above (``()`` vs ``NOT_TRANSCRIBED``).

    The gate asserts ``decision == DECLARED`` iff the row QUALIFIES (parent hosted ∧ every tool
    registered ∧ id non-deprecated in the pinned enum ∧ requires_scenarios transcribed ∧ every
    scenario backed) — so a DECLARED row that does not qualify AND a DECLINED row that secretly
    does both redden. That two-way check, not the transcription, is what keeps the table honest;
    the bundle is not installed, so the transcription itself cannot be machine-diffed against
    upstream (finding 1). ``_BACKED_SPECIALISM_SCENARIOS`` lives in the test layer: ``src`` states
    the protocol REQUIREMENT, the test layer states what SATISFIES it.
    """

    decision: _SpecialismDecision
    parent_protocol: SupportedProtocol | None
    required_tools: tuple[str, ...]
    requires_scenarios: tuple[str, ...] | None
    rationale: str


# Full audit of every pinned AdcpSpecialism. Nothing is DECLARED (#1329): every media_buy row
# whose tools are all implemented is declined on the storyboard gate (its requires_scenarios are
# not backed end-to-end); every other row is declined on parent-protocol, a missing tool, or
# deprecation. required_tools is verbatim from ``dist/compliance/3.1.1/index.json @ v3.1.1``.
_SPECIALISM_AUDIT: dict[AdcpSpecialism, _SpecialismAudit] = {
    # --- media_buy parent, required_tools all met, declined on the storyboard gate ---
    # (requires_scenarios NOT_TRANSCRIBED; the real upstream count + source are named per row so
    # the old fabricated ids — "named 2, one invented" — are removed and the gap size is honest.)
    AdcpSpecialism.sales_non_guaranteed: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "get_products", "create_media_buy"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="tools implemented; specialisms/sales-non-guaranteed/index.yaml @ v3.1.1 lists 15 "
        "required storyboards, none backed end-to-end — declined until the storyboard gate is backed.",
    ),
    AdcpSpecialism.sales_guaranteed: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "get_products", "create_media_buy"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="tools implemented; specialisms/sales-guaranteed/index.yaml @ v3.1.1 lists 11 "
        "required storyboards (incl. guaranteed IO-approval), none backed end-to-end.",
    ),
    AdcpSpecialism.sales_broadcast_tv: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "get_products", "create_media_buy"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="tools implemented; specialisms/sales-broadcast-tv/index.yaml @ v3.1.1 lists 8 "
        "required storyboards (broadcast/FCC semantics), none backed end-to-end.",
    ),
    AdcpSpecialism.sales_catalog_driven: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "get_products", "create_media_buy"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="tools implemented; specialisms/sales-catalog-driven/index.yaml @ v3.1.1 lists 5 "
        "required storyboards (catalog/conversion), none backed end-to-end.",
    ),
    AdcpSpecialism.governance_aware_seller: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "create_media_buy"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="binding is registered via sync_governance (tools met) but check_governance "
        "enforcement is deliberately not implemented; specialisms/governance-aware-seller/index.yaml "
        "@ v3.1.1 lists 5 required storyboards, none backed end-to-end.",
    ),
    # --- media_buy parent, a required tool NOT implemented (declined on the tool gate) ---
    AdcpSpecialism.sales_social: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=(
            "sync_governance",
            "sync_audiences",
            "sync_catalogs",
            "sync_creatives",
            "sync_event_sources",
            "preview_creative",
        ),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="required tools sync_audiences/sync_catalogs/sync_event_sources/preview_creative "
        "not implemented (index.json @ v3.1.1).",
    ),
    AdcpSpecialism.audience_sync: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("list_accounts", "sync_audiences"),
        requires_scenarios=(),  # upstream lists no required storyboards
        rationale="required tool sync_audiences not implemented (index.json @ v3.1.1).",
    ),
    # --- deprecated in the pinned enum (deprecation is the disqualifier; read by the gate) ---
    AdcpSpecialism.sales_proposal_mode: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("get_products", "create_media_buy"),
        requires_scenarios=(),  # upstream lists no required storyboards
        rationale="DEPRECATED in the pinned adcp 6.6.0 enums/specialism.json x-deprecated-enum-values; "
        "a deprecated slot is never declared even though its tools are implemented. "
        "Enforced by test_declared_specialisms_are_valid_non_deprecated_pinned_enum_ids.",
    ),
    # --- parent protocol NOT hosted (declined on the parent rule; tools also not implemented) ---
    AdcpSpecialism.collection_lists: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.governance,
        required_tools=("create_collection_list",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: governance (not hosted).",
    ),
    AdcpSpecialism.content_standards: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.governance,
        required_tools=("list_content_standards",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: governance (not hosted).",
    ),
    AdcpSpecialism.property_lists: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.governance,
        required_tools=("create_property_list",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: governance (not hosted).",
    ),
    AdcpSpecialism.governance_delivery_monitor: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.governance,
        required_tools=("check_governance",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: governance (not hosted).",
    ),
    AdcpSpecialism.governance_spend_authority: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.governance,
        required_tools=("sync_plans", "check_governance"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: governance (not hosted).",
    ),
    AdcpSpecialism.creative_ad_server: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.creative,
        required_tools=("build_creative",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: creative — we CALL remote creative agents, do not host the creative protocol.",
    ),
    AdcpSpecialism.creative_generative: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.creative,
        required_tools=("build_creative",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: creative (not hosted).",
    ),
    AdcpSpecialism.creative_template: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.creative,
        required_tools=("build_creative",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: creative (not hosted).",
    ),
    AdcpSpecialism.creative_transformers: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.creative,
        required_tools=("list_transformers", "build_creative"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: creative (not hosted).",
    ),
    AdcpSpecialism.brand_rights: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.brand,
        required_tools=("get_brand_identity",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: brand (not hosted).",
    ),
    AdcpSpecialism.signal_marketplace: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.signals,
        required_tools=("get_signals", "activate_signal"),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: signals — signals tools intentionally removed (dedicated signal agents).",
    ),
    AdcpSpecialism.signal_owned: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.signals,
        required_tools=("get_signals",),
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="parent: signals (not hosted).",
    ),
    AdcpSpecialism.sponsored_intelligence: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=SupportedProtocol.sponsored_intelligence,
        required_tools=(),  # index.json @ v3.1.1: preview, no required tools
        requires_scenarios=(),  # upstream lists no required storyboards
        rationale="parent: sponsored-intelligence (PREVIEW/ungraded, not hosted).",
    ),
    # --- not a compliance specialism (no bundle entry; deprecated in the pinned enum) ---
    AdcpSpecialism.signed_requests: _SpecialismAudit(
        decision=_SpecialismDecision.DECLINED,
        parent_protocol=None,
        required_tools=(),  # not in the compliance bundle
        requires_scenarios=NOT_TRANSCRIBED,
        rationale="DEPRECATED in the pinned enum; not a compliance specialism — request signing is "
        "expressed via the request_signing.supported capability, not a specialism.",
    ),
}

# Derived view — the declared specialism ids the wire advertises. Derived from the audit table
# so the two cannot drift; empty today (no specialism's full gate is backed — see the table).
_DECLARED_SPECIALISMS: list[AdcpSpecialism] = [
    sid for sid, row in _SPECIALISM_AUDIT.items() if row.decision is _SpecialismDecision.DECLARED
]


def _adcp_metadata() -> Adcp:
    """Agent-level AdCP metadata (major versions + idempotency).

    ``idempotency.supported = False`` (the ``Idempotency3`` variant). The 3.1.1 schema
    scopes the ``supported = True`` claim to ALL mutating requests ("the seller
    deduplicates replays … without re-executing side effects") and has NO per-tool field,
    so the declaration is agent-wide. Honest status of the mutating tools (#1329):

    * create_media_buy — the spend-committing tool — DOES dedup via
      ``uow.idempotency_attempts`` (a replay returns the cached response, no double spend).
    * update_media_buy, sync_accounts and sync_governance accept ``idempotency_key`` but do
      NOT dedup — a replay re-executes (sync_governance re-emits its audit event).

    Only 1 of the 4 wire-facing mutating tools dedups, so the agent-wide ``supported =
    True`` claim would be false. The schema's ``supported`` is a ``Literal[True]`` const, so
    the only honest way to NOT claim agent-wide dedup is the ``Idempotency3`` (``supported =
    False``) variant — advertised here. The tension the reviewer noted is real and
    upstream-worthy: ``False`` UNDERSTATES create_media_buy, but a per-tool declaration does
    not exist at 3.1.1, and understating a covered tool is safe (a buyer merely retries
    without relying on server dedup) where OVERSTATING three uncovered tools is not.
    Implementing storyboard-graded replay (replay/CONFLICT/EXPIRED) for the remaining
    mutating tools — which would license flipping this to ``True`` — is the tracked
    follow-up (#1934), NOT silently claimed done here.
    """
    return Adcp(
        major_versions=[MajorVersion(root=3)],
        idempotency=IdempotencyUnsupported(supported=False),
    )


def _build_capabilities_response(
    tenant: TenantLike | None,
    *,
    media_buy: MediaBuy | None = None,
    last_updated: datetime | None = None,
) -> GetAdcpCapabilitiesResponse:
    """Build the capabilities envelope shared by the minimal (no-tenant) and full paths.

    The two paths differ ONLY in the account tenant, the media_buy block, and
    last_updated; the adcp metadata, supported protocols, and declared specialisms are
    identical. Constructing the envelope once here removes the twice-built duplicate the
    two paths otherwise carry — adding a specialism now lands on both paths (#1329).
    """
    return GetAdcpCapabilitiesResponse(
        adcp=_adcp_metadata(),
        supported_protocols=list(_SUPPORTED_PROTOCOLS),
        specialisms=list(_DECLARED_SPECIALISMS),
        account=_build_account_capability(tenant),
        media_buy=media_buy,
        last_updated=last_updated,
    )


def _get_adcp_capabilities_impl(
    req: GetAdcpCapabilitiesRequest | None = None, identity: ResolvedIdentity | None = None
) -> GetAdcpCapabilitiesResponse:
    """Shared implementation for get_adcp_capabilities.

    Returns the capabilities of this sales agent per AdCP spec.

    Args:
        req: GetAdcpCapabilitiesRequest (optional, currently unused)
        identity: Resolved identity from transport boundary

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    # Extract principal and tenant from resolved identity
    principal_id = identity.principal_id if identity else None
    tenant = identity.tenant if identity else None

    if not tenant:
        # Return minimal capabilities if no tenant context
        return _build_capabilities_response(None)

    # If we got here, tenant is truthy, which means identity was not None on line 84
    identity = require_identity(identity, context=req.context if req else None)

    tenant_id = tenant["tenant_id"]
    tenant_name = tenant.get("name", "Unknown")

    # Log activity
    log_tool_activity(identity, "get_adcp_capabilities")

    # Get adapter to determine channels and capabilities
    primary_channels: list[MediaChannel] = []
    adapter = None
    try:
        # Get the Principal object to pass to adapter
        principal = get_principal_object(principal_id, tenant_id=identity.tenant_id) if principal_id else None

        if principal:
            adapter = get_adapter(principal, dry_run=True, tenant=tenant)
            if adapter and hasattr(adapter, "default_channels"):
                for channel_name in adapter.default_channels:
                    if channel_name.lower() in CHANNEL_MAPPING:
                        primary_channels.append(CHANNEL_MAPPING[channel_name.lower()])
    except Exception as e:
        logger.warning(f"Could not get adapter channels: {e}")

    # Default to display if we couldn't determine from adapter
    if not primary_channels:
        primary_channels = [MediaChannel.display]

    # Get publisher domains from database
    publisher_domains: list[PublisherDomain] = []
    try:
        with TenantConfigUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            partners = uow.tenant_config.list_publisher_partners()
            for partner in partners:
                if partner.publisher_domain:
                    publisher_domains.append(PublisherDomain(root=partner.publisher_domain))
    except Exception as e:
        logger.warning(f"Could not get publisher domains: {e}")

    # If no domains found, use a placeholder
    if not publisher_domains:
        # Use tenant name as placeholder domain
        publisher_domains = [PublisherDomain(root=f"{tenant.get('subdomain', 'unknown')}.example.com")]

    # Get advertising policies from tenant config
    advertising_policies: str | None = None
    if tenant.get("advertising_policy"):
        policy = tenant["advertising_policy"]
        if isinstance(policy, dict) and policy.get("description"):
            advertising_policies = policy["description"]

    # Build portfolio
    portfolio = Portfolio(
        description=f"Advertising inventory from {tenant_name}",
        primary_channels=primary_channels if primary_channels else None,
        publisher_domains=publisher_domains,
        advertising_policies=advertising_policies,
    )

    # Build features - be honest about what we actually support
    # These should be adapter-dependent in the future
    features = MediaBuyFeatures(
        # inline_creative_management: We have sync_creatives/list_creatives tools
        inline_creative_management=True,
        # property_list_filtering: True iff the bound adapter actually compiles
        # `targeting_overlay.property_list` into native ad-server targeting.
        # Today no adapter sets this — capability remains False; create/update
        # emit per-package UNSUPPORTED_FEATURE advisories on the success envelope
        # so buyers can see the silent-drop window. Kevel's siteId resolver flips
        # this True and the other 4 adapters hard-reject — same source of truth
        # via `supports_property_list_filtering()`.
        property_list_filtering=supports_property_list_filtering(adapter),
        # catalog_management: declared False until a sync_catalogs tool ships.
        # AdCP spec binds this flag to the buyer-driven sync_catalogs task
        # (SyncCatalogsRequest with account + catalogs[] + delete_missing) —
        # NOT the internal admin CRUD over the products table. Declaring True
        # without the tool would let buyers reach the boundary and get
        # UNSUPPORTED_FEATURE there instead of being warned at capability
        # discovery. Mirrors the property_list_filtering=False rationale above.
        catalog_management=False,
    )

    # Build targeting capabilities from adapter
    targeting_caps = None
    if adapter and hasattr(adapter, "get_targeting_capabilities"):
        targeting_caps = adapter.get_targeting_capabilities()

    # Build GeoMetros if any metro targeting is supported
    geo_metros = None
    if targeting_caps and any(
        [
            targeting_caps.nielsen_dma,
            targeting_caps.eurostat_nuts2,
            targeting_caps.uk_itl1,
            targeting_caps.uk_itl2,
        ]
    ):
        geo_metros = GeoMetros(
            nielsen_dma=targeting_caps.nielsen_dma or None,
            eurostat_nuts2=targeting_caps.eurostat_nuts2 or None,
            uk_itl1=targeting_caps.uk_itl1 or None,
            uk_itl2=targeting_caps.uk_itl2 or None,
        )

    # Build PostalAreaSupport if any postal targeting is supported
    geo_postal_areas = None
    if targeting_caps and any(
        [
            targeting_caps.us_zip,
            targeting_caps.us_zip_plus_four,
            targeting_caps.ca_fsa,
            targeting_caps.ca_full,
            targeting_caps.gb_outward,
            targeting_caps.gb_full,
            targeting_caps.de_plz,
            targeting_caps.fr_code_postal,
            targeting_caps.au_postcode,
        ]
    ):
        geo_postal_areas = PostalAreaSupport(
            us_zip=targeting_caps.us_zip or None,
            us_zip_plus_four=targeting_caps.us_zip_plus_four or None,
            ca_fsa=targeting_caps.ca_fsa or None,
            ca_full=targeting_caps.ca_full or None,
            gb_outward=targeting_caps.gb_outward or None,
            gb_full=targeting_caps.gb_full or None,
            de_plz=targeting_caps.de_plz or None,
            fr_code_postal=targeting_caps.fr_code_postal or None,
            au_postcode=targeting_caps.au_postcode or None,
        )

    targeting = Targeting(
        geo_countries=targeting_caps.geo_countries if targeting_caps else True,
        geo_regions=targeting_caps.geo_regions if targeting_caps else True,
        geo_metros=geo_metros,
        geo_postal_areas=geo_postal_areas,
    )

    # Build execution capabilities
    execution = Execution(
        targeting=targeting,
    )

    # Build media_buy capabilities
    media_buy = MediaBuy(
        portfolio=portfolio,
        features=features,
        execution=execution,
    )

    # Envelope shared with the no-tenant path (specialisms audit + idempotency
    # rationale live on _DECLARED_SPECIALISMS / _adcp_metadata above).
    return _build_capabilities_response(tenant, media_buy=media_buy, last_updated=datetime.now(UTC))


async def get_adcp_capabilities(
    protocols: list[str] | None = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Get the capabilities of this AdCP sales agent.

    MCP tool wrapper aligned with adcp v3.x spec.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Build request object (currently minimal)
    req = GetAdcpCapabilitiesRequest()

    # Call shared implementation
    response = _get_adcp_capabilities_impl(req, identity)

    # Build human-readable summary
    protocols = [enum_value(p) for p in response.supported_protocols]
    summary_parts = [
        f"AdCP v{response.adcp.major_versions[0].root} Capabilities",
        f"Supported protocols: {', '.join(protocols)}",
    ]

    if response.media_buy and response.media_buy.portfolio:
        portfolio = response.media_buy.portfolio
        if portfolio.description:
            summary_parts.append(f"Portfolio: {portfolio.description}")
        if portfolio.primary_channels:
            channels = [enum_value(c) for c in portfolio.primary_channels]
            summary_parts.append(f"Channels: {', '.join(channels)}")

    summary = "\n".join(summary_parts)

    return mcp_result(response, content=summary)


async def get_adcp_capabilities_raw(
    protocols: list[str] | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> GetAdcpCapabilitiesResponse:
    """Get the capabilities of this AdCP sales agent.

    Raw function without @mcp.tool decorator for A2A server use.

    Args:
        protocols: Specific protocols to query (optional, currently ignored)
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    req = GetAdcpCapabilitiesRequest()
    return _get_adcp_capabilities_impl(req, identity)
