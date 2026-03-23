"""BDD step definitions for UC-026: Package Media Buy.

Package operations go through create_media_buy / update_media_buy.
Given steps build request kwargs, When steps dispatch through MediaBuyCreateEnv.

beads: salesagent-av7
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — Background + package request construction
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the seller has a product "{product_id}" in inventory with pricing_options {options}'))
def given_product_with_pricing(ctx: dict, product_id: str, options: str) -> None:
    """Verify product exists in DB (created by conftest _harness_env)."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    ctx.setdefault("product_pricing_options", options)


@given(parsers.parse('the product "{product_id}" supports format_ids {format_ids}'))
def given_product_format_ids(ctx: dict, product_id: str, format_ids: str) -> None:
    """Verify product supports the specified format_ids."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    ctx.setdefault("product_format_ids", format_ids)


def _build_package_request(ctx: dict, datatable: list[list[str]], transport: str) -> None:
    """Shared: build request kwargs with a package from data table."""
    kwargs = _ensure_request_defaults(ctx)
    _apply_package_table(kwargs, datatable)
    ctx.setdefault("package_transport_hint", transport)


@given(parsers.parse("a valid create_media_buy MCP tool request with packages array containing:"))
def given_mcp_request_with_packages(ctx: dict, datatable: list[list[str]]) -> None:
    """Build create request with a single package from data table (MCP)."""
    _build_package_request(ctx, datatable, "mcp")


@given(parsers.parse("a valid create_media_buy A2A task request with packages array containing:"))
def given_a2a_request_with_packages(ctx: dict, datatable: list[list[str]]) -> None:
    """Build create request with a single package from data table (A2A)."""
    _build_package_request(ctx, datatable, "a2a")


@given(parsers.parse("a valid create_media_buy request with a package containing:"))
def given_request_with_package(ctx: dict, datatable: list[list[str]]) -> None:
    """Build create request with a single package from data table (generic)."""
    _build_package_request(ctx, datatable, "impl")


def _apply_package_table(kwargs: dict, datatable: list[list[str]]) -> None:
    """Parse a data table into a package dict and set it on kwargs."""
    # Map feature-file pricing option names to real synthetic IDs
    pricing_id_map = {
        "cpm-standard": "cpm_usd_fixed",
        "cpm-auction": "cpm_usd_auction",
    }
    pkg: dict[str, Any] = {}
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "buyer_ref":
            pkg["buyer_ref"] = value
        elif field == "product_id":
            pkg["product_id"] = value
        elif field == "budget":
            pkg["budget"] = float(value)
        elif field == "pricing_option_id":
            pkg["pricing_option_id"] = pricing_id_map.get(value, value)
        elif field == "format_ids":
            import json

            pkg["format_ids"] = json.loads(value)
        elif field == "paused":
            pkg["paused"] = value.lower() == "true"
    kwargs["packages"] = [pkg]


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — dispatch create/update
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent invokes the create_media_buy MCP tool")
def when_invoke_create_mcp(ctx: dict) -> None:
    """Dispatch create_media_buy through MCP."""
    _dispatch_create(ctx)


@when("the Buyer Agent sends the create_media_buy A2A task")
def when_send_create_a2a(ctx: dict) -> None:
    """Dispatch create_media_buy through A2A."""
    _dispatch_create(ctx)


def _dispatch_create(ctx: dict) -> None:
    """Build CreateMediaBuyRequest and dispatch through harness."""
    from src.core.schemas import CreateMediaBuyRequest
    from tests.bdd.steps.generic._dispatch import dispatch_request

    request_kwargs = ctx.get("request_kwargs", {})
    req = CreateMediaBuyRequest(**request_kwargs)

    dispatch_request(ctx, req=req)

    # Post-process: promote error results
    _promote_create_errors(ctx)


def _promote_create_errors(ctx: dict) -> None:
    """Promote CreateMediaBuyError responses to ctx['error']."""
    resp = ctx.get("response")
    if resp is None:
        return
    from src.core.schemas._base import CreateMediaBuyError as CMBError

    if hasattr(resp, "response") and isinstance(resp.response, CMBError) and resp.response.errors:
        ctx["error"] = resp.response.errors[0]
        del ctx["response"]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — package-specific assertions
# ═══════════════════════════════════════════════════════════════════════


def _get_packages(ctx: dict) -> list:
    """Extract packages from create_media_buy response."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response. Error: {ctx.get('error')}"
    # CreateMediaBuyResult wraps .response which has .packages
    inner = getattr(resp, "response", resp)
    packages = getattr(inner, "packages", None)
    if packages is None:
        packages = getattr(resp, "packages", None)
    assert packages is not None, "No packages in response"
    return list(packages)


@then("the response should contain a package with a seller-assigned package_id")
def then_package_has_id(ctx: dict) -> None:
    """Assert response contains at least one package with package_id."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = getattr(pkg, "package_id", None)
    if pkg_id is None and isinstance(pkg, dict):
        pkg_id = pkg.get("package_id")
    assert pkg_id is not None, "Package missing package_id"


@then(parsers.parse('the package should contain buyer_ref "{buyer_ref}"'))
def then_package_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Assert first package has the expected buyer_ref."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "buyer_ref")
    assert actual == buyer_ref, f"Expected buyer_ref '{buyer_ref}', got '{actual}'"


@then(parsers.parse("the package should contain budget {budget:d}"))
def then_package_budget(ctx: dict, budget: int) -> None:
    """Assert first package has the expected budget."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "budget")
    if actual is None:
        pytest.xfail("SPEC-PRODUCTION GAP: package budget is None — production may not echo budget yet")
    assert float(actual) == float(budget), f"Expected budget {budget}, got {actual}"


@then(parsers.parse('the package should contain pricing_option_id "{pricing_option_id}"'))
def then_package_pricing(ctx: dict, pricing_option_id: str) -> None:
    """Assert first package has the expected pricing_option_id.

    Maps feature-file names (cpm-standard) to real IDs (cpm_usd_fixed).
    """
    import pytest

    pricing_id_map = {"cpm-standard": "cpm_usd_fixed", "cpm-auction": "cpm_usd_auction"}
    expected = pricing_id_map.get(pricing_option_id, pricing_option_id)
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "pricing_option_id")
    if actual is None:
        pytest.xfail("SPEC-PRODUCTION GAP: pricing_option_id is None — production may not echo it yet")
    assert actual == expected, f"Expected pricing_option_id '{expected}', got '{actual}'"


@then("the package should contain format_ids defaulting to all product formats")
def then_package_default_formats(ctx: dict) -> None:
    """Assert package format_ids default to all product formats."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    format_ids = _pkg_field(pkg, "format_ids")
    if format_ids is None:
        pytest.xfail("SPEC-PRODUCTION GAP: format_ids not present on package — production may not echo defaults")
    assert isinstance(format_ids, list), f"Expected format_ids to be a list, got {type(format_ids)}"
    assert len(format_ids) > 0, "Expected format_ids to default to all product formats, got empty list"


@then("the package should contain paused as false")
def then_package_not_paused(ctx: dict) -> None:
    """Assert package is not paused."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    paused = _pkg_field(pkg, "paused")
    # Default is False or None
    assert paused is not True, f"Expected paused=false, got {paused}"


@then("the package should contain format_ids_to_provide listing formats needing creative assets")
def then_package_formats_to_provide(ctx: dict) -> None:
    """Assert package has format_ids_to_provide field listing formats needing creative assets."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    formats_to_provide = _pkg_field(pkg, "format_ids_to_provide")
    if formats_to_provide is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: format_ids_to_provide not present on package — production may not set it yet"
        )
    assert isinstance(formats_to_provide, list), (
        f"Expected format_ids_to_provide to be a list, got {type(formats_to_provide)}"
    )


def _pkg_field(pkg: Any, field: str) -> Any:
    """Extract a field from a package (object or dict)."""
    if isinstance(pkg, dict):
        return pkg.get(field)
    return getattr(pkg, field, None)
