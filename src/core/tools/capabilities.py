"""Get AdCP Capabilities tool implementation.

Returns the capabilities of this sales agent including supported protocols,
targeting dimensions, creative specs, and portfolio information.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

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
    Targeting,
)

from src.adapters.base import TargetingCapabilities
from src.core.agent_identity import brand_json_url, canonical_agent_url, jwks_origin
from src.core.billing_policy import BillingParty, resolve_account_sandbox, resolve_supported_billing
from src.core.database.repositories.uow import CapabilitiesUoW
from src.core.errors.codes import ErrorCode
from src.core.errors.details import CapabilityRefusalDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.helpers import enum_value
from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import (
    get_adapter_class_for_tenant,
)
from src.core.helpers.channel_helpers import effective_channel_names
from src.core.resolved_identity import PublicIdentity
from src.core.schemas import Error, GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse
from src.core.schemas.capability_declarations import (
    DEFAULT_SPECIALISMS,
    DEFAULT_SUPPORTED_PROTOCOLS,
    CapabilityDeclarations,
    SigningPlatformBacking,
)
from src.core.signing.posture import (
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
from src.core.tenant_context import TenantContext
from src.services.targeting_capabilities import supports_property_list_filtering

logger = logging.getLogger(__name__)

# NOTE (#1592 -> #1291 D1): the two module-level literals that used to sit here --
# `_WEBHOOK_SIGNING_UNSUPPORTED = WebhookSigning(supported=False)` and its
# `request_signing` twin -- are GONE, and `test_architecture_signing_block_construction`
# now forbids their shape. They were written when no RFC 9421 signing existed; by the
# time #1291 C1 was signing a keyed tenant's outbound webhooks, both construction sites
# still declared `supported: false`, so the wire said "receivers MUST NOT expect a
# Signature header" while the socket carried one. A posture fixed at IMPORT time cannot
# see key material or trust-root publishability, so it can only ever be a stale second
# source. `_build_signing_blocks` below is the single builder, and it takes resolved
# posture objects.

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

    ONE helper for EVERY degradation site in ``_get_adcp_capabilities_impl`` — the
    discovery lookups routed through :func:`_resolve_or_degrade` and the two reads
    inside the ``CapabilitiesUoW`` block that need their own terminal-error posture.
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

    ONE body for every discovery lookup that degrades rather than fails the
    response. Each site used to spell its own try/except/_record_degradation/
    fall-back-to-a-default, which is one chance per site to forget the advisory
    (and silently emit a placeholder, the quiet-failure class CLAUDE.md bans) or
    to let an exception escape and 500 a response that is meant to degrade.

    Broad ``except Exception`` is deliberate and matches what it replaces: this
    is the degradation boundary, and the advisory is how the buyer learns a
    section is missing rather than empty.

    It absorbs ``AdCPConfigurationError`` along with everything else, and that is
    load carried on purpose: ``get_adapter_class`` raises it for a tenant whose
    adapter type is unknown, and an unresolvable adapter must still degrade to an
    advisory rather than fail discovery. The one read that must NOT degrade a
    configuration error — a capability declaration the platform cannot back — is
    therefore NOT routed through here; see ``_resolve_signing_blocks``'s call site.
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
    declarations: CapabilityDeclarations,
    *,
    now: datetime,
) -> SigningBlocks:
    """Resolve every signing input INSIDE *uow*, then build the blocks.

    Both DB reads and every ORM attribute access happen here, on the one session the
    capabilities request owns: the tenant row (whose stored host IS the agent identity),
    and the signing keys behind ``webhook_signing``. Only resolved values leave.

    The declared-vs-derived cross-checks run here too, for the same reason — they are the
    two relation rules that need platform state, and this is the only caller that has it.

    *declarations* is never ``None``: ``CapabilityDeclarations.from_tenant`` returns an
    EMPTY instance for a tenant that declared nothing, so the platform-backing check runs
    on every request instead of being skipped by an ``if declarations is not None``
    guard — and an undeclared tenant is exactly the case where a derived pointer must
    still be validated against what this host can actually serve.
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
    # selector that well_known._publishable_keys feeds into build_jwks -- gating on
    # .publishes can never advertise an origin whose JWKS is actually empty.
    return _build_signing_blocks(
        posture=posture,
        webhook_signing=webhook_signing,
        brand_json_url=brand_json_url(agent) if publishable else None,
        jwks_origin=jwks_origin(agent) if (publishable and key_backing.publishes) else None,
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


def _get_adcp_capabilities_impl(
    req: GetAdcpCapabilitiesRequest | None, identity: PublicIdentity
) -> GetAdcpCapabilitiesResponse:
    """Shared implementation for get_adcp_capabilities.

    Returns the capabilities of this sales agent per AdCP spec.

    Args:
        req: GetAdcpCapabilitiesRequest (optional, currently unused)
        identity: Resolved identity from transport boundary

    Returns:
        GetAdcpCapabilitiesResponse containing agent capabilities
    """
    # Version negotiation is NOT here. It runs at the boundary, before this or any other
    # implementation is called, so every tool answers a bad pin the same way and none of them
    # can forget to ask. It stays un-tenant-gated by construction: the boundary rejects before
    # an identity is enriched, let alone a tenant read.

    tenant = identity.tenant

    if not tenant:
        # Return minimal capabilities if no tenant context. `request_signing` is an
        # AGENT-level fact and stays honest here (inbound verification runs in
        # `_resolve_identity` for every tool, ahead of any tenant read);
        # `webhook_signing` is per-tenant, and with no tenant there is no key, so
        # `supported: false` is the honest answer rather than dead config.
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
        )

    tenant_id = tenant.tenant_id
    tenant_name = tenant.name

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
    declarations = CapabilityDeclarations.from_tenant(tenant.capability_declarations)

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

    # ONE session for every database read this response needs: the publisher partners,
    # and the signing family's key + tenant-host reads. Two reads, each with its OWN
    # degradation label, because a signing-key failure advertised as "could not resolve
    # publisher domains" tells the buyer the wrong thing.
    #
    # The signing read is the one that may NOT absorb an AdCPConfigurationError: a
    # declaration the platform cannot back is a terminal CONFIGURATION_ERROR, not a
    # partial result (#1291 D1). `_resolve_or_degrade` deliberately DOES absorb it --
    # `get_adapter_class` raises it for an unknown adapter type and the adapter lookup
    # above has to keep degrading -- so the signing read carries its own handler instead
    # of the shared one being widened for it.
    #
    # `publisher_domains` stays empty when the read fails or the tenant has no partners.
    # No placeholder `<subdomain>.example.com` is substituted: Portfolio requires
    # publisher_domains minItems 1, so the honest answer is to omit `portfolio` entirely
    # (see the Portfolio construction below) rather than fabricate a domain the seller
    # does not own (salesagent-piyo).
    publisher_domains: list[PublisherDomain] = []
    signing = _build_signing_blocks(
        posture=posture_from_declarations(declarations),
        webhook_signing=unsupported_webhook_signing_posture(),
        brand_json_url=None,
        jwks_origin=None,
    )
    try:
        with CapabilitiesUoW(tenant_id) as uow:
            tenant_config = uow.tenant_config
            assert tenant_config is not None

            def _resolve_publisher_domains() -> list[PublisherDomain]:
                return [
                    PublisherDomain(root=partner.publisher_domain)
                    for partner in tenant_config.list_publisher_partners()
                    if partner.publisher_domain
                ]

            publisher_domains = _resolve_or_degrade(
                advisories, "publisher domains", _resolve_publisher_domains, default=[]
            )

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
    policy = tenant.advertising_policy
    if policy:
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
        reporting_delivery_methods=declarations.reporting_delivery_methods,
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
        supported_protocols=declarations.emitted_supported_protocols(_DEFAULT_SUPPORTED_PROTOCOLS),
        specialisms=declarations.emitted_specialisms(_DEFAULT_SPECIALISMS),
        measurement=declarations.measurement,
        experimental_features=declarations.emitted_experimental_features(),
        media_buy=media_buy,
        account=_build_account_block(tenant),
        webhook_signing=signing.webhook_signing,
        request_signing=signing.request_signing,
        identity=signing.identity,
        errors=advisories or None,
        last_updated=datetime.now(UTC),
    )

    # Filter protocol-domain sections to the requested protocols. adcp/
    # supported_protocols/account are protocol-invariant (describe the seller
    # as a whole, not a specific protocol domain) and always survive.
    #
    # The section names come from the request's own Protocol enum, not a literal
    # tuple: the two are the same five names today, and a hand-copied list would
    # silently stop filtering a domain the spec later adds — the response would
    # then carry a section the buyer did not ask for. model_copy rather than
    # setattr so the filtered response is built, not mutated after validation.
    if req and req.protocols:
        requested = {enum_value(p) for p in req.protocols}
        dropped = {name: None for name in _PROTOCOL_DOMAIN_SECTIONS if name not in requested}
        if dropped:
            response = response.model_copy(update=dropped)

    return response
