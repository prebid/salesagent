"""UC-010 step definitions: get_adcp_capabilities discovery (batch 1).

Batch 1 covers the envelope + account families (#1592 / salesagent-4sn7):
main flow, auth policy, no-tenant minimal, account.* outlines, protocols
filter, context echo, and version negotiation. Batches 2-3 (media_buy
families, long tail) land behind the conftest wired-tags gate.

Wire-assert philosophy (tests/CLAUDE.md § wire_response): Then steps grade
the SERIALIZED wire (``ctx["wire_response"]``) via dotted-path resolution,
not the coerced typed payload. Absent means the key is NOT on the wire —
a JSON null would be schema-invalid for these optional-object fields and
is deliberately not treated as absent.

Givens describing tenant-config surface production does not have yet record
intent in ``ctx["capabilities_config"]``; those scenarios xfail at the Then
— the correct red for the S1/S2/S3 slices (do NOT invent config columns here).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import WIRE_MISSING, wire_absent, wire_dict, wire_field, wire_lookup
from tests.bdd.steps.generic._dispatch import dispatch_request

#: 3.1.1 billing-party enum (dist/schemas/3.1.1/enums/billing-party.json).
BILLING_PARTY_ENUM = {"operator", "agent", "advertiser"}

#: 3.1.1 pricing-model enum (dist/schemas/3.1.1/enums/pricing-model.json).
PRICING_MODEL_ENUM = {"cpm", "vcpm", "cpc", "cpcv", "cpv", "cpp", "cpa", "flat_rate", "time"}

#: 3.1.1 reporting delivery methods enum
#: (get-adcp-capabilities-response.json#/properties/media_buy/properties/reporting_delivery_methods).
REPORTING_DELIVERY_ENUM = {"webhook", "offline"}

#: 3.1.1 webhook-signing algorithm enum — the closed set the adcp/webhook-signing/v1 profile
#: permits (get-adcp-capabilities-response.json#/properties/webhook_signing/properties/algorithms/items/enum;
#: minItems 1, uniqueItems). Other values are reserved and MUST NOT be emitted under v1.
WEBHOOK_SIGNING_ALGORITHM_ENUM = {"ed25519", "ecdsa-p256-sha256"}

#: 3.1.1 TMP surface enum — the closed set media_buy.execution.trusted_match.surfaces
#: items may carry (get-adcp-capabilities-response.json#/properties/media_buy/properties/
#: execution/properties/trusted_match/properties/surfaces/items/enum).
TRUSTED_MATCH_SURFACE_ENUM = {
    "website",
    "mobile_app",
    "ctv_app",
    "desktop_app",
    "dooh",
    "podcast",
    "radio",
    "streaming_audio",
    "ai_assistant",
}

#: 3.1.1 media channels enum, in schema order — the 20 canonical values
#: (dist/schemas/3.1.1/enums/channels.json#/enum). primary_channels items $ref
#: this enum; there is no minItems/uniqueItems constraint, so the graded contract
#: is set-equality against these 20 values.
CHANNELS_ENUM = [
    "display",
    "olv",
    "social",
    "search",
    "ctv",
    "linear_tv",
    "radio",
    "streaming_audio",
    "podcast",
    "dooh",
    "ooh",
    "print",
    "cinema",
    "email",
    "gaming",
    "retail_media",
    "influencer",
    "affiliate",
    "product_placement",
    "sponsored_intelligence",
]

# ── Wire helpers ─────────────────────────────────────────────────────


def _config(ctx: dict) -> dict:
    """Declared tenant-capability intent recorded by Given steps."""
    return ctx.setdefault("capabilities_config", {})


def _error_details(ctx: dict) -> dict:
    """details block of the wire error envelope (errors[0] preferred)."""
    envelope = ctx.get("wire_error_envelope") or ctx.get("synthesized_error_envelope")
    assert isinstance(envelope, dict), f"no wire error envelope captured (error={ctx.get('error')!r})"
    errors = envelope.get("errors") or [{}]
    details = errors[0].get("details") or envelope.get("adcp_error", {}).get("details")
    assert isinstance(details, dict), f"error envelope carries no details block: {envelope}"
    return details


def _quoted_list(text: str) -> list[str]:
    """Parse '"a", "b"' / 'display, social' step fragments into a list."""
    quoted = re.findall(r'"([^"]+)"', text)
    if quoted:
        return quoted
    return [part.strip() for part in text.split(",") if part.strip()]


def _assert_schema_valid(ctx: dict) -> None:
    """Re-validate the serialized wire body through the pinned GetAdcpCapabilitiesResponse
    model (generated from get-adcp-capabilities-response.json) — a response that dropped a
    required field or carried an out-of-constraint value raises ValidationError here."""
    from adcp.types import GetAdcpCapabilitiesResponse

    GetAdcpCapabilitiesResponse.model_validate(wire_dict(ctx))


def _assert_capabilities_success(ctx: dict) -> None:
    """A valid (possibly degraded) capabilities response: no recorded error, no wire error
    envelope, and the top-level required blocks (adcp, supported_protocols) on the wire
    (get-adcp-capabilities-response.json#/required = [adcp, supported_protocols])."""
    assert ctx.get("error") is None, f"expected a valid response, got error: {ctx.get('error')!r}"
    assert ctx.get("wire_error_envelope") is None, (
        f"expected a valid response, got a wire error envelope: {ctx.get('wire_error_envelope')!r}"
    )
    for path in ("adcp", "supported_protocols"):
        wire_field(ctx, path)


def _assert_capabilities_config_error(ctx: dict, message_substr: str | None = None) -> None:
    """A seller-side config rejection: the builder refused to emit a conformant response and
    surfaced CONFIGURATION_ERROR (recovery terminal — a deployment fault the buyer cannot fix
    and MUST NOT auto-retry; enums/error-code.json#/enumMetadata/CONFIGURATION_ERROR). When
    given, message_substr must appear in errors[0].message."""
    from tests.helpers.envelope_assertions import assert_envelope_shape

    assert ctx.get("error") is not None, "expected a CONFIGURATION_ERROR rejection, got a success response"
    assert_envelope_shape(
        ctx.get("wire_error_envelope"),
        "CONFIGURATION_ERROR",
        recovery="terminal",
        message_substr=message_substr,
    )


# ── Givens: tenant / adapter / DB state ──────────────────────────────


@given("the tenant has full capabilities configured")
@given("the tenant uses the mock adapter with full capabilities configured")
def given_full_capabilities(ctx: dict) -> None:
    """Declare the full-capability tenant. Production has no capability config
    surface yet — this records intent; value asserts xfail until S1/S3 land."""
    _config(ctx)["full"] = True


@given("the tenant supports audience targeting")
def given_supports_audience_targeting(ctx: dict) -> None:
    """Declare audience-targeting support. Production does not emit the
    media_buy.audience_targeting block yet (#1855) — records intent; the
    value asserts xfail until the block lands."""
    _config(ctx)["audience_targeting"] = True


@given("the tenant supports conversion tracking")
def given_supports_conversion_tracking(ctx: dict) -> None:
    """Declare conversion-tracking support. Production does not emit the
    media_buy.conversion_tracking block yet (#1855) — records intent; the
    presence/value asserts xfail until the block lands."""
    _config(ctx)["conversion_tracking"] = True


@given('"creative" is in supported_protocols')
def given_creative_in_supported_protocols(ctx: dict) -> None:
    """Declare the creative protocol. Production advertises only the media_buy
    protocol, so the creative section is not emitted (#1724) — records intent."""
    _config(ctx).setdefault("supported_protocols", []).append("creative")


@given("the tenant declares creative supports_compliance true")
def given_creative_supports_compliance(ctx: dict) -> None:
    """Declare the optional creative.supports_compliance value. Records intent;
    production does not emit the creative section yet (#1724)."""
    _config(ctx)["creative_supports_compliance"] = True


@given("the adapter is unavailable")
def given_adapter_unavailable(ctx: dict) -> None:
    """Adapter factory raises — production degrades to the [display] default channel."""
    ctx["env"].make_adapter_unavailable()


@given("the database query fails")
def given_database_query_fails(ctx: dict) -> None:
    """Publisher-partner DB read fails — production degrades to the placeholder domain."""
    ctx["env"].break_tenant_config_db()


@given(parsers.parse("the tenant has an adapter with channels {channels}"))
def given_adapter_channels(ctx: dict, channels: str) -> None:
    ctx["env"].set_adapter_channels(_quoted_list(channels))


@given(parsers.parse("the tenant has registered publisher partnerships with domains {domains}"))
def given_publisher_partnerships(ctx: dict, domains: str) -> None:
    from tests.factories.core import PublisherPartnerFactory

    parsed = _quoted_list(domains)
    for domain in parsed:
        PublisherPartnerFactory(tenant=ctx["tenant"], publisher_domain=domain)
    ctx["publisher_domains"] = parsed


@given("the adapter provides targeting capabilities including geo")
def given_adapter_geo_targeting(ctx: dict) -> None:
    ctx["env"].set_targeting_capabilities(geo_countries=True, geo_regions=True, nielsen_dma=True)


@given("the adapter reports all 20 channels enum values")
def given_adapter_all_canonical_channels(ctx: dict) -> None:
    """Seed the adapter with every 3.1.1 channels enum value (channels.json#/enum).
    Production maps each recognized value through CHANNEL_MAPPING onto primary_channels."""
    ctx["env"].set_adapter_channels(list(CHANNELS_ENUM))


@given(parsers.parse("the adapter is in {adapter_state} state"))
def given_adapter_state(ctx: dict, adapter_state: str) -> None:
    """available → default happy adapter (channels + full targeting);
    unavailable → the adapter factory raises, so production degrades to the
    [display] default and drops adapter-derived media_buy sections."""
    if adapter_state == "unavailable":
        ctx["env"].make_adapter_unavailable()
    elif adapter_state != "available":
        raise ValueError(f"unknown adapter_state: {adapter_state!r}")


@given(parsers.parse("the tenant has {capability} configured as {capability_state}"))
def given_capability_configured(ctx: dict, capability: str, capability_state: str) -> None:
    """Record tenant-config capability intent (audience targeting / conversion
    tracking, enabled|disabled). Production has no capability-config surface yet
    (#1592) — this records intent; the media_buy.<section> block is not emitted,
    so the 'present' rows xfail at the Then."""
    if capability_state not in ("enabled", "disabled"):
        raise ValueError(f"unknown capability_state: {capability_state!r}")
    key = capability.strip().replace(" ", "_")
    _config(ctx)[key] = capability_state == "enabled"


@given("the adapter provides full targeting capabilities")
def given_adapter_full_targeting(ctx: dict) -> None:
    """Seed the adapter with every TargetingCapabilities boolean dimension True.
    Production maps geo_countries/geo_regions/geo_metros/geo_postal_areas off this;
    the richer dimensions (age_restriction, language, keyword_targets,
    negative_keywords, geo_proximity) have NO adapter surface and are never built."""
    from dataclasses import fields as _dc_fields

    from src.adapters.base import TargetingCapabilities

    ctx["env"].set_targeting_capabilities(**{f.name: True for f in _dc_fields(TargetingCapabilities)})


@given(parsers.parse("the adapter provides targeting as {config}"))
def given_adapter_targeting_config(ctx: dict, config: str) -> None:
    """Realize an outline-row adapter targeting config (targeting-partitions).

    Only the TargetingCapabilities boolean dimensions have an adapter surface, so
    dotted/native tokens (age_restriction.supported, geo_postal_areas US=[...],
    keyword_targets.*, geo_proximity.*) are recorded as intent — production has no
    way to emit them, which is exactly why those rows are production gaps."""
    from dataclasses import fields as _dc_fields

    from src.adapters.base import TargetingCapabilities

    ctx["targeting_config"] = config
    if "adapter unavailable" in config:
        ctx["env"].make_adapter_unavailable()
        return
    known = {f.name for f in _dc_fields(TargetingCapabilities)}
    if "all dimensions reported" in config:
        ctx["env"].set_targeting_capabilities(**dict.fromkeys(known, True))
        return
    # Default geo_countries/geo_regions on; every nested dimension off unless the
    # row explicitly sets it (so "no nested sub-properties" yields metros/postal absent).
    dims = dict.fromkeys(known, False)
    dims["geo_countries"] = True
    dims["geo_regions"] = True
    for match in re.finditer(r"([a-z_]+(?:\.[a-z_]+)?)\s*=\s*(true|false)", config):
        field = match.group(1).split(".")[-1]
        if field in known:
            dims[field] = match.group(2) == "true"
    # Native postal tokens (e.g. 'US=["zip"]', 'DE=["plz"]') -- the R4 native-map
    # scenario rows spell postal dimensions by (country, system), not by field
    # name; translate via the SAME table production uses (single source, DRY).
    from src.core.tools.capabilities import _POSTAL_AREA_TABLE

    postal_field_by_country_system = {(c, s): field for field, (c, s) in _POSTAL_AREA_TABLE.items()}
    for match in re.finditer(r'([A-Z]{2})=\["([a-z_]+)"\]', config):
        field = postal_field_by_country_system.get((match.group(1), match.group(2)))
        if field:
            dims[field] = True
    ctx["env"].set_targeting_capabilities(**dims)


@given("a tenant is resolvable and adapter and DB are available with all features")
def given_full_degradation_baseline(ctx: dict) -> None:
    """full_response degradation row: happy path (default env — adapter + DB up)."""
    ctx["has_tenant"] = True
    _config(ctx)["full"] = True


@given("a tenant is resolvable but adapter is unavailable")
def given_tenant_adapter_unavailable(ctx: dict) -> None:
    """adapter_fail row: tenant resolves, adapter factory raises → [display] default."""
    ctx["has_tenant"] = True
    ctx["env"].make_adapter_unavailable()


@given("a tenant is resolvable but database query fails")
def given_tenant_db_fails(ctx: dict) -> None:
    """db_fail row: tenant resolves, publisher-partner DB read fails → placeholder domain."""
    ctx["has_tenant"] = True
    ctx["env"].break_tenant_config_db()


@given("a tenant is resolvable but both adapter and DB fail")
def given_tenant_adapter_and_db_fail(ctx: dict) -> None:
    """adapter_and_db_fail row: both degrade — [display] channels + placeholder domain."""
    ctx["has_tenant"] = True
    ctx["env"].make_adapter_unavailable()
    ctx["env"].break_tenant_config_db()


@given("a tenant is resolvable but no auth principal available")
def given_tenant_no_principal(ctx: dict) -> None:
    """no_principal row: tenant resolves, caller is principal-less (anonymous identity).
    Per INV-4 the adapter is tenant-only/principal-free, so adapter-derived channels
    are NOT degraded by the missing principal — the [display] expectation is the gap."""
    ctx["has_tenant"] = True
    ctx["identity"] = ctx["env"].anonymous_identity()


@given(
    parsers.re(
        r"a tenant is resolvable but adapter unavailable or "
        r"(?P<capability>audience targeting|conversion tracking) disabled$"
    )
)
def given_tenant_section_absent(ctx: dict, capability: str) -> None:
    """audience_targeting_absent / conversion_tracking_absent rows: model the
    'adapter unavailable' leg (the capabilities builder never emits these blocks
    regardless, so they are absent on the wire)."""
    ctx["has_tenant"] = True
    ctx["env"].make_adapter_unavailable()
    _config(ctx)[capability.replace(" ", "_")] = False


@given("a tenant is resolvable but creative not in supported_protocols")
def given_tenant_creative_absent(ctx: dict) -> None:
    """creative_absent row: production advertises only the media_buy protocol, so
    the creative section is never emitted."""
    ctx["has_tenant"] = True
    _config(ctx)["supported_protocols"] = ["media_buy"]


@given("the system has known state before the request")
def given_state_snapshot(ctx: dict) -> None:
    ctx["state_snapshot"] = _db_state_snapshot(ctx["env"])


def _db_state_snapshot(env: Any) -> dict[str, int]:
    """Row counts of the mutable tables a capabilities call could touch."""
    from src.core.database.models import MediaBuy, Principal, PublisherPartner, Tenant

    env._commit_factory_data()
    return {model.__tablename__: len(env.query(model)) for model in (Tenant, Principal, PublisherPartner, MediaBuy)}


# ── Givens: auth state ───────────────────────────────────────────────


@given(parsers.re(r"the Buyer has (?P<token_state>no|valid|invalid) authentication$"))
def given_token_state(ctx: dict, token_state: str) -> None:
    ctx["token_state"] = token_state


@given("the Buyer has an invalid authentication token")
def given_invalid_token(ctx: dict) -> None:
    ctx["token_state"] = "invalid"


def _identity_for_token_state(ctx: dict) -> Any:
    """Map the declared token_state onto a harness identity.

    - no      → principal-less tenant identity (tenant Given holds; no credential)
    - valid   → env default (real factory token)
    - invalid → token matching no Principal row (real chain on MCP/A2A;
                in-process REST models the treat-as-absent outcome via the
                dep seam — the real header path is exercised on e2e_rest)
    """
    env = ctx["env"]
    state = ctx.get("token_state", "valid")
    if state == "no":
        return env.anonymous_identity()
    if state == "invalid":
        return env.invalid_token_identity()
    return _DEFAULT


_DEFAULT = object()


# ── Givens: account-family tenant config ─────────────────────────────


@given(parsers.parse("the tenant is configured with require_operator_auth {configured}"))
def given_require_operator_auth(ctx: dict, configured: str) -> None:
    _config(ctx)["require_operator_auth"] = configured  # true|false|omitted (intent only)


@given(parsers.parse("the tenant is configured with require_operator_auth true and OAuth support {oauth_state}"))
def given_oauth_support(ctx: dict, oauth_state: str) -> None:
    _config(ctx)["require_operator_auth"] = "true"
    match = re.search(r"enabled at (\S+)", oauth_state)
    _config(ctx)["authorization_endpoint"] = match.group(1) if match else None


@given(parsers.parse("the tenant is configured with required_for_products {configured}"))
def given_required_for_products(ctx: dict, configured: str) -> None:
    _config(ctx)["required_for_products"] = configured


@given(parsers.parse("the tenant billing policy is configured as {billing_config}"))
def given_billing_policy(ctx: dict, billing_config: str) -> None:
    """REAL config: tenants.supported_billing exists (#1521 lineage) — write it."""
    billing = _quoted_list(billing_config)
    ctx["env"].configure_tenant_field("supported_billing", billing)
    _config(ctx)["supported_billing"] = billing


@given("the tenant does not expose the get_account_financials task")
def given_no_account_financials(ctx: dict) -> None:
    _config(ctx)["account_financials"] = False


@given("a tenant is resolvable with partial account config")
def given_partial_account_config(ctx: dict) -> None:
    ctx["has_tenant"] = True
    _config(ctx)["partial_account"] = True


@given(parsers.parse("the tenant capabilities are configured as {capability_config}"))
def given_capability_config(ctx: dict, capability_config: str) -> None:
    """Outline-row config declaration (features-partitions). Records the raw
    row text; the row→assertion table in the satisfy-Then grades it.

    ``sandbox={true,false}`` is the one token in this outline with a REAL
    tenant-config surface (Tenant.account_sandbox) rather than a pure Then-side
    echo -- write it through configure_tenant_field so the sandbox_disabled row
    isn't silently graded against the untouched DB default (#1721 M4; was the
    same missing-Given-side-write class of gap as T-UC-010-main's).
    """
    _config(ctx)["row"] = capability_config
    match = re.search(r"\bsandbox=(true|false)\b", capability_config)
    if match:
        ctx["env"].configure_tenant_field("account_sandbox", match.group(1) == "true")


# ── Givens: creative_approval_mode (real config — salesagent-y9ld R7) ─────


@given(parsers.parse("the tenant creative approval mode is configured as {configured}"))
def given_creative_approval_mode(ctx: dict, configured: str) -> None:
    """REAL config for the require_human half: tenant.human_review_required
    (NOT NULL DEFAULT TRUE at the schema level, tests/factories/core.py's
    TenantFactory default is False) is the tenant-side signal
    media_buy.creative_approval_mode is designed to derive from
    (resolve_manual_approval_signal, salesagent-y9ld). "require_human" writes
    human_review_required=True through the real DB column production reads;
    "omitted" writes it False (the factory default, made explicit so the row
    is not accidentally coupled to the factory's current default). No
    production config surface exists yet for an affirmative auto_approve
    claim (Q2, deferred) — that row's Given is intent-only.
    """
    configured = configured.strip()
    _config(ctx)["creative_approval_mode"] = configured
    if configured == "require_human":
        ctx["env"].configure_tenant_field("human_review_required", True)
    elif configured == "omitted":
        ctx["env"].configure_tenant_field("human_review_required", False)
    # "auto_approve": no production config surface yet (Q2) — intent only.


# ── Givens: version negotiation ──────────────────────────────────────


@given(parsers.parse("the seller speaks adcp release-precision versions {versions}"))
def given_seller_versions(ctx: dict, versions: str) -> None:
    versions_list = _quoted_list(versions)
    _config(ctx)["supported_versions"] = versions_list
    ctx["env"].set_supported_versions(versions_list)


@given(parsers.parse('the seller\'s build_version is "{build_version}"'))
def given_seller_build_version(ctx: dict, build_version: str) -> None:
    _config(ctx)["build_version"] = build_version
    ctx["env"].set_build_version(build_version)


# ── When cluster ─────────────────────────────────────────────────────


def _call_capabilities(ctx: dict, **kwargs: Any) -> None:
    """Single funnel for every capabilities dispatch (DRY).

    Honors ctx["identity"] = None (no-tenant Givens) and appends each
    response to ctx["response_history"] for dual-call comparisons.
    """
    if "identity" not in kwargs and "identity" in ctx:
        kwargs["identity"] = ctx["identity"]
    dispatch_request(ctx, **kwargs)
    ctx.setdefault("response_history", []).append((ctx.get("response"), ctx.get("error")))


@when("the Buyer Agent calls get_adcp_capabilities")
@when("the Buyer Agent calls get_adcp_capabilities without context")
@when("the Buyer Agent calls get_adcp_capabilities authenticated with a valid principal_id")
def when_call_capabilities(ctx: dict) -> None:
    """Plain dispatch aliases: no-context and valid-principal are the default
    call shape (the env default identity IS the valid principal)."""
    _call_capabilities(ctx)


@when(parsers.parse("the Buyer Agent calls get_adcp_capabilities with protocols filter {protocols}"))
def when_call_with_protocols(ctx: dict, protocols: str) -> None:
    _call_capabilities(ctx, protocols=json.loads(protocols))


@when(parsers.parse("the Buyer Agent calls get_adcp_capabilities with context {context}"))
def when_call_with_context(ctx: dict, context: str) -> None:
    request_context = json.loads(context)
    ctx["request_context"] = request_context
    _call_capabilities(ctx, context=request_context)


@when(parsers.parse('the Buyer Agent calls get_adcp_capabilities with adcp_version "{version}"'))
def when_call_with_adcp_version(ctx: dict, version: str) -> None:
    _call_capabilities(ctx, adcp_version=version)


@when(parsers.parse("the Buyer Agent calls get_adcp_capabilities with adcp_major_version {major:d}"))
def when_call_with_major_version(ctx: dict, major: int) -> None:
    _call_capabilities(ctx, adcp_major_version=major)


@when("the Buyer Agent calls get_adcp_capabilities without authentication")
def when_call_unauthenticated(ctx: dict) -> None:
    _call_capabilities(ctx, identity=ctx["env"].anonymous_identity())


@when(parsers.re(r"the Buyer Agent invokes get_adcp_capabilities via (?P<channel>MCP|A2A|REST)$"))
def when_invoke_via_channel(ctx: dict, channel: str) -> None:
    """Auth-outline dispatch: the <channel> column IS the transport (the
    pytest-level parametrization is redundant for this outline by design)."""
    ctx["transport"] = channel
    identity = _identity_for_token_state(ctx)
    if identity is _DEFAULT:
        _call_capabilities(ctx)
    else:
        _call_capabilities(ctx, identity=identity)


@when("the Buyer Agent calls get_adcp_capabilities via MCP with the token")
def when_call_mcp_invalid_token(ctx: dict) -> None:
    ctx["transport"] = "MCP"
    _call_capabilities(ctx, identity=ctx["env"].invalid_token_identity())


@when("the Buyer Agent sends a get_adcp_capabilities skill request via A2A with the token")
def when_call_a2a_invalid_token(ctx: dict) -> None:
    ctx["transport"] = "A2A"
    _call_capabilities(ctx, identity=ctx["env"].invalid_token_identity())


# ── Thens: adcp envelope ─────────────────────────────────────────────


@then("the response should include adcp.major_versions containing 3")
def then_major_versions(ctx: dict) -> None:
    assert 3 in wire_field(ctx, "adcp.major_versions")


@then(
    "adcp.idempotency.supported should be exactly true or false, and when false "
    "replay_ttl_seconds and in_flight_max_seconds should be absent"
)
def then_idempotency_discriminator(ctx: dict) -> None:
    """oneOf discriminator invariant (get-adcp-capabilities-response.json
    #/properties/adcp/properties/idempotency/oneOf): supported is the boolean
    discriminator; on the IdempotencyUnsupported branch (supported=false) the
    schema's `not.anyOf` forbids replay_ttl_seconds and in_flight_max_seconds —
    they "have no meaning without replay support"."""
    idempotency = wire_dict(ctx, "adcp.idempotency")
    supported = idempotency.get("supported")
    assert isinstance(supported, bool), f"idempotency.supported not a boolean: {supported!r}"
    if supported is False:
        for forbidden in ("replay_ttl_seconds", "in_flight_max_seconds"):
            assert forbidden not in idempotency, (
                f"IdempotencyUnsupported (supported=false) must omit {forbidden}: {idempotency!r}"
            )


@then("adcp.idempotency should be present in the response")
def then_idempotency_present(ctx: dict) -> None:
    """adcp.idempotency is REQUIRED (get-adcp-capabilities-response.json
    #/properties/adcp/required includes idempotency; "Clients MUST NOT assume a
    default"). wire_dict pins present AND non-null AND a JSON object."""
    wire_dict(ctx, "adcp.idempotency")


@then(parsers.parse("adcp.idempotency.supported should equal {supported}"))
def then_idempotency_supported_equals(ctx: dict, supported: str) -> None:
    """oneOf discriminator: supported is the const true/false branch selector
    (get-adcp-capabilities-response.json#/properties/adcp/properties/idempotency/oneOf)."""
    expected = {"true": True, "false": False}[supported.strip()]
    value = wire_field(ctx, "adcp.idempotency.supported")
    assert value is expected, f"idempotency.supported expected {expected!r}, got {value!r}"


def _parse_expected_fields(text: str) -> list[tuple[str, str, str | None]]:
    """Parse an 'expected_fields' column fragment into (field, verb, value) triples.

    Supports 'X equals N[, Y equals M, ...]' (comma-joined 'field equals value'
    clauses -- value may be an int or 'true'/'false'), and
    'X absent and Y absent' (space-'and'-joined 'field absent' clauses).
    """
    text = text.strip()
    if " absent" in text:
        return [(clause.replace(" absent", "").strip(), "absent", None) for clause in text.split(" and ")]
    triples = []
    for clause in text.split(","):
        clause = clause.strip()
        if " equals " in clause:
            field, _, value = clause.partition(" equals ")
        else:
            # Bare 'field value' form (e.g. 'account_id_is_opaque true').
            field, _, value = clause.rpartition(" ")
        triples.append((field.strip(), "equals", value.strip()))
    return triples


@then(parsers.parse("adcp.idempotency should satisfy {expected_fields}"))
def then_idempotency_satisfies(ctx: dict, expected_fields: str) -> None:
    """Grades the idempotency-supported outline's per-row field expectations --
    either declared fields echoed exactly (IdempotencySupported branch) or the
    schema's not.anyOf-forbidden fields genuinely absent (IdempotencyUnsupported
    branch)."""
    idempotency = wire_dict(ctx, "adcp.idempotency")
    for field, verb, raw_value in _parse_expected_fields(expected_fields):
        if verb == "absent":
            assert field not in idempotency, f"expected {field!r} absent, but present: {idempotency[field]!r}"
            continue
        actual = idempotency.get(field)
        if raw_value in ("true", "false"):
            expected: bool | int = raw_value == "true"
        else:
            expected = int(raw_value)
        assert actual == expected, f"idempotency.{field} expected {expected!r}, got {actual!r}"


def _wire_int(value: object, field: str) -> int:
    """Coerce a wire numeric value to int, tolerating A2A's protobuf Struct
    encoding (NumberValue is always double -- an integer round-trips as an
    integral float, e.g. 86400.0, on that transport only)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"{field} not a number: {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise AssertionError(f"{field} not an integral value: {value!r}")
    return int(value)


@then("adcp.idempotency.in_flight_max_seconds should be less than or equal to adcp.idempotency.replay_ttl_seconds")
def then_idempotency_in_flight_bound(ctx: dict) -> None:
    """Cross-field rule the JSON Schema cannot express (test-layer-enforced):
    in_flight_max_seconds MUST be no greater than replay_ttl_seconds."""
    idempotency = wire_dict(ctx, "adcp.idempotency")
    in_flight = _wire_int(idempotency.get("in_flight_max_seconds"), "in_flight_max_seconds")
    ttl = _wire_int(idempotency.get("replay_ttl_seconds"), "replay_ttl_seconds")
    assert in_flight <= ttl, f"in_flight_max_seconds {in_flight!r} exceeds replay_ttl_seconds {ttl!r}"


@then("adcp.idempotency.replay_ttl_seconds should be an integer between 3600 and 604800")
def then_idempotency_ttl_range(ctx: dict) -> None:
    """IdempotencySupported requires replay_ttl_seconds, integer in [3600, 604800]."""
    ttl = wire_field(ctx, "adcp.idempotency.replay_ttl_seconds")
    assert isinstance(ttl, int) and not isinstance(ttl, bool), f"replay_ttl_seconds not an integer: {ttl!r}"
    assert 3600 <= ttl <= 604800, f"replay_ttl_seconds {ttl!r} outside [3600, 604800]"


@then("the response should include adcp.supported_versions as a non-empty array")
def then_supported_versions_nonempty(ctx: dict) -> None:
    versions = wire_field(ctx, "adcp.supported_versions")
    assert isinstance(versions, list) and versions, f"adcp.supported_versions not a non-empty array: {versions!r}"


@then(parsers.parse('each value in adcp.supported_versions should match pattern "{pattern}"'))
def then_supported_versions_pattern(ctx: dict, pattern: str) -> None:
    for value in wire_field(ctx, "adcp.supported_versions"):
        assert re.fullmatch(pattern, value), f"supported_versions entry {value!r} does not match {pattern!r}"


@then(parsers.parse('the response should include supported_protocols containing "{protocol}"'))
def then_supported_protocols_contains(ctx: dict, protocol: str) -> None:
    assert protocol in wire_field(ctx, "supported_protocols")


@then(parsers.parse('supported_protocols should contain "{protocol}"'))
def then_supported_protocols_contains_short(ctx: dict, protocol: str) -> None:
    assert protocol in wire_field(ctx, "supported_protocols")


@then("last_updated should be an RFC 3339 date-time string")
@then("last_updated should parse as an RFC 3339 date-time value")
def then_last_updated_valid(ctx: dict) -> None:
    """Format pinned to the schema keyword (last_updated: format date-time).
    The value must be a string that parses as an RFC 3339 / JSON-Schema
    date-time (e.g. "2025-10-14T14:25:30Z")."""
    from datetime import datetime

    raw = wire_field(ctx, "last_updated")
    assert isinstance(raw, str), f"last_updated not a string: {raw!r}"
    datetime.fromisoformat(raw.replace("Z", "+00:00"))  # raises on malformed


# ── Thens: account block ─────────────────────────────────────────────


def _assert_billing_party_array(value: Any) -> None:
    assert isinstance(value, list) and value, f"supported_billing not a non-empty array: {value!r}"
    assert set(value) <= BILLING_PARTY_ENUM, f"supported_billing carries non-enum values: {value!r}"


@then("account.supported_billing should be a non-empty array")
def then_supported_billing_nonempty(ctx: dict) -> None:
    value = wire_field(ctx, "account.supported_billing")
    assert isinstance(value, list) and value, f"supported_billing not a non-empty array: {value!r}"


@then(parsers.parse("account.supported_billing should equal {expected_set}"))
def then_supported_billing_equals(ctx: dict, expected_set: str) -> None:
    expected = [part.strip() for part in expected_set.strip("[]").split(",") if part.strip()]
    value = wire_field(ctx, "account.supported_billing")
    assert sorted(value) == sorted(expected), f"supported_billing {value!r} != {expected!r}"


@then('each supported_billing value should be one of "operator", "agent", "advertiser"')
def then_supported_billing_enum(ctx: dict) -> None:
    _assert_billing_party_array(wire_field(ctx, "account.supported_billing"))


@then(parsers.parse("account.sandbox should equal {expected}"))
def then_account_sandbox_equals(ctx: dict, expected: str) -> None:
    """account.sandbox is a boolean (default false). Pinned to the exact
    scenario value — no silent-skip on missing config."""
    value = wire_field(ctx, "account.sandbox")
    want = json.loads(expected)
    assert value is want, f"account.sandbox expected {want!r}, got {value!r}"


# boundary_point -> Tenant.account_sandbox value to configure, or None to
# deliberately leave the column at its DB default (True, models.py:82) --
# the "absent" row's premise. Shared by @T-UC-010-v31-account-sandbox and
# T-UC-010-main's explicit-production row (#1721 M4).
_SANDBOX_BOUNDARY_CONFIG: dict[str, bool | None] = {
    "sandbox: true in response (sandbox account)": True,
    "sandbox absent in response (production account)": None,
    "sandbox: false in response (explicit production)": False,
}


@given(parsers.parse("the tenant account is configured for {boundary_point}"))
def given_tenant_account_sandbox_boundary(ctx: dict, boundary_point: str) -> None:
    """Configure Tenant.account_sandbox per the account.sandbox boundary table."""
    if boundary_point not in _SANDBOX_BOUNDARY_CONFIG:
        raise ValueError(f"unknown account-sandbox boundary_point: {boundary_point!r}")
    value = _SANDBOX_BOUNDARY_CONFIG[boundary_point]
    if value is not None:
        ctx["env"].configure_tenant_field("account_sandbox", value)


@then(parsers.parse("the capabilities response should be schema-valid and account.sandbox should be {expected_value}"))
def then_account_sandbox_boundary(ctx: dict, expected_value: str) -> None:
    """Schema-valid is implicit in a successful dispatch (a schema violation
    would already have failed at construction/serialization); the boundary
    grades account.sandbox's exact value per the row."""
    _expect_flag(ctx, "account.sandbox", expected_value.removesuffix(" (buyer-default false)"))


def _expect_flag(ctx: dict, path: str, expected: str) -> None:
    """Grade an outline <expected> column: 'equal to true/false' | 'absent' |
    'absent or false' | 'equal to "..." as a URI'."""
    value = wire_lookup(ctx, path)
    if expected == "absent":
        assert value is WIRE_MISSING, f"{path} unexpectedly present: {value!r}"
    elif expected == "absent or false":
        assert value is WIRE_MISSING or value is False, f"{path} expected absent-or-false, got {value!r}"
    elif expected in ("equal to true", "equal to false"):
        assert value is (expected == "equal to true"), f"{path} expected {expected}, got {value!r}"
    else:
        match = re.match(r'equal to "([^"]+)"', expected)
        assert match, f"unrecognized expected column: {expected!r}"
        assert value == match.group(1), f"{path} expected {match.group(1)!r}, got {value!r}"


@then(parsers.parse("account.require_operator_auth should be {expected}"))
def then_require_operator_auth(ctx: dict, expected: str) -> None:
    _expect_flag(ctx, "account.require_operator_auth", expected)


@then(parsers.parse("account.authorization_endpoint should be {expected}"))
def then_authorization_endpoint(ctx: dict, expected: str) -> None:
    _expect_flag(ctx, "account.authorization_endpoint", expected.removesuffix(" as a URI"))


@then(parsers.parse("account.required_for_products should be {expected}"))
def then_required_for_products(ctx: dict, expected: str) -> None:
    _expect_flag(ctx, "account.required_for_products", expected)


@then("account.account_financials should be absent or false")
def then_account_financials(ctx: dict) -> None:
    _expect_flag(ctx, "account.account_financials", "absent or false")


@then("the account section should be present with a non-empty supported_billing")
def then_account_present_with_billing(ctx: dict) -> None:
    account = wire_field(ctx, "account")
    assert isinstance(account, dict), f"account not an object: {account!r}"
    _assert_billing_party_array(account.get("supported_billing"))


@then(
    parsers.re(
        r"the account section should be (?P<state>absent|present"
        r"|present with supported_billing only and no optional fields)$"
    )
)
def then_account_state(ctx: dict, state: str) -> None:
    if state == "absent":
        wire_absent(ctx, "account")
        return
    account = wire_field(ctx, "account")
    _assert_billing_party_array(account.get("supported_billing"))
    if state.startswith("present with supported_billing only"):
        extras = set(account) - {"supported_billing"}
        assert not extras, f"degraded account block carries optional fields: {sorted(extras)}"


@then("a present account section should include supported_billing as a non-empty array of billing-party enum values")
def then_present_account_billing(ctx: dict) -> None:
    account = wire_lookup(ctx, "account")
    if account is WIRE_MISSING:
        return  # conditional Then: only grades a present block
    _assert_billing_party_array(account.get("supported_billing"))


# ── Thens: media_buy block (batch-1 subset) ──────────────────────────

_FEATURE_FLAGS = (
    "inline_creative_management",
    "property_list_filtering",
    "catalog_management",
    "committed_metrics_supported",
)


@then(parsers.parse("media_buy.features should have boolean flags {flags}"))
def then_features_boolean_flags(ctx: dict, flags: str) -> None:
    """media-buy-features.json: 4 named flags, all boolean, additionalProperties
    boolean. The per-flag VALUE is a production choice (spec-silent), so the
    graded contract is the shape: every one of the 4 named flags this scenario
    names is ACTUALLY EMITTED as a boolean key on the wire (not merely typed
    correctly if present -- salesagent-y9ld R3: a presence-only check here
    would silently pass with committed_metrics_supported omitted from the
    response, the exact regression this Then exists to catch), and every
    other property on the object (named or additional) is also a boolean."""
    features = wire_field(ctx, "media_buy.features")
    assert isinstance(features, dict), f"media_buy.features not an object: {features!r}"
    named = [name.strip() for name in re.split(r",|\band\b", flags) if name.strip()]
    assert set(named) == set(_FEATURE_FLAGS), f"scenario names unexpected feature flags: {named!r}"
    missing = set(named) - set(features)
    assert not missing, f"media_buy.features missing named flags {sorted(missing)}: {features!r}"
    for key, value in features.items():
        assert isinstance(value, bool), f"features.{key} not a boolean: {value!r}"


@then("media_buy.supported_pricing_models should be a non-empty unique array of pricing-model enum values")
def then_pricing_models_shape(ctx: dict) -> None:
    """supported_pricing_models: minItems 1, uniqueItems, items ∈ pricing-model enum.
    The exact SET is config-derived (spec: "products may support a subset") — a
    production config surface (#1592), so only the spec-pinned shape is graded here."""
    models = wire_field(ctx, "media_buy.supported_pricing_models")
    assert isinstance(models, list) and models, f"supported_pricing_models not a non-empty array: {models!r}"
    assert len(models) == len(set(models)), f"supported_pricing_models has duplicates: {models!r}"
    invalid = set(models) - PRICING_MODEL_ENUM
    assert not invalid, f"supported_pricing_models carries non-enum values: {sorted(invalid)}"


@then(parsers.parse("each pricing model should be one of {allowed}"))
def then_pricing_models_enum(ctx: dict, allowed: str) -> None:
    allowed_set = set(_quoted_list(allowed))
    models = wire_field(ctx, "media_buy.supported_pricing_models")
    invalid = set(models) - allowed_set
    assert not invalid, f"supported_pricing_models has values outside {sorted(allowed_set)}: {sorted(invalid)}"


@then("media_buy.supported_pricing_models should contain no duplicates")
def then_pricing_models_no_duplicates(ctx: dict) -> None:
    models = wire_field(ctx, "media_buy.supported_pricing_models")
    assert len(models) == len(set(models)), f"supported_pricing_models has duplicates: {models!r}"


@then(parsers.parse("media_buy.reporting_delivery_methods should be a non-empty unique subset of {allowed}"))
def then_reporting_methods_subset(ctx: dict, allowed: str) -> None:
    """reporting_delivery_methods: minItems 1, uniqueItems, items ∈ {webhook, offline}.
    The exact SET is a seller config choice (spec-silent on value) — only the
    spec-pinned shape/enum is graded (#1592 for the emission itself)."""
    allowed_set = set(_quoted_list(allowed))
    assert allowed_set <= REPORTING_DELIVERY_ENUM, f"scenario allows non-enum reporting methods: {allowed_set!r}"
    methods = wire_field(ctx, "media_buy.reporting_delivery_methods")
    assert isinstance(methods, list) and methods, f"reporting_delivery_methods not a non-empty array: {methods!r}"
    assert len(methods) == len(set(methods)), f"reporting_delivery_methods has duplicates: {methods!r}"
    invalid = set(methods) - allowed_set
    assert not invalid, f"reporting_delivery_methods carries values outside {sorted(allowed_set)}: {sorted(invalid)}"


@then("media_buy.execution.targeting should include geo_countries and geo_regions as booleans")
def then_targeting_geo_booleans(ctx: dict) -> None:
    targeting = wire_field(ctx, "media_buy.execution.targeting")
    for key in ("geo_countries", "geo_regions"):
        assert isinstance(targeting.get(key), bool), f"targeting.{key} not a boolean: {targeting!r}"


def _assert_wire_equals(ctx: dict, path: str, expected: str) -> None:
    """Exact-equality oracle on a dotted success-path wire field.

    Shared by every "<block>.<field> should equal <json>" Then (targeting,
    audience_targeting, conversion_tracking, creative) — one loud-guarded
    dual-assert read (wire_field) plus a JSON-parsed exact compare, so the four
    step families do not each hand-roll the same three lines (DRY)."""
    actual = wire_field(ctx, path)
    want = json.loads(expected)
    assert actual == want, f"{path} expected {want!r}, got {actual!r}"


@then(parsers.parse("media_buy.execution.targeting.{field} should equal {expected}"))
def then_targeting_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Exact value on a targeting sub-field (e.g. geo_countries should equal true).
    targeting.<field> types are booleans/objects per the response schema."""
    _assert_wire_equals(ctx, f"media_buy.execution.targeting.{field}", expected)


@then(
    parsers.re(
        r'media_buy\.execution\.targeting\.geo_postal_areas should be a country-keyed map where (?P<country>[A-Z]{2}) contains "(?P<token>[a-z_]+)"$'
    )
)
def then_geo_postal_native_map(ctx: dict, country: str, token: str) -> None:
    """geo_postal_areas is the NATIVE country-keyed map (postal-area-support.json:
    named country props US/GB/CA/... plus additionalProperties = array items enum
    [postal_code, custom]) — NOT the deprecated country-fused boolean aliases
    (us_zip/de_plz, marked deprecated: true). Grades the native shape: the given
    country key maps to an array containing the given item."""
    postal = wire_field(ctx, "media_buy.execution.targeting.geo_postal_areas")
    assert isinstance(postal, dict), f"geo_postal_areas not an object: {postal!r}"
    assert country in postal, f"geo_postal_areas has no native country key {country!r}: {sorted(postal)}"
    assert token in postal[country], f"geo_postal_areas.{country} does not contain {token!r}: {postal[country]!r}"


@then(parsers.parse("media_buy.execution.targeting should not contain {names}"))
def then_targeting_absent_keys(ctx: dict, names: str) -> None:
    """Removed-in-3.1 dimensions: device_platform / device_type ("implied by
    media_buy support") and audience_include / audience_exclude (moved to the
    presence of media_buy.audience_targeting) MUST NOT appear on targeting."""
    targeting = wire_field(ctx, "media_buy.execution.targeting")
    forbidden = [name.strip() for name in re.split(r",|\bor\b|\band\b", names) if name.strip()]
    present = [name for name in forbidden if name in targeting]
    assert not present, f"targeting carries removed dimensions {present}: {sorted(targeting)}"


@then(parsers.parse("media_buy.audience_targeting.{field} should equal {expected}"))
def then_audience_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Exact value on an audience_targeting sub-field. Required members
    (supported_identifier_types, minimum_audience_size) plus the optional
    members are each pinned to the scenario fixture value."""
    _assert_wire_equals(ctx, f"media_buy.audience_targeting.{field}", expected)


@then(parsers.parse("media_buy.{section} should be present"))
def then_media_buy_section_present(ctx: dict, section: str) -> None:
    """3.1.1 presence-indicates-support model: content_standards /
    conversion_tracking / audience_targeting are objects whose PRESENCE is the
    support signal (the equivalent features.* flags were removed in 3.0). wire_dict
    pins the dual assert — present AND non-null AND a JSON object. Covers each
    presence-object under media_buy with one grounded helper (DRY)."""
    wire_dict(ctx, f"media_buy.{section}")


@then(
    parsers.re(
        r"the media_buy\.(?P<section>audience_targeting|conversion_tracking) "
        r"section should be (?P<state>absent|present)$"
    )
)
def then_media_buy_section_state(ctx: dict, section: str, state: str) -> None:
    """Adapter-dependence of the audience_targeting / conversion_tracking blocks
    (production choice; the spec is silent on WHY a section is present). 'present'
    pins the dual assert (non-null JSON object); 'absent' pins wire-key absence —
    a JSON null would be schema-invalid for these optional-object fields, so it is
    NOT treated as absent."""
    path = f"media_buy.{section}"
    if state == "absent":
        wire_absent(ctx, path)
    else:
        wire_dict(ctx, path)


@then("a present audience_targeting section should include supported_identifier_types and minimum_audience_size")
def then_present_audience_required_members(ctx: dict) -> None:
    """Conditional required-member grade: audience_targeting.required =
    [supported_identifier_types, minimum_audience_size]
    (get-adcp-capabilities-response.json#/properties/media_buy/properties/audience_targeting/required).
    Only grades a present block (mirrors then_present_account_billing)."""
    section = wire_lookup(ctx, "media_buy.audience_targeting")
    if section is WIRE_MISSING:
        return  # conditional Then: only grades a present block
    assert isinstance(section, dict), f"audience_targeting not an object: {section!r}"
    for member in ("supported_identifier_types", "minimum_audience_size"):
        assert member in section, f"present audience_targeting missing required member {member!r}: {section}"


@then(parsers.parse("media_buy.conversion_tracking.{field} should equal {expected}"))
def then_conversion_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Exact value on a conversion_tracking sub-field. The block has no required
    members; each declared sub-field is pinned to its scenario fixture value —
    items drawn from the pinned event-type / uid-type / action-source enums,
    attribution_windows items are window objects with a required post_click array."""
    _assert_wire_equals(ctx, f"media_buy.conversion_tracking.{field}", expected)


@then(parsers.parse("creative.{field} should equal {expected}"))
def then_creative_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Exact value on a creative sub-field (e.g. supports_compliance should equal
    true). Pinned to the declared fixture value — the creative block is optional
    and only present when creative is in supported_protocols."""
    _assert_wire_equals(ctx, f"creative.{field}", expected)


@then(parsers.parse("the response should include media_buy.portfolio with publisher_domains {domains}"))
def then_portfolio_domains(ctx: dict, domains: str) -> None:
    actual = wire_field(ctx, "media_buy.portfolio.publisher_domains")
    assert sorted(actual) == sorted(_quoted_list(domains)), f"publisher_domains {actual!r} != {domains}"


@then(parsers.parse("the response should include media_buy.portfolio with primary_channels {channels}"))
def then_portfolio_channels(ctx: dict, channels: str) -> None:
    actual = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert sorted(actual) == sorted(_quoted_list(channels)), f"primary_channels {actual!r} != {channels}"


@then("primary_channels should equal the channels enum's 20 canonical values")
def then_primary_channels_all_canonical(ctx: dict) -> None:
    """Every 3.1.1 channels enum value round-trips onto primary_channels.
    primary_channels items $ref channels.json#/enum (20 values, no minItems/
    uniqueItems) — so the graded contract is set-equality against all 20.
    Strict xfail today: CHANNEL_MAPPING omits sponsored_intelligence, so the
    20th value is dropped and the wire carries only 19."""
    actual = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert sorted(actual) == sorted(CHANNELS_ENUM), (
        f"primary_channels is not the full 20-value channels enum: "
        f"missing {sorted(set(CHANNELS_ENUM) - set(actual))}, extra {sorted(set(actual) - set(CHANNELS_ENUM))}"
    )


@then("the wire response should not contain a media_buy key")
def then_no_media_buy(ctx: dict) -> None:
    """Absence asserted as wire-key absence — top-level required is
    [adcp, supported_protocols], so media_buy is optional and a minimal response
    omits the key entirely (there is no `media_buy.details` wire key)."""
    wire_absent(ctx, "media_buy")


@then(parsers.parse("media_buy.creative_approval_mode should be {expected}"))
def then_creative_approval_mode(ctx: dict, expected: str) -> None:
    """NEW at 3.1.1: closed enum [auto_approve, require_human], absence =
    legacy-unspecified (not an auto-approve claim) — salesagent-y9ld R7.
    "absent" grades the omission itself; "equal to \"<value>\"" grades an
    exact enum match."""
    expected = expected.strip()
    if expected == "absent":
        wire_absent(ctx, "media_buy.creative_approval_mode")
        return
    match = re.fullmatch(r'equal to "([^"]+)"', expected)
    assert match, f"unrecognized expected clause for creative_approval_mode: {expected!r}"
    actual = wire_field(ctx, "media_buy.creative_approval_mode")
    assert actual == match.group(1), f"media_buy.creative_approval_mode expected {match.group(1)!r}, got {actual!r}"


@then("the wire response should not contain an adcp_error field")
def then_no_adcp_error(ctx: dict) -> None:
    """A successful (degraded-but-valid) response carries no envelope error signal:
    protocol-envelope adcp_error is the transport-level error field for FATAL task
    failures only, so it is absent on a non-failure."""
    wire_absent(ctx, "adcp_error")


@then("the response should pass schema validation for get-adcp-capabilities-response")
def then_schema_valid(ctx: dict) -> None:
    """Storyboard response_schema check: the serialized wire MUST conform to
    get-adcp-capabilities-response.json. Re-validate the actual wire body through
    the pinned GetAdcpCapabilitiesResponse model (generated from that schema) — a
    degraded response that dropped a required field or emitted an out-of-constraint
    value raises ValidationError here, so the assertion is non-vacuous."""
    _assert_schema_valid(ctx)


@then("the response should NOT include account section")
def then_no_account(ctx: dict) -> None:
    wire_absent(ctx, "account")


# ── Thens: read-only invariant ───────────────────────────────────────


@then("the row counts of tenants, principals, publisher_partners and media_buys should equal their pre-request values")
def then_state_unchanged(ctx: dict) -> None:
    """Read-only invariant with a concrete observable set: the four mutable
    tables a capabilities call could touch (tenants, principals,
    publisher_partners, media_buys) — snapshotted by _db_state_snapshot."""
    before = ctx["state_snapshot"]
    after = _db_state_snapshot(ctx["env"])
    assert after == before, f"state changed by a read-only call: before={before} after={after}"


# ── Thens: auth outline ──────────────────────────────────────────────


@then(parsers.re(r"the response should be (?P<outcome>success|AUTH_INVALID)$"))
def then_auth_outcome(ctx: dict, outcome: str) -> None:
    if outcome == "success":
        # A success outcome is a non-error completed discovery envelope: no wire
        # error envelope was produced (auth accepted / treated-as-absent) and the
        # spec-required top-level blocks are on the wire (top-level required is
        # [adcp, supported_protocols]). The fuller section shape is graded by the
        # companion "a success outcome should carry ..." Then.
        assert ctx.get("error") is None, f"expected success, got error: {ctx.get('error')!r}"
        assert ctx.get("wire_error_envelope") is None, (
            f"expected success, got a wire error envelope: {ctx.get('wire_error_envelope')!r}"
        )
        for path in ("adcp", "supported_protocols"):
            wire_field(ctx, path)
        return
    from tests.helpers.envelope_assertions import assert_envelope_shape

    assert ctx.get("error") is not None, "expected AUTH_INVALID, got a success response"
    assert_envelope_shape(
        ctx.get("wire_error_envelope"),
        "AUTH_INVALID",
        recovery="terminal",
    )


@then(
    "a success outcome should carry adcp.major_versions, adcp.idempotency, supported_protocols and the media_buy section"
)
def then_success_carries_sections(ctx: dict) -> None:
    if ctx.get("response") is None:
        return  # conditional Then: only grades success outcomes
    for path in ("adcp.major_versions", "adcp.idempotency", "supported_protocols", "media_buy"):
        wire_field(ctx, path)


@then("both responses should contain identical capabilities data ignoring last_updated and context")
def then_dual_call_identity(ctx: dict) -> None:
    history = ctx.get("response_history", [])
    assert len(history) == 2, f"expected exactly 2 dispatches, saw {len(history)}"
    dumps = []
    for response, error in history:
        assert response is not None, f"one of the dual calls errored: {error!r}"
        data = response.model_dump(mode="json")
        for volatile in ("last_updated", "context", "context_id", "timestamp"):
            data.pop(volatile, None)
        dumps.append(data)
    assert dumps[0] == dumps[1], f"auth state changed response data: {dumps[0]} != {dumps[1]}"


@then("the response should be a success carrying adcp.major_versions, adcp.idempotency and supported_protocols")
def then_mcp_invalid_token_success(ctx: dict) -> None:
    assert ctx.get("response") is not None, f"expected success, got error: {ctx.get('error')!r}"
    for path in ("adcp.major_versions", "adcp.idempotency", "supported_protocols"):
        wire_field(ctx, path)


@then("the response should carry the tenant's normal capabilities, not gated on the invalid token")
def then_capabilities_not_gated_on_token(ctx: dict) -> None:
    """INV-4 (AdCP v3.1.1, salesagent-dn2s): capability discovery describes
    the SELLER, not the caller — an invalid/absent token must not degrade
    adapter-derived data. Channels are tenant-resolved (get_adapter_class_for_tenant)
    regardless of whether the presented token resolved a principal, so they
    must equal the harness's tenant-level adapter seed, unaffected by the
    invalid token.

    audience_targeting/conversion_tracking are asserted absent because
    production doesn't emit them at all yet (separate #1592 gap) — NOT
    because a principal is missing.
    """
    from tests.harness.capabilities import DEFAULT_ADAPTER_CHANNELS

    for path in ("media_buy.audience_targeting", "media_buy.conversion_tracking"):
        wire_absent(ctx, path)
    channels = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert channels == DEFAULT_ADAPTER_CHANNELS, (
        f"adapter-derived channels degraded by an invalid token (INV-4 violation): "
        f"expected {DEFAULT_ADAPTER_CHANNELS!r}, got {channels!r}"
    )


@then(parsers.re(r'the wire error message should contain "(?P<first>[^"]+)" and "(?P<second>[^"]+)"$'))
def then_wire_error_message_contains(ctx: dict, first: str, second: str) -> None:
    """errors[0].message on the wire envelope must carry BOTH pinned substrings
    (case-insensitive). core/error.json message is a free string, so the spec
    cannot pin content — the substrings are pinned to production's ACTUAL
    AUTH_INVALID wording: resolved_identity.py "Authentication token is invalid
    for tenant '...'" and adcp_a2a_server.py "Authentication token is invalid or
    expired." both contain "token" and "invalid". Requiring both rejects the
    AUTH_REQUIRED missing-credential wording ("authentication required")."""
    envelope = ctx["result"].wire_error_envelope
    assert isinstance(envelope, dict), f"no wire error envelope captured (error={ctx.get('error')!r})"
    errors = envelope.get("errors") or [{}]
    message = errors[0].get("message") or ""
    assert message, f"errors[0].message is empty on the wire envelope: {envelope}"
    lowered = message.lower()
    for substring in (first, second):
        assert substring.lower() in lowered, (
            f"wire error message {message!r} is missing the pinned substring {substring!r}"
        )


# ── Thens: protocols filter (ext-d) ──────────────────────────────────


@then(
    parsers.re(
        r"the response should include the (?P<section>media_buy|signals|governance|sponsored_intelligence|creative) section$"
    )
)
def then_section_present(ctx: dict, section: str) -> None:
    wire_field(ctx, section)


@then("the response should include adcp, supported_protocols and account as protocol-invariant blocks")
def then_protocol_invariant_blocks(ctx: dict) -> None:
    for path in ("adcp", "supported_protocols", "account"):
        wire_field(ctx, path)


@then("the response should NOT include the signals, governance, sponsored_intelligence or creative sections")
def then_unrequested_sections_absent(ctx: dict) -> None:
    for section in ("signals", "governance", "sponsored_intelligence", "creative"):
        wire_absent(ctx, section)


# ── Thens: context echo (ext-e) ──────────────────────────────────────


@then(parsers.parse("the response context should equal {expected}"))
def then_context_equals(ctx: dict, expected: str) -> None:
    actual = wire_field(ctx, "context")
    assert actual == json.loads(expected), f"context echo mismatch: {actual!r} != {expected}"


@then("the wire response should not contain a context field")
def then_wire_no_context(ctx: dict) -> None:
    wire_absent(ctx, "context")


@then("the wire response context should equal {}")
def then_wire_context_empty(ctx: dict) -> None:
    actual = wire_field(ctx, "context")
    assert actual == {}, f"wire context expected {{}}, got {actual!r}"


# ── Thens: version negotiation error details ─────────────────────────


@then("the error details should include supported_versions as a non-empty array")
def then_details_supported_versions(ctx: dict) -> None:
    versions = _error_details(ctx).get("supported_versions")
    assert isinstance(versions, list) and versions, f"details.supported_versions not a non-empty array: {versions!r}"


@then(parsers.parse('each supported_versions entry should match pattern "{pattern}"'))
def then_details_versions_pattern(ctx: dict, pattern: str) -> None:
    # The feature escapes backslashes for Gherkin — unescape before matching.
    unescaped = pattern.replace("\\\\", "\\")
    for value in _error_details(ctx)["supported_versions"]:
        assert re.fullmatch(unescaped, value), f"supported_versions entry {value!r} does not match {unescaped!r}"


@then(parsers.parse('the error details should include supported_versions containing "{v1}" and "{v2}"'))
def then_details_versions_containing(ctx: dict, v1: str, v2: str) -> None:
    versions = _error_details(ctx).get("supported_versions", [])
    assert v1 in versions and v2 in versions, f"details.supported_versions {versions!r} missing {v1!r}/{v2!r}"


@then(parsers.parse('the error details should include build_version equal to "{build_version}"'))
def then_details_build_version(ctx: dict, build_version: str) -> None:
    actual = _error_details(ctx).get("build_version")
    assert actual == build_version, f"details.build_version {actual!r} != {build_version!r}"


# ── Thens: targeting outline row→assertion dispatch (targeting-partitions) ──

#: The 9 canonical targeting property names
#: (get-adcp-capabilities-response.json#/.../targeting/properties).
_NINE_TARGETING_KEYS = {
    "geo_countries",
    "geo_regions",
    "geo_metros",
    "geo_postal_areas",
    "age_restriction",
    "language",
    "keyword_targets",
    "negative_keywords",
    "geo_proximity",
}

_TARGETING_PREFIX = "media_buy.execution.targeting"


def _tp_full_adapter(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert set(targeting) == _NINE_TARGETING_KEYS, f"targeting keys != 9 canonical: {sorted(targeting)}"
    assert targeting["geo_countries"] is True and targeting["geo_regions"] is True, (
        f"geo flags not both true: {targeting!r}"
    )


def _tp_defaults(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert targeting == {"geo_countries": True, "geo_regions": True}, f"targeting != production default: {targeting!r}"


def _tp_partial(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert targeting.get("geo_countries") is True, f"geo_countries not true: {targeting!r}"
    assert targeting.get("geo_regions") is False, f"geo_regions not false: {targeting!r}"
    assert targeting.get("age_restriction", {}).get("supported") is True, (
        f"age_restriction.supported not true: {targeting!r}"
    )
    stray = {"geo_metros", "geo_postal_areas"} & set(targeting)
    assert not stray, f"partial config leaked geo keys {sorted(stray)}: {sorted(targeting)}"


def _tp_nested_populated(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert targeting.get("geo_metros") == {"nielsen_dma": True}, (
        f"geo_metros != {{nielsen_dma: true}}: {targeting.get('geo_metros')!r}"
    )
    postal = targeting.get("geo_postal_areas") or {}
    assert "US" in postal and "zip" in postal["US"], f"geo_postal_areas US containing zip not present: {postal!r}"


def _tp_nested_absent(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    stray = {"geo_metros", "geo_postal_areas"} & set(targeting)
    assert not stray, f"geo_metros/geo_postal_areas should be absent: {sorted(targeting)}"


def _tp_keyword(ctx: dict) -> None:
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert targeting.get("keyword_targets", {}).get("supported_match_types") == ["broad", "phrase", "exact"], (
        f"keyword_targets match types wrong: {targeting.get('keyword_targets')!r}"
    )
    assert targeting.get("negative_keywords", {}).get("supported_match_types") == ["exact"], (
        f"negative_keywords match types wrong: {targeting.get('negative_keywords')!r}"
    )


def _tp_postal_legacy(ctx: dict) -> None:
    postal = wire_field(ctx, f"{_TARGETING_PREFIX}.geo_postal_areas")
    assert isinstance(postal, dict) and "DE" in postal and "plz" in postal["DE"], (
        f"native DE containing plz not present: {postal!r}"
    )


#: Targeting outline expected-column → assertion. Rows production satisfies
#: (adapter_unavailable_defaults, nested_absent) pass; the rest execute the real
#: assertion and fail on dimensions the builder never emits (#1592 gaps, marked
#: strict via conftest _SELECTIVE_XFAIL).
_TARGETING_SATISFY: dict[str, Any] = {
    "targeting has exactly the keys geo_countries, geo_regions, geo_metros, geo_postal_areas, "
    "age_restriction, language, keyword_targets, negative_keywords and geo_proximity with "
    "geo_countries true and geo_regions true": _tp_full_adapter,
    "targeting equals exactly {geo_countries: true, geo_regions: true}": _tp_defaults,
    "geo_countries true, geo_regions false, age_restriction.supported true, no other geo keys": _tp_partial,
    'geo_metros equals {nielsen_dma: true} and geo_postal_areas has US containing "zip"': _tp_nested_populated,
    "geo_metros and geo_postal_areas absent from targeting": _tp_nested_absent,
    "age_restriction equals {supported: true, verification_methods: [id_document]}": lambda ctx: _assert_wire_equals(
        ctx, f"{_TARGETING_PREFIX}.age_restriction", '{"supported": true, "verification_methods": ["id_document"]}'
    ),
    "keyword_targets match types equal [broad, phrase, exact] and negative_keywords match types equal [exact]": _tp_keyword,
    "geo_proximity equals {radius: true, travel_time: true, geometry: false, transport_modes: [driving, walking]}": lambda ctx: (
        _assert_wire_equals(
            ctx,
            f"{_TARGETING_PREFIX}.geo_proximity",
            '{"radius": true, "travel_time": true, "geometry": false, "transport_modes": ["driving", "walking"]}',
        )
    ),
    "geo_postal_areas equals {DE: [plz], CH: [plz], AT: [plz]}": lambda ctx: _assert_wire_equals(
        ctx, f"{_TARGETING_PREFIX}.geo_postal_areas", '{"DE": ["plz"], "CH": ["plz"], "AT": ["plz"]}'
    ),
    'geo_postal_areas has native DE containing "plz" and MAY carry the deprecated de_plz alias': _tp_postal_legacy,
}


@then(parsers.parse("media_buy.execution.targeting should satisfy {expected_targeting}"))
def then_targeting_satisfies(ctx: dict, expected_targeting: str) -> None:
    assertion = _TARGETING_SATISFY.get(expected_targeting.strip())
    if assertion is None:
        raise NotImplementedError(f"UC-010 targeting assertion row not wired: {expected_targeting!r} (#1592)")
    assertion(ctx)


# ── Thens: degradation + features-partitions outline dispatch ────────


def _deg_full_response(ctx: dict) -> None:
    wire = wire_dict(ctx)
    for key in ("adcp", "supported_protocols", "account", "media_buy", "last_updated"):
        assert key in wire, f"full_response missing top-level {key!r}: {sorted(wire)}"
    billing = wire["account"].get("supported_billing")
    assert isinstance(billing, list) and billing, f"account.supported_billing not non-empty: {billing!r}"
    assert isinstance(wire["adcp"].get("idempotency"), dict), f"adcp.idempotency absent: {wire['adcp']!r}"


def _deg_no_tenant(ctx: dict) -> None:
    wire = wire_dict(ctx)
    assert set(wire) <= {"adcp", "supported_protocols"}, f"no_tenant top-level not minimal: {sorted(wire)}"
    adcp = wire["adcp"]
    for key in ("major_versions", "supported_versions", "idempotency"):
        assert key in adcp, f"no_tenant adcp missing {key!r}: {sorted(adcp)}"


def _deg_display_default(ctx: dict) -> None:
    channels = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert channels == ["display"], f"primary_channels not the [display] default: {channels!r}"
    targeting = wire_field(ctx, _TARGETING_PREFIX)
    assert targeting == {"geo_countries": True, "geo_regions": True}, (
        f"targeting not the degraded default: {targeting!r}"
    )
    for path in (
        "media_buy.reporting_delivery_methods",
        "media_buy.audience_targeting",
        "media_buy.conversion_tracking",
    ):
        wire_absent(ctx, path)


def _assert_placeholder_domain(ctx: dict) -> None:
    domains = wire_field(ctx, "media_buy.portfolio.publisher_domains")
    assert isinstance(domains, list) and len(domains) == 1 and str(domains[0]).endswith(".example.com"), (
        f"publisher_domains not the single placeholder domain: {domains!r}"
    )


def _deg_db_fail(ctx: dict) -> None:
    _assert_placeholder_domain(ctx)
    channels = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert channels == ["display", "social", "ctv"], f"adapter channels degraded on a DB-only failure: {channels!r}"


def _deg_adapter_and_db_fail(ctx: dict) -> None:
    channels = wire_field(ctx, "media_buy.portfolio.primary_channels")
    assert channels == ["display"], f"primary_channels not the [display] default: {channels!r}"
    _assert_placeholder_domain(ctx)
    for path in ("media_buy.audience_targeting", "media_buy.conversion_tracking"):
        wire_absent(ctx, path)


def _deg_account_degraded(ctx: dict) -> None:
    account = wire_field(ctx, "account")
    billing = account.get("supported_billing")
    assert isinstance(billing, list) and billing, f"degraded account.supported_billing not non-empty: {billing!r}"
    extras = set(account) - {"supported_billing"}
    assert not extras, f"degraded account carries optional fields: {sorted(extras)}"


# Row-text → assertion closure (features-partitions account.sandbox + degradation rows).
# Rows production satisfies pass; production-gap rows execute the real assertion and
# fail (strict per-row xfail in conftest _SELECTIVE_XFAIL).
_SATISFY_TABLE: dict[str, Any] = {
    "account.sandbox is true": lambda ctx: _expect_flag(ctx, "account.sandbox", "equal to true"),
    "account.sandbox is false": lambda ctx: _expect_flag(ctx, "account.sandbox", "equal to false"),
    "top-level keys include adcp, supported_protocols, account, media_buy and last_updated "
    "with account.supported_billing non-empty and adcp.idempotency present": _deg_full_response,
    "only adcp and supported_protocols at top level, with adcp carrying major_versions, "
    "supported_versions and idempotency": _deg_no_tenant,
    "primary_channels equals [display] and targeting equals exactly {geo_countries: true, "
    "geo_regions: true} with no reporting_delivery_methods, audience_targeting or conversion_tracking": _deg_display_default,
    "publisher_domains equals the placeholder domain and primary_channels equals [display, social, ctv]": _deg_db_fail,
    "primary_channels equals [display] and publisher_domains equals the placeholder domain, "
    "adapter-dependent sections absent": _deg_adapter_and_db_fail,
    "account present with non-empty supported_billing and no optional account fields": _deg_account_degraded,
    "media_buy.audience_targeting absent": lambda ctx: wire_absent(ctx, "media_buy.audience_targeting"),
    "media_buy.conversion_tracking absent": lambda ctx: wire_absent(ctx, "media_buy.conversion_tracking"),
    "creative section absent": lambda ctx: wire_absent(ctx, "creative"),
}


@then(parsers.parse("the response should satisfy {expected_assertion}"))
def then_response_satisfies(ctx: dict, expected_assertion: str) -> None:
    assertion = _SATISFY_TABLE.get(expected_assertion.strip())
    if assertion is None:
        raise NotImplementedError(f"UC-010 assertion row not wired yet: {expected_assertion!r} (#1592)")
    assertion(ctx)


# ══════════════════════════════════════════════════════════════════════════
# Batch 5 (salesagent-scgh): v3.1.1 signing / brand / reporting / measurement
#
# reporting_delivery_methods, offline_delivery_protocols, webhook_signing, the
# brand block, the identity signing posture and the measurement block are NOT
# built by the capabilities builder (src/core/tools/capabilities.py emits only
# adcp / supported_protocols / specialisms / media_buy{features,execution}).
# The Givens record declared intent; the Thens grade the exact v3.1.1-pinned
# shape on the wire. Rows production already satisfies (polling-only absence,
# the no-emission webhook row, the valid identity row) PASS; the rest execute
# the real assertion and fail on the unemitted block — strict per-row/per-tag
# xfail in conftest (_SELECTIVE_XFAIL / _XFAIL_TAGS), never a dormant skip.
# ══════════════════════════════════════════════════════════════════════════


#: 3.1.1 cloud-storage-protocol enum (enums/cloud-storage-protocol.json).
CLOUD_STORAGE_PROTOCOL_ENUM = {"s3", "gcs", "azure_blob"}

#: 3.1.1 vendor-metric-id constraints (core/vendor-metric-id.json).
_VENDOR_METRIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _parse_bracket_list(token: str) -> list[str]:
    """Parse a Gherkin '[a, b]' fragment into a list of bare string tokens."""
    inner = token.strip().removeprefix("[").removesuffix("]").strip()
    if not inner:
        return []
    return [part.strip() for part in inner.split(",") if part.strip()]


def _grade_array_or_absent(ctx: dict, path: str, expected: str) -> None:
    """Grade an outline <expected> column of the form 'absent' | 'equal to [a, b]'
    against a success-path array field on the wire."""
    expected = expected.strip()
    if expected == "absent":
        wire_absent(ctx, path)
        return
    match = re.fullmatch(r"equal to (\[[^\]]*\])", expected)
    assert match, f"unrecognized array expected column: {expected!r}"
    want = _parse_bracket_list(match.group(1))
    actual = wire_field(ctx, path)
    assert actual == want, f"{path} expected {want!r}, got {actual!r}"


# ── Givens: declared-intent recorders (production has no config surface) ──


@given('"brand" is in supported_protocols')
def given_brand_in_supported_protocols(ctx: dict) -> None:
    """Declare the brand protocol. Production advertises only media_buy, so the
    brand top-level block is never emitted (#1724 — the brand family is re-homed
    entirely, not partially delivered) — records intent."""
    _config(ctx).setdefault("supported_protocols", []).append("brand")


@given('"measurement" is in supported_protocols')
def given_measurement_in_supported_protocols(ctx: dict) -> None:
    """Declare the measurement protocol on the tenant's capability declaration
    store (batch B1, salesagent-3xmz — a REAL tenant-config write, not a ctx
    record-intent dict, so the Given runs identically on a2a/mcp/rest/e2e_rest).

    ``measurement`` is a protocol CLAIM, not a replacement: the seller keeps
    everything it already advertises and adds this one, so the emitted list is
    the union of the defaults and the declaration.
    """
    env = ctx["env"]
    env.declare_capabilities(supported_protocols=["measurement"])


@given(
    "the tenant declares brand.rights=true right_types=[talent, music] "
    'available_uses=[likeness, sync] generation_providers=["openai"]'
)
def given_brand_posture(ctx: dict) -> None:
    """Declare a concrete brand posture (rights/right_types/available_uses/
    generation_providers). Records intent; the capabilities builder does not emit
    the brand block (#1724) so the value Thens xfail."""
    _config(ctx)["brand"] = {
        "rights": True,
        "right_types": ["talent", "music"],
        "available_uses": ["likeness", "sync"],
        "generation_providers": ["openai"],
    }


@given(parsers.parse("the tenant declares reporting delivery methods {methods} with offline protocols {protocols}"))
def given_reporting_delivery_methods(ctx: dict, methods: str, protocols: str) -> None:
    """Declare push-based reporting delivery methods + offline protocols. Records
    intent; the declaration store deliberately carries no field for either under
    the STRICT capability policy (#1291) — declaring [webhook] would fire the
    schema must_equal_when forcing webhook_signing.supported=true, and no offline
    report delivery is implemented."""
    _config(ctx)["reporting_delivery_methods"] = None if methods.strip() == "omitted" else _parse_bracket_list(methods)
    _config(ctx)["offline_delivery_protocols"] = (
        None if protocols.strip() == "omitted" else _parse_bracket_list(protocols)
    )


@given(
    parsers.re(
        r"the tenant declares (?P<emission_state>(?:media_buy\.|wholesale_feed_webhooks\.).+|no mutating-webhook emission)$"
    )
)
def given_webhook_emission_state(ctx: dict, emission_state: str) -> None:
    """Declare a mutating-webhook emission posture (or its absence) for the
    webhook-signing required_when invariant. Records intent; the declaration store
    deliberately carries no webhook_signing field under the STRICT capability policy
    (#1291) so the must_equal_when invariant is ungraded."""
    _config(ctx)["webhook_emission_state"] = emission_state.strip()


@given(parsers.parse("the tenant declares {signing_posture} with identity block {identity_state}"))
def given_signing_posture_with_identity(ctx: dict, signing_posture: str, identity_state: str) -> None:
    """Declare a signing posture + identity-block state for the identity
    required_when invariant. Records intent; the declaration store deliberately
    carries no identity or request_signing field under the STRICT capability policy
    (#1291), so a signing posture missing brand_json_url cannot be declared and the
    required_when rejection has nothing to fire on."""
    _config(ctx)["signing_posture"] = signing_posture.strip()
    _config(ctx)["identity_state"] = identity_state.strip()


@given(parsers.parse('the tenant declares measurement.metrics with metric_id "{metric_id}"'))
def given_measurement_metric(ctx: dict, metric_id: str) -> None:
    """Declare the tenant's measurement metric catalog (batch B1, salesagent-3xmz).

    ``metric_id`` is the only required field of a metrics entry
    (get-adcp-capabilities-response.json#/properties/measurement/properties/
    metrics/items, required ["metric_id"]), so a one-metric catalog is the
    minimal conformant declaration — the seller's own vocabulary, a business
    fact the response echoes.
    """
    env = ctx["env"]
    env.declare_capabilities(measurement={"metrics": [{"metric_id": metric_id}]})


@given(parsers.parse("the tenant declares specialisms {specialisms} with supported_protocols {protocols}"))
def given_declares_specialisms_and_protocols(ctx: dict, specialisms: str, protocols: str) -> None:
    """Declare both halves of a specialism claim (local-uc010-declaration-backing).

    Backing rules are evaluated on the READ path, so an UNBACKED declaration
    persists here and surfaces as CONFIGURATION_ERROR at the
    ``get_adcp_capabilities`` call — the graded observable — rather than blowing
    up inside this Given where no wire assertion could see it.
    """
    ctx["env"].declare_capabilities(
        specialisms=_quoted_list(specialisms),
        supported_protocols=_quoted_list(protocols),
    )


@given(parsers.parse("the tenant declares specialisms {specialisms} without declaring its parent protocol"))
def given_declares_orphaned_specialism(ctx: dict, specialisms: str) -> None:
    """Declare a specialism but NOT its parent protocol — the roll-up boundary.

    Distinct from the unbacked case: the specialism itself IS backed, so only the
    roll-up rule ("the runner rejects a specialism claim whose parent protocol is
    missing", #/properties/specialisms) can catch it.
    """
    ctx["env"].declare_capabilities(specialisms=_quoted_list(specialisms))


@given(parsers.parse("the tenant declares supported_protocols {protocols}"))
def given_declares_protocols(ctx: dict, protocols: str) -> None:
    """Declare protocol claims alone, with no specialism, to grade the protocol
    backing rule in isolation from the specialism rules."""
    ctx["env"].declare_capabilities(supported_protocols=_quoted_list(protocols))


# metric_id is required on every metrics entry, but the accreditation outline does
# not vary it — it varies only the accreditation fields. Pinning one id here keeps
# the Examples table about the thing under test.
_ACCREDITED_METRIC_ID = "viewable_impressions"


def _parse_accreditation_fixture(raw: str) -> dict[str, str]:
    """Parse a ``k=v k=v`` accreditation fixture into the declared field map.

    ONE parser shared by the Given that declares it and the Then that asserts on
    it — if the two parsed independently they could drift, and the Then would be
    grading its own copy of the fixture rather than the round-trip.
    """
    fields: dict[str, str] = {}
    for token in raw.strip().split():
        key, _, value = token.partition("=")
        assert value, f"malformed accreditation fixture token {token!r} in {raw!r}"
        fields[key] = value
    assert "accrediting_body" in fields, f"accrediting_body is required by the schema, missing in {raw!r}"
    return fields


@given(parsers.parse("the tenant declares a measurement metric with accreditation {accreditation}"))
def given_measurement_metric_accreditation(ctx: dict, accreditation: str) -> None:
    """Declare a one-metric catalog carrying one third-party accreditation.

    accreditations[] items: ``accrediting_body`` required (open string), optional
    ``certification_id`` / ``valid_until`` (date) / ``evidence_url`` (uri), with
    ``additionalProperties: false``
    (get-adcp-capabilities-response.json#/properties/measurement/properties/
    metrics/items/properties/accreditations). Only the declared keys are sent, so
    the exact-field-set Then can prove absent keys stay absent.
    """
    env = ctx["env"]
    env.declare_capabilities(
        measurement={
            "metrics": [
                {
                    "metric_id": _ACCREDITED_METRIC_ID,
                    "accreditations": [_parse_accreditation_fixture(accreditation)],
                }
            ]
        }
    )


# ── Thens: reporting_delivery_methods outline ────────────────────────────


@then(parsers.re(r"media_buy\.reporting_delivery_methods should be (?P<expected>absent|equal to \[[^\]]*\])$"))
def then_reporting_methods_value(ctx: dict, expected: str) -> None:
    """reporting_delivery_methods: absent (baseline polling only) or exactly the
    declared push methods (items ∈ {webhook, offline}, minItems 1, uniqueItems)."""
    _grade_array_or_absent(ctx, "media_buy.reporting_delivery_methods", expected)


@then(parsers.re(r"media_buy\.offline_delivery_protocols should be (?P<expected>absent|equal to \[[^\]]*\])$"))
def then_offline_protocols_value(ctx: dict, expected: str) -> None:
    """offline_delivery_protocols: absent unless reporting includes 'offline', then
    exactly the declared protocols (items ∈ cloud-storage-protocol enum)."""
    _grade_array_or_absent(ctx, "media_buy.offline_delivery_protocols", expected)
    value = wire_lookup(ctx, "media_buy.offline_delivery_protocols")
    if value is not WIRE_MISSING:
        invalid = set(value) - CLOUD_STORAGE_PROTOCOL_ENUM
        assert not invalid, f"offline_delivery_protocols carries non-enum values: {sorted(invalid)}"


@then(parsers.re(r"webhook_signing\.supported should be (?P<expected>.+)$"))
def then_webhook_signing_supported(ctx: dict, expected: str) -> None:
    """webhook_signing.supported conditional invariant (must_equal_when): when the
    seller advertises mutating-webhook emission it MUST equal true; when no trigger
    fires it may be true, false, or absent (honest tautology — no cross-field
    constraint). 'equal to true/false' grades the exact value; anything else is the
    no-trigger row (present→boolean or absent)."""
    expected = expected.strip()
    path = "webhook_signing.supported"
    if expected in ("equal to true", "equal to false"):
        actual = wire_field(ctx, path)
        assert actual is (expected == "equal to true"), f"{path} expected {expected}, got {actual!r}"
        return
    value = wire_lookup(ctx, path)
    if value is not WIRE_MISSING:
        assert isinstance(value, bool), f"{path} present but not a boolean: {value!r}"


# ── Thens: brand block ───────────────────────────────────────────────────


@then("the response should include brand section")
def then_brand_section_present(ctx: dict) -> None:
    """brand top-level block is present only when 'brand' is in supported_protocols.
    wire_dict pins present AND non-null AND a JSON object."""
    wire_dict(ctx, "brand")


@then(parsers.parse("brand.{field} should equal {expected}"))
def then_brand_field_equals(ctx: dict, field: str, expected: str) -> None:
    """Exact-echo a declared brand sub-field: rights (boolean), right_types /
    available_uses (arrays of right-type / right-use enum members),
    generation_providers (array of open strings)."""
    _assert_wire_equals(ctx, f"brand.{field}", expected)


# ── Thens: identity required_when verdict ────────────────────────────────


@then(parsers.parse("the seller capabilities builder should treat the configuration as {verdict}"))
def then_identity_signing_verdict(ctx: dict, verdict: str) -> None:
    """identity.brand_json_url required_when invariant: a seller declaring a signing
    posture with an absent/empty identity MUST be rejected (it cannot produce a
    conformant response), surfaced as a CONFIGURATION_ERROR (seller-side deployment
    fault, recovery terminal) naming brand_json_url. A no-posture config emits a
    valid success response (adcp + supported_protocols, no adcp_error)."""
    verdict = verdict.strip()
    if verdict.startswith("rejected"):
        _assert_capabilities_config_error(ctx, message_substr="brand_json_url")
        return
    assert verdict == "a valid capabilities response", f"unrecognized verdict column: {verdict!r}"
    _assert_capabilities_success(ctx)


# ── Thens: measurement block ─────────────────────────────────────────────


@then("the response should include measurement section")
def then_measurement_section_present(ctx: dict) -> None:
    """measurement (x-status experimental) block present. wire_dict pins present
    AND non-null AND a JSON object."""
    wire_dict(ctx, "measurement")


@then("measurement.metrics should be a non-empty array")
def then_measurement_metrics_nonempty(ctx: dict) -> None:
    """metrics: minItems 1 (measurement requires at least one computed metric)."""
    metrics = wire_field(ctx, "measurement.metrics")
    assert isinstance(metrics, list) and metrics, f"measurement.metrics not a non-empty array: {metrics!r}"


@then(parsers.parse('each metrics entry\'s metric_id should match pattern "{pattern}" with length 1..64'))
def then_measurement_metric_id_shape(ctx: dict, pattern: str) -> None:
    """Every metrics[].metric_id is a vendor-metric-id: pattern ^[a-z][a-z0-9_]*$,
    minLength 1, maxLength 64 (core/vendor-metric-id.json)."""
    assert pattern == "^[a-z][a-z0-9_]*$", f"scenario pattern drifted from vendor-metric-id.json: {pattern!r}"
    metrics = wire_field(ctx, "measurement.metrics")
    for entry in metrics:
        metric_id = entry.get("metric_id")
        assert isinstance(metric_id, str) and _VENDOR_METRIC_ID_RE.match(metric_id), (
            f"metric_id does not match {pattern}: {metric_id!r}"
        )
        assert 1 <= len(metric_id) <= 64, f"metric_id length outside 1..64: {metric_id!r}"


@then(parsers.parse('a metrics entry should carry metric_id "{metric_id}"'))
def then_measurement_metric_present(ctx: dict, metric_id: str) -> None:
    """The declared metric_id round-trips: some metrics entry carries it exactly."""
    metrics = wire_field(ctx, "measurement.metrics")
    ids = [entry.get("metric_id") for entry in metrics]
    assert metric_id in ids, f"no metrics entry carries metric_id {metric_id!r}: {ids!r}"


@then(parsers.parse("the metric accreditation should equal the declared {accreditation} fields exactly"))
def then_measurement_accreditation_exact(ctx: dict, accreditation: str) -> None:
    """The declared accreditation round-trips with EXACTLY the declared field set.

    Exact-set equality, not a subset check: the scenario says "exactly", and
    ``Accreditation`` is ``additionalProperties: false``, so a key the operator did
    not declare appearing on the wire is a defect just as much as a declared key
    going missing. A "may include" style assert would pass on both.

    Compares SERIALIZED values — ``valid_until`` is a ``date`` and ``evidence_url``
    an ``AnyUrl`` in the model, so the wire carries ``"2027-01-01"`` /
    ``"https://abc.org/listing"``. Asserting against the coerced Python objects
    would grade the harness's deserialization instead of the buyer's bytes.
    """
    expected = _parse_accreditation_fixture(accreditation)
    metrics = wire_field(ctx, "measurement.metrics")
    entry = next((m for m in metrics if m.get("metric_id") == _ACCREDITED_METRIC_ID), None)
    assert entry is not None, f"no metrics entry with metric_id {_ACCREDITED_METRIC_ID!r}: {metrics!r}"

    accreditations = entry.get("accreditations")
    assert isinstance(accreditations, list) and len(accreditations) == 1, (
        f"expected exactly one accreditation on {_ACCREDITED_METRIC_ID}, got {accreditations!r}"
    )
    actual = {key: value for key, value in accreditations[0].items() if value is not None}
    assert actual == expected, f"accreditation field set differs — expected {expected!r}, wire carried {actual!r}"


@then(parsers.parse('experimental_features should contain "{feature}"'))
def then_experimental_features_contains(ctx: dict, feature: str) -> None:
    """Agents implementing measurement MUST list measurement.core in
    experimental_features (measurement block + supported_protocols descriptions)."""
    features = wire_field(ctx, "experimental_features")
    assert feature in features, f"experimental_features does not contain {feature!r}: {features!r}"


# ══════════════════════════════════════════════════════════════════════════
# Batch 6 (salesagent-e4ad): compliance_testing / specialisms / advisory errors
#
# The capabilities builder (src/core/tools/capabilities.py) emits only
# adcp / supported_protocols / a HARD-CODED specialisms=[sales-non-guaranteed] /
# media_buy{features,execution}. It never emits the compliance_testing block, a
# top-level errors[] array, or a config-derived specialisms set, and advertises
# only the media_buy protocol. The Givens record declared intent; the Thens grade
# the exact v3.1.1-pinned shape on the wire. Every scenario in this batch strict-
# xfails on the genuinely-unemitted block (per-family GH homes: #1855/#1856/#1724), never a dormant skip.
# ══════════════════════════════════════════════════════════════════════════


#: 3.1.1 specialism enum, closed 22-value kebab-case set (enums/specialism.json#/enum).
SPECIALISM_ENUM = {
    "audience-sync",
    "brand-rights",
    "collection-lists",
    "content-standards",
    "creative-ad-server",
    "creative-generative",
    "creative-template",
    "creative-transformers",
    "governance-aware-seller",
    "governance-delivery-monitor",
    "governance-spend-authority",
    "property-lists",
    "sales-broadcast-tv",
    "sales-catalog-driven",
    "sales-guaranteed",
    "sales-non-guaranteed",
    "sales-proposal-mode",
    "sales-social",
    "signal-marketplace",
    "signal-owned",
    "signed-requests",
    "sponsored-intelligence",
}


# ── Givens: declared-intent recorders (production has no config surface) ──


@given("the tenant declares the compliance-testing scenarios it implements")
def given_compliance_scenarios(ctx: dict) -> None:
    """Declare compliance_testing support. Production emits no compliance_testing
    block (#1592) — records intent; the scenario xfails at the first Then."""
    _config(ctx)["compliance_testing"] = True


@given('"creative" and "media_buy" are in supported_protocols')
def given_creative_and_media_buy_protocols(ctx: dict) -> None:
    """Declare both protocols so the specialism roll-up parents are present.
    Production advertises only media_buy, so the creative roll-up parent is
    missing on the wire (#1592) — records intent."""
    protocols = _config(ctx).setdefault("supported_protocols", [])
    for protocol in ("creative", "media_buy"):
        if protocol not in protocols:
            protocols.append(protocol)


@given(parsers.parse("the tenant claims specialisms {specialisms}"))
def given_tenant_specialisms(ctx: dict, specialisms: str) -> None:
    """Declare a config-derived specialisms set. Production hard-codes
    specialisms=[sales-non-guaranteed] regardless of tenant config (#1592) —
    records intent; the equality Then xfails."""
    _config(ctx)["specialisms"] = _quoted_list(specialisms)


@given("the seller surfaces an advisory warning during discovery")
def given_advisory_warning(ctx: dict) -> None:
    """Make a real discovery degradation happen (batch B5, salesagent-3xmz).

    Realized through the EXISTING ``make_adapter_unavailable`` rather than a new
    "declare a warning" store field: a warning is a real CONDITION, not a
    declarable business fact, and letting config assert one would be exactly the
    fabrication the STRICT policy forbids. It also needs no new escape hatch —
    ``make_adapter_unavailable`` is already ``@realize_e2e``-backed via
    ``AdapterConfig.config_json["test_behavior"]["unavailable"]``, so the e2e pin
    does not grow.

    The unavailable adapter makes the adapter-channels lookup RAISE, landing in
    the except handler that records the advisory — the except path is the only
    one that advises (see ``_record_degradation``).
    """
    ctx["env"].make_adapter_unavailable()


# ── Thens: compliance_testing block ──────────────────────────────────────


@then("compliance_testing.scenarios should be a non-empty array of strings")
def then_compliance_scenarios_nonempty(ctx: dict) -> None:
    """compliance_testing.scenarios: minItems 1, items type string, OPEN (no enum)
    (get-adcp-capabilities-response.json#/properties/compliance_testing/properties/scenarios)."""
    scenarios = wire_field(ctx, "compliance_testing.scenarios")
    assert isinstance(scenarios, list) and scenarios, (
        f"compliance_testing.scenarios not a non-empty array: {scenarios!r}"
    )
    for entry in scenarios:
        assert isinstance(entry, str) and entry, (
            f"compliance_testing.scenarios carries a non-string/empty entry: {entry!r}"
        )


@then(parsers.parse('compliance_testing.scenarios should NOT contain "{value}"'))
def then_compliance_scenarios_excludes(ctx: dict, value: str) -> None:
    """list_scenarios is excluded from the advertised set — it is a discovery
    operation, not a test capability (schema description)."""
    scenarios = wire_field(ctx, "compliance_testing.scenarios")
    assert value not in scenarios, f"compliance_testing.scenarios must exclude {value!r}: {scenarios!r}"


def _controller_list_scenarios(ctx: dict) -> set[str] | None:
    """Scenario ids the seller's comply_test_controller returns for
    scenario:'list_scenarios' — the schema's named runtime source of truth. This
    seller exposes NO comply_test_controller in production (#1592), so there is no
    reference set to cross-check against; returns None (REPORTED, salesagent-e4ad)."""
    return None


@then(
    "compliance_testing.scenarios should be a subset of the scenario ids returned "
    "by the seller's comply_test_controller list_scenarios call"
)
def then_compliance_scenarios_subset(ctx: dict) -> None:
    """Cross-system consistency: the advertised compliance_testing.scenarios MUST be
    a subset of what the seller's comply_test_controller returns for
    scenario:'list_scenarios' (schema description: "the runtime source of truth
    remains comply_test_controller"). This seller exposes no comply_test_controller
    (#1592), so the reference set is unavailable — the relation is ungraded until the
    controller lands and the scenario already strict-xfails on the unemitted
    compliance_testing block. When both surfaces exist this asserts the true subset.
    Grounds: get-adcp-capabilities-response.json#/properties/compliance_testing
    (scenarios open strings, minItems 1; list_scenarios excluded)."""
    scenarios = set(wire_field(ctx, "compliance_testing.scenarios"))
    controller_scenarios = _controller_list_scenarios(ctx)
    assert controller_scenarios is not None, (
        "seller exposes no comply_test_controller list_scenarios to cross-check the "
        "advertised compliance_testing.scenarios against (production gap #1592)"
    )
    extra = scenarios - controller_scenarios
    assert not extra, f"compliance_testing.scenarios advertises ids the controller does not return: {sorted(extra)}"


# ── Thens: specialisms ───────────────────────────────────────────────────


@then(parsers.parse("specialisms should equal {expected}"))
def then_specialisms_equal(ctx: dict, expected: str) -> None:
    """specialisms echoes the declared kebab-case claims exactly (items $ref
    enums/specialism.json, uniqueItems). Order-independent set equality against the
    scenario fixture."""
    want = _quoted_list(expected)
    actual = wire_field(ctx, "specialisms")
    assert sorted(actual) == sorted(want), f"specialisms {actual!r} != {want!r}"


@then(parsers.parse("supported_protocols should equal {expected}"))
def then_supported_protocols_equal(ctx: dict, expected: str) -> None:
    """supported_protocols is the UNION of the platform baseline and the tenant's
    declaration — exact set equality, so a REPLACING semantics (which would drop
    media_buy and orphan the unconditional sales-non-guaranteed specialism) fails
    here rather than only surfacing as a runner rejection downstream."""
    want = _quoted_list(expected)
    actual = wire_field(ctx, "supported_protocols")
    assert sorted(actual) == sorted(want), f"supported_protocols {actual!r} != {want!r}"


# ── Thens: STRICT declaration-backing rejections (local-uc010-declaration-backing) ──


@then("the capability declaration should be rejected as terminal misconfiguration")
def then_declaration_rejected(ctx: dict) -> None:
    """An unbacked declaration is a DEPLOYMENT fault, not a buyer error.

    CONFIGURATION_ERROR with recovery ``terminal`` (enums/error-code.json): the
    buyer can do nothing about it and must not retry — only the operator can fix
    the tenant config. Asserted on the WIRE envelope via the canonical helper, so
    the recovery value is pinned from the AdCP enum rather than restated here.
    """
    ctx["result"].assert_wire_error("CONFIGURATION_ERROR", recovery="terminal")


@then(parsers.parse('the rejection should name "{token}"'))
def then_rejection_names(ctx: dict, token: str) -> None:
    """The rejection identifies WHICH claim was unbacked.

    An operator who declared several blocks needs to know which one to remove; a
    bare CONFIGURATION_ERROR would make them bisect their own config. Pins the
    message content because ``core/error.json`` leaves ``message`` a free string,
    so only production's actual wording can be asserted. Always follows
    ``then_declaration_rejected`` in every scenario using this step, so the
    code/recovery are the same CONFIGURATION_ERROR/terminal pair asserted there.
    """
    ctx["result"].assert_wire_error("CONFIGURATION_ERROR", recovery="terminal", message_substr=token)


@then("each specialism should be a member of the 3.1.1 specialism enum")
def then_specialisms_enum(ctx: dict) -> None:
    """Every specialisms entry is a member of the closed 22-value kebab-case enum
    (enums/specialism.json#/enum)."""
    actual = wire_field(ctx, "specialisms")
    invalid = set(actual) - SPECIALISM_ENUM
    assert not invalid, f"specialisms carries non-enum values: {sorted(invalid)}"


@then(
    parsers.re(r'specialism "(?P<specialism>[a-z-]+)" should roll up to "(?P<protocol>[a-z_]+)" in supported_protocols')
)
def then_specialism_rollup(ctx: dict, specialism: str, protocol: str) -> None:
    """Roll-up invariant (specialisms description): "Each specialism rolls up to one
    of the protocols in supported_protocols — the runner rejects a specialism claim
    whose parent protocol is missing". Graded on the wire: the claimed specialism is
    present in specialisms AND its declared parent protocol is present in
    supported_protocols."""
    specialisms = wire_field(ctx, "specialisms")
    protocols = wire_field(ctx, "supported_protocols")
    assert specialism in specialisms, f"specialism {specialism!r} not advertised in specialisms: {specialisms!r}"
    assert protocol in protocols, (
        f"specialism {specialism!r} rolls up to {protocol!r}, absent from supported_protocols: {protocols!r}"
    )


# ── Thens: advisory errors[] ─────────────────────────────────────────────


@then("the response should include errors as an array of error objects")
def then_errors_array(ctx: dict) -> None:
    """Top-level errors is an optional array of core/error.json objects
    (get-adcp-capabilities-response.json#/properties/errors)."""
    errors = wire_field(ctx, "errors")
    assert isinstance(errors, list) and errors, f"errors not a non-empty array: {errors!r}"
    for entry in errors:
        assert isinstance(entry, dict), f"errors entry not an object: {entry!r}"


@then("each errors entry should carry code and message")
def then_errors_code_and_message(ctx: dict) -> None:
    """core/error.json#/required = [code, message] — every entry carries both as
    non-empty strings."""
    errors = wire_field(ctx, "errors")
    for entry in errors:
        for member in ("code", "message"):
            value = entry.get(member)
            assert isinstance(value, str) and value, f"errors entry missing non-empty {member!r}: {entry!r}"


@then('the response envelope status should equal "completed" and the envelope should not carry adcp_error')
def then_success_envelope_no_adcp_error(ctx: dict) -> None:
    """Advisory errors do not fail discovery: the synchronous read-only metadata
    call MUST emit envelope status "completed" and the envelope MUST NOT carry
    adcp_error for a non-failure (protocol-envelope.json#/properties/status,
    #/properties/adcp_error — "the envelope MUST NOT carry adcp_error for non-
    failures"). For this status-less payload, "completed" is proven by no recorded
    error and no wire error envelope; the top-level required blocks stay on the wire."""
    assert ctx.get("error") is None, f"expected a completed envelope, got error: {ctx.get('error')!r}"
    assert ctx.get("wire_error_envelope") is None, (
        f"expected a completed envelope, got a wire error envelope: {ctx.get('wire_error_envelope')!r}"
    )
    wire_absent(ctx, "adcp_error")
    for path in ("adcp", "supported_protocols"):
        wire_field(ctx, path)


# ══════════════════════════════════════════════════════════════════════════
# Batch 7 (salesagent-jd6a): bounds / monotonicity boundary outlines
#
# request_signing subset/disjoint relations, adcp.idempotency replay_ttl_seconds
# bounds, VERSION_UNSUPPORTED details supported_versions bound, and the
# identity.brand_json_url required_when rule. Each <expected> column drives a
# concrete graded observable — a schema-valid success whose emitted block satisfies
# the pinned relation/bound, or a seller-side CONFIGURATION_ERROR rejection — never
# a vague valid/invalid word. Idempotency posture derivation and version negotiation
# are now implemented; request_signing/webhook_signing/identity remain undeclarable —
# the declaration store deliberately carries no field for these postures under the
# STRICT capability policy (#1291); the Givens record declared intent and the graded
# rows strict-xfail on the undeclarable block (tag-level for the all-fail outlines;
# selective for the identity outline whose no-rejection valid rows pass).
# ══════════════════════════════════════════════════════════════════════════


# ── Givens: declared-intent recorders (production has no config surface) ──


@given(parsers.parse("the tenant declares request_signing posture sets for {boundary_point}"))
def given_request_signing_posture_sets(ctx: dict, boundary_point: str) -> None:
    """Declare a request_signing posture-set boundary (supported_for/required_for/
    warn_for and their protocol_methods_* siblings). Records intent; the declaration
    store deliberately carries no request_signing field under the STRICT capability
    policy (#1291)."""
    _config(ctx)["request_signing_boundary"] = boundary_point.strip()


#: idempotency-ttl boundary rows → the concrete declared posture they name.
#: replay_ttl_seconds minimum 3600, maximum 604800 (schema); in_flight_max_seconds
#: MUST be ≤ replay_ttl_seconds (test-layer cross-field rule). 86400 is a valid
#: mid-range replay window used for the in_flight rows.
_IDEMPOTENCY_BOUNDARY_POSTURES: dict[str, dict[str, Any]] = {
    "replay_ttl_seconds = 3599 (below min)": {"supported": True, "replay_ttl_seconds": 3599},
    "replay_ttl_seconds = 604800 (max, 7d)": {"supported": True, "replay_ttl_seconds": 604800},
    "replay_ttl_seconds = 604801 (above max)": {"supported": True, "replay_ttl_seconds": 604801},
    "in_flight_max_seconds == replay_ttl_seconds": {
        "supported": True,
        "replay_ttl_seconds": 86400,
        "in_flight_max_seconds": 86400,
    },
    "in_flight_max_seconds > replay_ttl_seconds": {
        "supported": True,
        "replay_ttl_seconds": 86400,
        "in_flight_max_seconds": 86401,
    },
}


@given(parsers.parse("the tenant declares idempotency posture at {boundary_point}"))
def given_idempotency_posture(ctx: dict, boundary_point: str) -> None:
    """Declare a concrete idempotency posture for the replay_ttl_seconds boundary."""
    boundary_point = boundary_point.strip()
    posture = _IDEMPOTENCY_BOUNDARY_POSTURES.get(boundary_point)
    assert posture is not None, f"unmapped idempotency boundary_point: {boundary_point!r}"
    _config(ctx)["idempotency"] = posture
    ctx["env"].set_idempotency_posture(**posture)


def _parse_idempotency_posture(text: str) -> dict[str, Any]:
    """Parse a free-form 'key=value key=value ...' posture fragment
    (e.g. 'supported=true replay_ttl_seconds=3600 in_flight_max_seconds=300
    account_id_is_opaque=true') into an IdempotencyPosture kwargs dict."""
    posture: dict[str, Any] = {}
    for token in text.strip().split():
        key, _, raw_value = token.partition("=")
        if raw_value in ("true", "false"):
            posture[key] = raw_value == "true"
        else:
            posture[key] = int(raw_value)
    assert "supported" in posture, f"posture fragment {text!r} missing required 'supported=' token"
    return posture


@given(parsers.re(r"the tenant declares idempotency posture (?P<posture>supported=.+)$"))
def given_idempotency_posture_freeform(ctx: dict, posture: str) -> None:
    """Declare a free-form idempotency posture (idempotency-supported /
    idempotency-in-flight-bound scenario outlines) -- distinct from the
    fixed-label {boundary_point} form above, which the ttl-bounds outline uses."""
    parsed = _parse_idempotency_posture(posture)
    _config(ctx)["idempotency"] = parsed
    ctx["env"].set_idempotency_posture(**parsed)


@given(parsers.parse("the seller's error-details builder is configured for {boundary_point}"))
def given_error_details_builder(ctx: dict, boundary_point: str) -> None:
    """Declare a (malformed) VERSION_UNSUPPORTED details configuration — empty
    supported_versions array or omitted. Records intent; version negotiation is
    now implemented (src/core/version_negotiation.py) and grades this boundary
    directly, so this Given only seeds the declared boundary_point value."""
    _config(ctx)["version_unsupported_details"] = boundary_point.strip()


@given(parsers.parse("the tenant identity and signing posture are configured for {boundary_point}"))
def given_identity_signing_posture(ctx: dict, boundary_point: str) -> None:
    """Declare an identity + signing-posture boundary for the brand_json_url
    required_when rule. Records intent; the declaration store deliberately carries
    no identity or request_signing field under the STRICT capability policy (#1291),
    so a signing posture missing brand_json_url cannot be declared and the
    required_when rejection has nothing to fire on."""
    _config(ctx)["identity_signing_boundary"] = boundary_point.strip()


# ── Thens: request_signing subset/disjoint relations ─────────────────────


def _assert_request_signing_relations(ctx: dict) -> None:
    """The emitted request_signing posture MUST satisfy every x-adcp-validation relation:
    required_for ⊆ supported_for; warn_for disjoint from required_for AND ⊆ supported_for;
    protocol_methods_required_for ⊆ protocol_methods_supported_for."""
    posture = wire_dict(ctx, "request_signing")

    def _members(key: str) -> set:
        return set(posture.get(key) or [])

    supported = _members("supported_for")
    required = _members("required_for")
    warn = _members("warn_for")
    pm_supported = _members("protocol_methods_supported_for")
    pm_required = _members("protocol_methods_required_for")
    assert required <= supported, f"required_for {sorted(required)} ⊄ supported_for {sorted(supported)}"
    assert warn.isdisjoint(required), (
        f"warn_for {sorted(warn)} shares operations with required_for {sorted(required)} (must be disjoint)"
    )
    assert warn <= supported, f"warn_for {sorted(warn)} ⊄ supported_for {sorted(supported)}"
    assert pm_required <= pm_supported, (
        f"protocol_methods_required_for {sorted(pm_required)} ⊄ protocol_methods_supported_for {sorted(pm_supported)}"
    )


@then(parsers.parse("request_signing should hold the subset and disjoint relations for a {expected} posture"))
def then_request_signing_relations(ctx: dict, expected: str) -> None:
    """valid → schema-valid success whose request_signing satisfies every subset/disjoint
    relation; invalid → the builder rejects the relation-violating config with
    CONFIGURATION_ERROR (recovery terminal) rather than emitting the violating posture."""
    expected = expected.strip()
    if expected == "invalid":
        _assert_capabilities_config_error(ctx)
        return
    assert expected == "valid", f"unrecognized expected column: {expected!r}"
    _assert_capabilities_success(ctx)
    _assert_schema_valid(ctx)
    _assert_request_signing_relations(ctx)


# ── Thens: adcp.idempotency replay_ttl_seconds bounds ────────────────────


@then(parsers.parse("adcp.idempotency should echo a {expected} posture within the replay_ttl_seconds bounds"))
def then_idempotency_bounds(ctx: dict, expected: str) -> None:
    """valid → adcp.idempotency echoes the declared posture exactly and passes schema
    validation (replay_ttl_seconds ∈ [3600, 604800]; when in_flight_max_seconds is declared it
    is present, equal, and ≤ replay_ttl_seconds); invalid → the builder rejects the
    out-of-bounds / cross-field-violating posture with CONFIGURATION_ERROR (recovery terminal)
    and never emits it."""
    expected = expected.strip()
    if expected == "invalid":
        _assert_capabilities_config_error(ctx)
        return
    assert expected == "valid", f"unrecognized expected column: {expected!r}"
    _assert_capabilities_success(ctx)
    _assert_schema_valid(ctx)
    declared = _config(ctx).get("idempotency") or {}
    idempotency = wire_dict(ctx, "adcp.idempotency")
    ttl = _wire_int(idempotency.get("replay_ttl_seconds"), "replay_ttl_seconds")
    assert 3600 <= ttl <= 604800, f"replay_ttl_seconds {ttl!r} outside the schema bounds 3600..604800"
    assert ttl == declared.get("replay_ttl_seconds"), (
        f"replay_ttl_seconds {ttl!r} does not echo the declared {declared.get('replay_ttl_seconds')!r}"
    )
    if "in_flight_max_seconds" in declared:
        in_flight = _wire_int(idempotency.get("in_flight_max_seconds"), "in_flight_max_seconds")
        assert in_flight == declared["in_flight_max_seconds"], (
            f"in_flight_max_seconds {in_flight!r} does not echo the declared {declared['in_flight_max_seconds']!r}"
        )
        assert in_flight <= ttl, (
            f"in_flight_max_seconds {in_flight!r} exceeds replay_ttl_seconds {ttl!r} (cross-field rule)"
        )


# ── Thens: VERSION_UNSUPPORTED details supported_versions bound ───────────


@then(
    'the emitted VERSION_UNSUPPORTED details must carry supported_versions equal to ["3.0", "3.1"], '
    "never empty or omitted"
)
def then_version_details_supported_versions(ctx: dict) -> None:
    """When the (Recommended) version-unsupported details block is emitted it MUST carry a
    non-empty supported_versions (version-unsupported.json#/required = [supported_versions],
    minItems 1) — here exactly the release-precision versions the seller speaks, ["3.0", "3.1"].
    An empty array or omitted field is a conformance violation."""
    from tests.helpers.envelope_assertions import assert_envelope_shape

    envelope = ctx.get("wire_error_envelope") or ctx.get("synthesized_error_envelope")
    assert envelope is not None, (
        "expected a VERSION_UNSUPPORTED error envelope, got a success response "
        "(the capabilities builder runs no version negotiation)"
    )
    assert_envelope_shape(envelope, "VERSION_UNSUPPORTED", recovery="correctable")
    versions = _error_details(ctx).get("supported_versions")
    assert isinstance(versions, list) and versions, (
        f"details.supported_versions is empty or omitted (required, minItems 1): {versions!r}"
    )
    assert versions == ["3.0", "3.1"], f"details.supported_versions {versions!r} != ['3.0', '3.1']"


# ── Thens: identity.brand_json_url required_when rule ─────────────────────


@then(parsers.parse("identity.brand_json_url should be graded {expected} against its required_when rule"))
def then_brand_json_url_bounds(ctx: dict, expected: str) -> None:
    """valid → a schema-valid success and, when identity.brand_json_url is emitted, it matches
    format uri / pattern "^https://"; invalid → the builder rejects the signing-posture-without-
    brand_json_url config with CONFIGURATION_ERROR (recovery terminal) naming brand_json_url."""
    expected = expected.strip()
    if expected == "invalid":
        _assert_capabilities_config_error(ctx, message_substr="brand_json_url")
        return
    assert expected == "valid", f"unrecognized expected column: {expected!r}"
    _assert_capabilities_success(ctx)
    _assert_schema_valid(ctx)
    url = wire_lookup(ctx, "identity.brand_json_url")
    if url is not WIRE_MISSING:
        assert isinstance(url, str) and url.startswith("https://"), (
            f'identity.brand_json_url must match pattern "^https://": {url!r}'
        )


# ── Thens: webhook_signing must_equal_when + algorithm-enum bounds ────────


@given(parsers.parse("the tenant declares webhook_signing posture described as {boundary_point}"))
def given_webhook_signing_boundary(ctx: dict, boundary_point: str) -> None:
    """Declare a webhook_signing boundary — a mutating-webhook trigger paired with a
    supported value, or an algorithms set. Records intent; the declaration store
    deliberately carries no webhook_signing field under the STRICT capability policy
    (#1291), so the outline strict-xfails on all transports."""
    _config(ctx)["webhook_signing_boundary"] = boundary_point.strip()


def _assert_webhook_signing_must_equal_when(ctx: dict, webhook_signing: dict) -> None:
    """must_equal_when (webhook_signing.supported/x-adcp-validation): when ANY mutating-webhook
    trigger is present on the wire — media_buy.reporting_delivery_methods contains "webhook",
    media_buy.content_standards.supports_webhook_delivery is true, or
    wholesale_feed_webhooks.supported is true — webhook_signing.supported MUST be true."""
    reporting = wire_lookup(ctx, "media_buy.reporting_delivery_methods")
    triggers = (
        isinstance(reporting, list) and "webhook" in reporting,
        wire_lookup(ctx, "media_buy.content_standards.supports_webhook_delivery") is True,
        wire_lookup(ctx, "wholesale_feed_webhooks.supported") is True,
    )
    if any(triggers):
        assert webhook_signing.get("supported") is True, (
            "must_equal_when: a mutating-webhook trigger is present on the wire but "
            f"webhook_signing.supported != true: {webhook_signing.get('supported')!r}"
        )


@then(parsers.parse("webhook_signing should be graded {expected} against its must_equal_when and algorithm-enum rules"))
def then_webhook_signing_bounds(ctx: dict, expected: str) -> None:
    """valid → a schema-valid success whose webhook_signing echoes the declared posture:
    supported is a boolean and true whenever a mutating-webhook trigger fires (must_equal_when),
    and algorithms — when present — is a non-empty, unique array drawn from the closed enum
    {ed25519, ecdsa-p256-sha256}. invalid → the builder rejects the posture (must_equal_when
    fired with supported != true, or an algorithm outside the closed enum) with
    CONFIGURATION_ERROR (recovery terminal) rather than emitting a non-conformant block."""
    expected = expected.strip()
    if expected == "invalid":
        _assert_capabilities_config_error(ctx)
        return
    assert expected == "valid", f"unrecognized expected column: {expected!r}"
    _assert_capabilities_success(ctx)
    _assert_schema_valid(ctx)
    webhook_signing = wire_dict(ctx, "webhook_signing")
    supported = webhook_signing.get("supported")
    assert supported is True or supported is False, f"webhook_signing.supported (required) not a boolean: {supported!r}"
    algorithms = webhook_signing.get("algorithms")
    if algorithms is not None:
        assert isinstance(algorithms, list) and algorithms, (
            f"webhook_signing.algorithms present but not a non-empty array (minItems 1): {algorithms!r}"
        )
        assert len(algorithms) == len(set(algorithms)), (
            f"webhook_signing.algorithms violates uniqueItems: {algorithms!r}"
        )
        invalid = set(algorithms) - WEBHOOK_SIGNING_ALGORITHM_ENUM
        assert not invalid, (
            f"webhook_signing.algorithms carries values outside the closed enum "
            f"{sorted(WEBHOOK_SIGNING_ALGORITHM_ENUM)}: {sorted(invalid)}"
        )
    _assert_webhook_signing_must_equal_when(ctx, webhook_signing)


# ══════════════════════════════════════════════════════════════════════════
# Batch 11 (salesagent-3xmz): trusted_match TMP surfaces
#
# The declaration is a REAL tenant-config write through the env
# (CapabilitiesEnv.declare_capabilities -> configure_tenant_field), not a ctx
# record-intent dict: the same Given runs identically on a2a/mcp/rest/e2e_rest
# because production resolves the tenant itself on every transport.
# ══════════════════════════════════════════════════════════════════════════


@given(parsers.parse("the tenant declares trusted_match surfaces {surfaces}"))
def given_trusted_match_surfaces(ctx: dict, surfaces: str) -> None:
    """Declare the seller's deployed TMP surfaces on the tenant's capability
    declaration store. Presence of the trusted_match object is itself the signal
    that TMP infrastructure is deployed, so the declared surface list is the
    seller-configured fact the response echoes."""
    env = ctx["env"]
    env.declare_capabilities(trusted_match={"surfaces": _parse_bracket_list(surfaces)})


@then(parsers.parse("media_buy.execution.trusted_match.surfaces should equal {expected}"))
def then_trusted_match_surfaces_equal(ctx: dict, expected: str) -> None:
    """Exact echo of the declared TMP surfaces, in declaration order, on the wire."""
    want = _parse_bracket_list(expected)
    actual = wire_field(ctx, "media_buy.execution.trusted_match.surfaces")
    assert actual == want, f"media_buy.execution.trusted_match.surfaces expected {want!r}, got {actual!r}"


@then(parsers.parse("each surface should be one of {allowed}"))
def then_trusted_match_surfaces_in_enum(ctx: dict, allowed: str) -> None:
    """surfaces items are drawn from the closed 3.1.1 TMP surface enum. The
    scenario's own enumeration is pinned against the schema constant first, so a
    drifted scenario fails loudly instead of grading a weaker set."""
    scenario_enum = set(_quoted_list(allowed))
    assert scenario_enum == TRUSTED_MATCH_SURFACE_ENUM, (
        f"scenario surface enum drifted from get-adcp-capabilities-response.json: "
        f"{sorted(scenario_enum ^ TRUSTED_MATCH_SURFACE_ENUM)}"
    )
    surfaces = wire_field(ctx, "media_buy.execution.trusted_match.surfaces")
    invalid = set(surfaces) - TRUSTED_MATCH_SURFACE_ENUM
    assert not invalid, f"trusted_match.surfaces carries non-enum values: {sorted(invalid)}"
