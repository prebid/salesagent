"""BDD step definitions for UC-003 extension/error scenarios (ext-a through ext-r).

Given steps set up error conditions (missing auth, wrong principal, bad budget,
invalid creatives, etc.). Then steps assert error fields (recovery, suggestion).

beads: salesagent-05b
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then

from tests.bdd.steps._harness_db import db_session
from tests.bdd.steps.domain.uc003_update_media_buy import _ensure_update_defaults
from tests.bdd.steps.generic._auth import authenticate_env_as


def _inject_privilege_error(ctx: dict) -> None:
    """Arm the adapter to refuse an admin-only update with PERMISSION_DENIED.

    Storyboard BR-UC-003-ext-n grounds the privilege check at the ADAPTER
    (step 9b: "Adapter checks admin privilege requirement — operation requires
    admin, principal is not admin"), not at a Principal/buyer role in our DB
    (the AdCP buyer protocol has no principal-role concept — roles belong to the
    admin-UI ``User`` model). The pinned error-code enum @04f59d2d5 has no
    ``INSUFFICIENT_PRIVILEGES``; the canonical reconciliation is
    ``PERMISSION_DENIED`` (adcp-req BR-UC-003 impl-coverage), recovery
    correctable, with a buyer-facing "privileges" suggestion.

    So we arm the method production actually calls during update
    (``adapter.update_media_buy``) with the canonical rejection. This makes the
    test wire-ready: the instant production gates admin-only actions and lets
    the adapter rejection surface on the wire, the strict xfail in conftest
    (T-UC-003-ext-n) flips to a real PERMISSION_DENIED pass. Today production
    short-circuits the fields-less ext-n request through the empty-update path
    and never reaches the adapter — hence the documented production gap.
    """
    from src.core.exceptions import AdCPError

    env = ctx["env"]
    # MediaBuyDualEnv keys the UPDATE adapter under "update_adapter" (the create
    # adapter is "adapter" and is never used by the update path). Inject into the
    # update adapter's update_media_buy — the method production invokes during
    # the adapter execution step (media_buy_update.py:628/692/760) — NOT
    # validate_media_buy_request, which the update path never calls.
    mock_adapter = env.mock["update_adapter"].return_value
    # PERMISSION_DENIED is canonical (pinned enum @04f59d2d5, recovery
    # correctable) but no typed subclass models it, so synthesize the code.
    error = AdCPError.synthesize(
        "This operation requires admin privileges",
        error_code="PERMISSION_DENIED",
        recovery="correctable",
        details={"suggestion": "Request admin privileges or contact an administrator to perform this action"},
    )
    mock_adapter.update_media_buy.side_effect = error


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — authentication / principal overrides
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the Buyer is authenticated as principal "{principal_id}"'))
def given_buyer_authenticated_as(ctx: dict, principal_id: str) -> None:
    """Authenticate as *principal_id* and mark the auth state.

    Delegates the principal switch, the canonical ``ctx["principal_id"]``, and the
    identity post-condition to the shared ``authenticate_env_as`` helper; adds only
    the use-case-specific ``has_auth`` flag.

    NOTE: this module is currently dormant (see ``steps/generic/_auth.py``) — it is
    not registered in ``tests/bdd/conftest.py`` ``pytest_plugins`` and UC-003 is not
    wired into the BDD harness, so these steps do not execute (UC-003 update
    scenarios auto-xfail). Kept on the shared helper so it is correct when UC-003 is
    activated.
    """
    authenticate_env_as(ctx, principal_id)
    ctx["has_auth"] = True


@given(parsers.parse('the principal "{principal_id}" does not exist in the database'))
def given_principal_not_in_db(ctx: dict, principal_id: str) -> None:
    """Ensure the specified principal does not exist in the tenant database.

    For integration env: delete the principal if it exists.
    For unit env: configure mock to return None.
    """
    from sqlalchemy import delete, select

    from src.core.database.models import Principal

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with db_session(ctx) as session:
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

    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with db_session(ctx) as session:
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

    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with db_session(ctx) as session:
        existing = session.scalars(
            select(CurrencyLimit).filter_by(currency_code=currency, tenant_id=tenant.tenant_id)
        ).first()
        if existing:
            session.execute(
                delete(CurrencyLimit).where(
                    CurrencyLimit.currency_code == currency,
                    CurrencyLimit.tenant_id == tenant.tenant_id,
                )
            )
            session.commit()


@given(parsers.parse("the tenant has max_daily_package_spend of {amount:d}"))
def given_tenant_max_daily_spend(ctx: dict, amount: int) -> None:
    """Configure the tenant's CurrencyLimit to have the given max_daily_package_spend."""
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    mb = ctx.get("existing_media_buy")
    currency = mb.currency if mb else "USD"
    with db_session(ctx) as session:
        cl = session.scalars(
            select(CurrencyLimit).filter_by(currency_code=currency, tenant_id=tenant.tenant_id)
        ).first()
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


@given("the request includes 1 package update without package_id")
def given_package_update_no_id(ctx: dict) -> None:
    """Add a package update with no package_id to trigger ext-h (missing identifier)."""
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

    from src.core.database.models import MediaPackage

    with db_session(ctx) as session:
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
    # Step says "in the media buy" — verify the package is actually linked to it
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify package linkage"
    assert pkg.media_buy_id == mb.media_buy_id, (
        f"Package '{pkg.package_id}' belongs to media_buy '{pkg.media_buy_id}', "
        f"not the current media buy '{mb.media_buy_id}'"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative errors
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('creative "{creative_id}" does not exist in the creative library'))
def given_creative_not_in_library(ctx: dict, creative_id: str) -> None:
    """Ensure the specified creative does not exist in the database.

    Declarative guard — for integration env, verify it's not in DB.
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with db_session(ctx) as session:
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

    # Dynamically determine an incompatible format by reading the product's actual format_ids
    product = _get_product(ctx)
    product_format_ids: set[str] = set()
    if product is not None:
        raw_formats = getattr(product, "format_ids", None) or []
        product_format_ids = {
            f.get("id", str(f)) if isinstance(f, dict) else getattr(f, "id", str(f)) for f in raw_formats
        }

    # Pick a format guaranteed not to be in the product's format_ids
    incompatible_candidates = [
        "video_1920x1080",
        "audio_podcast_30s",
        "native_in_feed_900x600",
        "display_interstitial_320x480",
    ]
    incompatible_format = next(
        (fmt for fmt in incompatible_candidates if fmt not in product_format_ids),
        f"deliberately_incompatible_{id(ctx)}",
    )

    CreativeFactory(
        creative_id=creative_id,
        tenant=tenant,
        principal=principal,
        format=incompatible_format,
        approved=True,
        data={"assets": {"primary": {"url": "https://example.com/video.mp4"}}},
    )
    env._commit_factory_data()
    ctx.setdefault("referenced_creative_ids", []).append(creative_id)


@given("the request includes 1 package update with inline creatives")
def given_package_update_inline_creatives_bare(ctx: dict) -> None:
    """Add a package update with a VALID inline creative (ext-k scenario).

    Uses the canonical AssetSpec factory (``build_assets`` / ``image_spec``) so the
    asset carries its ``asset_type`` discriminator and the request parses cleanly —
    the scenario must reach the adapter creative-sync step (where the upload is
    configured to fail), NOT be rejected at request-validation time for a malformed
    asset map.
    """
    from tests.factories.creative_asset import build_assets, image_spec

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
            "assets": build_assets(image_spec("primary")),
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

    from src.core.database.models import Product

    tenant = ctx.get("tenant")
    if tenant is None:
        return None
    with db_session(ctx) as session:
        product = session.scalars(select(Product).filter_by(tenant_id=tenant.tenant_id)).first()
    return product


def _ensure_referenced_creatives_valid(ctx: dict) -> None:
    """Create valid (approved, format-compatible) creatives for any referenced_creative_ids
    not already in the DB.

    Placement scenarios reference a creative (e.g. cr_001) but the generated feature
    does not include a creative-setup Given. Creative validation now runs before
    placement validation in the creative_assignments handler (CREATIVE_REJECTED), so
    without a valid creative the placement check is never reached. This seeds the
    referenced creatives with a format matching the package product so the scenario
    exercises placement validation as intended.
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel
    from tests.factories.creative import CreativeFactory

    creative_ids = ctx.get("referenced_creative_ids") or []
    if not creative_ids:
        return
    product = _get_product(ctx)
    fmt = "display_300x250"
    if product is not None and product.format_ids:
        first = product.format_ids[0]
        fmt = (first.get("id") or first.get("format_id") or fmt) if isinstance(first, dict) else fmt
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with db_session(ctx) as session:
        existing = {
            c.creative_id
            for c in session.scalars(
                select(CreativeModel)
                .filter_by(tenant_id=tenant.tenant_id)
                .where(CreativeModel.creative_id.in_(creative_ids))
            ).all()
        }
    for cid in creative_ids:
        if cid in existing:
            continue
        CreativeFactory(
            creative_id=cid,
            tenant=tenant,
            principal=principal,
            format=fmt,
            approved=True,
            status="approved",
            data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
        )
    ctx["env"]._commit_factory_data()


@given(parsers.parse('placement "{placement_id}" is not valid for the package product'))
def given_placement_invalid_for_product(ctx: dict, placement_id: str) -> None:
    """Declare that the placement_id is NOT valid for the product.

    Ensures the product defines placements plc_a and plc_b; any other
    placement_id is invalid. This step verifies the placement is indeed
    not in the valid set.
    """
    product = _get_product(ctx)
    assert product is not None, "No product in ctx or DB"
    # Ensure the product supports placement targeting with a known valid set, so the
    # requested placement_id exercises production's invalid-id branch
    # (media_buy_update.py:944) rather than the no-placements branch (:958). The model
    # column is `placements` (list of dicts with placement_id), not `placement_configs`.
    if not product.placements:
        product.placements = [
            {"placement_id": "plc_a", "name": "Placement A"},
            {"placement_id": "plc_b", "name": "Placement B"},
        ]
        ctx["env"]._commit_factory_data()
        product = _get_product(ctx)
    # Seed valid creatives so creative validation passes and placement validation is reached.
    _ensure_referenced_creatives_valid(ctx)
    valid_ids = {p.get("placement_id") for p in (product.placements or []) if isinstance(p, dict)}
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
    # The model column is `placements`; clearing it (None) means the product defines no
    # placements, so production treats placement-level targeting as unsupported
    # (media_buy_update.py:958 -> UNSUPPORTED_FEATURE).
    product.placements = None
    env = ctx["env"]
    env._commit_factory_data()
    # Seed valid creatives so creative validation passes and the unsupported-placement
    # check is reached.
    _ensure_referenced_creatives_valid(ctx)
    # Post-condition: verify placements were actually cleared
    reloaded = _get_product(ctx)
    cleared = reloaded.placements or []
    assert len(cleared) == 0, f"placements not cleared after commit — still has {len(cleared)} entries"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — privileges / admin
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer does not have admin privileges")
def given_buyer_no_admin(ctx: dict) -> None:
    """Mark the buyer as non-admin.

    The AdCP buyer protocol has NO principal-role concept: the ``Principal``
    ORM model (src/core/database/models.py:536) carries no ``role`` column —
    roles (admin/manager/viewer) belong to the admin-UI ``User`` model. The
    storyboard BR-UC-003-ext-n places the admin gate at the ADAPTER (e.g.
    activating guaranteed items in GAM requires an admin account), not on the
    buyer principal. So "non-admin buyer" is recorded as scenario intent in ctx
    and enforced via the adapter rejection (``_inject_privilege_error``), not by
    mutating a non-existent ``principal.role`` attribute.
    """
    ctx["buyer_is_admin"] = False

    # If update already requires admin, arm the adapter privilege error now
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

    # Post-condition: if both flags are set, verify injection actually happened.
    # This catches the case where step ordering left the env unconfigured.
    if ctx.get("update_requires_admin") and not ctx.get("buyer_is_admin", True):
        env = ctx["env"]
        mock_adapter = env.mock["update_adapter"].return_value
        assert mock_adapter.update_media_buy.side_effect is not None, (
            "Both 'update_requires_admin' and 'buyer_is_admin=False' are set, "
            "but update_adapter.update_media_buy.side_effect was not injected — "
            "the privilege error will not fire during the When step"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — mid-flight package addition (ext-u) + cancellation (ext-v)
#   #1417. Both are production gaps: update_media_buy never reads
#   new_packages or canceled, and has_updatable_fields() (schemas/_base.py)
#   omits both — so a request carrying only media_buy_id + one of them trips the
#   empty-update VALIDATION_ERROR path instead of UNSUPPORTED_FEATURE /
#   NOT_CANCELLABLE. These steps build the real request and dispatch it on the
#   wire; the strict xfail markers in conftest flip to passes when production
#   implements the mid-flight capability gate and state-based cancellation.
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the media buy "{mb_label}" is in "{status}" status'))
def given_named_media_buy_status(ctx: dict, mb_label: str, status: str) -> None:
    """Set the existing media buy (referenced by Gherkin label) to a status.

    Alias of the unquoted-label status step for scenarios that name the media
    buy explicitly (e.g. ext-v 'the media buy "mb_existing" is in "active"
    status'). The conftest Background already created the existing media buy;
    we mutate its status precondition.
    """
    mb = ctx.get("existing_media_buy")
    assert mb is not None, (
        f"No existing_media_buy in ctx — step names '{mb_label}' in '{status}' status "
        "but no media buy exists to set status on"
    )
    mb.status = status
    ctx["env"]._commit_factory_data()


@given("the request includes new_packages with one complete package-request")
def given_request_new_packages_one(ctx: dict) -> None:
    """Add one complete package-request to the update's new_packages list.

    new_packages IS a valid UpdateMediaBuyRequest field, but production never
    reads it and has_updatable_fields() omits it (production gap, ext-u).
    """
    kwargs = _ensure_update_defaults(ctx)
    product = ctx.get("default_product")
    product_id = product.product_id if product else "guaranteed_display"
    kwargs["new_packages"] = [
        {
            "product_id": product_id,
            "budget": 5000.0,
            "pricing_option_id": "cpm_usd_fixed",
        }
    ]


@given(parsers.parse('the media buy\'s valid_actions does NOT advertise "{action}"'))
def given_valid_actions_excludes(ctx: dict, action: str) -> None:
    """Record that the media buy's valid_actions does NOT advertise an action.

    BR-RULE-217 INV-1: new_packages on a seller not advertising add_packages
    must be rejected with UNSUPPORTED_FEATURE. Production has no such capability
    gate (it never reads new_packages), so this precondition is recorded for the
    wire-ready strict xfail. We assert the precondition is meaningful: the action
    must be a real valid-action name the gate would consult.
    """
    assert action, "valid_actions exclusion step requires a non-empty action name"
    ctx.setdefault("excluded_valid_actions", set()).add(action)


@given("the media buy has committed delivery that the seller cannot cancel mid-flight")
def given_media_buy_uncancellable(ctx: dict) -> None:
    """Mark the active media buy as carrying committed delivery + request cancel.

    BR-RULE-216 INV-4: a buy not cancellable in its current state must reject a
    cancel with NOT_CANCELLABLE. Production never reads canceled and has no
    state-based cancellation check (gap, ext-v). We arm the update adapter to
    refuse the cancel (the seller-side gate) and set canceled=true so the real
    cancellation path is exercised on the wire.
    """
    from src.core.exceptions import AdCPError

    kwargs = _ensure_update_defaults(ctx)
    kwargs["canceled"] = True
    ctx["uncancellable"] = True
    # Arm the seller-side refusal at the update adapter with the canonical code.
    env = ctx["env"]
    mock_adapter = env.mock["update_adapter"].return_value
    mock_adapter.update_media_buy.side_effect = AdCPError.synthesize(
        "Media buy cannot be canceled in its current state with committed delivery",
        error_code="NOT_CANCELLABLE",
        recovery="correctable",
        details={"suggestion": "Pause the buy instead (paused: true) or contact the seller to arrange cancellation"},
    )


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
    from src.core.exceptions import AdCPAdapterError

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    error = AdCPAdapterError(
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
    # Also write to DB so Docker adapter raises the same error
    tenant = ctx.get("tenant")
    if tenant is not None:
        from tests.factories.core import set_adapter_test_behavior

        set_adapter_test_behavior(
            env, tenant.tenant_id, fail_on_update=True, error_message="Ad server returned error during update"
        )


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

    Verifies ALL mutable fields on the existing MediaBuy AND its child
    MediaPackage records match their pre-operation state.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy, MediaPackage

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify DB state"
    existing_pkg = ctx.get("existing_package")

    with db_session(ctx) as session:
        # --- MediaBuy: verify all mutable fields unchanged ---
        db_mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=mb.media_buy_id)).first()
        assert db_mb is not None, f"Media buy {mb.media_buy_id} not found in DB"
        _MB_MUTABLE_FIELDS = (
            "status",
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
        for field in _MB_MUTABLE_FIELDS:
            original = getattr(mb, field, None)
            actual = getattr(db_mb, field, None)
            assert actual == original, (
                f"MediaBuy field '{field}' changed from {original!r} to {actual!r} — "
                "POST-F1 violated: system state should be unchanged on failure"
            )

        # --- MediaPackage: verify child records unchanged ---
        db_pkgs = session.scalars(select(MediaPackage).filter_by(media_buy_id=mb.media_buy_id)).all()

        if existing_pkg is not None:
            # Verify the specific package we know about hasn't changed
            original_pkg_id = existing_pkg.package_id
            db_pkg = next((p for p in db_pkgs if p.package_id == original_pkg_id), None)
            assert db_pkg is not None, (
                f"MediaPackage '{original_pkg_id}' disappeared from DB — POST-F1 violated: package deleted on failure"
            )
            _PKG_MUTABLE_FIELDS = ("budget", "bid_price", "pacing", "package_config")
            for field in _PKG_MUTABLE_FIELDS:
                original = getattr(existing_pkg, field, None)
                actual = getattr(db_pkg, field, None)
                assert actual == original, (
                    f"MediaPackage '{original_pkg_id}' field '{field}' changed "
                    f"from {original!r} to {actual!r} — "
                    "POST-F1 violated: package state should be unchanged on failure"
                )


@given(parsers.parse("the seller's minimum budget for this media buy is {amount:d} {currency}"))
def given_seller_minimum_budget(ctx: dict, amount: int, currency: str) -> None:
    """Configure the seller's minimum budget for the media buy under update.

    SPEC-PRODUCTION GAP: Production does not carry per-media-buy minimum
    budget metadata on the seller side. The v3.1 spec expects BUDGET_TOO_LOW
    errors to include structured details (minimum_budget, currency), but
    production validation uses CurrencyLimit.min_package_budget which does
    not populate error details with those fields.

    This step stores the expected values in ctx so downstream Then steps
    can assert on error details shape when the gap is closed.

    FIXME(salesagent-9vgz.1): Wire seller minimum budget to production
    validation and error details.
    """
    import pytest

    ctx["expected_min_budget"] = amount
    ctx["expected_min_budget_currency"] = currency
    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Seller minimum budget ({amount} {currency}) "
        "not carried in production. v3.1 BUDGET_TOO_LOW error details "
        "(minimum_budget, currency) not populated. FIXME(salesagent-9vgz.1)"
    )


@then(parsers.parse('the suggestion should contain "{text1}" or "{text2}"'))
def then_suggestion_contains_either(ctx: dict, text1: str, text2: str) -> None:
    """Assert error suggestion contains either text1 or text2 (case-insensitive).

    Wire-first (ztl6.6/ztl6.8): read the buyer-facing suggestion from the real
    wire envelope when the scenario dispatched through a transport, falling back
    to the reconstructed ``ctx['error']`` only for IMPL/no-wire — matching its
    wire-first sibling ``then_suggestion_contains``.
    """
    from tests.bdd.steps.generic.then_error import _get_error_dict, _wire_suggestion

    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"
        suggestion = _get_error_dict(error).get("suggestion") or ""
    suggestion = suggestion.lower()
    assert text1.lower() in suggestion or text2.lower() in suggestion, (
        f"Expected suggestion to contain '{text1}' or '{text2}', got: {suggestion}"
    )
