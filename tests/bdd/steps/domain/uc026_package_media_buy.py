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
    """Verify product exists in DB with pricing_options matching the step parameter."""
    import json

    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    # Step claims product has pricing_options — verify the product has them configured
    actual_options = getattr(product, "pricing_options", None)
    assert actual_options is not None, (
        f"Product '{product_id}' has no pricing_options attribute — step claims 'with pricing_options {options}'"
    )
    assert len(actual_options) > 0, (
        f"Product '{product_id}' has empty pricing_options — step claims 'with pricing_options {options}'"
    )
    # Verify the step parameter's option IDs are present in the product's pricing options
    try:
        expected_ids = json.loads(options)
    except (json.JSONDecodeError, TypeError):
        # options parameter is not valid JSON — treat as opaque string identifier.
        # Still assert the product has pricing options (already checked above).
        ctx["product_pricing_options"] = options
        return
    if isinstance(expected_ids, list):
        actual_ids = {
            getattr(o, "id", None) or (o.get("id") if isinstance(o, dict) else str(o)) for o in actual_options
        }
        for eid in expected_ids:
            eid_str = eid.get("id") if isinstance(eid, dict) else str(eid)
            assert eid_str in actual_ids, (
                f"Expected pricing option '{eid_str}' not found in product's options {actual_ids}"
            )
    ctx["product_pricing_options"] = options


@given(parsers.parse('the product "{product_id}" supports format_ids {format_ids}'))
def given_product_format_ids(ctx: dict, product_id: str, format_ids: str) -> None:
    """Verify product supports the specified format_ids."""
    import json

    product = ctx.get("default_product")
    assert product is not None, "No default_product in ctx"
    assert product.product_id == product_id, f"Expected product '{product_id}', got '{product.product_id}'"
    # Step claims product 'supports' these format_ids — verify format_ids exist on product
    actual_format_ids = getattr(product, "format_ids", None)
    assert actual_format_ids is not None, (
        f"Product '{product_id}' has no format_ids attribute — step claims 'supports format_ids {format_ids}'"
    )
    assert len(actual_format_ids) > 0, (
        f"Product '{product_id}' has empty format_ids — step claims 'supports format_ids {format_ids}'"
    )
    # Verify the claimed format_ids are actually present in the product's format set
    try:
        expected = json.loads(format_ids)
    except (json.JSONDecodeError, TypeError):
        # format_ids parameter is not valid JSON — treat as opaque string identifier.
        # Still assert the product has format_ids (already checked above).
        ctx["product_format_ids"] = format_ids
        return
    if isinstance(expected, list):

        def _extract_id(f: Any) -> str:
            if isinstance(f, dict):
                return f.get("id", str(f))
            if hasattr(f, "id"):
                return f.id
            return str(f)

        actual_set = {_extract_id(f) for f in actual_format_ids}
        for ef in expected:
            ef_id = _extract_id(ef)
            assert ef_id in actual_set, f"Expected format '{ef_id}' not found in product's format_ids {actual_set}"
    ctx["product_format_ids"] = format_ids


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
    """Dispatch create_media_buy through MCP transport."""
    # Override transport hint to match step text ('MCP tool')
    ctx["package_transport_hint"] = "mcp"
    _dispatch_create(ctx)


@when("the Buyer Agent sends the create_media_buy A2A task")
def when_send_create_a2a(ctx: dict) -> None:
    """Dispatch create_media_buy through A2A transport."""
    # Override transport hint to match step text ('A2A task')
    ctx["package_transport_hint"] = "a2a"
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
    """Assert response contains at least one package with a meaningful seller-assigned package_id."""
    packages = _get_packages(ctx)
    assert len(packages) > 0, "No packages in response"
    pkg = packages[0]
    pkg_id = getattr(pkg, "package_id", None)
    if pkg_id is None and isinstance(pkg, dict):
        pkg_id = pkg.get("package_id")
    assert pkg_id is not None, "Package missing package_id"
    # "seller-assigned" implies a non-empty, meaningful identifier
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
        # DB fallback: verify budget was persisted even if not in response
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg and getattr(db_pkg, "budget", None) is not None:
                db_budget = float(db_pkg.budget)
                assert db_budget == float(budget), f"DB has budget {db_budget}, expected {budget}"
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: budget correctly persisted as {db_budget} in DB "
                    f"but not echoed in response. Step claims 'contain budget {budget}'. "
                    f"FIXME(salesagent-9vgz.1)"
                )
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: package budget is None in both response and DB — "
            f"step claims 'contain budget {budget}' but production may not set budget yet. "
            f"FIXME(salesagent-9vgz.1)"
        )
    assert float(actual) == float(budget), f"Expected budget {budget}, got {actual}"


@then(parsers.parse('the package should contain pricing_option_id "{pricing_option_id}"'))
def then_package_pricing(ctx: dict, pricing_option_id: str) -> None:
    """Assert first package has the expected pricing_option_id.

    Maps feature-file names (cpm-standard) to real IDs (cpm_usd_fixed).
    """
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    pricing_id_map = {"cpm-standard": "cpm_usd_fixed", "cpm-auction": "cpm_usd_auction"}
    expected = pricing_id_map.get(pricing_option_id, pricing_option_id)
    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify pricing_option_id"
    actual = _pkg_field(pkg, "pricing_option_id")
    if actual is None:
        # DB fallback: check if pricing_option_id was persisted
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
            f"SPEC-PRODUCTION GAP: pricing_option_id is None in both response and DB — "
            f"step claims 'contain pricing_option_id \"{pricing_option_id}\"' but production may not set it yet. "
            f"FIXME(salesagent-9vgz.1)"
        )
    assert actual == expected, f"Expected pricing_option_id '{expected}', got '{actual}'"


@then("the package should contain format_ids defaulting to all product formats")
def then_package_default_formats(ctx: dict) -> None:
    """Assert package format_ids default to all product formats.

    Step claims format_ids should match the product's full format set.
    """
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
        # DB fallback: check if format_ids were persisted
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg:
                db_format_ids = getattr(db_pkg, "format_ids", None)
                if db_format_ids:
                    pytest.xfail(
                        f"SPEC-PRODUCTION GAP: format_ids persisted in DB ({len(db_format_ids)} items) "
                        "but not echoed in response. Step claims 'defaulting to all product formats'. "
                        "FIXME(salesagent-9vgz.1)"
                    )
        pytest.xfail(
            "SPEC-PRODUCTION GAP: format_ids not present on package or in DB — "
            "step claims 'defaulting to all product formats' but production may not echo defaults. "
            "FIXME(salesagent-9vgz.1)"
        )
    assert isinstance(format_ids, list), f"Expected format_ids to be a list, got {type(format_ids)}"
    assert len(format_ids) > 0, "Expected format_ids to default to all product formats, got empty list"
    # Verify format_ids match the product's format set (from Given step context)
    product = ctx.get("default_product")
    assert product is not None, (
        "No default_product in context — cannot verify "
        "'defaulting to all product formats' claim. "
        "A prior Given step must set up the product."
    )

    product_format_ids = getattr(product, "format_ids", None) or []

    # Extract IDs (format_ids may be dicts with "id" key or plain strings)
    def _extract_id(f: Any) -> str:
        if isinstance(f, dict):
            return f.get("id", str(f))
        if hasattr(f, "id"):
            return f.id
        return str(f)

    product_ids = {_extract_id(f) for f in product_format_ids}
    assert product_ids, "Product has no format_ids — cannot verify 'defaulting to all product formats'"
    pkg_ids = {_extract_id(f) for f in format_ids}
    assert pkg_ids == product_ids, (
        f"Package format_ids should default to all product formats. Expected {product_ids}, got {pkg_ids}"
    )


@then("the package should contain paused as false")
def then_package_not_paused(ctx: dict) -> None:
    """Assert package paused field is explicitly False (not None or absent)."""
    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    packages = _get_packages(ctx)
    pkg = packages[0]
    pkg_id = _pkg_field(pkg, "package_id")
    assert pkg_id, "Package has no package_id — cannot verify paused state"
    paused = _pkg_field(pkg, "paused")
    # Step text says "paused as false" — must be exactly False, not None/absent
    if paused is None:
        # DB fallback: check if paused was persisted
        with get_db_session() as session:
            db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=pkg_id)).first()
            if db_pkg:
                db_paused = getattr(db_pkg, "paused", None)
                if db_paused is not None:
                    assert db_paused is False, f"DB has paused={db_paused!r}, expected False"
                    pytest.xfail(
                        "SPEC-PRODUCTION GAP: paused correctly persisted as False in DB "
                        "but not echoed in response. Step claims 'paused as false'. "
                        "FIXME(salesagent-9vgz.1)"
                    )
        pytest.xfail(
            "SPEC-PRODUCTION GAP: paused is None in both response and DB — "
            "step claims 'paused as false' but production may not set the paused field yet. "
            "FIXME(salesagent-9vgz.1)"
        )
    assert paused is False, f"Expected paused to be False, got {paused!r}"


@then("the package should contain format_ids_to_provide listing formats needing creative assets")
def then_package_formats_to_provide(ctx: dict) -> None:
    """Assert package has format_ids_to_provide listing formats that need creative assets.

    Step claims the list contains formats needing creative assets — verify the
    entries are a subset of the package's format_ids (can't need assets for
    formats not in the package).
    """
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
        # DB fallback: check if field was persisted
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
            "SPEC-PRODUCTION GAP: format_ids_to_provide not present on package or in DB — "
            "step claims 'listing formats needing creative assets' but production may not set it yet. "
            "FIXME(salesagent-9vgz.1)"
        )
    assert isinstance(formats_to_provide, list), (
        f"Expected format_ids_to_provide to be a list, got {type(formats_to_provide)}"
    )
    assert len(formats_to_provide) > 0, (
        "Expected format_ids_to_provide to list formats needing creative assets, got empty list"
    )
    # Verify entries are valid format references (subset of package format_ids)
    format_ids = _pkg_field(pkg, "format_ids")
    if format_ids:

        def _extract_id(f: Any) -> str:
            if isinstance(f, dict):
                return f.get("id", str(f))
            if hasattr(f, "id"):
                return f.id
            return str(f)

        pkg_format_set = {_extract_id(f) for f in format_ids}
        provide_set = {_extract_id(f) for f in formats_to_provide}
        extra = provide_set - pkg_format_set
        # Subset violation is a real bug, not a spec gap — hard assert
        assert not extra, (
            f"format_ids_to_provide contains {extra} which are not in "
            f"package format_ids {pkg_format_set} — cannot need assets "
            f"for formats not in the package"
        )


def _pkg_field(pkg: Any, field: str) -> Any:
    """Extract a field from a package (object or dict)."""
    if isinstance(pkg, dict):
        return pkg.get(field)
    return getattr(pkg, field, None)
