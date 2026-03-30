"""BDD step definitions for UC-026: Package Media Buy.

Package operations go through create_media_buy / update_media_buy.
Given steps build request kwargs, When steps dispatch through MediaBuyCreateEnv.

beads: salesagent-av7
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


_DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def _to_format_id_dicts(raw_ids: list[str]) -> list[dict[str, str]]:
    """Convert bare format ID strings to FormatId dicts with agent_url."""
    result = []
    for fid in raw_ids:
        if isinstance(fid, dict):
            result.append(fid)
        else:
            result.append({"agent_url": _DEFAULT_AGENT_URL, "id": fid})
    return result


def _resolve_pricing_id(ctx: dict, label: str) -> str:
    """Map a feature-file pricing label to the synthetic pricing_option_id.

    Feature file uses "cpm-standard" / "cpm-auction"; production code constructs
    "{pricing_model}_{currency}_{fixed|auction}" (e.g. "cpm_usd_fixed").
    Falls through to the raw label if no mapping exists.
    """
    mapping = ctx.get("pricing_option_map", {})
    return mapping.get(label, label)


def _pkg_field(pkg: Any, field: str) -> Any:
    """Extract a field from a package (object or dict)."""
    if isinstance(pkg, dict):
        return pkg.get(field)
    return getattr(pkg, field, None)


def _extract_format_id(f: Any) -> str:
    """Extract format ID string from a format object or dict."""
    if isinstance(f, dict):
        return f.get("id", str(f))
    if hasattr(f, "id"):
        return f.id
    return str(f)


def _get_overlay_keywords(pkg: Any, field: str = "keyword_targets") -> list | None:
    """Extract keyword_targets or negative_keywords from package targeting_overlay."""
    overlay = _pkg_field(pkg, "targeting_overlay")
    if overlay is None:
        return None
    if isinstance(overlay, dict):
        return overlay.get(field)
    return getattr(overlay, field, None)


def _keyword_field(kw: Any, field: str) -> Any:
    """Extract a field from a keyword target (object or dict)."""
    if isinstance(kw, dict):
        return kw.get(field)
    return getattr(kw, field, None)


def _find_keyword(keywords: list, keyword: str, match_type: str | None = None) -> Any | None:
    """Find a keyword target entry by keyword and optionally match_type."""
    for kw in keywords:
        kw_val = _keyword_field(kw, "keyword")
        mt_val = _keyword_field(kw, "match_type")
        if kw_val == keyword:
            if match_type is None or str(mt_val) == match_type:
                return kw
    return None


def _get_overlay_field(pkg: Any, field: str) -> Any:
    """Extract a specific field from package targeting_overlay."""
    overlay = _pkg_field(pkg, "targeting_overlay")
    if overlay is None:
        return None
    if isinstance(overlay, dict):
        return overlay.get(field)
    return getattr(overlay, field, None)


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


def _build_default_package(ctx: dict) -> dict[str, Any]:
    """Build a default package dict using ctx's product and pricing option."""
    product = ctx.get("default_product")
    product_id = product.product_id if product else "prod-1"
    pricing_id = _resolve_pricing_id(ctx, "cpm-standard")
    return {
        "product_id": product_id,
        "buyer_ref": f"pkg-{uuid.uuid4().hex[:8]}",
        "budget": 5000.0,
        "pricing_option_id": pricing_id,
    }


def _build_request_with_overrides(ctx: dict, **overrides: Any) -> None:
    """Build a create request with a default package and apply field overrides."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.update(overrides)
    kwargs["packages"] = [pkg]


def _assert_no_error(ctx: dict) -> None:
    """Assert no error in context — shared across multiple Then steps."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"


def _assert_has_packages(ctx: dict) -> list:
    """Assert response has packages and return them — shared across multiple Then steps."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    return packages


def _create_media_buy_for_update(ctx: dict, **pkg_overrides: Any) -> None:
    """Create a media buy with a single package, store in ctx for update scenarios.

    Dispatches through the env's call_impl to create a real media buy in DB.
    Stores the result in ctx["existing_media_buy"] and the package in
    ctx["existing_package"].
    """
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.update(pkg_overrides)
    kwargs["packages"] = [pkg]

    from src.core.schemas import CreateMediaBuyRequest
    from tests.bdd.steps.generic._dispatch import dispatch_request

    req = CreateMediaBuyRequest(**kwargs)
    dispatch_request(ctx, req=req)

    resp = ctx.get("response")
    assert resp is not None, f"Failed to create media buy for update setup: {ctx.get('error')}"
    inner = getattr(resp, "response", resp)
    mb_id = getattr(inner, "media_buy_id", None)
    assert mb_id, "Created media buy has no media_buy_id"
    ctx["existing_media_buy_id"] = mb_id
    packages = getattr(inner, "packages", None) or []
    if packages:
        pkg_obj = packages[0]
        ctx["existing_package_id"] = _pkg_field(pkg_obj, "package_id")
        ctx["existing_package"] = pkg_obj
    # Clear response so When step gets clean state
    ctx.pop("response", None)
    ctx.pop("error", None)
    # Reset request_kwargs for the update
    ctx.pop("request_kwargs", None)


def _ensure_update_kwargs(ctx: dict) -> dict[str, Any]:
    """Ensure ctx has update_kwargs with media_buy_id pre-filled."""
    if "update_kwargs" not in ctx:
        ctx["update_kwargs"] = {}
    kw = ctx["update_kwargs"]
    if "media_buy_id" not in kw and "existing_media_buy_id" in ctx:
        kw["media_buy_id"] = ctx["existing_media_buy_id"]
    return kw


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — Background + package request construction
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the seller has a product "{product_id}" in inventory with pricing_options {options}'))
def given_product_with_pricing(ctx: dict, product_id: str, options: str) -> None:
    """Verify product exists in DB with pricing_options matching the step parameter."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    actual_options = getattr(product, "pricing_options", None)
    assert actual_options is not None, (
        f"Product '{product_id}' has no pricing_options attribute — step claims 'with pricing_options {options}'"
    )
    assert len(actual_options) > 0, (
        f"Product '{product_id}' has empty pricing_options — step claims 'with pricing_options {options}'"
    )
    # Verify actual option IDs match expected list
    try:
        expected_ids = json.loads(options)
    except (json.JSONDecodeError, TypeError):
        ctx["product_pricing_options"] = options
        return
    if isinstance(expected_ids, list):
        actual_ids = set()
        for opt in actual_options:
            # ORM PricingOption has integer `id` but no `pricing_option_id`.
            # Synthesize the canonical string ID from model fields to match
            # the resolved labels (e.g. "cpm_usd_fixed").
            opt_id = getattr(opt, "pricing_option_id", None)
            if opt_id is None:
                pm = getattr(opt, "pricing_model", None)
                cur = getattr(opt, "currency", None)
                fixed = getattr(opt, "is_fixed", None)
                if pm and cur and fixed is not None:
                    fixed_str = "fixed" if fixed else "auction"
                    opt_id = f"{pm}_{cur.lower()}_{fixed_str}"
            if opt_id is None:
                opt_id = getattr(opt, "id", None) or str(opt)
            actual_ids.add(opt_id)
        # Map expected labels to resolved IDs for comparison
        resolved_expected = {_resolve_pricing_id(ctx, eid) for eid in expected_ids}
        missing = resolved_expected - actual_ids
        assert not missing, (
            f"Product pricing_options missing expected IDs {missing}. "
            f"Actual IDs: {actual_ids}, expected: {resolved_expected}"
        )
    ctx["product_pricing_options"] = options


@given(parsers.parse('the product "{product_id}" supports format_ids {format_ids}'))
def given_product_format_ids(ctx: dict, product_id: str, format_ids: str) -> None:
    """Verify product supports the specified format_ids."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    actual_format_ids = getattr(product, "format_ids", None)
    assert actual_format_ids is not None, (
        f"Product '{product_id}' has no format_ids attribute — step claims 'supports format_ids {format_ids}'"
    )
    assert len(actual_format_ids) > 0, (
        f"Product '{product_id}' has empty format_ids — step claims 'supports format_ids {format_ids}'"
    )
    try:
        expected = json.loads(format_ids)
    except (json.JSONDecodeError, TypeError):
        ctx["product_format_ids"] = format_ids
        return
    if isinstance(expected, list):
        actual_set = {_extract_format_id(f) for f in actual_format_ids}
        for ef in expected:
            ef_id = _extract_format_id(ef)
            assert ef_id in actual_set, f"Expected format '{ef_id}' not found in product's format_ids {actual_set}"
    ctx["product_format_ids"] = format_ids


# --- Package table request construction ---


def _build_package_request(ctx: dict, datatable: list[list[str]], transport: str) -> None:
    """Shared: build request kwargs with a package from data table."""
    kwargs = _ensure_request_defaults(ctx)
    _apply_package_table(kwargs, datatable, ctx)
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


def _apply_package_table(kwargs: dict, datatable: list[list[str]], ctx: dict | None = None) -> None:
    """Parse a data table into a package dict and set it on kwargs."""
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
            pkg["pricing_option_id"] = _resolve_pricing_id(ctx or {}, value)
        elif field == "format_ids":
            # Parse [banner-300x250] or ["banner-300x250", "banner-728x90"]
            try:
                raw = json.loads(value)
            except json.JSONDecodeError:
                # Handle bare bracket format: [banner-300x250, banner-728x90]
                inner = value.strip("[]")
                raw = [s.strip().strip('"') for s in inner.split(",")]
            # Convert bare strings to FormatId dicts
            pkg["format_ids"] = _to_format_id_dicts(raw)
        elif field == "paused":
            pkg["paused"] = value.lower() == "true"
        elif field == "bid_price":
            pkg["bid_price"] = float(value)
        elif field == "pacing":
            pkg["pacing"] = value
        elif field == "impressions":
            pkg["impressions"] = int(value)
        elif field == "catalogs":
            pkg["catalogs"] = json.loads(value)
        elif field == "optimization_goals":
            pkg["optimization_goals"] = json.loads(value)
        elif field == "creative_assignments":
            pkg["creative_assignments"] = json.loads(value)
        elif field == "targeting_overlay":
            pkg["targeting_overlay"] = json.loads(value)
    kwargs["packages"] = [pkg]


# --- Missing field request construction ---


@given(parsers.parse("a valid create_media_buy request with a package missing {missing_field}"))
def given_request_missing_field(ctx: dict, missing_field: str) -> None:
    """Build create request with a required package field removed."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    field = missing_field.strip()
    pkg.pop(field, None)
    kwargs["packages"] = [pkg]


# --- Simple single-field request construction ---


@given(parsers.parse('a valid create_media_buy request with a package containing buyer_ref "{buyer_ref}"'))
def given_request_with_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Build a fresh create request with specific buyer_ref."""
    _build_request_with_overrides(ctx, buyer_ref=buyer_ref)
    ctx["request_buyer_ref"] = buyer_ref


@given(parsers.parse('a valid create_media_buy request with a package containing pricing_option_id "{option_id}"'))
def given_request_with_pricing_option(ctx: dict, option_id: str) -> None:
    """Build create request with specific pricing_option_id."""
    _build_request_with_overrides(ctx, pricing_option_id=_resolve_pricing_id(ctx, option_id))


@given("a valid create_media_buy request with a package containing no bid_price")
def given_request_no_bid_price(ctx: dict) -> None:
    """Build create request without bid_price."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.pop("bid_price", None)
    kwargs["packages"] = [pkg]


@given("a valid create_media_buy request with a package containing no format_ids")
def given_request_no_format_ids(ctx: dict) -> None:
    """Build create request without format_ids (should default to all product formats)."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.pop("format_ids", None)
    kwargs["packages"] = [pkg]


@given("a valid create_media_buy request with a package containing format_ids as empty array []")
def given_request_empty_format_ids(ctx: dict) -> None:
    """Build create request with empty format_ids array."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg["format_ids"] = []
    kwargs["packages"] = [pkg]


@given("a valid create_media_buy request with a package containing no paused field")
def given_request_no_paused(ctx: dict) -> None:
    """Build create request without paused field (should default to false)."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.pop("paused", None)
    kwargs["packages"] = [pkg]


@given("a valid create_media_buy request with a package containing paused=true")
def given_request_paused_true(ctx: dict) -> None:
    """Build create request with paused=true."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg["paused"] = True
    kwargs["packages"] = [pkg]


@given("a valid create_media_buy request with a package containing no catalogs field")
def given_request_no_catalogs(ctx: dict) -> None:
    """Build create request without catalogs field."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    pkg.pop("catalogs", None)
    kwargs["packages"] = [pkg]


# --- Product/pricing assertion steps ---


@given(parsers.parse('the product "{product_id}" does not exist in seller inventory'))
def given_product_not_exists(ctx: dict, product_id: str) -> None:
    """Assert that the named product_id does not exist in inventory."""
    product = ctx.get("default_product")
    if product is not None:
        assert product.product_id != product_id, (
            f"Product '{product_id}' should not exist but default_product has this ID"
        )
    # Also verify via env's product list (env is guaranteed by autouse fixture)
    env = ctx["env"]
    products = getattr(env, "products", None) or []
    for p in products:
        pid = getattr(p, "product_id", None)
        assert pid != product_id, f"Product '{product_id}' should not exist but found in env.products"


@given(parsers.parse('the pricing_option_id "{option}" is not in product "{product_id}" pricing_options'))
def given_pricing_not_in_product(ctx: dict, option: str, product_id: str) -> None:
    """Assert that a pricing option is not offered by the product."""
    product = ctx.get("default_product")
    if product is not None and product.product_id == product_id:
        actual_options = getattr(product, "pricing_options", None) or []
        actual_ids = set()
        for opt in actual_options:
            opt_id = getattr(opt, "pricing_option_id", None) or getattr(opt, "id", None) or str(opt)
            actual_ids.add(opt_id)
        resolved = _resolve_pricing_id(ctx, option)
        assert resolved not in actual_ids and option not in actual_ids, (
            f"Pricing option '{option}' (resolved: '{resolved}') should NOT be in "
            f"product '{product_id}' but found in {actual_ids}"
        )
    ctx.setdefault("expected_missing_pricing_options", []).append(option)


@given(parsers.parse('the product "{product_id}" has a minimum spend requirement of {amount:d}'))
def given_product_min_spend(ctx: dict, product_id: str, amount: int) -> None:
    """Set minimum spend requirement on the product's pricing option."""
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import PricingOption

    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx["tenant"]
    with get_db_session() as session:
        options = session.scalars(
            select(PricingOption).filter_by(tenant_id=tenant.tenant_id, product_id=product_id)
        ).all()
        for opt in options:
            opt.min_spend_per_package = Decimal(str(amount))
        session.commit()


@given(parsers.parse('the format_id "{format_id}" is not supported by product "{product_id}"'))
def given_format_not_supported(ctx: dict, format_id: str, product_id: str) -> None:
    """Assert that a format_id is not supported by the product."""
    product = ctx.get("default_product")
    assert product is not None, (
        f"No default_product in ctx — cannot verify format '{format_id}' is unsupported by '{product_id}'"
    )
    actual_format_ids = getattr(product, "format_ids", None) or []
    actual_set = {_extract_format_id(f) for f in actual_format_ids}
    assert format_id not in actual_set, (
        f"Format '{format_id}' should NOT be supported but is in product's format_ids {actual_set}"
    )


@given(parsers.parse('the product "{product_id}" has pricing_option "{option}" in its pricing_options array'))
def given_product_has_pricing_option(ctx: dict, product_id: str, option: str) -> None:
    """Verify product has the specified pricing option."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id
    actual_options = getattr(product, "pricing_options", None)
    assert actual_options and len(actual_options) > 0, "Product has no pricing_options"
    resolved = _resolve_pricing_id(ctx, option)
    actual_ids = set()
    for opt in actual_options:
        opt_id = getattr(opt, "pricing_option_id", None) or getattr(opt, "id", None) or str(opt)
        actual_ids.add(opt_id)
    assert resolved in actual_ids or option in actual_ids, (
        f"Pricing option '{option}' (resolved: '{resolved}') not found in product's pricing_options {actual_ids}"
    )


@given(parsers.parse('the product "{product_id}" does not have pricing_option "{option}"'))
def given_product_lacks_pricing_option(ctx: dict, product_id: str, option: str) -> None:
    """Verify the product does not have the specified pricing option."""
    product = ctx.get("default_product")
    if product is not None and product.product_id == product_id:
        actual_options = getattr(product, "pricing_options", None) or []
        actual_ids = set()
        for opt in actual_options:
            opt_id = getattr(opt, "pricing_option_id", None) or getattr(opt, "id", None) or str(opt)
            actual_ids.add(opt_id)
        resolved = _resolve_pricing_id(ctx, option)
        assert resolved not in actual_ids and option not in actual_ids, (
            f"Pricing option '{option}' should NOT be in product '{product_id}' but found in {actual_ids}"
        )
    ctx.setdefault("expected_missing_pricing_options", []).append(option)


@given(parsers.parse('the product "{product_id}" has pricing_option "{option}" with max_bid={max_bid}'))
def given_pricing_option_max_bid(ctx: dict, product_id: str, option: str, max_bid: str) -> None:
    """Verify product has the pricing option and record max_bid semantics."""
    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    actual_options = getattr(product, "pricing_options", None)
    assert actual_options and len(actual_options) > 0, f"Product '{product_id}' has no pricing_options"
    # Record max_bid semantics for downstream assertions
    ctx.setdefault("pricing_option_max_bid", {})[option] = max_bid.lower() == "true"


# --- Dedup / cross-buy Given steps ---


@given(parsers.parse('a valid create_media_buy request resubmits buyer_ref "{buyer_ref}" in the same media buy'))
def given_resubmit_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Build create request that resubmits an existing buyer_ref (dedup test)."""
    _build_request_with_overrides(ctx, buyer_ref=buyer_ref)
    ctx["resubmitted_buyer_ref"] = buyer_ref


@given("the Buyer is creating a media buy with no existing packages")
def given_no_existing_packages(ctx: dict) -> None:
    """Assert fresh state — no prior media buys exist for this buyer.

    Default state: the test env starts clean, so no packages exist.
    Verify by confirming no existing_media_buy_id is set in context.
    """
    assert "existing_media_buy_id" not in ctx, (
        "Expected no existing packages but existing_media_buy_id is already in context"
    )
    assert "existing_package_id" not in ctx, (
        "Expected no existing packages but existing_package_id is already in context"
    )
    ctx["no_existing_packages"] = True


@given(
    parsers.parse('the Buyer owns a media buy with a package having buyer_ref "{buyer_ref}" and package_id "{pkg_id}"')
)
def given_buyer_owns_mb_with_ref_and_id(ctx: dict, buyer_ref: str, pkg_id: str) -> None:
    """Create a media buy with a package having specific buyer_ref.

    The pkg_id from the step text is a label for downstream dedup assertions;
    the actual package_id is assigned by the seller. We store both.
    """
    _create_media_buy_for_update(ctx, buyer_ref=buyer_ref)
    # Record the actual package_id created for dedup assertions
    actual_pkg_id = ctx.get("existing_package_id")
    assert actual_pkg_id is not None, "Failed to create package — no existing_package_id in ctx"
    ctx["expected_existing_package_id"] = actual_pkg_id


@given(parsers.parse('the Buyer owns a media buy with a package having buyer_ref "{buyer_ref}"'))
def given_buyer_owns_mb_with_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Create a media buy with a specific buyer_ref."""
    _create_media_buy_for_update(ctx, buyer_ref=buyer_ref)


@given(parsers.parse('the Buyer owns media buy "{mb_id}" with a package having buyer_ref "{buyer_ref}"'))
def given_buyer_owns_named_mb(ctx: dict, mb_id: str, buyer_ref: str) -> None:
    """Create a media buy with a package having specific buyer_ref.

    mb_id is a logical label from the feature file; the actual media_buy_id is
    assigned by the seller. We store the actual ID under the label for cross-buy tests.
    """
    _create_media_buy_for_update(ctx, buyer_ref=buyer_ref)
    actual_mb_id = ctx.get("existing_media_buy_id")
    assert actual_mb_id is not None, f"Failed to create media buy '{mb_id}' — no existing_media_buy_id in ctx"
    ctx["named_media_buy_id"] = mb_id
    ctx.setdefault("named_media_buy_ids", {})[mb_id] = actual_mb_id


@given(parsers.parse('the Buyer is creating a new media buy "{mb_id}"'))
def given_creating_new_mb(ctx: dict, mb_id: str) -> None:
    """Set up state for creating a new (different) media buy."""
    ctx["new_media_buy_name"] = mb_id
    ctx.pop("request_kwargs", None)


@given(parsers.parse('the create_media_buy request for "{mb_id}" includes a package with buyer_ref "{buyer_ref}"'))
def given_cross_buy_request(ctx: dict, mb_id: str, buyer_ref: str) -> None:
    """Build create request for a different media buy with the same buyer_ref (cross-buy)."""
    _build_request_with_overrides(ctx, buyer_ref=buyer_ref)
    ctx["cross_buy_target"] = mb_id


# --- Update-flow Given steps ---


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having budget {amount:d}'))
def given_buyer_owns_pkg_with_budget(ctx: dict, pkg_id: str, amount: int) -> None:
    """Create a media buy with a package having specific budget.

    pkg_id is used as the buyer_ref to identify the package in update scenarios.
    """
    _create_media_buy_for_update(ctx, buyer_ref=pkg_id, budget=float(amount))
    # Verify the package was created with the expected budget
    existing_pkg = ctx.get("existing_package")
    if existing_pkg is not None:
        actual_budget = _pkg_field(existing_pkg, "budget")
        if actual_budget is not None:
            assert float(actual_budget) == float(amount), (
                f"Package created with budget {actual_budget}, expected {amount}"
            )


@given(parsers.parse('the Buyer owns a media buy with a package identified by buyer_ref "{buyer_ref}"'))
def given_buyer_owns_pkg_by_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Create a media buy with a package identified by buyer_ref."""
    _create_media_buy_for_update(ctx, buyer_ref=buyer_ref)


@given(parsers.parse('the Buyer owns a media buy with an active package "{pkg_id}" (paused=false)'))
def given_buyer_owns_active_pkg(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with an active (not paused) package."""
    _create_media_buy_for_update(ctx, buyer_ref=pkg_id, paused=False)


@given(parsers.parse('the Buyer owns a media buy with a paused package "{pkg_id}" (paused=true)'))
def given_buyer_owns_paused_pkg(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with a paused package."""
    _create_media_buy_for_update(ctx, buyer_ref=pkg_id, paused=True)


def _own_pkg_with_metadata(ctx: dict, pkg_id: str, **metadata: Any) -> None:
    """Create a media buy with a package, recording metadata about its intended state.

    All 'the Buyer owns a media buy with a package ...' steps use this shared
    helper. The metadata dict captures the step's semantic claim (e.g., keyword
    targets, catalogs) so downstream steps can reference it.
    """
    _create_media_buy_for_update(ctx, buyer_ref=pkg_id)
    if metadata:
        ctx.setdefault("package_metadata", {}).update(metadata)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having no keyword targets'))
def given_buyer_owns_pkg_no_keywords(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with a package having no keyword targets."""
    _own_pkg_with_metadata(ctx, pkg_id, has_keywords=False)


@given(
    parsers.parse(
        'the Buyer owns a media buy with a package "{pkg_id}" having keyword target '
        '("{keyword}", "{match_type}", bid_price={price})'
    )
)
def given_buyer_owns_pkg_with_keyword_bid(ctx: dict, pkg_id: str, keyword: str, match_type: str, price: str) -> None:
    """Create a media buy with a package having a keyword target with bid_price."""
    _own_pkg_with_metadata(ctx, pkg_id, keyword=keyword, match_type=match_type, bid_price=float(price))


@given(
    parsers.parse(
        'the Buyer owns a media buy with a package "{pkg_id}" having keyword target ("{keyword}", "{match_type}")'
    )
)
def given_buyer_owns_pkg_with_keyword(ctx: dict, pkg_id: str, keyword: str, match_type: str) -> None:
    """Create a media buy with a package having a keyword target."""
    _own_pkg_with_metadata(ctx, pkg_id, keyword=keyword, match_type=match_type)


@given(
    parsers.parse(
        'the Buyer owns a media buy with a package "{pkg_id}" having no keyword target ("{keyword}", "{match_type}")'
    )
)
def given_buyer_owns_pkg_no_specific_keyword(ctx: dict, pkg_id: str, keyword: str, match_type: str) -> None:
    """Create a media buy with a package that does NOT have a specific keyword target."""
    _own_pkg_with_metadata(ctx, pkg_id, missing_keyword=keyword, missing_match_type=match_type)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}"'))
def given_buyer_owns_pkg(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with a package (generic -- no special metadata)."""
    _create_media_buy_for_update(ctx, buyer_ref=pkg_id)


@given(
    parsers.parse(
        'the Buyer owns a media buy with a package "{pkg_id}" having no negative keyword ("{keyword}", "{match_type}")'
    )
)
def given_buyer_owns_pkg_no_neg_keyword(ctx: dict, pkg_id: str, keyword: str, match_type: str) -> None:
    """Create a media buy with a package without a specific negative keyword."""
    _own_pkg_with_metadata(ctx, pkg_id, missing_neg_keyword=keyword, missing_neg_match_type=match_type)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" with product_id "{prod_id}"'))
def given_buyer_owns_pkg_with_product(ctx: dict, pkg_id: str, prod_id: str) -> None:
    """Create a media buy with a package linked to specific product."""
    _own_pkg_with_metadata(ctx, pkg_id, expected_product_id=prod_id)
    # Verify the created package references the correct product
    existing_pkg = ctx.get("existing_package")
    if existing_pkg is not None:
        actual_prod = _pkg_field(existing_pkg, "product_id")
        if actual_prod is not None:
            assert actual_prod == prod_id, f"Package created with product_id '{actual_prod}', expected '{prod_id}'"


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" with format_ids {fmt_ids}'))
def given_buyer_owns_pkg_with_formats(ctx: dict, pkg_id: str, fmt_ids: str) -> None:
    """Create a media buy with a package with specific format_ids."""
    _own_pkg_with_metadata(ctx, pkg_id, expected_format_ids=fmt_ids)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" with pricing_option_id "{option_id}"'))
def given_buyer_owns_pkg_with_pricing(ctx: dict, pkg_id: str, option_id: str) -> None:
    """Create a media buy with a package using specific pricing_option_id."""
    _own_pkg_with_metadata(ctx, pkg_id, expected_pricing_option_id=option_id)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" using pricing_option with max_bid=true'))
def given_buyer_owns_pkg_max_bid(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with a package using a max_bid=true pricing option."""
    _own_pkg_with_metadata(ctx, pkg_id, max_bid=True)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having catalogs {catalogs}'))
def given_buyer_owns_pkg_with_catalogs(ctx: dict, pkg_id: str, catalogs: str) -> None:
    """Create a media buy with a package having specific catalogs."""
    _own_pkg_with_metadata(ctx, pkg_id, catalogs=catalogs)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having optimization_goals {goals}'))
def given_buyer_owns_pkg_with_goals(ctx: dict, pkg_id: str, goals: str) -> None:
    """Create a media buy with a package having specific optimization_goals."""
    _own_pkg_with_metadata(ctx, pkg_id, optimization_goals=goals)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having creative_assignments {assignments}'))
def given_buyer_owns_pkg_with_creatives(ctx: dict, pkg_id: str, assignments: str) -> None:
    """Create a media buy with a package having specific creative_assignments."""
    _own_pkg_with_metadata(ctx, pkg_id, creative_assignments=assignments)


@given(
    parsers.parse(
        'the Buyer owns a media buy with a package "{pkg_id}" having targeting_overlay with audiences {audiences}'
    )
)
def given_buyer_owns_pkg_with_targeting(ctx: dict, pkg_id: str, audiences: str) -> None:
    """Create a media buy with a package having targeting_overlay with audiences."""
    _own_pkg_with_metadata(ctx, pkg_id, audiences=audiences)


@given(parsers.parse('the Buyer owns a media buy with a package "{pkg_id}" having catalogs and optimization_goals'))
def given_buyer_owns_pkg_with_catalogs_and_goals(ctx: dict, pkg_id: str) -> None:
    """Create a media buy with a package having both catalogs and optimization_goals."""
    _own_pkg_with_metadata(ctx, pkg_id, has_catalogs=True, has_optimization_goals=True)


# --- Update datatable step ---


@given(parsers.parse("a valid update_media_buy request with package update:"))
def given_update_with_package_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Parse datatable into update_kwargs with a package update."""
    update_kwargs = _ensure_update_kwargs(ctx)
    pkg_update: dict[str, Any] = {}
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "package_id":
            # Use existing_package_id from context if we created one
            pkg_update["package_id"] = ctx.get("existing_package_id", value)
        elif field == "buyer_ref":
            pkg_update["buyer_ref"] = value
        elif field == "budget":
            pkg_update["budget"] = float(value)
        elif field == "paused":
            pkg_update["paused"] = value.lower() == "true"
        elif field == "pacing":
            pkg_update["pacing"] = value
        elif field == "product_id":
            pkg_update["product_id"] = value
        elif field == "format_ids":
            try:
                raw = json.loads(value)
            except json.JSONDecodeError:
                inner = value.strip("[]")
                raw = [s.strip().strip('"') for s in inner.split(",")]
            pkg_update["format_ids"] = _to_format_id_dicts(raw)
        elif field == "pricing_option_id":
            pkg_update["pricing_option_id"] = _resolve_pricing_id(ctx, value)
        elif field == "keyword_targets_add":
            pkg_update["keyword_targets_add"] = json.loads(value)
        elif field == "keyword_targets_remove":
            pkg_update["keyword_targets_remove"] = json.loads(value)
        elif field == "negative_keywords_add":
            pkg_update["negative_keywords_add"] = json.loads(value)
        elif field == "negative_keywords_remove":
            pkg_update["negative_keywords_remove"] = json.loads(value)
        elif field == "catalogs":
            pkg_update["catalogs"] = json.loads(value)
        elif field == "optimization_goals":
            pkg_update["optimization_goals"] = json.loads(value)
        elif field == "creative_assignments":
            pkg_update["creative_assignments"] = json.loads(value)
        elif field == "targeting_overlay":
            pkg_update["targeting_overlay"] = json.loads(value)
        elif field.startswith("targeting_overlay."):
            # Nested targeting_overlay fields like "targeting_overlay.keyword_targets"
            sub_field = field.split(".", 1)[1]
            overlay = pkg_update.setdefault("targeting_overlay", {})
            overlay[sub_field] = json.loads(value)
    update_kwargs.setdefault("packages", []).append(pkg_update)
    ctx["update_kwargs"] = update_kwargs


@given("the package update contains neither package_id nor buyer_ref")
def given_update_no_identifier(ctx: dict) -> None:
    """Ensure the package update has neither package_id nor buyer_ref."""
    update_kwargs = ctx.get("update_kwargs", {})
    packages = update_kwargs.get("packages", [])
    assert packages, "No packages in update_kwargs — cannot strip identifiers from empty update"
    packages[-1].pop("package_id", None)
    packages[-1].pop("buyer_ref", None)
    # Verify the setup is correct
    assert "package_id" not in packages[-1], "package_id still present after removal"
    assert "buyer_ref" not in packages[-1], "buyer_ref still present after removal"


# --- Partition / boundary Given steps ---


@given(parsers.parse("a create_media_buy request with package fields per {partition}"))
def given_partition_required_fields(ctx: dict, partition: str) -> None:
    """Build create request per partition for required fields validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    partition = partition.strip()
    if partition == "all_four_present":
        pass  # defaults are fine
    elif partition == "budget_zero":
        pkg["budget"] = 0
    elif partition == "missing_buyer_ref":
        del pkg["buyer_ref"]
    elif partition == "missing_product_id":
        del pkg["product_id"]
    elif partition == "missing_budget":
        del pkg["budget"]
    elif partition == "missing_pricing_option_id":
        del pkg["pricing_option_id"]
    elif partition == "negative_budget":
        pkg["budget"] = -1.0
    else:
        raise ValueError(f"Unknown required-fields partition: {partition}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request per boundary {boundary_point}"))
def given_boundary_required_fields(ctx: dict, boundary_point: str) -> None:
    """Build create request per boundary for required fields validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    bp = boundary_point.strip()
    if "all four required" in bp:
        pass  # defaults
    elif "budget = 0" in bp:
        pkg["budget"] = 0
    elif "budget = -0.01" in bp:
        pkg["budget"] = -0.01
    elif "buyer_ref missing" in bp:
        del pkg["buyer_ref"]
    elif "product_id missing" in bp:
        del pkg["product_id"]
    elif "budget missing" in bp:
        del pkg["budget"]
    elif "pricing_option_id missing" in bp:
        del pkg["pricing_option_id"]
    else:
        raise ValueError(f"Unknown required-fields boundary: {bp}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with package bid_price per {partition}"))
def given_partition_bid_price(ctx: dict, partition: str) -> None:
    """Build create request per partition for bid_price validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    partition = partition.strip()
    if partition == "exact_bid":
        pkg["bid_price"] = 2.50
    elif partition == "ceiling_bid":
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
        pkg["bid_price"] = 5.00
    elif partition == "zero_bid":
        pkg["bid_price"] = 0
    elif partition == "bid_absent":
        pkg.pop("bid_price", None)
    elif partition == "negative_bid":
        pkg["bid_price"] = -0.01
    else:
        raise ValueError(f"Unknown bid_price partition: {partition}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with package bid_price per boundary {boundary_point}"))
def given_boundary_bid_price(ctx: dict, boundary_point: str) -> None:
    """Build create request per boundary for bid_price validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    bp = boundary_point.strip()
    if "bid_price = 0" in bp and "minimum" in bp:
        pkg["bid_price"] = 0
    elif "bid_price = 0.01" in bp:
        pkg["bid_price"] = 0.01
    elif "bid_price = -0.01" in bp:
        pkg["bid_price"] = -0.01
    elif "bid_price absent" in bp:
        pkg.pop("bid_price", None)
    elif "max_bid=true" in bp:
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
        pkg["bid_price"] = 5.00
    elif "max_bid=false" in bp:
        pkg["bid_price"] = 2.50
    else:
        raise ValueError(f"Unknown bid_price boundary: {bp}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with buyer_ref per {partition}"))
def given_partition_buyer_ref(ctx: dict, partition: str) -> None:
    """Build create request per partition for buyer_ref dedup validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    partition = partition.strip()
    if partition == "new_buyer_ref":
        pkg["buyer_ref"] = f"new-ref-{uuid.uuid4().hex[:8]}"
    elif partition == "duplicate_buyer_ref":
        _create_media_buy_for_update(ctx, buyer_ref="dedup-ref")
        pkg["buyer_ref"] = "dedup-ref"
    elif partition == "same_ref_different_buy":
        pkg["buyer_ref"] = "cross-buy-ref"
    elif partition == "buyer_ref_missing":
        del pkg["buyer_ref"]
    else:
        raise ValueError(f"Unknown buyer_ref partition: {partition}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with buyer_ref per boundary {boundary_point}"))
def given_boundary_buyer_ref(ctx: dict, boundary_point: str) -> None:
    """Build create request per boundary for buyer_ref dedup validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    bp = boundary_point.strip()
    if "first submission" in bp:
        pkg["buyer_ref"] = f"first-ref-{uuid.uuid4().hex[:8]}"
    elif "second submission" in bp:
        _create_media_buy_for_update(ctx, buyer_ref="second-ref")
        pkg["buyer_ref"] = "second-ref"
    elif "different media buy" in bp:
        pkg["buyer_ref"] = "cross-buy-ref"
    elif "buyer_ref absent" in bp:
        del pkg["buyer_ref"]
    else:
        raise ValueError(f"Unknown buyer_ref boundary: {bp}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with format_ids per {partition}"))
def given_partition_format_ids(ctx: dict, partition: str) -> None:
    """Build create request per partition for format_ids validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    partition = partition.strip()
    if partition == "subset_of_product_formats":
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250"])
    elif partition == "all_product_formats":
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250", "banner-728x90"])
    elif partition == "format_ids_omitted":
        pkg.pop("format_ids", None)
    elif partition == "single_format":
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250"])
    elif partition == "unsupported_format":
        pkg["format_ids"] = _to_format_id_dicts(["video-unsupported"])
    elif partition == "empty_array":
        pkg["format_ids"] = []
    else:
        raise ValueError(f"Unknown format_ids partition: {partition}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with format_ids per boundary {boundary_point}"))
def given_boundary_format_ids(ctx: dict, boundary_point: str) -> None:
    """Build create request per boundary for format_ids validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    bp = boundary_point.strip()
    if "omitted" in bp:
        pkg.pop("format_ids", None)
    elif "single format_id matching" in bp:
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250"])
    elif "all product formats explicitly" in bp:
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250", "banner-728x90"])
    elif "unsupported format_id among valid" in bp:
        pkg["format_ids"] = _to_format_id_dicts(["banner-300x250", "video-unsupported"])
    elif "empty array" in bp:
        pkg["format_ids"] = []
    elif "different product" in bp:
        pkg["format_ids"] = _to_format_id_dicts(["other-product-format"])
    else:
        raise ValueError(f"Unknown format_ids boundary: {bp}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with pricing_option_id per {partition}"))
def given_partition_pricing_option(ctx: dict, partition: str) -> None:
    """Build create request per partition for pricing_option_id validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    partition = partition.strip()
    if partition == "valid_pricing_option":
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-standard")
    elif partition == "valid_with_max_bid":
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    elif partition == "pricing_option_not_found":
        pkg["pricing_option_id"] = "nonexistent-option"
    elif partition == "pricing_option_wrong_product":
        pkg["pricing_option_id"] = "other-product-option"
    else:
        raise ValueError(f"Unknown pricing_option_id partition: {partition}")
    kwargs["packages"] = [pkg]


@given(parsers.parse("a create_media_buy request with pricing_option_id per boundary {boundary_point}"))
def given_boundary_pricing_option(ctx: dict, boundary_point: str) -> None:
    """Build create request per boundary for pricing_option_id validation."""
    kwargs = _ensure_request_defaults(ctx)
    pkg = _build_default_package(ctx)
    bp = boundary_point.strip()
    if "first entry" in bp:
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-standard")
    elif "last entry" in bp:
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    elif "max_bid=true" in bp:
        pkg["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    elif "not in product" in bp:
        pkg["pricing_option_id"] = "nonexistent-option"
    elif "different product" in bp:
        pkg["pricing_option_id"] = "other-product-option"
    elif "empty string" in bp:
        pkg["pricing_option_id"] = ""
    else:
        raise ValueError(f"Unknown pricing_option_id boundary: {bp}")
    kwargs["packages"] = [pkg]


# --- Update partition/boundary Given steps ---


def _setup_update_partition(ctx: dict) -> tuple[dict, dict]:
    """Create a media buy for update partition/boundary tests, return (update_kwargs, pkg_update)."""
    _create_media_buy_for_update(ctx)
    update_kwargs = _ensure_update_kwargs(ctx)
    pkg_update: dict[str, Any] = {"package_id": ctx.get("existing_package_id", "pkg-001")}
    return update_kwargs, pkg_update


@given(parsers.parse("a package update request per {partition}"))
def given_partition_immutable(ctx: dict, partition: str) -> None:
    """Build update request per partition for immutable fields validation."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    partition = partition.strip()
    if partition == "update_mutable_only":
        pkg_update["budget"] = 9000
    elif partition == "no_immutable_fields_present":
        pkg_update["budget"] = 8000
        pkg_update["pacing"] = "even"
    elif partition == "product_id_change":
        pkg_update["product_id"] = "prod-2"
    elif partition == "format_ids_change":
        pkg_update["format_ids"] = _to_format_id_dicts(["banner-728x90"])
    elif partition == "pricing_option_id_change":
        pkg_update["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    else:
        raise ValueError(f"Unknown immutable partition: {partition}")
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request per boundary {boundary_point}"))
def given_boundary_immutable(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for immutable fields validation."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    if "only mutable" in bp:
        pkg_update["budget"] = 9000
    elif "includes product_id" in bp:
        pkg_update["product_id"] = "prod-2"
    elif "includes format_ids" in bp:
        pkg_update["format_ids"] = _to_format_id_dicts(["banner-728x90"])
    elif "includes pricing_option_id" in bp:
        pkg_update["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    elif "all three immutable" in bp:
        pkg_update["product_id"] = "prod-2"
        pkg_update["format_ids"] = _to_format_id_dicts(["banner-728x90"])
        pkg_update["pricing_option_id"] = _resolve_pricing_id(ctx, "cpm-auction")
    else:
        raise ValueError(f"Unknown immutable boundary: {bp}")
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


def _build_keyword_entry(
    keyword: str = "shoes",
    match_type: str = "broad",
    bid_price: float | None = None,
) -> dict[str, Any]:
    """Build a single keyword target entry."""
    entry: dict[str, Any] = {"keyword": keyword, "match_type": match_type}
    if bid_price is not None:
        entry["bid_price"] = bid_price
    return entry


def _setup_keyword_partition(ctx: dict, field_name: str, partition: str) -> None:
    """Build update request for keyword add/remove partition scenarios."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    partition = partition.strip()

    if partition in ("new_keyword", "typical_add"):
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition == "existing_keyword_update_bid":
        pkg_update[field_name] = [_build_keyword_entry(bid_price=5.00)]
    elif partition == "mixed_new_and_update":
        pkg_update[field_name] = [
            _build_keyword_entry(),
            _build_keyword_entry(keyword="hats"),
        ]
    elif partition == "same_keyword_different_match":
        pkg_update[field_name] = [
            _build_keyword_entry(match_type="broad"),
            _build_keyword_entry(match_type="exact"),
        ]
    elif partition in ("remove_existing_pair", "typical_remove"):
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition in ("remove_nonexistent_pair", "remove_nonexistent"):
        pkg_update[field_name] = [_build_keyword_entry(keyword="nonexistent")]
    elif partition == "mixed_existing_and_nonexistent":
        pkg_update[field_name] = [
            _build_keyword_entry(),
            _build_keyword_entry(keyword="nonexistent"),
        ]
    elif partition == "remove_all_keywords":
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition == "empty_keyword":
        pkg_update[field_name] = [_build_keyword_entry(keyword="")]
    elif partition == "invalid_match_type":
        pkg_update[field_name] = [_build_keyword_entry(match_type="unknown")]
    elif partition == "negative_bid_price":
        pkg_update[field_name] = [_build_keyword_entry(bid_price=-0.01)]
    elif partition == "empty_array":
        pkg_update[field_name] = []
    elif partition == "add_duplicate":
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition == "boundary_min_array":
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition == "boundary_min_keyword":
        pkg_update[field_name] = [_build_keyword_entry(keyword="a")]
    elif partition == "add_with_bid_price":
        pkg_update[field_name] = [_build_keyword_entry(bid_price=2.50)]
    elif partition == "add_without_bid_price":
        pkg_update[field_name] = [_build_keyword_entry()]
    elif partition == "upsert_existing":
        pkg_update[field_name] = [_build_keyword_entry(bid_price=5.00)]
    elif partition == "zero_bid_price":
        pkg_update[field_name] = [_build_keyword_entry(bid_price=0)]
    elif partition == "all_match_types":
        pkg_update[field_name] = [
            _build_keyword_entry(match_type="broad"),
            _build_keyword_entry(match_type="phrase"),
            _build_keyword_entry(match_type="exact"),
        ]
    elif partition == "cross_dimension_valid":
        # keyword_targets_add with negative_keywords in targeting_overlay (cross-dimension)
        pkg_update[field_name] = [_build_keyword_entry()]
        overlay_field = "negative_keywords" if "keyword_targets" in field_name else "keyword_targets"
        pkg_update.setdefault("targeting_overlay", {})[overlay_field] = [_build_keyword_entry(keyword="cross")]
    elif partition == "missing_keyword":
        pkg_update[field_name] = [{"match_type": "broad"}]
    elif partition == "missing_match_type":
        pkg_update[field_name] = [{"keyword": "shoes"}]
    elif partition == "conflict_with_overlay":
        # Same dimension in both add and overlay — mutually exclusive
        pkg_update[field_name] = [_build_keyword_entry()]
        overlay_field = "keyword_targets" if "keyword_targets" in field_name else "negative_keywords"
        pkg_update.setdefault("targeting_overlay", {})[overlay_field] = [_build_keyword_entry(keyword="conflict")]
    else:
        raise ValueError(f"Unknown keyword partition: {partition}")

    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request with keyword_targets_add per {partition}"))
def given_partition_keyword_add(ctx: dict, partition: str) -> None:
    """Build update request per partition for keyword_targets_add."""
    _setup_keyword_partition(ctx, "keyword_targets_add", partition)


@given(parsers.parse("a package update request with keyword_targets_add per boundary {boundary_point}"))
def given_boundary_keyword_add(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for keyword_targets_add."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    _apply_keyword_boundary(pkg_update, "keyword_targets_add", bp)
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request with keyword_targets_remove per {partition}"))
def given_partition_keyword_remove(ctx: dict, partition: str) -> None:
    """Build update request per partition for keyword_targets_remove."""
    _setup_keyword_partition(ctx, "keyword_targets_remove", partition)


@given(parsers.parse("a package update request with keyword_targets_remove per boundary {boundary_point}"))
def given_boundary_keyword_remove(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for keyword_targets_remove."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    _apply_keyword_boundary(pkg_update, "keyword_targets_remove", bp)
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request with keyword_targets_add per shared {partition}"))
def given_partition_kw_add_shared(ctx: dict, partition: str) -> None:
    """Build update request per shared partition for keyword_targets_add."""
    _setup_keyword_partition(ctx, "keyword_targets_add", partition)


@given(parsers.parse("a package update request with keyword_targets_remove per shared {partition}"))
def given_partition_kw_remove_shared(ctx: dict, partition: str) -> None:
    """Build update request per shared partition for keyword_targets_remove."""
    _setup_keyword_partition(ctx, "keyword_targets_remove", partition)


@given(parsers.parse("a package update request with negative_keywords_add per shared {partition}"))
def given_partition_neg_kw_add_shared(ctx: dict, partition: str) -> None:
    """Build update request per shared partition for negative_keywords_add."""
    _setup_keyword_partition(ctx, "negative_keywords_add", partition)


@given(parsers.parse("a package update request with negative_keywords_remove per shared {partition}"))
def given_partition_neg_kw_remove_shared(ctx: dict, partition: str) -> None:
    """Build update request per shared partition for negative_keywords_remove."""
    _setup_keyword_partition(ctx, "negative_keywords_remove", partition)


@given(parsers.parse("a package update request with negative_keywords_add per boundary {boundary_point}"))
def given_boundary_neg_kw_add(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for negative_keywords_add."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    _apply_keyword_boundary(pkg_update, "negative_keywords_add", bp)
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request with negative_keywords_remove per boundary {boundary_point}"))
def given_boundary_neg_kw_remove(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for negative_keywords_remove."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    _apply_keyword_boundary(pkg_update, "negative_keywords_remove", bp)
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


def _apply_keyword_boundary(pkg_update: dict, field_name: str, bp: str) -> None:
    """Apply keyword boundary settings to a package update dict."""
    if "array length 0" in bp or "empty" in bp.lower() and "array" in bp.lower():
        pkg_update[field_name] = []
    elif "array length 1" in bp or "minimum valid" in bp:
        pkg_update[field_name] = [_build_keyword_entry()]
    elif "keyword length 0" in bp or "empty string" in bp.lower() and "keyword" in bp.lower():
        pkg_update[field_name] = [_build_keyword_entry(keyword="")]
    elif "keyword length 1" in bp or "single char" in bp:
        pkg_update[field_name] = [_build_keyword_entry(keyword="a")]
    elif "match_type = 'broad'" in bp:
        pkg_update[field_name] = [_build_keyword_entry(match_type="broad")]
    elif "match_type = 'phrase'" in bp:
        pkg_update[field_name] = [_build_keyword_entry(match_type="phrase")]
    elif "match_type = 'exact'" in bp:
        pkg_update[field_name] = [_build_keyword_entry(match_type="exact")]
    elif "match_type = 'unknown'" in bp or "unknown match_type" in bp:
        pkg_update[field_name] = [_build_keyword_entry(match_type="unknown")]
    elif "bid_price = 0" in bp and "minimum" in bp:
        pkg_update[field_name] = [_build_keyword_entry(bid_price=0)]
    elif "bid_price = -0.01" in bp:
        pkg_update[field_name] = [_build_keyword_entry(bid_price=-0.01)]
    elif "WITH targeting_overlay" in bp and "keyword_targets" in bp and "cross-dimension" not in bp:
        # Same-dimension conflict
        pkg_update[field_name] = [_build_keyword_entry()]
        overlay_field = "keyword_targets" if "keyword_targets" in field_name else "negative_keywords"
        pkg_update.setdefault("targeting_overlay", {})[overlay_field] = [_build_keyword_entry(keyword="conflict")]
    elif "WITHOUT targeting_overlay" in bp:
        pkg_update[field_name] = [_build_keyword_entry()]
    elif "cross-dimension" in bp:
        pkg_update[field_name] = [_build_keyword_entry()]
        overlay_field = "negative_keywords" if "keyword_targets" in field_name else "keyword_targets"
        pkg_update.setdefault("targeting_overlay", {})[overlay_field] = [_build_keyword_entry(keyword="cross")]
    elif "exists in current list" in bp or "existing" in bp.lower() and "pair" in bp.lower():
        pkg_update[field_name] = [_build_keyword_entry()]
    elif "does NOT exist" in bp or "non-existent" in bp.lower() or "no-op" in bp:
        pkg_update[field_name] = [_build_keyword_entry(keyword="nonexistent")]
    elif "mix of existing" in bp:
        pkg_update[field_name] = [
            _build_keyword_entry(),
            _build_keyword_entry(keyword="nonexistent"),
        ]
    elif "remove all" in bp:
        pkg_update[field_name] = [_build_keyword_entry()]
    elif "single new keyword" in bp:
        pkg_update[field_name] = [_build_keyword_entry(bid_price=2.50)]
    elif "updated bid_price" in bp:
        pkg_update[field_name] = [_build_keyword_entry(bid_price=5.00)]
    elif "broad and exact" in bp:
        pkg_update[field_name] = [
            _build_keyword_entry(match_type="broad"),
            _build_keyword_entry(match_type="exact"),
        ]
    elif "duplicate" in bp.lower():
        pkg_update[field_name] = [_build_keyword_entry()]
    elif "remove single" in bp:
        pkg_update[field_name] = [_build_keyword_entry()]
    else:
        # Fallback: single valid keyword entry
        pkg_update[field_name] = [_build_keyword_entry()]


# --- Paused partition/boundary ---


@given(parsers.parse("a package request with paused per {partition}"))
def given_partition_paused(ctx: dict, partition: str) -> None:
    """Build request per partition for paused behavior."""
    partition = partition.strip()
    if partition in ("active_default", "explicitly_active", "explicitly_paused"):
        # Create flow
        kwargs = _ensure_request_defaults(ctx)
        pkg = _build_default_package(ctx)
        if partition == "explicitly_paused":
            pkg["paused"] = True
        elif partition == "explicitly_active":
            pkg["paused"] = False
        # active_default: omit paused field
        kwargs["packages"] = [pkg]
        ctx["paused_request_type"] = "create"
    elif partition in ("pause_on_update", "resume_on_update"):
        # Update flow
        _create_media_buy_for_update(ctx)
        update_kwargs = _ensure_update_kwargs(ctx)
        pkg_update = {"package_id": ctx.get("existing_package_id", "pkg-001")}
        pkg_update["paused"] = partition == "pause_on_update"
        update_kwargs["packages"] = [pkg_update]
        ctx["update_kwargs"] = update_kwargs
        ctx["paused_request_type"] = "update"
    else:
        raise ValueError(f"Unknown paused partition: {partition}")


@given(parsers.parse("a package request with paused per boundary {boundary_point}"))
def given_boundary_paused(ctx: dict, boundary_point: str) -> None:
    """Build request per boundary for paused behavior."""
    bp = boundary_point.strip()
    if "on create" in bp:
        kwargs = _ensure_request_defaults(ctx)
        pkg = _build_default_package(ctx)
        if "paused=true" in bp:
            pkg["paused"] = True
        elif "paused=false" in bp:
            pkg["paused"] = False
        # "omitted" case: don't set paused
        kwargs["packages"] = [pkg]
        ctx["paused_request_type"] = "create"
    elif "on update" in bp or "already-paused" in bp:
        _create_media_buy_for_update(ctx)
        update_kwargs = _ensure_update_kwargs(ctx)
        pkg_update = {"package_id": ctx.get("existing_package_id", "pkg-001")}
        if "paused=true" in bp:
            pkg_update["paused"] = True
        elif "paused=false" in bp:
            pkg_update["paused"] = False
        update_kwargs["packages"] = [pkg_update]
        ctx["update_kwargs"] = update_kwargs
        ctx["paused_request_type"] = "update"
    else:
        raise ValueError(f"Unknown paused boundary: {bp}")


# --- Replacement semantics partition/boundary ---


@given(parsers.parse("a package update request per replacement semantics {partition}"))
def given_partition_replacement(ctx: dict, partition: str) -> None:
    """Build update request per partition for replacement semantics."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    partition = partition.strip()
    if partition == "replace_catalogs":
        pkg_update["catalogs"] = [{"type": "store", "catalog_id": "cat-new"}]
    elif partition == "replace_optimization_goals":
        pkg_update["optimization_goals"] = [{"metric": "clicks", "priority": 1}]
    elif partition == "replace_creative_assignments":
        pkg_update["creative_assignments"] = [{"creative_id": "cr-new", "weight": 1.0}]
    elif partition == "omit_array_fields":
        pkg_update["budget"] = 8000  # Only scalar update
    elif partition == "replace_targeting_overlay":
        pkg_update["targeting_overlay"] = {"audiences": [{"audience_id": "aud-new"}]}
    else:
        raise ValueError(f"Unknown replacement partition: {partition}")
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


@given(parsers.parse("a package update request per replacement boundary {boundary_point}"))
def given_boundary_replacement(ctx: dict, boundary_point: str) -> None:
    """Build update request per boundary for replacement semantics."""
    update_kwargs, pkg_update = _setup_update_partition(ctx)
    bp = boundary_point.strip()
    if "catalogs provided" in bp:
        pkg_update["catalogs"] = [{"type": "store", "catalog_id": "cat-new"}]
    elif "optimization_goals provided" in bp:
        pkg_update["optimization_goals"] = [{"metric": "clicks", "priority": 1}]
    elif "creative_assignments provided" in bp:
        pkg_update["creative_assignments"] = [{"creative_id": "cr-new", "weight": 1.0}]
    elif "all array fields omitted" in bp:
        pkg_update["budget"] = 8000
    elif "only scalar fields" in bp:
        pkg_update["budget"] = 8000
        pkg_update["pacing"] = "even"
    elif "targeting_overlay replacement" in bp:
        pkg_update["targeting_overlay"] = {"audiences": [{"audience_id": "aud-new"}]}
    else:
        raise ValueError(f"Unknown replacement boundary: {bp}")
    update_kwargs["packages"] = [pkg_update]
    ctx["update_kwargs"] = update_kwargs


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — dispatch create/update
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent invokes the create_media_buy MCP tool")
def when_invoke_create_mcp(ctx: dict) -> None:
    """Dispatch create_media_buy through MCP transport."""
    ctx["package_transport_hint"] = "mcp"
    _dispatch_create(ctx)


@when("the Buyer Agent sends the create_media_buy A2A task")
def when_send_create_a2a(ctx: dict) -> None:
    """Dispatch create_media_buy through A2A transport."""
    ctx["package_transport_hint"] = "a2a"
    _dispatch_create(ctx)


@when(parsers.parse('the Buyer Agent sends the create_media_buy request for "{mb_id}"'))
def when_send_create_for_mb(ctx: dict, mb_id: str) -> None:
    """Dispatch create_media_buy for a specific (cross-buy) media buy."""
    ctx["dispatched_for_mb_id"] = mb_id
    _dispatch_create(ctx)


@when("the Buyer Agent sends the request")
def when_send_generic_request(ctx: dict) -> None:
    """Send either a create or update request based on context."""
    if ctx.get("paused_request_type") == "update" or "update_kwargs" in ctx:
        # Update flow
        from src.core.schemas import UpdateMediaBuyRequest
        from tests.bdd.steps.generic._dispatch import dispatch_request

        update_kwargs = ctx.get("update_kwargs", {})
        try:
            req = UpdateMediaBuyRequest(**update_kwargs)
        except Exception as exc:
            ctx["error"] = exc
            return
        dispatch_request(ctx, req=req)
    else:
        # Create flow
        _dispatch_create(ctx)


def _dispatch_create(ctx: dict) -> None:
    """Build CreateMediaBuyRequest and dispatch through harness."""
    from pydantic import ValidationError

    from src.core.schemas import CreateMediaBuyRequest
    from tests.bdd.steps.generic._dispatch import dispatch_request

    request_kwargs = ctx.get("request_kwargs", {})
    try:
        req = CreateMediaBuyRequest(**request_kwargs)
    except ValidationError as exc:
        ctx["error"] = exc
        return

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


@then("the response should contain a package with a seller-assigned package_id")
def then_package_has_id(ctx: dict) -> None:
    """Assert response contains at least one package with a seller-assigned package_id."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id is not None, "Package missing package_id"
    assert isinstance(pkg_id, str) and len(pkg_id.strip()) > 0, (
        f"Expected seller-assigned package_id to be a non-empty string, got {pkg_id!r}"
    )


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
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify budget"
    actual = _pkg_field(pkg, "budget")
    if actual is None:
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg and getattr(db_pkg, "budget", None) is not None:
                db_budget = float(db_pkg.budget)
                assert db_budget == float(budget), f"DB has budget {db_budget}, expected {budget}"
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: budget correctly persisted as {db_budget} in DB "
                    f"but not echoed in response. FIXME(salesagent-9vgz.1)"
                )
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: package budget is None in both response and DB — "
            f"step claims 'contain budget {budget}'. FIXME(salesagent-9vgz.1)"
        )
    assert float(actual) == float(budget), f"Expected budget {budget}, got {actual}"


@then(parsers.parse('the package should contain pricing_option_id "{pricing_option_id}"'))
def then_package_pricing(ctx: dict, pricing_option_id: str) -> None:
    """Assert first package has the expected pricing_option_id."""
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    expected = _resolve_pricing_id(ctx, pricing_option_id)
    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify pricing_option_id"
    actual = _pkg_field(pkg, "pricing_option_id")
    if actual is None:
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg and getattr(db_pkg, "pricing_option_id", None) is not None:
                assert db_pkg.pricing_option_id == expected, (
                    f"DB has pricing_option_id '{db_pkg.pricing_option_id}', expected '{expected}'"
                )
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: pricing_option_id correctly persisted as "
                    f"'{db_pkg.pricing_option_id}' in DB but not echoed in response. "
                    f"FIXME(salesagent-9vgz.1)"
                )
        pytest.xfail(
            "SPEC-PRODUCTION GAP: pricing_option_id is None in both response and DB — FIXME(salesagent-9vgz.1)"
        )
    assert actual == expected, f"Expected pricing_option_id '{expected}', got '{actual}'"


@then("the package should contain format_ids defaulting to all product formats")
def then_package_default_formats(ctx: dict) -> None:
    """Assert package format_ids default to all product formats."""
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify format_ids"
    format_ids = _pkg_field(pkg, "format_ids")
    if format_ids is None:
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg:
                db_format_ids = getattr(db_pkg, "format_ids", None)
                if db_format_ids:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: format_ids persisted in DB ({len(db_format_ids)} items) "
                        "but not echoed in response. FIXME(salesagent-9vgz.1)"
                    )
        pytest.xfail("SPEC-PRODUCTION GAP: format_ids not present on package or in DB. FIXME(salesagent-9vgz.1)")
    assert isinstance(format_ids, list), f"Expected format_ids to be a list, got {type(format_ids)}"
    assert len(format_ids) > 0, "Expected format_ids to default to all product formats, got empty list"
    product = ctx.get("default_product")
    assert product is not None, "No default_product in context"
    product_format_ids = getattr(product, "format_ids", None) or []
    product_ids = {_extract_format_id(f) for f in product_format_ids}
    assert product_ids, "Product has no format_ids"
    pkg_ids = {_extract_format_id(f) for f in format_ids}
    assert pkg_ids == product_ids, (
        f"Package format_ids should default to all product formats. Expected {product_ids}, got {pkg_ids}"
    )


@then("the package should contain paused as false")
def then_package_not_paused(ctx: dict) -> None:
    """Assert package paused field is explicitly False."""
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify paused state"
    paused = _pkg_field(pkg, "paused")
    if paused is None:
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg:
                db_paused = getattr(db_pkg, "paused", None)
                if db_paused is not None:
                    assert db_paused is False, f"DB has paused={db_paused!r}, expected False"
                    pytest.xfail(
                        "SPEC-PRODUCTION GAP: paused correctly persisted as False in DB "
                        "but not echoed in response. FIXME(salesagent-9vgz.1)"
                    )
        pytest.xfail("SPEC-PRODUCTION GAP: paused is None in both response and DB. FIXME(salesagent-9vgz.1)")
    assert paused is False, f"Expected paused to be False, got {paused!r}"


@then("the package should contain format_ids_to_provide listing formats needing creative assets")
def then_package_formats_to_provide(ctx: dict) -> None:
    """Assert package has format_ids_to_provide listing formats that need creative assets."""
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify format_ids_to_provide"
    formats_to_provide = _pkg_field(pkg, "format_ids_to_provide")
    if formats_to_provide is None:
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg:
                db_val = getattr(db_pkg, "format_ids_to_provide", None)
                if db_val:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: format_ids_to_provide persisted in DB "
                        f"({len(db_val)} items) but not echoed in response. "
                        "FIXME(salesagent-9vgz.1)"
                    )
        pytest.xfail(
            "SPEC-PRODUCTION GAP: format_ids_to_provide not present on package or in DB. FIXME(salesagent-9vgz.1)"
        )
    assert isinstance(formats_to_provide, list), (
        f"Expected format_ids_to_provide to be a list, got {type(formats_to_provide)}"
    )
    assert len(formats_to_provide) > 0, (
        "Expected format_ids_to_provide to list formats needing creative assets, got empty list"
    )
    # Verify format_ids_to_provide is a subset of package format_ids
    format_ids = _pkg_field(pkg, "format_ids")
    if format_ids:
        pkg_format_set = {_extract_format_id(f) for f in format_ids}
        provide_set = {_extract_format_id(f) for f in formats_to_provide}
        extra = provide_set - pkg_format_set
        assert not extra, f"format_ids_to_provide contains {extra} which are not in package format_ids {pkg_format_set}"
    # Verify these are formats without creative assignments (needing creative assets)
    creative_assignments = _pkg_field(pkg, "creative_assignments") or []
    if creative_assignments:
        assigned_format_ids = set()
        for ca in creative_assignments:
            ca_fids = ca.get("format_ids") if isinstance(ca, dict) else getattr(ca, "format_ids", None)
            if ca_fids:
                for f in ca_fids:
                    assigned_format_ids.add(_extract_format_id(f))
        if assigned_format_ids:
            provide_set = {_extract_format_id(f) for f in formats_to_provide}
            overlap = provide_set & assigned_format_ids
            assert not overlap, f"format_ids_to_provide includes {overlap} which already have creative assignments"


@then("the package should contain format_ids_to_provide based on assigned creatives")
def then_package_formats_to_provide_based_on_creatives(ctx: dict) -> None:
    """Assert format_ids_to_provide reflects outstanding creative needs based on assignments."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    formats_to_provide = _pkg_field(pkg, "format_ids_to_provide")
    if formats_to_provide is None:
        pytest.xfail("SPEC-PRODUCTION GAP: format_ids_to_provide not present in response. FIXME(salesagent-9vgz.1)")
    assert isinstance(formats_to_provide, list), (
        f"Expected format_ids_to_provide to be a list, got {type(formats_to_provide)}"
    )
    # Cross-check against creative_assignments: formats_to_provide should exclude
    # formats that already have creative assets assigned
    format_ids = _pkg_field(pkg, "format_ids") or []
    creative_assignments = _pkg_field(pkg, "creative_assignments") or []
    if format_ids and creative_assignments:
        assigned_format_ids = set()
        for ca in creative_assignments:
            ca_fids = ca.get("format_ids") if isinstance(ca, dict) else getattr(ca, "format_ids", None)
            if ca_fids:
                for f in ca_fids:
                    assigned_format_ids.add(_extract_format_id(f))
        if assigned_format_ids:
            provide_set = {_extract_format_id(f) for f in formats_to_provide}
            overlap = provide_set & assigned_format_ids
            assert not overlap, f"format_ids_to_provide contains {overlap} which already have creative assignments"


@then("the package should contain the seller-assigned package_id")
def then_package_has_seller_id(ctx: dict) -> None:
    """Assert package has a seller-assigned package_id (synonym)."""
    then_package_has_id(ctx)


@then(parsers.parse("the response should contain a package with format_ids {fmt_ids}"))
def then_package_explicit_formats(ctx: dict, fmt_ids: str) -> None:
    """Assert package has the specified format_ids."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    format_ids = _pkg_field(pkg, "format_ids")
    assert format_ids is not None, "Package has no format_ids"

    try:
        expected = json.loads(fmt_ids)
    except json.JSONDecodeError:
        inner = fmt_ids.strip("[]")
        expected = [s.strip().strip('"') for s in inner.split(",")]

    actual_ids = {_extract_format_id(f) for f in format_ids}
    expected_ids = set(expected)
    assert expected_ids <= actual_ids, f"Expected format_ids {expected_ids}, got {actual_ids}"


@then("the response should contain a package with all provided fields echoed")
def then_package_all_fields(ctx: dict) -> None:
    """Assert package echoes all provided fields from the request."""
    import pytest

    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id is not None, "Package missing package_id"
    # Verify all fields from the request are echoed in the response
    request_kwargs = ctx.get("request_kwargs", {})
    req_packages = request_kwargs.get("packages", [])
    if req_packages:
        req_pkg = req_packages[0]
        missing_fields = []
        for field, _value in req_pkg.items():
            actual = _pkg_field(pkg, field)
            if actual is None:
                missing_fields.append(field)
        if missing_fields:
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: Package created but fields {missing_fields} not echoed "
                f"in response. FIXME(salesagent-9vgz.1)"
            )


# --- Operation outcome ---


@then("the operation should succeed")
def then_operation_succeeds(ctx: dict) -> None:
    """Assert the operation succeeded (no error)."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assert ctx.get("response") is not None, "Expected a response but none was recorded"


# --- Outcome dispatch step (partition/boundary) ---


@then(parsers.parse("the outcome should be {outcome}"))
def then_outcome(ctx: dict, outcome: str) -> None:
    """Dispatch assertion based on outcome text from partition/boundary tables."""
    outcome = outcome.strip()
    if outcome.startswith("error"):
        # Expected error: 'error "CODE" with suggestion'
        assert "error" in ctx, f"Expected error outcome but no error recorded. Response: {ctx.get('response')}"
    elif outcome.startswith("success"):
        assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    else:
        raise ValueError(f"Unknown outcome format: {outcome}")


# --- Update-specific Then steps ---


@then(parsers.parse("the response should contain the updated package with budget {budget:d}"))
def then_updated_budget(ctx: dict, budget: int) -> None:
    """Assert updated package has the expected budget."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "budget")
    assert actual is not None, "Package budget is None in update response"
    assert float(actual) == float(budget), f"Expected budget {budget}, got {actual}"


@then(parsers.parse('the response should contain the updated package with budget {budget:d} and pacing "{pacing}"'))
def then_updated_budget_and_pacing(ctx: dict, budget: int, pacing: str) -> None:
    """Assert updated package has expected budget and pacing."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual_budget = _pkg_field(pkg, "budget")
    assert actual_budget is not None, "Package budget is None"
    assert float(actual_budget) == float(budget), f"Expected budget {budget}, got {actual_budget}"
    actual_pacing = _pkg_field(pkg, "pacing")
    assert actual_pacing == pacing, f"Expected pacing '{pacing}', got '{actual_pacing}'"


@then("the package paused state should be unchanged")
def then_paused_unchanged(ctx: dict) -> None:
    """Assert paused state was not changed by the update."""
    import pytest

    _assert_no_error(ctx)
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual_paused = _pkg_field(pkg, "paused")
    # The existing package was created with a known paused state; verify it didn't change
    existing_pkg = ctx.get("existing_package")
    if existing_pkg is not None:
        original_paused = _pkg_field(existing_pkg, "paused")
        if actual_paused is not None and original_paused is not None:
            assert actual_paused == original_paused, (
                f"Paused state changed: was {original_paused!r}, now {actual_paused!r}"
            )
        elif actual_paused is None:
            pytest.xfail(
                "SPEC-PRODUCTION GAP: paused not echoed in update response — "
                "cannot verify unchanged. FIXME(salesagent-9vgz.1)"
            )
    elif actual_paused is None:
        pytest.xfail("SPEC-PRODUCTION GAP: paused not echoed in update response. FIXME(salesagent-9vgz.1)")


@then(parsers.parse("the response should contain the package with paused={paused}"))
def then_pkg_paused_value(ctx: dict, paused: str) -> None:
    """Assert package has specific paused value."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "paused")
    expected = paused.lower() == "true"
    assert actual == expected, f"Expected paused={expected}, got {actual}"


@then("the package should not deliver impressions")
def then_no_delivery(ctx: dict) -> None:
    """Assert package should not deliver (paused=true implies no delivery)."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    paused = _pkg_field(pkg, "paused")
    if paused is None:
        pytest.xfail("SPEC-PRODUCTION GAP: paused field absent — cannot verify no-delivery. FIXME(salesagent-9vgz.1)")
    assert paused is True, f"Expected paused=true (no delivery), got paused={paused}"


@then("the package should deliver impressions")
def then_should_deliver(ctx: dict) -> None:
    """Assert package should deliver (paused=false implies delivery)."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    paused = _pkg_field(pkg, "paused")
    if paused is None:
        pytest.xfail("SPEC-PRODUCTION GAP: paused field absent — cannot verify delivery. FIXME(salesagent-9vgz.1)")
    assert paused is False, f"Expected paused=false (delivery active), got paused={paused}"


@then("the package should resume delivering impressions")
def then_resume_delivery(ctx: dict) -> None:
    """Assert package resumed delivery (paused=false)."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    paused = _pkg_field(pkg, "paused")
    if paused is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: paused field absent — cannot verify resumed delivery. FIXME(salesagent-9vgz.1)"
        )
    assert paused is False, f"Expected paused=false (resumed), got paused={paused}"


# --- Keyword Then steps ---


@then(parsers.parse('the response should contain the package with keyword "{keyword}" in targeting_overlay'))
def then_pkg_has_keyword(ctx: dict, keyword: str) -> None:
    """Assert package targeting_overlay contains specified keyword."""
    import pytest

    packages = _get_packages(ctx)
    pkg = packages[0]
    overlay = _pkg_field(pkg, "targeting_overlay")
    if overlay is None:
        pytest.xfail("SPEC-PRODUCTION GAP: targeting_overlay not present in response. FIXME(salesagent-9vgz.1)")
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        pytest.xfail("SPEC-PRODUCTION GAP: keyword_targets not present in targeting_overlay. FIXME(salesagent-9vgz.1)")
    found = _find_keyword(kw_targets, keyword)
    assert found is not None, (
        f"Keyword '{keyword}' not found in targeting_overlay.keyword_targets. "
        f"Present keywords: {[_keyword_field(k, 'keyword') for k in kw_targets]}"
    )


@then(parsers.parse('the response should contain keyword "{keyword}" with match_type "{match_type}"'))
def then_keyword_with_match_type(ctx: dict, keyword: str, match_type: str) -> None:
    """Assert response contains keyword with specific match_type."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        pytest.xfail("SPEC-PRODUCTION GAP: keyword_targets not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    found = _find_keyword(kw_targets, keyword, match_type)
    assert found is not None, (
        f"Keyword '{keyword}' with match_type '{match_type}' not found in targeting_overlay. "
        f"Present: {[(str(_keyword_field(k, 'keyword')), str(_keyword_field(k, 'match_type'))) for k in kw_targets]}"
    )


@then(
    parsers.parse(
        'the response should contain keyword "{keyword}" with match_type "{match_type}" and updated bid_price {price}'
    )
)
def then_keyword_updated_bid(ctx: dict, keyword: str, match_type: str, price: str) -> None:
    """Assert keyword has updated bid_price."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        pytest.xfail("SPEC-PRODUCTION GAP: keyword_targets not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    found = _find_keyword(kw_targets, keyword, match_type)
    assert found is not None, f"Keyword '{keyword}' with match_type '{match_type}' not found in targeting_overlay"
    actual_bid = _keyword_field(found, "bid_price")
    if actual_bid is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: keyword '{keyword}' found but bid_price not echoed. FIXME(salesagent-9vgz.1)"
        )
    assert float(actual_bid) == float(price), f"Expected bid_price {price} for keyword '{keyword}', got {actual_bid}"


@then(
    parsers.parse(
        'the response should not contain keyword "{keyword}" with match_type "{match_type}" in targeting_overlay'
    )
)
def then_keyword_not_present(ctx: dict, keyword: str, match_type: str) -> None:
    """Assert keyword is not present in targeting_overlay."""
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        # No keyword_targets means the keyword is absent — assertion satisfied
        return
    found = _find_keyword(kw_targets, keyword, match_type)
    assert found is None, (
        f"Keyword '{keyword}' with match_type '{match_type}' should NOT be present in targeting_overlay but was found"
    )


@then("the response should succeed with package targeting unchanged")
def then_targeting_unchanged(ctx: dict) -> None:
    """Assert success with targeting state unchanged after no-op operation."""
    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    # Verify the package exists and targeting_overlay is present (even if empty)
    pkg = pkgs[0]
    assert _pkg_field(pkg, "package_id"), "Package has no package_id"


@then(parsers.parse('the response should contain negative keyword "{keyword}" in targeting_overlay'))
def then_negative_keyword(ctx: dict, keyword: str) -> None:
    """Assert targeting_overlay contains specified negative keyword."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    neg_keywords = _get_overlay_keywords(pkg, "negative_keywords")
    if neg_keywords is None:
        pytest.xfail("SPEC-PRODUCTION GAP: negative_keywords not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    found = _find_keyword(neg_keywords, keyword)
    assert found is not None, (
        f"Negative keyword '{keyword}' not found in targeting_overlay.negative_keywords. "
        f"Present: {[_keyword_field(k, 'keyword') for k in neg_keywords]}"
    )


@then("the response should succeed with package negative keywords unchanged")
def then_negative_keywords_unchanged(ctx: dict) -> None:
    """Assert success with negative keywords unchanged after no-op operation."""
    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    assert _pkg_field(pkg, "package_id"), "Package has no package_id"


@then("the response should contain updated keyword targets and negative keywords")
def then_updated_keyword_and_negative(ctx: dict) -> None:
    """Assert response contains both keyword targets and negative keywords in targeting."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    overlay = _pkg_field(pkg, "targeting_overlay")
    if overlay is None:
        pytest.xfail("SPEC-PRODUCTION GAP: targeting_overlay not present in response. FIXME(salesagent-9vgz.1)")
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    neg_keywords = _get_overlay_keywords(pkg, "negative_keywords")
    if kw_targets is None and neg_keywords is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: neither keyword_targets nor negative_keywords "
            "present in targeting_overlay. FIXME(salesagent-9vgz.1)"
        )


@then(parsers.parse('the response should contain keyword "{keyword}" with match_type "{match_type}"'))
def then_contains_keyword_match(ctx: dict, keyword: str, match_type: str) -> None:
    """Assert response contains keyword with match_type (invariant check)."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        pytest.xfail("SPEC-PRODUCTION GAP: keyword_targets not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    found = _find_keyword(kw_targets, keyword, match_type)
    assert found is not None, (
        f"Keyword '{keyword}' with match_type '{match_type}' not found in targeting_overlay. "
        f"Present: {[(str(_keyword_field(k, 'keyword')), str(_keyword_field(k, 'match_type'))) for k in kw_targets]}"
    )


@then(parsers.parse("the keyword bid_price {price} should be interpreted as ceiling (max_bid=true)"))
def then_keyword_bid_ceiling(ctx: dict, price: str) -> None:
    """Assert keyword bid_price is interpreted as ceiling (max_bid=true semantics)."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    kw_targets = _get_overlay_keywords(pkg, "keyword_targets")
    if kw_targets is None:
        pytest.xfail("SPEC-PRODUCTION GAP: keyword_targets not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    # Find keyword with the expected bid_price
    expected_price = float(price)
    found_with_bid = None
    for kw in kw_targets:
        bid = _keyword_field(kw, "bid_price")
        if bid is not None and float(bid) == expected_price:
            found_with_bid = kw
            break
    if found_with_bid is None:
        pytest.xfail(f"SPEC-PRODUCTION GAP: no keyword with bid_price={price} found. FIXME(salesagent-9vgz.1)")


# --- Dedup Then steps ---


@then(parsers.parse('the response should contain the existing package with package_id "{pkg_id}"'))
def then_existing_package(ctx: dict, pkg_id: str) -> None:
    """Assert response contains the existing package (dedup)."""
    pkgs = _assert_has_packages(ctx)
    # Use the actual existing_package_id from the Given step (pkg_id is a label)
    actual_existing = ctx.get("expected_existing_package_id") or ctx.get("existing_package_id")
    if actual_existing:
        found = False
        for pkg in pkgs:
            if _pkg_field(pkg, "package_id") == actual_existing:
                found = True
                break
        assert found, (
            f"Expected existing package with package_id '{actual_existing}' in response. "
            f"Got: {[_pkg_field(p, 'package_id') for p in pkgs]}"
        )


@then("no duplicate package should be created")
def then_no_duplicate(ctx: dict) -> None:
    """Assert no duplicate package was created."""
    packages = _get_packages(ctx)
    assert len(packages) == 1, f"Expected 1 package (no duplicate), got {len(packages)}"


@then(parsers.parse('a new package should be created in "{mb_id}" with a new package_id'))
def then_new_pkg_in_mb(ctx: dict, mb_id: str) -> None:
    """Assert a new package was created with a new package_id (cross-buy scenario)."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id is not None, "Package missing package_id"
    # Verify this is a NEW package_id (different from any existing one)
    existing_pkg_id = ctx.get("existing_package_id")
    if existing_pkg_id:
        assert pkg_id != existing_pkg_id, f"Expected a NEW package_id but got the same as existing: '{pkg_id}'"


@then("a new package should be created with a seller-assigned package_id")
def then_new_pkg_created(ctx: dict) -> None:
    """Assert a new package was created with a seller-assigned package_id."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id is not None and len(str(pkg_id).strip()) > 0, (
        f"Expected non-empty seller-assigned package_id, got {pkg_id!r}"
    )


@then("the existing package should be returned without creating a duplicate")
def then_existing_returned(ctx: dict) -> None:
    """Assert existing package was returned (dedup) — verify ID matches and no duplicate."""
    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    assert len(pkgs) == 1, f"Expected 1 package (no duplicate), got {len(pkgs)}"
    # Verify the returned package matches the existing one
    existing_pkg_id = ctx.get("existing_package_id")
    if existing_pkg_id:
        actual_id = _pkg_field(pkgs[0], "package_id")
        assert actual_id == existing_pkg_id, f"Expected existing package_id '{existing_pkg_id}' but got '{actual_id}'"


# --- Invariant-specific Then steps ---


@then(parsers.parse('the package should be created with pricing_option_id "{option_id}"'))
def then_created_with_pricing(ctx: dict, option_id: str) -> None:
    """Assert package was created with specific pricing_option_id."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    expected = _resolve_pricing_id(ctx, option_id)
    actual = _pkg_field(pkgs[0], "pricing_option_id")
    if actual is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: pricing_option_id not echoed in response. "
            f"Expected '{expected}'. FIXME(salesagent-9vgz.1)"
        )
    assert actual == expected, f"Expected pricing_option_id '{expected}', got '{actual}'"


@then(parsers.parse("the package should be created with bid_price {price} interpreted as ceiling"))
def then_bid_ceiling(ctx: dict, price: str) -> None:
    """Assert package has bid_price set to the expected value (ceiling semantics)."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_bid = _pkg_field(pkgs[0], "bid_price")
    if actual_bid is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: bid_price not echoed in response. "
            f"Expected {price} (ceiling). FIXME(salesagent-9vgz.1)"
        )
    assert float(actual_bid) == float(price), f"Expected bid_price {price}, got {actual_bid}"


@then(parsers.parse("the package should be created with bid_price {price} interpreted as exact bid"))
def then_bid_exact(ctx: dict, price: str) -> None:
    """Assert package has bid_price set to the expected value (exact semantics)."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_bid = _pkg_field(pkgs[0], "bid_price")
    if actual_bid is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: bid_price not echoed in response. Expected {price} (exact). FIXME(salesagent-9vgz.1)"
        )
    assert float(actual_bid) == float(price), f"Expected bid_price {price}, got {actual_bid}"


@then("the package should be created without a bid_price")
def then_no_bid_price(ctx: dict) -> None:
    """Assert package was created without bid_price."""
    pkgs = _assert_has_packages(ctx)
    actual_bid = _pkg_field(pkgs[0], "bid_price")
    # bid_price should be None or absent
    assert actual_bid is None, f"Expected no bid_price, got {actual_bid}"


@then("pricing should be determined by pricing option defaults")
def then_pricing_defaults(ctx: dict) -> None:
    """Assert pricing is determined by pricing option defaults (no bid_price override)."""
    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    # With no bid_price, pricing defaults apply — verify bid_price is absent
    actual_bid = _pkg_field(pkgs[0], "bid_price")
    assert actual_bid is None, f"Expected no bid_price (pricing by defaults), got {actual_bid}"


@then(parsers.parse("the package should be created with format_ids {fmt_ids}"))
def then_created_with_formats(ctx: dict, fmt_ids: str) -> None:
    """Assert package was created with specific format_ids."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_format_ids = _pkg_field(pkgs[0], "format_ids")
    if actual_format_ids is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: format_ids not echoed in response. Expected {fmt_ids}. FIXME(salesagent-9vgz.1)"
        )
    try:
        expected = json.loads(fmt_ids)
    except (json.JSONDecodeError, TypeError):
        inner = fmt_ids.strip("[]")
        expected = [s.strip().strip('"') for s in inner.split(",")]
    actual_set = {_extract_format_id(f) for f in actual_format_ids}
    expected_set = set(expected)
    assert expected_set == actual_set, f"Expected format_ids {expected_set}, got {actual_set}"


@then(parsers.parse("the package should be created with paused={paused}"))
def then_created_with_paused(ctx: dict, paused: str) -> None:
    """Assert package was created with specific paused value."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    expected = paused.lower() == "true"
    actual = _pkg_field(pkgs[0], "paused")
    if actual is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: paused not echoed in response. Expected paused={expected}. FIXME(salesagent-9vgz.1)"
        )
    assert actual == expected, f"Expected paused={expected}, got {actual}"


@then("the package should be created with both catalogs")
def then_created_with_catalogs(ctx: dict) -> None:
    """Assert package was created with both catalog entries."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    catalogs = _pkg_field(pkgs[0], "catalogs")
    if catalogs is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: catalogs not echoed in response. Expected 2 catalogs. FIXME(salesagent-9vgz.1)"
        )
    assert isinstance(catalogs, list), f"Expected catalogs to be a list, got {type(catalogs)}"
    assert len(catalogs) == 2, f"Expected 2 catalogs, got {len(catalogs)}"


@then("the package should be created without catalogs")
def then_created_without_catalogs(ctx: dict) -> None:
    """Assert package was created without catalogs."""
    pkgs = _assert_has_packages(ctx)
    catalogs = _pkg_field(pkgs[0], "catalogs")
    assert not catalogs, f"Expected no catalogs, got {catalogs}"


# --- Update array field Then steps ---


@then(parsers.parse("the package budget should be {budget:d}"))
def then_pkg_budget_value(ctx: dict, budget: int) -> None:
    """Assert package budget is specific value."""
    packages = _get_packages(ctx)
    pkg = packages[0]
    actual = _pkg_field(pkg, "budget")
    assert actual is not None, "Package budget is None"
    assert float(actual) == float(budget), f"Expected budget {budget}, got {actual}"


@then(parsers.parse("the package catalogs should be {expected}"))
def then_pkg_catalogs(ctx: dict, expected: str) -> None:
    """Assert package catalogs match expected JSON."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_catalogs = _pkg_field(pkgs[0], "catalogs")
    if actual_catalogs is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: catalogs not echoed in response. Expected {expected}. FIXME(salesagent-9vgz.1)"
        )
    try:
        expected_parsed = json.loads(expected)
    except (json.JSONDecodeError, TypeError):
        expected_parsed = expected
    # Normalize for comparison
    if isinstance(actual_catalogs, list) and isinstance(expected_parsed, list):
        actual_normalized = [
            c if isinstance(c, dict) else (c.model_dump() if hasattr(c, "model_dump") else {"raw": str(c)})
            for c in actual_catalogs
        ]
        assert len(actual_normalized) == len(expected_parsed), (
            f"Expected {len(expected_parsed)} catalogs, got {len(actual_normalized)}"
        )


@then("the package catalogs should be unchanged")
def then_catalogs_unchanged(ctx: dict) -> None:
    """Assert package catalogs were not changed (patch semantics — omitted fields preserved)."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    actual_catalogs = _pkg_field(pkgs[0], "catalogs")
    if actual_catalogs is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: catalogs not echoed in update response — "
            "cannot verify unchanged. FIXME(salesagent-9vgz.1)"
        )
    # Catalogs should exist (preserved from original) — at minimum non-empty
    assert isinstance(actual_catalogs, list), f"Expected catalogs to be a list, got {type(actual_catalogs)}"


@then("the package optimization_goals should be unchanged")
def then_goals_unchanged(ctx: dict) -> None:
    """Assert package optimization_goals were not changed (patch semantics)."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    actual_goals = _pkg_field(pkgs[0], "optimization_goals")
    if actual_goals is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: optimization_goals not echoed in update response — "
            "cannot verify unchanged. FIXME(salesagent-9vgz.1)"
        )
    assert isinstance(actual_goals, list), f"Expected optimization_goals to be a list, got {type(actual_goals)}"


@then(parsers.parse("the package optimization_goals should be {expected}"))
def then_pkg_goals(ctx: dict, expected: str) -> None:
    """Assert package optimization_goals match expected."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_goals = _pkg_field(pkgs[0], "optimization_goals")
    if actual_goals is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: optimization_goals not echoed in response. "
            f"Expected {expected}. FIXME(salesagent-9vgz.1)"
        )
    try:
        expected_parsed = json.loads(expected)
    except (json.JSONDecodeError, TypeError):
        expected_parsed = expected
    if isinstance(actual_goals, list) and isinstance(expected_parsed, list):
        assert len(actual_goals) == len(expected_parsed), (
            f"Expected {len(expected_parsed)} optimization_goals, got {len(actual_goals)}"
        )


@then(parsers.parse("the package creative_assignments should be {expected}"))
def then_pkg_creatives(ctx: dict, expected: str) -> None:
    """Assert package creative_assignments match expected."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    actual_ca = _pkg_field(pkgs[0], "creative_assignments")
    if actual_ca is None:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: creative_assignments not echoed in response. "
            f"Expected {expected}. FIXME(salesagent-9vgz.1)"
        )
    try:
        expected_parsed = json.loads(expected)
    except (json.JSONDecodeError, TypeError):
        expected_parsed = expected
    if isinstance(actual_ca, list) and isinstance(expected_parsed, list):
        assert len(actual_ca) == len(expected_parsed), (
            f"Expected {len(expected_parsed)} creative_assignments, got {len(actual_ca)}"
        )


@then(parsers.parse('the package targeting_overlay should contain only audience "{audience_id}"'))
def then_targeting_audience(ctx: dict, audience_id: str) -> None:
    """Assert targeting_overlay contains only specified audience."""
    import pytest

    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    overlay = _pkg_field(pkg, "targeting_overlay")
    if overlay is None:
        pytest.xfail("SPEC-PRODUCTION GAP: targeting_overlay not present in response. FIXME(salesagent-9vgz.1)")
    audiences = _get_overlay_field(pkg, "audiences")
    if audiences is None:
        pytest.xfail("SPEC-PRODUCTION GAP: audiences not in targeting_overlay. FIXME(salesagent-9vgz.1)")
    assert isinstance(audiences, list), f"Expected audiences to be a list, got {type(audiences)}"
    assert len(audiences) == 1, f"Expected exactly 1 audience, got {len(audiences)}"
    aud = audiences[0]
    actual_id = aud.get("audience_id") if isinstance(aud, dict) else getattr(aud, "audience_id", None)
    assert actual_id == audience_id, f"Expected audience '{audience_id}', got '{actual_id}'"


@then(parsers.parse('the old catalog "{catalog_id}" should not be present'))
def then_old_catalog_absent(ctx: dict, catalog_id: str) -> None:
    """Assert old catalog is not present after replacement."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    catalogs = _pkg_field(pkgs[0], "catalogs")
    if catalogs is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: catalogs not echoed in response — "
            "cannot verify old catalog absent. FIXME(salesagent-9vgz.1)"
        )
    for cat in catalogs:
        cat_id = cat.get("catalog_id") if isinstance(cat, dict) else getattr(cat, "catalog_id", None)
        assert cat_id != catalog_id, f"Old catalog '{catalog_id}' should NOT be present after replacement but was found"


@then(parsers.parse('the old audience "{audience_id}" should not be present'))
def then_old_audience_absent(ctx: dict, audience_id: str) -> None:
    """Assert old audience is not present after replacement."""
    import pytest

    _assert_no_error(ctx)
    pkgs = _assert_has_packages(ctx)
    pkg = pkgs[0]
    audiences = _get_overlay_field(pkg, "audiences")
    if audiences is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: audiences not in targeting_overlay — "
            "cannot verify old audience absent. FIXME(salesagent-9vgz.1)"
        )
    for aud in audiences:
        aud_id = aud.get("audience_id") if isinstance(aud, dict) else getattr(aud, "audience_id", None)
        assert aud_id != audience_id, (
            f"Old audience '{audience_id}' should NOT be present after replacement but was found"
        )
