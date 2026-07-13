"""Steps for UC-010 discover seller capabilities — salesagent-8wf2/fxot.

The @T-UC-010-main-* scenarios run a real get_adcp_capabilities through the
wire transports on CapabilitiesEnv. Only the adapter is mocked (channels +
targeting capabilities are adapter facts); publisher partners are real DB
rows, and the wrappers (MCP tool / A2A skill / REST GET) are production code.

Assertions read the REAL serialized wire body via ``wire_path``. The
"all 4 flags" features assert is reconciled with the pinned spec v3.1.1
(core/media-buy-features.json declares exactly 4 properties) — see the
feature-file comment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field, wire_path
from tests.bdd.steps.generic._dispatch import dispatch_request

# Pinned spec v3.1.1 core/media-buy-features.json — exactly these properties,
# ALL OPTIONAL ("Optional media-buy protocol features"; no required array):
# a seller omits a flag it does not declare. Production declares the first
# three today; committed_metrics_supported is asserted only-if-present.
_FEATURE_FLAGS = (
    "inline_creative_management",
    "property_list_filtering",
    "catalog_management",
)
_OPTIONAL_FEATURE_FLAGS = ("committed_metrics_supported",)


def _split_quoted(values: str) -> list[str]:
    """Parse a '"a", "b", "c"' step-text list into ['a', 'b', 'c']."""
    return [v.strip().strip('"') for v in values.split(",")]


# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('the tenant has an adapter with channels "{channels}"'))
def given_adapter_channels(ctx: dict, channels: str) -> None:
    env = ctx["env"]
    channel_list = [c.strip() for c in channels.split(",")]
    env.set_adapter_channels(channel_list)
    ctx["expected_channels"] = channel_list


@given(parsers.parse("the tenant has registered publisher partnerships with domains {domains}"))
def given_publisher_partnerships(ctx: dict, domains: str) -> None:
    from tests.factories import PublisherPartnerFactory

    domain_list = _split_quoted(domains)
    for domain in domain_list:
        PublisherPartnerFactory(tenant=ctx["tenant"], publisher_domain=domain)
    ctx["expected_publisher_domains"] = domain_list


@given("the adapter provides targeting capabilities including geo and device")
def given_targeting_capabilities(ctx: dict) -> None:
    """Configure the adapter's targeting surface (geo dimensions).

    TargetingCapabilities (src/adapters/base.py) models geo/metro/postal
    dimensions only — device is not part of the adapter capability contract;
    the graded Then asserts the geo dimensions on the wire.
    """
    from src.adapters.base import TargetingCapabilities

    env = ctx["env"]
    env.set_targeting_capabilities(TargetingCapabilities(geo_countries=True, geo_regions=True))


@given("the system has known state before the request")
def given_known_state(ctx: dict) -> None:
    """Snapshot mutable-domain row counts for the read-only invariant."""
    env = ctx["env"]
    ctx["state_snapshot"] = _state_snapshot(env)


@given("the tenant has full capabilities configured")
def given_full_capabilities(ctx: dict) -> None:
    from src.adapters.base import TargetingCapabilities

    env = ctx["env"]
    env.set_adapter_channels(["display", "video"])
    env.set_targeting_capabilities(TargetingCapabilities(geo_countries=True))


def _state_snapshot(env: Any) -> dict[str, int]:
    """Row counts of the mutable domain tables a capabilities read must not touch."""
    from sqlalchemy import func, select

    from src.core.database.models import Creative, MediaBuy, Product

    session = env.get_session()
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in (MediaBuy, Creative, Product)
    }


# ── When steps ──────────────────────────────────────────────────────


@when("the Buyer Agent calls get_adcp_capabilities MCP tool")
def when_get_capabilities_mcp(ctx: dict) -> None:
    """MCP-flavored storyboard text. Untagged scenarios are parametrized (the
    parametrized transport wins); @mcp-tagged ones pin MCP here."""
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.MCP)
    dispatch_request(ctx)


@when("the Buyer Agent sends a get_adcp_capabilities skill request")
def when_get_capabilities_skill(ctx: dict) -> None:
    """A2A-flavored storyboard text (tagged @a2a upstream)."""
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.A2A)
    dispatch_request(ctx)


# ── Then steps ──────────────────────────────────────────────────────


@then("the response should include adcp.major_versions containing 3")
def then_major_versions(ctx: dict) -> None:
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    assert 3 in wire_path(ctx, "adcp.major_versions")


@then('the response should include supported_protocols containing "media_buy"')
def then_supported_protocols(ctx: dict) -> None:
    assert "media_buy" in wire_path(ctx, "supported_protocols")


@then("the response should include account section with sandbox flag and billing models")
def then_account_section(ctx: dict) -> None:
    account = wire_path(ctx, "account")
    assert isinstance(account, dict), f"account section missing: {account!r}"
    assert isinstance(account.get("sandbox"), bool), f"account.sandbox not a bool: {account.get('sandbox')!r}"
    assert account.get("supported_billing"), f"account.supported_billing empty: {account.get('supported_billing')!r}"


@then("the response should include media_buy.features section with the declared feature flags")
def then_features_section(ctx: dict) -> None:
    features = wire_path(ctx, "media_buy.features")
    assert isinstance(features, dict), f"media_buy.features missing: {features!r}"
    for flag in _FEATURE_FLAGS:
        assert isinstance(features.get(flag), bool), f"features.{flag} not a bool: {features.get(flag)!r}"
    for flag in _OPTIONAL_FEATURE_FLAGS:
        # Spec-optional: absence means "not declared"; a present value must be bool.
        assert features.get(flag) is None or isinstance(features[flag], bool), (
            f"features.{flag} present but not a bool: {features[flag]!r}"
        )
    unknown = set(features) - set(_FEATURE_FLAGS) - set(_OPTIONAL_FEATURE_FLAGS)
    assert all(isinstance(features[f], bool) for f in unknown), (
        f"additionalProperties must be booleans per the pinned schema: {unknown}"
    )


@then("the response should include media_buy.supported_pricing_models")
def then_pricing_models(ctx: dict) -> None:
    assert wire_path(ctx, "media_buy.supported_pricing_models"), "supported_pricing_models empty"


@then("the response should include media_buy.reporting_delivery_methods section")
def then_reporting_delivery_methods(ctx: dict) -> None:
    assert wire_path(ctx, "media_buy.reporting_delivery_methods"), "reporting_delivery_methods empty"


@then("the response should include media_buy.execution section with targeting")
def then_execution_targeting(ctx: dict) -> None:
    targeting = wire_path(ctx, "media_buy.execution.targeting")
    assert isinstance(targeting, dict), f"execution.targeting missing: {targeting!r}"
    assert targeting.get("geo_countries") is True, f"geo_countries not reported: {targeting.get('geo_countries')!r}"


@then(parsers.parse("the response should include media_buy.portfolio with publisher_domains {domains}"))
def then_portfolio_domains(ctx: dict, domains: str) -> None:
    portfolio = wire_path(ctx, "media_buy.portfolio")
    assert isinstance(portfolio, dict), f"portfolio missing: {portfolio!r}"
    actual = set(portfolio.get("publisher_domains") or [])
    expected = set(_split_quoted(domains))
    assert expected <= actual, f"publisher_domains {actual} missing {expected - actual}"


@then(parsers.parse("the response should include media_buy.portfolio with primary_channels {channels}"))
def then_portfolio_channels(ctx: dict, channels: str) -> None:
    portfolio = wire_path(ctx, "media_buy.portfolio")
    actual = set(portfolio.get("primary_channels") or [])
    expected = set(_split_quoted(channels))
    assert expected <= actual, f"primary_channels {actual} missing {expected - actual}"


@then("the response should include last_updated as a valid timestamp")
@then("the response should include last_updated as a valid ISO 8601 timestamp")
def then_last_updated(ctx: dict) -> None:
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    last_updated = wire_field(ctx, "last_updated")
    assert last_updated, "last_updated missing from the wire response"
    parsed = datetime.fromisoformat(str(last_updated).replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"last_updated has no timezone: {last_updated!r}"


@then("the system state should be unchanged after the response")
def then_state_unchanged(ctx: dict) -> None:
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    env = ctx["env"]
    after = _state_snapshot(env)
    assert after == ctx["state_snapshot"], (
        f"read-only capabilities call mutated state: before={ctx['state_snapshot']}, after={after}"
    )
