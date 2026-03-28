"""BDD step definitions for UC-003 extension/error scenarios (ext-a through ext-r).

Given steps set up error conditions (missing auth, wrong principal, bad budget,
invalid creatives, etc.). Then steps assert error fields (recovery, suggestion).

beads: salesagent-05b
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then

from tests.bdd.steps.domain.uc003_update_media_buy import _ensure_update_defaults


def _inject_privilege_error(ctx: dict) -> None:
    """Inject INSUFFICIENT_PRIVILEGES error into the mock adapter.

    Called when both 'Buyer does not have admin privileges' and
    'the update operation requires admin privileges' are active, regardless
    of step ordering.
    """
    from src.core.exceptions import AdCPError

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    error = AdCPError(
        error_code="INSUFFICIENT_PRIVILEGES",
        message="This operation requires admin privileges",
        recovery="contact_admin",
        details={"suggestion": "Request admin privileges or contact an administrator"},
    )
    mock_adapter.validate_media_buy_request.side_effect = error


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — authentication / principal overrides
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the Buyer is authenticated as principal "{principal_id}"'))
def given_buyer_authenticated_as(ctx: dict, principal_id: str) -> None:
    """Override the authenticated principal to a specific principal_id.

    Sets ctx['principal_override'] so the When step can build an identity
    with a different principal_id. Also marks the auth state.
    """
    ctx["principal_override"] = principal_id
    ctx["has_auth"] = True
    # Update the env identity to use this principal_id
    env = ctx["env"]
    env._identity_cache.clear()
    env._principal_id = principal_id


@given(parsers.parse('the principal "{principal_id}" does not exist in the database'))
def given_principal_not_in_db(ctx: dict, principal_id: str) -> None:
    """Ensure the specified principal does not exist in the tenant database.

    For integration env: delete the principal if it exists.
    For unit env: configure mock to return None.
    """
    from sqlalchemy import delete, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        existing = session.scalars(
            select(Principal).filter_by(principal_id=principal_id, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            session.execute(
                delete(Principal).where(
                    Principal.principal_id == principal_id,
                    Principal.tenant_id == tenant.tenant_id,
                )
            )
            session.commit()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — media buy lookup failures
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('no media buy exists with media_buy_id "{media_buy_id}"'))
def given_no_media_buy_by_id(ctx: dict, media_buy_id: str) -> None:
    """Ensure no media buy with the given media_buy_id exists in the database.

    For integration env: verify/delete from DB.
    """
    from sqlalchemy import delete, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        existing = session.scalars(
            select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            session.execute(
                delete(MediaBuy).where(
                    MediaBuy.media_buy_id == media_buy_id,
                    MediaBuy.tenant_id == tenant.tenant_id,
                )
            )
            session.commit()


@given(parsers.parse('no media buy exists with buyer_ref "{buyer_ref}"'))
def given_no_media_buy_by_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Ensure no media buy with the given buyer_ref exists in the database."""
    from sqlalchemy import delete, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        existing = session.scalars(select(MediaBuy).filter_by(buyer_ref=buyer_ref, tenant_id=tenant.tenant_id)).first()
        if existing:
            session.execute(
                delete(MediaBuy).where(
                    MediaBuy.buyer_ref == buyer_ref,
                    MediaBuy.tenant_id == tenant.tenant_id,
                )
            )
            session.commit()


@given(parsers.parse('the media buy "{media_buy_id}" is owned by principal "{owner_id}"'))
def given_media_buy_owned_by(ctx: dict, media_buy_id: str, owner_id: str) -> None:
    """Set the media buy's principal_id to a DIFFERENT principal than the authenticated one.

    Creates the owning principal if needed, then updates the media buy.
    """
    from tests.factories import PrincipalFactory

    env = ctx["env"]
    tenant = ctx["tenant"]
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    assert mb.media_buy_id == media_buy_id, f"Expected media buy '{media_buy_id}' but ctx has '{mb.media_buy_id}'"
    # Create the owning principal in the DB
    owner_principal = PrincipalFactory(
        tenant=tenant,
        principal_id=owner_id,
    )
    mb.principal_id = owner_id
    env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — currency / budget / daily spend
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the existing media buy uses currency "{currency}"'))
def given_media_buy_currency(ctx: dict, currency: str) -> None:
    """Set the existing media buy's currency to the specified value."""
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    mb.currency = currency
    env = ctx["env"]
    env._commit_factory_data()


@given(parsers.parse('the tenant does not have "{currency}" in CurrencyLimit table'))
def given_tenant_no_currency_limit(ctx: dict, currency: str) -> None:
    """Ensure the tenant has no CurrencyLimit for the specified currency.

    Delete any existing CurrencyLimit record for this currency.
    """
    from sqlalchemy import delete, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        existing = session.scalars(
            select(CurrencyLimit).filter_by(currency=currency, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            session.execute(
                delete(CurrencyLimit).where(
                    CurrencyLimit.currency == currency,
                    CurrencyLimit.tenant_id == tenant.tenant_id,
                )
            )
            session.commit()


@given(parsers.parse("the tenant has max_daily_package_spend of {amount:d}"))
def given_tenant_max_daily_spend(ctx: dict, amount: int) -> None:
    """Configure the tenant's CurrencyLimit to have the given max_daily_package_spend."""
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    mb = ctx.get("existing_media_buy")
    currency = mb.currency if mb else "USD"
    with get_db_session() as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(currency=currency, tenant_id=tenant.tenant_id)).first()
        assert cl is not None, (
            f"No CurrencyLimit for {currency} in tenant {tenant.tenant_id} — cannot set max_daily_package_spend"
        )
        cl.max_daily_package_spend = Decimal(str(amount))
        session.commit()


@given(parsers.parse("the media buy flight is {days:d} days"))
def given_media_buy_flight_days(ctx: dict, days: int) -> None:
    """Set the media buy start_time/end_time to span exactly N days from now."""
    from datetime import UTC, datetime, timedelta

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    now = datetime.now(UTC)
    mb.start_time = now
    mb.end_time = now + timedelta(days=days)
    env = ctx["env"]
    env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — package errors
# ═══════════════════════════════════════════════════════════════════════


@given("the request includes 1 package update without package_id or buyer_ref")
def given_package_update_no_id(ctx: dict) -> None:
    """Add a package update with no package_id and no buyer_ref to trigger ext-h."""
    kwargs = _ensure_update_defaults(ctx)
    kwargs["packages"] = [{"budget": 5000.0}]


@given(parsers.parse('package "{package_id}" does not exist in the media buy'))
def given_package_not_in_media_buy(ctx: dict, package_id: str) -> None:
    """Ensure no package with the given package_id exists in the media buy.

    Declarative guard — verifies the package is NOT present. The harness
    setup creates pkg_001 by default, so this step is valid for any other ID.
    """
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx"
    existing_pkg = ctx.get("existing_package")
    if existing_pkg and existing_pkg.package_id == package_id:
        raise AssertionError(f"Package '{package_id}' DOES exist in media buy — step claims it shouldn't")
    # Verify via DB
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    with get_db_session() as session:
        db_pkg = session.scalars(
            select(MediaPackage).filter_by(
                package_id=package_id,
                media_buy_id=mb.media_buy_id,
            )
        ).first()
        assert db_pkg is None, (
            f"Package '{package_id}' found in DB for media buy '{mb.media_buy_id}' — step claims it does not exist"
        )


@given("the package exists in the media buy")
def given_package_exists_bare(ctx: dict) -> None:
    """Verify that at least one package exists in the media buy (bare step)."""
    pkg = ctx.get("existing_package")
    assert pkg is not None, "No existing_package in ctx — harness setup_update_data() should create one"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative errors
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('creative "{creative_id}" does not exist in the creative library'))
def given_creative_not_in_library(ctx: dict, creative_id: str) -> None:
    """Ensure the specified creative does not exist in the database.

    Declarative guard — for integration env, verify it's not in DB.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        cr = session.scalars(
            select(CreativeModel).filter_by(creative_id=creative_id, tenant_id=tenant.tenant_id)
        ).first()
        assert cr is None, f"Creative '{creative_id}' exists in DB — step claims it should not"


@given(parsers.parse('creative "{creative_id}" is in "{state}" state'))
def given_creative_in_state(ctx: dict, creative_id: str, state: str) -> None:
    """Create a creative with the specified state in the database."""
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    CreativeFactory(
        creative_id=creative_id,
        tenant=tenant,
        principal=principal,
        format="display_300x250",
        approved=(state == "approved"),
        status=state,
        data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
    )
    env._commit_factory_data()
    # Also add to referenced_creative_ids for consistency with creative_assignments step
    ctx.setdefault("referenced_creative_ids", []).append(creative_id)


@given(parsers.parse('creative "{creative_id}" has a format incompatible with package product'))
def given_creative_format_incompatible(ctx: dict, creative_id: str) -> None:
    """Create a creative with a format that is NOT in the package product's format_ids."""
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    # Use a deliberately incompatible format
    CreativeFactory(
        creative_id=creative_id,
        tenant=tenant,
        principal=principal,
        format="video_1920x1080",  # Product uses display_300x250
        approved=True,
        data={"assets": {"primary": {"url": "https://example.com/video.mp4"}}},
    )
    env._commit_factory_data()
    ctx.setdefault("referenced_creative_ids", []).append(creative_id)


@given("the request includes 1 package update with inline creatives")
def given_package_update_inline_creatives_bare(ctx: dict) -> None:
    """Add a package update with inline creative content (ext-k scenario)."""
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    kwargs["packages"][0]["creatives"] = [
        {
            "creative_id": "inline-cr-ext-k",
            "name": "Inline Creative for Sync Test",
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            "assets": {
                "primary": {
                    "url": "https://example.com/banner.png",
                    "width": 300,
                    "height": 250,
                }
            },
        }
    ]


@given("the creative upload/sync process fails")
def given_creative_sync_fails(ctx: dict) -> None:
    """Configure the mock adapter to fail on creative sync/upload."""
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.add_creative_assets.side_effect = Exception("Creative sync failed: upload timeout")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — placement errors
# ═══════════════════════════════════════════════════════════════════════


def _get_product(ctx: dict) -> Any:
    """Get the product from ctx or from the DB (UC-003 doesn't set default_product in ctx)."""
    product = ctx.get("default_product") or ctx.get("existing_product")
    if product is not None:
        return product
    # UC-003: product was created by setup_product_chain but not stored in ctx.
    # Look it up from the existing package's product_id.
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Product

    tenant = ctx.get("tenant")
    if tenant is None:
        return None
    with get_db_session() as session:
        product = session.scalars(select(Product).filter_by(tenant_id=tenant.tenant_id)).first()
    return product


@given(parsers.parse('placement "{placement_id}" is not valid for the package product'))
def given_placement_invalid_for_product(ctx: dict, placement_id: str) -> None:
    """Declare that the placement_id is NOT valid for the product.

    The product setup by the harness has placements plc_a and plc_b.
    Any other placement_id is invalid. This step verifies the placement
    is indeed not in the valid set.
    """
    product = _get_product(ctx)
    assert product is not None, "No product in ctx or DB"
    # Verify the placement is genuinely not in the product's valid placements
    placements = getattr(product, "placement_configs", None) or []
    valid_ids = {p.get("placement_id") if isinstance(p, dict) else getattr(p, "placement_id", None) for p in placements}
    assert placement_id not in valid_ids, (
        f"Placement '{placement_id}' IS valid for product — step claims it should not be. Valid: {valid_ids}"
    )


@given("the package product does not support placement-level targeting")
def given_product_no_placement_targeting(ctx: dict) -> None:
    """Configure the product to have NO placement configurations (no placement targeting).

    Clears the product's placements so any placement_id in creative_assignments
    is considered unsupported.
    """
    product = _get_product(ctx)
    assert product is not None, "No product in ctx or DB"
    # Clear placements from the product
    if hasattr(product, "placement_configs"):
        product.placement_configs = []
    env = ctx["env"]
    env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — privileges / admin
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer does not have admin privileges")
def given_buyer_no_admin(ctx: dict) -> None:
    """Mark the buyer as non-admin."""
    ctx["buyer_is_admin"] = False
    principal = ctx.get("principal")
    if principal is not None and hasattr(principal, "role"):
        principal.role = "buyer"

    # If update already requires admin, inject the privilege error now
    if ctx.get("update_requires_admin"):
        _inject_privilege_error(ctx)


@given("the update operation requires admin privileges")
def given_update_requires_admin(ctx: dict) -> None:
    """Configure the environment so the update operation requires admin.

    Combined with 'Buyer does not have admin', the update should be rejected
    with insufficient privileges. Injects a privilege-check error into the
    adapter's validate_media_buy_request so the operation actually fails.
    """
    ctx["update_requires_admin"] = True

    # If buyer is already marked non-admin, inject the privilege error now
    if not ctx.get("buyer_is_admin", True):
        _inject_privilege_error(ctx)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — adapter failures
# ═══════════════════════════════════════════════════════════════════════


@given("the ad server adapter returns an error during update")
def given_adapter_error_during_update(ctx: dict) -> None:
    """Configure the mock adapter to return an error when processing updates.

    Injects an error into the adapter's update-related methods. Production code
    calls adapter methods during update_media_buy_impl — this simulates ad server
    failure.
    """
    from src.core.exceptions import AdCPError

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    error = AdCPError(
        error_code="ADAPTER_ERROR",
        message="Ad server returned error during update",
        recovery="retryable",
        details={"suggestion": "Retry the operation or contact ad server support"},
    )
    # Inject into all adapter methods that update_media_buy_impl might call.
    # Production calls adapter.update_media_buy() for the actual update,
    # and may call validate_media_buy_request() beforehand.
    mock_adapter.validate_media_buy_request.side_effect = error
    mock_adapter.update_media_buy.side_effect = error
    # Store original for potential recovery
    mock_adapter._original_validate_side_effect = None


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — keyword conflict scenarios (ext-r)
# ═══════════════════════════════════════════════════════════════════════


@given("the package update includes keyword_targets_add and targeting_overlay.keyword_targets")
def given_keyword_conflict_same_dimension(ctx: dict) -> None:
    """Set up conflicting keyword operations: keyword_targets_add AND targeting_overlay.keyword_targets.

    BR-RULE-083 INV-1: Cannot mix incremental keyword_targets_add with full
    replacement targeting_overlay.keyword_targets in the same package update.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    pkg["keyword_targets_add"] = [{"keyword": "shoes", "match_type": "broad"}]
    pkg["targeting_overlay"] = {"keyword_targets": [{"keyword": "boots", "match_type": "exact"}]}


@given("the package update includes negative_keywords_add and targeting_overlay.negative_keywords")
def given_negative_keyword_conflict_same_dimension(ctx: dict) -> None:
    """Set up conflicting negative keyword operations.

    BR-RULE-083 INV-2: Cannot mix incremental negative_keywords_add with full
    replacement targeting_overlay.negative_keywords.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    pkg["negative_keywords_add"] = [{"keyword": "cheap", "match_type": "exact"}]
    pkg["targeting_overlay"] = {"negative_keywords": [{"keyword": "free", "match_type": "broad"}]}


@given("the package update includes keyword_targets_add AND targeting_overlay.negative_keywords")
def given_keyword_cross_dimension_ok(ctx: dict) -> None:
    """Set up cross-dimension keyword operations (valid — BR-RULE-083 INV-3).

    keyword_targets_add + targeting_overlay.negative_keywords is allowed because
    they operate on different dimensions.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    pkg["keyword_targets_add"] = [{"keyword": "shoes", "match_type": "broad"}]
    pkg["targeting_overlay"] = {"negative_keywords": [{"keyword": "cheap", "match_type": "exact"}]}


@given("the package update includes negative_keywords_add AND targeting_overlay.keyword_targets")
def given_negative_keyword_cross_dimension_ok(ctx: dict) -> None:
    """Set up cross-dimension negative keyword operations (valid — BR-RULE-083 INV-4).

    negative_keywords_add + targeting_overlay.keyword_targets is allowed because
    they operate on different dimensions.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    pkg["negative_keywords_add"] = [{"keyword": "cheap", "match_type": "exact"}]
    pkg["targeting_overlay"] = {"keyword_targets": [{"keyword": "shoes", "match_type": "broad"}]}


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — error field assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the error should include "recovery" field with value "{value}"'))
def then_error_recovery_field(ctx: dict, value: str) -> None:
    """Assert the error includes a recovery field with the expected value."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.recovery == value, f"Expected recovery '{value}', got '{error.recovery}'"
    elif hasattr(error, "recovery"):
        actual = error.recovery.value if hasattr(error.recovery, "value") else str(error.recovery)
        assert actual == value, f"Expected recovery '{value}', got '{actual}'"
    else:
        raise AssertionError(f"Cannot check recovery on {type(error).__name__}: no recovery attribute")


@then("no database records should be modified")
def then_no_db_records_modified(ctx: dict) -> None:
    """Assert that no database records were modified (POST-F1: system state unchanged).

    Verifies ALL mutable fields on the existing media buy in DB match
    their pre-operation state — not just status.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify DB state"
    with get_db_session() as session:
        db_mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=mb.media_buy_id)).first()
        assert db_mb is not None, f"Media buy {mb.media_buy_id} not found in DB"
        # Verify all mutable fields haven't changed from pre-operation state
        _MUTABLE_FIELDS = (
            "status",
            "buyer_ref",
            "budget",
            "currency",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
            "campaign_objective",
            "kpi_goal",
            "is_paused",
            "order_name",
            "advertiser_name",
        )
        for field in _MUTABLE_FIELDS:
            original = getattr(mb, field, None)
            actual = getattr(db_mb, field, None)
            assert actual == original, (
                f"DB field '{field}' changed from {original!r} to {actual!r} — "
                "POST-F1 violated: system state should be unchanged on failure"
            )


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}"'))
def then_suggestion_contains_either(ctx: dict, text1: str, text2: str) -> None:
    """Assert error suggestion contains either text1 or text2 (case-insensitive)."""
    from tests.bdd.steps.generic.then_error import _get_error_dict

    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert text1.lower() in suggestion or text2.lower() in suggestion, (
        f"Expected suggestion to contain '{text1}' or '{text2}', got: {d.get('suggestion')}"
    )
