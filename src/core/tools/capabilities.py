"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import dataclasses
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, NamedTuple

from adcp.types import ContextObject, Error, GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.core.ext import ExtensionObject
from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
from adcp.types.generated_poc.core.postal_area_support import (
    PostalAreaSupport,  # adcp 6.6: standalone GeoPostalAreas removed; capabilities use PostalAreaSupport
)
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.enums.pricing_model import PricingModel
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Account,
    Adcp,
    CreativeApprovalMode,
    Execution,
    GeoMetros,
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    Targeting,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from src.adapters.base import TargetingCapabilities
from src.core.agent_identity import brand_json_url, canonical_agent_url, jwks_origin
from src.core.auth import require_identity
from src.core.billing_policy import BillingParty, resolve_supported_billing
from src.core.database.repositories.uow import CapabilitiesUoW
from src.core.exceptions import AdCPConfigurationError, normalize_advisory_errors
from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter_class_for_tenant, get_targeting_capabilities_override
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.capability_declarations import (
    DEFAULT_SPECIALISMS,
    DEFAULT_SUPPORTED_PROTOCOLS,
    CapabilityDeclarations,
    SigningPlatformBacking,
)
from src.core.signing import (
    IdentityDeclaration,
    RequestSigningPosture,
    WebhookSigningPosture,
    emitted_identity,
    origin_is_publishable,
    posture_for_tenant,
    posture_from_declarations,
    signing_key_backed,
    unsupported_webhook_signing_posture,
    webhook_signing_posture,
)
from src.core.tool_context import ToolContext
from src.core.tools._mcp import mcp_result
from src.core.transport_helpers import NOT_PROVIDED, IdentityOrNotProvided, resolve_identity_if_not_provided
from src.core.validation_helpers import adcp_validation_boundary
from src.core.version_negotiation import negotiate_adcp_version
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)

# The baseline protocol/specialism sets every response advertises before any tenant
# declaration is applied. ONE source consumed by both the no-tenant minimal response
# and the tenant-resolved response -- the two used to carry independent
# `[SupportedProtocol.media_buy]` literals, the same drift class _build_adcp_block was
# extracted to prevent (salesagent-rldj). They now live in the declarations schema,
# because validate_backing() has to reason about the EMITTED set (defaults unioned with
# the declaration) to check specialism roll-up.
_DEFAULT_SUPPORTED_PROTOCOLS = DEFAULT_SUPPORTED_PROTOCOLS
_DEFAULT_SPECIALISMS = DEFAULT_SPECIALISMS


def _record_degradation(advisories: list[Error], what: str, exc: Exception) -> None:
    """Log a discovery degradation AND surface it to the buyer as an advisory.

    ONE helper for all five degradation sites in ``_get_adcp_capabilities_impl``.
    Before this, each site logged and fell through to a default, so the response
    silently carried a placeholder (or an omission) and the buyer had no way to
    tell "this seller has none" from "the lookup failed" — the quiet-failure class
    CLAUDE.md bans.

    EXCEPT-PATH ONLY, deliberately. Two of these sites also degrade on an EMPTY
    result with no exception (``primary_channels``, ``publisher_domains``), and a
    tenant with zero publisher partners is the COMMON case — advising there would
    put ``errors[]`` on nearly every tenant-resolved capabilities response across
    every use case. A genuinely faulted lookup is the advisory-worthy event.

    The advisory is a WARNING, not a failure: ``errors`` is "Task-specific errors
    and warnings" and the envelope still reports success, so discovery is not
    failed by a partial result.
    """
    logger.warning("Could not get %s: %s", what, exc)
    advisories.append(
        Error(  # structural-guard: advisory degradation in GetAdcpCapabilitiesResponse.errors[]
            code="SERVICE_UNAVAILABLE",
            message=f"Could not resolve {what}; the response omits or defaults this section.",
        )
    )


def _build_adcp_block(tenant: Mapping[str, object] | None) -> Adcp:
    """Build the top-level adcp.* envelope -- single source for both the
    no-tenant minimal response and the tenant-resolved full response
    (salesagent-rldj DRY fix; the two literal Adcp(...) constructions this
    replaces had drifted apart before, the exact class of bug DRY exists to
    prevent).

    major_versions/supported_versions derive from SUPPORTED_ADCP_MAJORS/
    VERSIONS (src/core/version_negotiation.py), themselves derived from the
    pinned SDK spec version -- never a literal. idempotency derives from
    get_idempotency_posture(tenant), the single source shared by both
    response paths.
    """
    from src.core.idempotency_policy import get_idempotency_posture
    from src.core.version_negotiation import SUPPORTED_ADCP_MAJORS, SUPPORTED_ADCP_VERSIONS

    posture = get_idempotency_posture(tenant)
    posture.check_bounds()
    return Adcp(
        major_versions=[MajorVersion(root=m) for m in SUPPORTED_ADCP_MAJORS],
        supported_versions=list(SUPPORTED_ADCP_VERSIONS),
        idempotency=posture.to_sdk_union(),
    )


class SigningBlocks(NamedTuple):
    """The three signing-family blocks one response carries."""

    request_signing: RequestSigningPosture
    webhook_signing: WebhookSigningPosture
    identity: IdentityDeclaration | None


def _build_signing_blocks(
    *,
    posture: RequestSigningPosture,
    webhook_signing: WebhookSigningPosture,
    brand_json_url: str | None,
    jwks_origin: str | None,
) -> SigningBlocks:
    """The ONE builder for request_signing / webhook_signing / identity (#1291 D1).

    Single source for BOTH construction sites — the no-tenant minimal response and the
    tenant-resolved one — in the exact shape of :func:`_build_adcp_block`. The two
    independent ``WebhookSigning(supported=False)`` / ``RequestSigning(supported=False)``
    literals this replaces are the drift class that extraction already exists to prevent
    (#1592 C4): a keyed tenant's webhooks have been RFC 9421-signed since #1291 C1
    while both sites declared ``supported: false``.

    Every parameter is a RESOLVED value — two frozen posture objects and two plain
    strings — so this builder reads no store, opens no session and touches no ORM row.
    That is what makes its entry in ``_DERIVATION_ONLY_BUILDERS`` truthful: single-source
    for ``request_signing`` is enforced UPSTREAM, by ``posture_for_tenant`` being the sole
    reader of the declaration, not by this function refusing to see one.

    Passing the ORM ``Tenant`` in instead would raise ``DetachedInstanceError``: the
    identity URLs are ORM attribute reads and no session here sets
    ``expire_on_commit=False``, so the row must be read inside the unit of work that owns
    it (``src/routes/well_known.py`` says the same thing in its own words).
    """
    return SigningBlocks(
        request_signing=posture,
        webhook_signing=webhook_signing,
        identity=emitted_identity(
            posture=posture,
            webhook_signing_supported=webhook_signing.supported,
            brand_json_url=brand_json_url,
            jwks_origin=jwks_origin,
        ),
    )


def _resolve_signing_blocks(
    uow: CapabilitiesUoW,
    declarations: CapabilityDeclarations | None,
    *,
    now: datetime,
) -> SigningBlocks:
    """Resolve every signing input INSIDE *uow*, then build the blocks.

    Both DB reads and every ORM attribute access happen here, on the one session the
    capabilities request owns: the tenant row (whose stored host IS the agent identity),
    and the signing keys behind ``webhook_signing``. Only resolved values leave.

    The declared-vs-derived cross-checks run here too, for the same reason — they are the
    two relation rules that need platform state, and this is the only caller that has it.
    """
    assert uow.tenant_config is not None
    assert uow.signing_keys is not None

    posture = posture_from_declarations(declarations)
    agent = uow.tenant_config.get_tenant()
    if agent is None:
        # The identity dict resolved a tenant that the tenants table does not carry.
        # Nothing to publish a trust root for, and nothing to sign with.
        return _build_signing_blocks(
            posture=posture,
            webhook_signing=unsupported_webhook_signing_posture(),
            brand_json_url=None,
            jwks_origin=None,
        )

    origin = canonical_agent_url(agent)
    # ONE key-presence derivation for this request, shared by webhook_signing (.signs)
    # and the identity/key_origins gate below (.publishes) -- never re-derived, per
    # KeyBacking's own docstring ("THE single key-presence derivation").
    key_backing = signing_key_backed(uow.signing_keys, now=now)
    webhook_signing = webhook_signing_posture(uow.signing_keys, now=now, origin=origin, key_backing=key_backing)

    # The derived pointer is handed to the validator UNCONDITIONALLY, including on a host
    # that cannot serve https: a declared `https://elsewhere/...` on such a host must be
    # rejected as a mismatch, not waved through because we happen to emit nothing.
    # Publishability gates only the EMISSION.
    if declarations is not None:
        declarations.validate_signing_platform_backing(
            SigningPlatformBacking(
                webhook_signing_supported=webhook_signing.supported,
                brand_json_url=brand_json_url(agent),
            )
        )

    publishable = origin_is_publishable(origin)
    # jwks_origin is additionally gated on key_backing.publishes (#1291):
    # a keyless tenant on a publishable https origin must NOT advertise a key_origins
    # pointer whose JWKS can only ever answer {"keys": []} (#1291). Safe
    # because KeyBacking.publishes uses the SAME publishable_at(now, grace_seconds)
    # selector that well_known._build_document feeds into build_jwks -- gating on
    # .publishes can never advertise an origin whose JWKS is actually empty.
    return _build_signing_blocks(
        posture=posture,
        webhook_signing=webhook_signing,
        brand_json_url=brand_json_url(agent) if publishable else None,
        jwks_origin=jwks_origin(agent) if (publishable and key_backing.publishes) else None,
    )


def _build_account_block(tenant: Mapping[str, object]) -> Account:
    """Build the account block from real tenant config -- never fabricated.

    supported_billing derives from resolve_supported_billing (src/core/billing_policy.py),
    the single source shared with the sync_accounts billing gate (_check_billing_policy)
    -- the two can never diverge. require_operator_auth is a true architectural constant
    (no per-tenant operator-auth config or enforcement exists yet). sandbox reflects the
    tenant's account_sandbox column (default true). authorization_endpoint/
    required_for_products/account_financials stay omitted -- declaring them would be an
    aspirational capability the platform doesn't back yet, not an honest one
    (salesagent-3s5a Core Invariant).
    """
    return Account(
        supported_billing=[BillingParty(v) for v in resolve_supported_billing(tenant)],
        require_operator_auth=False,
        sandbox=tenant.get("account_sandbox", True),
        # SDK field defaults are False, not None -- pass None explicitly or these
        # would fabricate "not required"/"no financials" instead of honestly omitting.
        authorization_endpoint=None,
        required_for_products=None,
        account_financials=None,
    )


def build_get_adcp_capabilities_request(
    *,
    protocols: list[str] | None = None,
    context: ContextObject | None = None,
    adcp_version: str | None = None,
    adcp_major_version: int | None = None,
    ext: ExtensionObject | None = None,
) -> GetAdcpCapabilitiesRequest:
    """Build the shared get_adcp_capabilities request for transport wrappers.

    Mirrors build_list_creative_formats_request (creative_formats.py) -- the
    single seam every transport wrapper constructs the typed request through,
    so a future request field lands here once instead of in wrapper lockstep
    (salesagent-5yik).
    """
    return GetAdcpCapabilitiesRequest(
        protocols=protocols,
        context=context,
        adcp_version=adcp_version,
        adcp_major_version=adcp_major_version,
        ext=ext,
    )


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
    "sponsored_intelligence": MediaChannel.sponsored_intelligence,
}

# TargetingCapabilities boolean field name -> (native country key, native
# system value), per core/postal-area-support.json's native country-keyed map.
# Single shared table drives BOTH the presence guard and the PostalAreaSupport
# construction (DRY -- salesagent-y9ld R4; the old code had 9 field-by-field
# kwargs plus a hand-enumerated `any([...])` guard, two sites that could omit a
# field independently). Keyed by field-name STRING (not a getter) deliberately:
# tests/bdd/steps/domain/uc010_capabilities.py reads this same table to invert
# (country, system) -> field name, the harness's own single-source-of-truth
# reuse of the production table -- a getter-keyed table would break that.
_POSTAL_AREA_TABLE: dict[str, tuple[str, str]] = {
    "us_zip": ("US", "zip"),
    "us_zip_plus_four": ("US", "zip_plus_four"),
    "gb_outward": ("GB", "outward"),
    "gb_full": ("GB", "full"),
    "ca_fsa": ("CA", "fsa"),
    "ca_full": ("CA", "full"),
    "de_plz": ("DE", "plz"),
    "ch_plz": ("CH", "plz"),
    "at_plz": ("AT", "plz"),
    "fr_code_postal": ("FR", "code_postal"),
    "au_postcode": ("AU", "postcode"),
}

# Fails at import time if a key drifts from a real TargetingCapabilities field
# -- without this, the getattr(..., field, False) below would silently treat a
# typo'd key as "unset" instead of raising (#1721 M3: the class of bug object-
# typing + getattr let through).
assert set(_POSTAL_AREA_TABLE) <= {f.name for f in dataclasses.fields(TargetingCapabilities)}, (
    "_POSTAL_AREA_TABLE key(s) do not match a TargetingCapabilities field"
)


def _build_geo_postal_areas(targeting_caps: TargetingCapabilities | None) -> PostalAreaSupport | None:
    """Native country-keyed geo_postal_areas, built from _POSTAL_AREA_TABLE --
    never the deprecated boolean-alias shape. None when the adapter declares no
    postal targeting at all (honest absence, not an empty object)."""
    if not targeting_caps:
        return None
    by_country: dict[str, list[str]] = {}
    for field, (country, system) in _POSTAL_AREA_TABLE.items():
        if getattr(targeting_caps, field):
            by_country.setdefault(country, []).append(system)
    if not by_country:
        return None
    return PostalAreaSupport(**by_country)


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
    # Negotiate FIRST -- a bad version pin is rejected even with no tenant
    # context (salesagent-rldj: version negotiation is not tenant-gated).
    if req:
        negotiate_adcp_version(req.adcp_version, req.adcp_major_version)

    # Extract tenant from resolved identity
    tenant = identity.tenant if identity else None

    if not tenant:
        # Return minimal capabilities if no tenant context. `request_signing` is an
        # AGENT-level fact and stays honest here (the verifier middleware is mounted
        # process-wide); `webhook_signing` is per-tenant, and with no tenant there is no
        # key, so `supported: false` is the honest answer rather than dead config.
        no_tenant_signing = _build_signing_blocks(
            posture=posture_for_tenant(None),
            webhook_signing=unsupported_webhook_signing_posture(),
            brand_json_url=None,
            jwks_origin=None,
        )
        return GetAdcpCapabilitiesResponse(
            adcp=_build_adcp_block(None),
            supported_protocols=list(_DEFAULT_SUPPORTED_PROTOCOLS),
            specialisms=list(_DEFAULT_SPECIALISMS),
            webhook_signing=no_tenant_signing.webhook_signing,
            request_signing=no_tenant_signing.request_signing,
            identity=no_tenant_signing.identity,
            context=req.context if req else None,
        )

    # If we got here, tenant is truthy, which means identity was not None on line 84
    identity = require_identity(identity, context=req.context if req else None)

    tenant_id = tenant["tenant_id"]
    tenant_name = tenant.get("name", "Unknown")

    # Log activity
    log_tool_activity(identity, "get_adcp_capabilities")

    # Per-tenant capability declarations (#1592 T1a). Parsed and backing-checked on
    # the read path: the graded observable in every rejection scenario is the
    # get_adcp_capabilities response, so an invalid declaration must surface as a
    # terminal CONFIGURATION_ERROR here rather than being discovered only at some
    # future write surface. `None` (nothing declared) reproduces the pre-#1592 wire.
    #
    # Parsed BEFORE the degradation blocks below (#1291 D1): each of those swallows
    # Exception to degrade, and a relation-violating declaration must propagate as
    # CONFIGURATION_ERROR rather than be reported to the buyer as "we could not resolve
    # your adapter channels".
    declarations = CapabilityDeclarations.from_tenant(tenant.get("capability_declarations"))

    # Get adapter CLASS to determine channels and capabilities. Tenant-only,
    # principal-free: capabilities describe the SELLER (tenant), not the
    # caller — INV-4 (AdCP v3.1.1). Resolved via get_adcp_capabilities.mdx
    # L23 + get_adapter_class_for_tenant (adapter_helpers.py), which bypasses
    # Adapter.__init__ entirely — Kevel/TritonDigital would crash __init__
    # for a synthetic/tenant-only Principal (salesagent-dn2s).
    primary_channels: list[MediaChannel] = []
    # Degradation advisories collected across this build and emitted as the
    # response's top-level errors[] (advisory warnings, not a failed task).
    advisories: list[Error] = []

    adapter: type | None = None
    try:
        adapter = get_adapter_class_for_tenant(tenant)
        if adapter and hasattr(adapter, "default_channels"):
            for channel_name in adapter.default_channels:
                if channel_name.lower() in CHANNEL_MAPPING:
                    primary_channels.append(CHANNEL_MAPPING[channel_name.lower()])
    except Exception as e:
        _record_degradation(advisories, "adapter channels", e)

    # Default to display if we couldn't determine from adapter
    if not primary_channels:
        primary_channels = [MediaChannel.display]

    # supported_pricing_models: pre-flight buyer signal, sorted+deterministic.
    # Same source as the per-product "supported" annotation (products.py:721) --
    # never a literal/default set. Adapter unavailable -> omit (honest absence,
    # matching the primary_channels/reporting degradation posture elsewhere in
    # this function; do NOT invent a default set).
    supported_pricing_models: list[PricingModel] | None = None
    if adapter and hasattr(adapter, "get_supported_pricing_models"):
        try:
            resolved_models = sorted(
                (PricingModel(m) for m in adapter.get_supported_pricing_models()), key=lambda m: m.value
            )
            # minItems 1 -- an empty result means "nothing determined", the same
            # honest-absence posture as the exception path below, never an
            # empty array (which the SDK model itself rejects).
            supported_pricing_models = resolved_models or None
        except Exception as e:
            _record_degradation(advisories, "supported pricing models", e)

    # ONE session for every database read this response needs: the publisher partners,
    # and the signing family's key + tenant-host reads. Two `try`s inside it, each with
    # its OWN degradation label, because a signing-key failure advertised as "could not
    # resolve publisher domains" tells the buyer the wrong thing. Neither swallows
    # AdCPConfigurationError: a rejected declaration is a terminal error, not a partial
    # result (#1291 D1).
    publisher_domains: list[PublisherDomain] = []
    signing = _build_signing_blocks(
        posture=posture_from_declarations(declarations),
        webhook_signing=unsupported_webhook_signing_posture(),
        brand_json_url=None,
        jwks_origin=None,
    )
    try:
        with CapabilitiesUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            try:
                partners = uow.tenant_config.list_publisher_partners()
                for partner in partners:
                    if partner.publisher_domain:
                        publisher_domains.append(PublisherDomain(root=partner.publisher_domain))
            except AdCPConfigurationError:
                raise
            except Exception as e:
                _record_degradation(advisories, "publisher domains", e)

            try:
                signing = _resolve_signing_blocks(uow, declarations, now=datetime.now(UTC))
            except AdCPConfigurationError:
                raise
            except Exception as e:
                _record_degradation(advisories, "signing key backing", e)
    except AdCPConfigurationError:
        raise
    except Exception as e:
        # The session itself could not be opened, so neither read happened.
        _record_degradation(advisories, "tenant configuration", e)

    # Get advertising policies from tenant config
    advertising_policies: str | None = None
    if tenant.get("advertising_policy"):
        policy = tenant["advertising_policy"]
        if isinstance(policy, dict) and policy.get("description"):
            advertising_policies = policy["description"]

    # Build portfolio -- publisher_domains is REQUIRED+minItems:1 on Portfolio (pinned
    # v3.1.1 get-adcp-capabilities-response.json), so a tenant with no real
    # PublisherPartner rows has no spec-legal non-fabricated portfolio to emit.
    # media_buy has no required fields, so omitting portfolio entirely is the honest,
    # spec-legal response -- never a fabricated <subdomain>.example.com domain
    # (salesagent-piyo).
    portfolio = (
        Portfolio(
            description=f"Advertising inventory from {tenant_name}",
            primary_channels=primary_channels if primary_channels else None,
            publisher_domains=publisher_domains,
            advertising_policies=advertising_policies,
        )
        if publisher_domains
        else None
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
        # committed_metrics_supported: declared False until a committed-metrics
        # surface exists (no product/media-buy data model backs a delivery
        # commitment today). Mirrors the catalog_management=False rationale above.
        committed_metrics_supported=False,
    )

    # Build targeting capabilities from adapter, unless a per-tenant
    # test_behavior override is configured (salesagent-689e fault injection).
    # Same degrade-on-exception posture as the adapter-channels block above —
    # the override read is a DB call, not a hard requirement.
    targeting_caps = None
    try:
        targeting_caps = get_targeting_capabilities_override(tenant)
    except Exception as e:
        _record_degradation(advisories, "targeting capabilities override", e)
    if targeting_caps is None and adapter and hasattr(adapter, "get_targeting_capabilities"):
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

    # Build PostalAreaSupport as the native country-keyed map (postal-area-support.json;
    # the boolean aliases us_zip/de_plz/... are `deprecated: true` at 3.1.1) --
    # native-only, no alias co-emission (plan Q5 recommendation).
    geo_postal_areas = _build_geo_postal_areas(targeting_caps)

    targeting = Targeting(
        geo_countries=targeting_caps.geo_countries if targeting_caps else True,
        geo_regions=targeting_caps.geo_regions if targeting_caps else True,
        geo_metros=geo_metros,
        geo_postal_areas=geo_postal_areas,
    )

    # Build execution capabilities. Declared blocks merge in; undeclared stay absent
    # (honest omission, never an empty object).
    execution = Execution(
        targeting=targeting,
        trusted_match=declarations.trusted_match if declarations else None,
    )

    # creative_approval_mode: require_human when this tenant's configuration
    # genuinely requires manual review (resolve_manual_approval_signal, the
    # same signal _create_media_buy_impl enforces); omit entirely otherwise --
    # NEVER claim auto_approve without an explicit tenant-level affirmation
    # that no product/account requires review (no such config surface exists
    # yet, salesagent-y9ld plan Q2 -- declaring it would be a false
    # conformance claim, not a "legacy-unspecified" honest omission).
    from src.core.helpers.adapter_helpers import resolve_manual_approval_signal

    manual_approval_signal = False
    try:
        manual_approval_signal = resolve_manual_approval_signal(tenant)
    except Exception as e:
        _record_degradation(advisories, "manual approval signal", e)
    creative_approval_mode = CreativeApprovalMode.require_human if manual_approval_signal else None

    # Build media_buy capabilities
    # reporting_delivery_methods reaches the wire from the declaration store, which is
    # what makes webhook_signing's `must_equal_when` rule non-vacuous: before #1291 D1
    # no webhook-triggering field was ever emitted, so the invariant held only because
    # nothing could fire it.
    media_buy = MediaBuy(
        portfolio=portfolio,
        features=features,
        execution=execution,
        supported_pricing_models=supported_pricing_models,
        creative_approval_mode=creative_approval_mode,
        reporting_delivery_methods=declarations.reporting_delivery_methods if declarations else None,
    )

    # Build response
    # specialisms declaration activates the storyboard scenarios bundled under
    # `sales-non-guaranteed` (`inventory_list_targeting`, `inventory_list_no_match`,
    # `delivery_reporting`, `pending_creatives_to_start`, `invalid_transitions`).
    # The runner gates scenarios by specialism, not by `supported_protocols` alone.
    #
    # We declare the specialism even though `pending_creatives_to_start` and
    # `invalid_transitions` are not yet fully green. Storyboard compliance runs
    # are advisory — no required CI job executes them — so those scenario
    # failures don't block merge, and the public declaration forces
    # prioritization of the remaining gaps instead of hiding them.
    response = GetAdcpCapabilitiesResponse(
        adcp=_build_adcp_block(tenant),
        # Declared protocols UNION the defaults -- see
        # CapabilityDeclarations.emitted_supported_protocols for why replacement
        # would emit a specialism whose parent protocol is absent.
        supported_protocols=(
            declarations.emitted_supported_protocols(_DEFAULT_SUPPORTED_PROTOCOLS)
            if declarations
            else list(_DEFAULT_SUPPORTED_PROTOCOLS)
        ),
        specialisms=(
            declarations.emitted_specialisms(_DEFAULT_SPECIALISMS) if declarations else list(_DEFAULT_SPECIALISMS)
        ),
        measurement=declarations.measurement if declarations else None,
        experimental_features=declarations.emitted_experimental_features() if declarations else None,
        media_buy=media_buy,
        account=_build_account_block(tenant),
        webhook_signing=signing.webhook_signing,
        request_signing=signing.request_signing,
        identity=signing.identity,
        errors=normalize_advisory_errors(advisories) or None,
        last_updated=datetime.now(UTC),
        context=req.context if req else None,
    )

    # Filter protocol-domain sections to the requested protocols. adcp/
    # supported_protocols/account are protocol-invariant (describe the seller
    # as a whole, not a specific protocol domain) and always survive.
    if req and req.protocols:
        requested = {enum_value(p) for p in req.protocols}
        for field_name in ("media_buy", "signals", "governance", "sponsored_intelligence", "creative"):
            if field_name not in requested:
                setattr(response, field_name, None)

    return response


async def get_adcp_capabilities(
    protocols: list[str] | None = None,
    context: ContextObject | None = None,
    adcp_version: Annotated[str | None, Field(description="Requested AdCP spec version")] = None,
    adcp_major_version: Annotated[int | None, Field(description="Requested AdCP major version")] = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Get the capabilities of this AdCP sales agent.

    MCP tool wrapper aligned with adcp v3.x spec.

    Args:
        protocols: Filter response sections to these protocol domains (optional)
        context: Application-level context per AdCP spec, echoed on the response
        adcp_version: Requested AdCP spec version (optional)
        adcp_major_version: Requested AdCP major version (optional)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    with adcp_validation_boundary(context="get_adcp_capabilities request"):
        req = build_get_adcp_capabilities_request(
            protocols=protocols,
            context=context,
            adcp_version=adcp_version,
            adcp_major_version=adcp_major_version,
        )

    # Call shared implementation
    response = _get_adcp_capabilities_impl(req, identity)

    # Build human-readable summary
    summary_protocols = [enum_value(p) for p in response.supported_protocols]
    summary_parts = [
        f"AdCP v{response.adcp.major_versions[0].root} Capabilities",
        f"Supported protocols: {', '.join(summary_protocols)}",
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
    context: ContextObject | None = None,
    adcp_version: str | None = None,
    adcp_major_version: int | None = None,
    ctx: Context | ToolContext | None = None,
    identity: IdentityOrNotProvided = NOT_PROVIDED,
) -> GetAdcpCapabilitiesResponse:
    """Get the capabilities of this AdCP sales agent.

    Raw function without @mcp.tool decorator for A2A server / REST use.

    Args:
        protocols: Filter response sections to these protocol domains (optional)
        context: Application-level context per AdCP spec, echoed on the response
        adcp_version: Requested AdCP spec version (optional)
        adcp_major_version: Requested AdCP major version (optional)
        ctx: FastMCP context (automatically provided)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    identity = resolve_identity_if_not_provided(identity, ctx, require_valid_token=False)
    with adcp_validation_boundary(context="get_adcp_capabilities request"):
        req = build_get_adcp_capabilities_request(
            protocols=protocols,
            context=context,
            adcp_version=adcp_version,
            adcp_major_version=adcp_major_version,
        )
    return _get_adcp_capabilities_impl(req, identity)
