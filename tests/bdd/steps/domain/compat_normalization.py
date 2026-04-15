"""Step definitions for COMPAT-001: deprecated field normalization.

Tests that normalize_request_params translates deprecated fields correctly.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from src.core.request_compat import normalize_request_params

# ── Given ──────────────────────────────────────────────────────────────


@given("a tenant with products configured")
def given_tenant_with_products(ctx: dict) -> None:
    """Create a tenant with at least one product and pricing option.

    Idempotent across transport parametrizations — delegates to the
    harness env which scopes tenant_id per run (uuid suffix on E2E).
    """
    env = ctx["env"]
    tenant, _principal = env.setup_default_data()
    product, _pricing = env.setup_product_chain(tenant)
    ctx["tenant"] = tenant
    ctx["product"] = product


# ── When ──────────────────────────────────────────────────────────────


@when(
    parsers.parse('get_products is called with brand_manifest "{url}" and brief "{brief}"'),
    target_fixture="compat_result",
)
def when_get_products_with_brand_manifest(ctx: dict, url: str, brief: str) -> dict:
    """Normalize a get_products call with brand_manifest instead of brand."""
    result = normalize_request_params("get_products", {"brand_manifest": url, "brief": brief})
    return {"normalization_result": result}


@when(
    parsers.parse('the normalizer translates campaign_ref "{value}" for {tool_name}'),
    target_fixture="compat_result",
)
def when_normalizer_translates_campaign_ref(value: str, tool_name: str) -> dict:
    """Call normalize_request_params with campaign_ref."""
    result = normalize_request_params(tool_name, {"campaign_ref": value, "buyer_ref": "ref1"})
    return {"normalization_result": result}


@when(
    parsers.parse('the normalizer translates account_id "{value}" for {tool_name}'),
    target_fixture="compat_result",
)
def when_normalizer_translates_account_id(value: str, tool_name: str) -> dict:
    """Call normalize_request_params with account_id."""
    result = normalize_request_params(tool_name, {"account_id": value, "brief": "ads"})
    return {"normalization_result": result}


@when(
    "get_products is called with both brand and brand_manifest",
    target_fixture="compat_result",
)
def when_get_products_with_both_brand_and_manifest(ctx: dict) -> dict:
    """Normalize with both brand (current) and brand_manifest (deprecated)."""
    result = normalize_request_params(
        "get_products",
        {
            "brand": {"domain": "current.com"},
            "brand_manifest": "https://old.com/.well-known/brand.json",
            "brief": "test",
        },
    )
    return {"normalization_result": result}


# ── Then ──────────────────────────────────────────────────────────────


@then("the request succeeds")
def then_request_succeeds(compat_result: dict) -> None:
    """Verify the normalization completed and detected a v2.5 caller.

    Both scenarios using this step send brand_manifest (a V25_SIGNALS field),
    so the normalizer must infer v2.5 and strip brand_manifest from params.
    The precedence scenario keeps the existing brand (no translation logged),
    so translations_applied may be empty there — we assert version + strip.
    """
    nr = compat_result["normalization_result"]
    assert isinstance(nr.params, dict), f"params must be dict, got {type(nr.params).__name__}"
    assert nr.inferred_version == "2.5", f"brand_manifest input must infer v2.5 caller, got {nr.inferred_version!r}"
    assert "brand_manifest" not in nr.params, (
        f"deprecated brand_manifest must be stripped, got keys: {sorted(nr.params.keys())}"
    )


@then(parsers.parse('the brand was resolved with domain "{domain}"'))
def then_brand_resolved(compat_result: dict, domain: str) -> None:
    """Verify brand_manifest was translated to brand with correct domain."""
    params = compat_result["normalization_result"].params
    assert "brand" in params, f"brand not in normalized params: {list(params.keys())}"
    assert params["brand"]["domain"] == domain
    assert "brand_manifest" not in params


@then(parsers.parse('the result contains buyer_campaign_ref "{value}"'))
def then_result_contains_buyer_campaign_ref(compat_result: dict, value: str) -> None:
    """Verify campaign_ref was renamed to buyer_campaign_ref."""
    params = compat_result["normalization_result"].params
    assert params["buyer_campaign_ref"] == value


@then("the result does not contain campaign_ref")
def then_no_campaign_ref(compat_result: dict) -> None:
    """Verify campaign_ref was removed AND the translation was recorded.

    Absence alone is insufficient — a silent delete would also satisfy it.
    The normalizer must log the rename so callers can trace v2.5→v3 mapping.
    """
    nr = compat_result["normalization_result"]
    assert "campaign_ref" not in nr.params, f"campaign_ref must be removed, got keys: {sorted(nr.params.keys())}"
    assert "campaign_ref → buyer_campaign_ref" in nr.translations_applied, (
        f"translation must be recorded, got {nr.translations_applied!r}"
    )
    assert nr.inferred_version == "2.5", f"campaign_ref input must infer v2.5 caller, got {nr.inferred_version!r}"


@then(parsers.parse('the result contains account with account_id "{value}"'))
def then_result_contains_account(compat_result: dict, value: str) -> None:
    """Verify account_id was wrapped into account object."""
    params = compat_result["normalization_result"].params
    assert params["account"] == {"account_id": value}


@then("the result does not contain account_id")
def then_no_account_id(compat_result: dict) -> None:
    """Verify account_id was removed."""
    params = compat_result["normalization_result"].params
    assert "account_id" not in params


@then(parsers.parse('the brand domain is "{current}" not "{deprecated}"'))
def then_brand_precedence(compat_result: dict, current: str, deprecated: str) -> None:
    """Verify current brand takes precedence over deprecated brand_manifest."""
    params = compat_result["normalization_result"].params
    assert params["brand"]["domain"] == current
    assert "brand_manifest" not in params
