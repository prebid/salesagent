"""What this seller is, and what it can do — derived once, for every consumer.

Two things answer that question on the wire: ``get_adcp_capabilities`` and the A2A
agent card. They describe the same seller from the same tenant, so they read one
derivation and differ only in how they render it. This module IS that derivation.

It takes an identity and nothing else. No request, no headers, no transport, no
protocol. That is what makes divergence impossible rather than merely discouraged:
both consumers make the same call, so the card cannot show data the tool would not
have shown — only more of it, since the tool additionally drops the protocol-domain
sections a buyer did not ask for. Handing that filter down here would let the two
callers pass different arguments, which is the defect this module removes.

The cost of keeping the filter out is real and accepted: a request naming one
protocol still derives the sections it discards. Discovery is not a hot path.
"""

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
from adcp.types.generated_poc.core.postal_area_support import (
    PostalAreaSupport,  # adcp 6.6: standalone GeoPostalAreas removed; capabilities use PostalAreaSupport
)
from adcp.types.generated_poc.enums.channels import MediaChannel
from adcp.types.generated_poc.enums.pricing_model import PricingModel
from adcp.types.generated_poc.protocol.get_adcp_capabilities_request import Protocol as RequestProtocol
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    # Aliased: three distinct types in src/ are named Account -- the ORM row
    # (imported as DBAccount in accounts.py), the domain schema
    # (src/core/schemas/account.py, imported BARE by accounts.py), and this
    # capabilities block. Two sibling modules in one package binding the same bare
    # name to different types is a rename waiting to go wrong. Mirrors this file's
    # own Measurement -> LibraryMeasurementDeclaration precedent; deliberately NOT
    # Library*-prefixed, since that prefix signals a schema-inheritance obligation
    # this tools-module alias does not carry.
    Account as AccountCapabilities,
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Adcp,
    CreativeApprovalMode,
    Execution,
    GeoMetros,
    MajorVersion,
    MediaBuy,
    Portfolio,
    PublisherDomain,
    RequestSigning,
    Targeting,
    WebhookSigning,
)
from pydantic import BaseModel, ConfigDict, Field

from src.adapters.base import TargetingCapabilities
from src.core.agent_identity import AGENT_ENDPOINT_PATHS, canonical_agent_url
from src.core.billing_policy import BillingParty, resolve_account_sandbox, resolve_supported_billing
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.errors.codes import ErrorCode
from src.core.errors.details import CapabilityRefusalDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.helpers.adapter_helpers import (
    get_adapter_class_for_tenant,
)
from src.core.helpers.channel_helpers import effective_channel_names
from src.core.resolved_identity import PublicIdentity
from src.core.schemas import Error
from src.core.schemas.capability_declarations import (
    DEFAULT_SPECIALISMS,
    DEFAULT_SUPPORTED_PROTOCOLS,
    CapabilityDeclarations,
)
from src.core.tenant_context import TenantContext
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)

# webhook_signing / request_signing: agent-level facts (no RFC 9421 request/webhook
# signing implemented today), not tenant config -- declared identically on every
# response, in-process and no-tenant alike (#1592).
#
# The must_equal_when invariant here is satisfied HONESTLY, not vacuously, and the
# distinction matters. v3.1.1 get-adcp-capabilities-response.json requires that when
# media_buy.reporting_delivery_methods contains "webhook", webhook_signing.supported
# MUST be true -- "emitting state-changing webhooks unsigned is a downgrade vector
# that lets an on-path attacker forge delivery callbacks".
#
# Production DOES push reporting webhooks, signed with LEGACY HMAC
# (get_adcp_signed_headers_for_webhook, src/services/protocol_webhook_service.py).
# But webhook_signing means RFC 9421 specifically, which is genuinely unimplemented
# (#1291). So declaring reporting_delivery_methods: ["webhook"] would be
# SPEC-FORBIDDEN while signing is off -- omitting it is the mandatory-honest choice,
# and this block is already correct. #1592's final field closes when #1291 lands: a
# real spec dependency, not a gap in this implementation.
#
# Whether HMAC-only delivery should be gated off pending RFC 9421 is the signing
# PR's decision, not this one's.
_WEBHOOK_SIGNING_UNSUPPORTED = WebhookSigning(supported=False)
_REQUEST_SIGNING_UNSUPPORTED = RequestSigning(supported=False)

# The baseline protocol/specialism sets every response advertises before any tenant
# declaration is applied. ONE source consumed by both the no-tenant minimal response
# and the tenant-resolved response -- the two used to carry independent
# `[SupportedProtocol.media_buy]` literals, the same drift class _build_adcp_block was
# extracted to prevent (salesagent-rldj). They now live in the declarations schema,
# because validate_backing() has to reason about the EMITTED set (defaults unioned with
# the declaration) to check specialism roll-up.
_DEFAULT_SUPPORTED_PROTOCOLS = DEFAULT_SUPPORTED_PROTOCOLS
_DEFAULT_SPECIALISMS = DEFAULT_SPECIALISMS

#: Response sections that belong to ONE protocol domain, so `protocols` filters them.
#: Derived from the request enum the buyer selects with, not hand-listed, so a domain
#: the spec adds cannot silently keep surviving a filter that never heard of it.
#: Pinned against the response model by test_architecture_capability_constant_parity.
_PROTOCOL_DOMAIN_SECTIONS: frozenset[str] = frozenset(p.value for p in RequestProtocol)


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
        Error.of(  # structural-guard: advisory degradation in GetAdcpCapabilitiesResponse.errors[]
            ErrorCode.SERVICE_UNAVAILABLE,
            details=CapabilityRefusalDetails(capability=what),
        )
    )


def _resolve_or_degrade[T](advisories: list[Error], what: str, resolve: Callable[[], T], *, default: T) -> T:
    """Run *resolve*; on failure record a degradation advisory and return *default*.

    ONE body for all five discovery lookups that degrade rather than fail the
    response. Each site used to spell its own try/except/_record_degradation/
    fall-back-to-a-default, which is five chances to forget the advisory (and
    silently emit a placeholder, the quiet-failure class CLAUDE.md bans) or to
    let an exception escape and 500 a response that is meant to degrade.

    Broad ``except Exception`` is deliberate and matches what it replaces: this
    is the degradation boundary, and the advisory is how the buyer learns a
    section is missing rather than empty.
    """
    try:
        return resolve()
    except Exception as e:
        _record_degradation(advisories, what, e)
        return default


def _build_adcp_block(tenant: TenantContext | None) -> Adcp:
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


def _build_account_block(tenant: TenantContext) -> AccountCapabilities | None:
    """Build the account block from real tenant config -- never fabricated.

    Returns None when the seller supports NO billing model. The block is
    all-or-nothing per schema: ``supported_billing`` is required on it and is
    minItems 1 (v3.1.1 get-adcp-capabilities-response.json#/properties/account),
    while ``account`` itself is optional. So a seller with an explicitly empty
    billing policy has no schema-legal block to emit -- omitting it is the only
    conformant answer, and emitting it with an empty array is a schema-INVALID
    response (which is what this function used to build unconditionally).

    supported_billing derives from resolve_supported_billing (src/core/billing_policy.py),
    the single source shared with the sync_accounts billing gate (_check_billing_policy)
    -- the two can never diverge. require_operator_auth is a true architectural constant
    (no per-tenant operator-auth config or enforcement exists yet). sandbox reflects the
    tenant's account_sandbox column via resolve_account_sandbox (default FALSE --
    support is opted into, never assumed from an unset column). authorization_endpoint/
    required_for_products/account_financials stay omitted -- declaring them would be an
    aspirational capability the platform doesn't back yet, not an honest one
    (#1592 Core Invariant).
    """
    supported_billing = resolve_supported_billing(tenant)
    if not supported_billing:
        return None

    return AccountCapabilities(
        supported_billing=[BillingParty(v) for v in supported_billing],
        require_operator_auth=False,
        sandbox=resolve_account_sandbox(tenant),
        # SDK field defaults are False, not None -- pass None explicitly or these
        # would fabricate "not required"/"no financials" instead of honestly omitting.
        authorization_endpoint=None,
        required_for_products=None,
        account_financials=None,
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
# An explicit raise, not `assert`: `python -O` strips asserts, and a stripped
# invariant is one that silently stops holding in exactly the environment where
# a typo'd key would do the most damage. RuntimeError, not AdCPSalesAgentError -- this
# fires at IMPORT time on a developer error; there is no request to attach a
# buyer-facing code or recovery to.
if not set(_POSTAL_AREA_TABLE) <= {f.name for f in dataclasses.fields(TargetingCapabilities)}:
    raise RuntimeError(
        "_POSTAL_AREA_TABLE key(s) do not match a TargetingCapabilities field: "
        f"{sorted(set(_POSTAL_AREA_TABLE) - {f.name for f in dataclasses.fields(TargetingCapabilities)})}"
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


class SellerCapabilities(BaseModel):
    """Everything either consumer needs about this seller, rendered by neither.

    A named type rather than a tuple of returns, and deliberately NOT the wire
    response: ``GetAdcpCapabilitiesResponse`` is one rendering of this, and the card
    is the other. ``agent_url`` lives here because where a seller is reachable is a
    fact about the seller, not about the request that asked — and it is the one field
    the card needs that the capabilities response has no home for.

    ``agent_url`` is ``None`` exactly when no tenant resolved. A caller with nothing
    to publish decides for itself what that means: the capabilities tool answers the
    minimal description, the agent card answers 404, because a host this deployment
    does not serve has no card.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    adcp: Adcp
    supported_protocols: list[Any]
    specialisms: list[Any]
    webhook_signing: WebhookSigning
    request_signing: RequestSigning
    agent_url: str | None = None
    measurement: Any | None = None
    experimental_features: Any | None = None
    media_buy: MediaBuy | None = None
    account: AccountCapabilities | None = None
    #: Degradations collected while deriving, for whichever consumer has a lane for
    #: them. The capabilities response emits them as top-level ``errors[]`` advisories;
    #: an AgentCard has no such field, so the card drops them and the server-side log
    #: made at each degradation site is the only record.
    advisories: list[Error] = Field(default_factory=list)


def describe_seller(identity: PublicIdentity) -> SellerCapabilities:
    """This seller's capabilities for *identity*'s tenant.

    The minimal description when no tenant resolved — this is the branch that used to
    sit in ``_get_adcp_capabilities_impl`` and is the reason a public tool can answer
    an unrouted host at all.
    """
    tenant = identity.tenant

    if not tenant:
        return SellerCapabilities(
            adcp=_build_adcp_block(None),
            supported_protocols=list(_DEFAULT_SUPPORTED_PROTOCOLS),
            specialisms=list(_DEFAULT_SPECIALISMS),
            webhook_signing=_WEBHOOK_SIGNING_UNSUPPORTED,
            request_signing=_REQUEST_SIGNING_UNSUPPORTED,
        )

    tenant_id = tenant.tenant_id
    tenant_name = tenant.name

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

    # Resolved OUTSIDE the channel-mapping closure, deliberately. `adapter` also
    # gates supported_pricing_models and the targeting-caps fallback below, so if
    # the closure owned this binding a failure while MAPPING channels would
    # discard an adapter class that resolved perfectly well -- one degradation
    # cascading into two more absent sections, and the pricing-models one would
    # vanish with no advisory of its own (its `if adapter` guard just skips).
    # Two lookups, two independently-reported degradations.
    adapter: type | None = _resolve_or_degrade(
        advisories, "adapter", lambda: get_adapter_class_for_tenant(tenant), default=None
    )

    def _map_portfolio_channels() -> None:
        # portfolio.primary_channels is "Primary advertising channels in this
        # PORTFOLIO" (get-adcp-capabilities-response.json), and the portfolio is the
        # tenant's product catalog -- the same thing its sibling publisher_domains
        # already summarizes from a real per-tenant table. So the channels are the
        # union of what each product effectively offers, under the ONE rule
        # get_products applies per product (channel_helpers). A seller whose catalog
        # declares its channels was previously described by its adapter CLASS's
        # constant, which is per-adapter-type and cannot vary by tenant at all.
        #
        # A tenant with NO catalog falls back to the adapter's defaults: an empty
        # catalog is not a claim of "no channels", and the ad server is the
        # next-best answer -- the same reasoning channel_helpers applies to a
        # product that declares none.
        from src.core.database.repositories.uow import ProductUoW

        defaults = adapter.default_channels if adapter and hasattr(adapter, "default_channels") else []
        # Resolved INSIDE the UoW block: the rows are session-bound, and reading
        # `channels` after the block closed raises DetachedInstanceError.
        with ProductUoW(tenant_id) as uow:
            assert uow.products is not None
            per_product = [
                effective_channel_names(product.channels, adapter_defaults=defaults)
                for product in uow.products.list_all()
            ]
        names = set().union(*per_product) if per_product else effective_channel_names(None, adapter_defaults=defaults)
        # Emitted in the pinned enum's own order (channels.json#/enum), not the
        # catalog's or a set's. A union has no order, and the wire list must be
        # deterministic for the same catalog on every call.
        mapped = {CHANNEL_MAPPING[name] for name in names if name in CHANNEL_MAPPING}
        primary_channels.extend(channel for channel in MediaChannel if channel in mapped)

    _resolve_or_degrade(advisories, "portfolio channels", _map_portfolio_channels, default=None)

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

        def _resolve_pricing_models() -> list[PricingModel] | None:
            resolved_models = sorted(
                (PricingModel(m) for m in adapter.get_supported_pricing_models()), key=lambda m: m.value
            )
            # minItems 1 -- an empty result means "nothing determined", the same
            # honest-absence posture as the degradation default, never an empty
            # array (which the SDK model itself rejects).
            return resolved_models or None

        supported_pricing_models = _resolve_or_degrade(
            advisories, "supported pricing models", _resolve_pricing_models, default=None
        )

    # Get publisher domains from database
    def _resolve_publisher_domains() -> list[PublisherDomain]:
        resolved: list[PublisherDomain] = []
        with TenantConfigUoW(tenant_id) as uow:
            if uow.tenant_config is None:
                raise AdCPConfigurationError()
            for partner in uow.tenant_config.list_publisher_partners():
                if partner.publisher_domain:
                    resolved.append(PublisherDomain(root=partner.publisher_domain))
        return resolved

    publisher_domains: list[PublisherDomain] = _resolve_or_degrade(
        advisories, "publisher domains", _resolve_publisher_domains, default=[]
    )

    # If no domains found, use a placeholder
    if not publisher_domains:
        # Use tenant name as placeholder domain
        publisher_domains = [PublisherDomain(root=f"{tenant.subdomain}.example.com")]

    # Get advertising policies from tenant config
    advertising_policies: str | None = None
    policy = tenant.advertising_policy
    if policy:
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
        # committed_metrics_supported: declared False until a committed-metrics
        # surface exists (no product/media-buy data model backs a delivery
        # commitment today). Mirrors the catalog_management=False rationale above.
        committed_metrics_supported=False,
    )

    # Build targeting capabilities from adapter, unless a per-tenant
    # test_behavior override is configured (salesagent-689e fault injection).
    # Same degrade-on-exception posture as the adapter-channels block above —
    # the override read is a DB call, not a hard requirement.
    def _resolve_targeting_caps() -> TargetingCapabilities | None:
        # INSIDE the degradation boundary: an adapter raising here is recorded as an
        # advisory, not surfaced as a 500.
        if adapter and hasattr(adapter, "get_targeting_capabilities"):
            return adapter.get_targeting_capabilities()
        return None

    targeting_caps = _resolve_or_degrade(advisories, "targeting capabilities", _resolve_targeting_caps, default=None)

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

    # Per-tenant capability declarations (#1592 T1a). Parsed and backing-checked on
    # the read path: the graded observable in every rejection scenario is the
    # get_adcp_capabilities response, so an invalid declaration must surface as a
    # terminal CONFIGURATION_ERROR here rather than being discovered only at some
    # future write surface. `None` (nothing declared) reproduces the pre-#1592 wire.
    declarations = CapabilityDeclarations.from_tenant(tenant.capability_declarations)

    # Build execution capabilities. Declared blocks merge in; undeclared stay absent
    # (honest omission, never an empty object).
    execution = Execution(
        targeting=targeting,
        trusted_match=declarations.trusted_match,
    )

    # creative_approval_mode: require_human when this tenant's configuration
    # genuinely requires manual review (resolve_manual_approval_signal, the
    # same signal _create_media_buy_impl enforces); omit entirely otherwise --
    # NEVER claim auto_approve without an explicit tenant-level affirmation
    # that no product/account requires review (no such config surface exists
    # yet, salesagent-y9ld plan Q2 -- declaring it would be a false
    # conformance claim, not a "legacy-unspecified" honest omission).
    from src.core.helpers.adapter_helpers import resolve_manual_approval_signal

    manual_approval_signal = _resolve_or_degrade(
        advisories, "manual approval signal", lambda: resolve_manual_approval_signal(tenant), default=False
    )
    creative_approval_mode = CreativeApprovalMode.require_human if manual_approval_signal else None

    # Build media_buy capabilities
    media_buy = MediaBuy(
        portfolio=portfolio,
        features=features,
        execution=execution,
        supported_pricing_models=supported_pricing_models,
        creative_approval_mode=creative_approval_mode,
    )
    # The canonical origin plus the path the app actually serves A2A at. Read from the
    # tenant's STORED host, never from a request header: a tenant answering on several
    # hosts would otherwise publish several identities, and the card's URL must be the
    # byte-identical string brand.json's ``agents[].url`` carries.
    agent_url = canonical_agent_url(tenant) + AGENT_ENDPOINT_PATHS["a2a"]

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
    return SellerCapabilities(
        adcp=_build_adcp_block(tenant),
        # Declared protocols UNION the defaults -- see
        # CapabilityDeclarations.emitted_supported_protocols for why replacement
        # would emit a specialism whose parent protocol is absent.
        supported_protocols=declarations.emitted_supported_protocols(_DEFAULT_SUPPORTED_PROTOCOLS),
        specialisms=declarations.emitted_specialisms(_DEFAULT_SPECIALISMS),
        measurement=declarations.measurement,
        experimental_features=declarations.emitted_experimental_features(),
        media_buy=media_buy,
        account=_build_account_block(tenant),
        webhook_signing=_WEBHOOK_SIGNING_UNSUPPORTED,
        request_signing=_REQUEST_SIGNING_UNSUPPORTED,
        agent_url=agent_url,
        advisories=advisories,
    )
