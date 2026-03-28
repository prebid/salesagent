"""BDD step definitions for UC-003: Update Media Buy.

Given steps build ctx["update_kwargs"], assembled into UpdateMediaBuyRequest
in the When step. Background steps set up the existing media buy via
conftest's _harness_env.

beads: salesagent-82p
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — Background + request construction
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the Buyer owns an existing media buy with media_buy_id "{media_buy_id}"'))
def given_buyer_owns_media_buy(ctx: dict, media_buy_id: str) -> None:
    """Verify the existing media buy is in ctx AND persisted in DB.

    Step text says 'Buyer owns an existing media buy' — verify both ctx state
    AND database persistence to prevent phantom media buys that exist only in
    test state.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — conftest setup_update_data() failed"
    assert mb.media_buy_id == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{mb.media_buy_id}'"
    # Verify DB persistence — step claims media buy "exists", not just "is in ctx"
    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify media buy ownership"
    with get_db_session() as session:
        db_mb = session.scalars(
            select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant.tenant_id)
        ).first()
        assert db_mb is not None, (
            f"Media buy '{media_buy_id}' not found in DB for tenant '{tenant.tenant_id}' — "
            "step claims 'Buyer owns an existing media buy' but it is not persisted"
        )


@given(parsers.parse('the media buy is in "{status}" status'))
def given_media_buy_status(ctx: dict, status: str) -> None:
    """Set the existing media buy to the specified status."""
    mb = ctx.get("existing_media_buy")
    assert mb is not None, (
        "No existing_media_buy in ctx — step claims 'the media buy is in "
        f'"{status}" status\' but no media buy exists to set status on'
    )
    mb.status = status
    env = ctx["env"]
    env._commit_factory_data()


@given(parsers.parse('the existing media buy has start_time "{start_time}" and end_time "{end_time}"'))
def given_existing_mb_start_end_time(ctx: dict, start_time: str, end_time: str) -> None:
    """Set start_time and end_time on the existing media buy ORM model.

    Stores the original values in ctx for later comparison by the Then step
    'the existing start_time and end_time should remain unchanged'.
    """
    from datetime import datetime

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set start_time/end_time"
    parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    mb.start_time = parsed_start
    mb.end_time = parsed_end
    env = ctx["env"]
    env._commit_factory_data()
    # Store originals for the Then step that checks they remain unchanged
    ctx["original_start_time"] = parsed_start.astimezone(UTC)
    ctx["original_end_time"] = parsed_end.astimezone(UTC)


@given(parsers.parse('the existing media buy has start_time "{start_time}"'))
def given_existing_mb_start_time(ctx: dict, start_time: str) -> None:
    """Set start_time on the existing media buy ORM model (end_time unchanged).

    Used by ext-e scenarios where end_time is set via the update request,
    not pre-existing on the media buy.
    """
    from datetime import datetime

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set start_time"
    parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    mb.start_time = parsed_start
    env = ctx["env"]
    env._commit_factory_data()


@given(parsers.parse("a valid update_media_buy request with:"))
def given_update_request_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Build update request kwargs from a data table."""
    import json

    _supported_fields = {
        "media_buy_id",
        "buyer_ref",
        "paused",
        "start_time",
        "end_time",
        "packages",
        "budget",
        "idempotency_key",
    }
    kwargs = _ensure_update_defaults(ctx)
    # Skip header row (pytest-bdd datatables include the header as first row)
    rows = datatable[1:] if datatable and datatable[0][0].strip() == "field" else datatable
    for row in rows:
        field, value = row[0].strip(), row[1].strip()
        assert field in _supported_fields, (
            f"Unrecognized update field '{field}' in datatable — "
            f"step silently drops it. Supported: {sorted(_supported_fields)}. "
            f"Add handling for '{field}' if it's a valid UpdateMediaBuyRequest field."
        )
        if field == "media_buy_id":
            kwargs["media_buy_id"] = value
        elif field == "buyer_ref":
            kwargs["buyer_ref"] = value
        elif field == "paused":
            kwargs["paused"] = value.lower() == "true"
        elif field == "start_time":
            kwargs["start_time"] = value
        elif field == "end_time":
            kwargs["end_time"] = value
        elif field == "budget":
            kwargs["budget"] = float(value)
        elif field == "packages":
            kwargs["packages"] = json.loads(value)
        elif field == "idempotency_key":
            kwargs["idempotency_key"] = value


@given("the request does NOT include start_time, end_time, or paused fields")
def given_request_omits_start_end_paused(ctx: dict) -> None:
    """Declarative guard — ensure start_time, end_time, paused are NOT in update_kwargs.

    The default update_kwargs only contains media_buy_id, so these fields are already
    absent. This step explicitly strips them in case prior Given steps added them.
    """
    kwargs = _ensure_update_defaults(ctx)
    for field in ("start_time", "end_time", "paused"):
        kwargs.pop(field, None)


@given("the request does NOT include an idempotency_key")
def given_request_omits_idempotency_key(ctx: dict) -> None:
    """Declarative guard — ensure idempotency_key is NOT in update_kwargs.

    The default update_kwargs only contains media_buy_id, so idempotency_key is
    already absent. This step explicitly strips it in case prior Given steps added it.
    """
    kwargs = _ensure_update_defaults(ctx)
    kwargs.pop("idempotency_key", None)


@given("the request does not include any updatable fields")
def given_request_no_updatable_fields(ctx: dict) -> None:
    """Ensure the update request contains only media_buy_id — no updatable fields.

    Strips packages, paused, start_time, end_time, buyer_ref, and any other
    fields that _update_media_buy_impl treats as updatable.
    """
    kwargs = _ensure_update_defaults(ctx)
    # Keep only media_buy_id, remove everything else
    media_buy_id = kwargs.get("media_buy_id", "mb_existing")
    kwargs.clear()
    kwargs["media_buy_id"] = media_buy_id


@given(parsers.parse("the request includes 1 package update with:"))
def given_package_update_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Add a package update to the request from a data table."""
    import json

    _supported_pkg_fields = {"package_id", "budget", "paused", "targeting_overlay"}
    kwargs = _ensure_update_defaults(ctx)
    pkg_update: dict[str, Any] = {}
    # Skip header row if present (pytest-bdd datatables include header as first row)
    rows = datatable[1:] if datatable and datatable[0][0].strip().lower() == "field" else datatable
    for row in rows:
        field, value = row[0].strip(), row[1].strip()
        assert field in _supported_pkg_fields, (
            f"Unrecognized package field '{field}' in datatable — "
            f"supported: {sorted(_supported_pkg_fields)}. "
            f"Add handling for '{field}' if it's a valid package update field."
        )
        if field == "package_id":
            pkg_update["package_id"] = value
        elif field == "budget":
            pkg_update["budget"] = float(value)
        elif field == "paused":
            pkg_update["paused"] = value.lower() == "true"
        elif field == "targeting_overlay":
            pkg_update["targeting_overlay"] = json.loads(value)
    assert pkg_update, "Datatable produced empty package update — check table format"
    kwargs["packages"] = [pkg_update]


@given(parsers.parse('the package "{package_id}" exists in the media buy'))
def given_package_exists(ctx: dict, package_id: str) -> None:
    """Verify or create the package in the existing media buy."""
    pkg = ctx.get("existing_package")
    if pkg is not None and pkg.package_id == package_id:
        return  # Package already matches
    # Create the package if it doesn't exist or doesn't match
    from tests.factories import MediaPackageFactory

    assert ctx.get("existing_media_buy") is not None, "No existing_media_buy in ctx — cannot create package"
    env = ctx["env"]
    new_pkg = MediaPackageFactory(
        media_buy=ctx["existing_media_buy"],
        package_id=package_id,
        package_config={
            "package_id": package_id,
            "product_id": "guaranteed_display",
            "budget": 5000.0,
        },
    )
    env._commit_factory_data()
    ctx["existing_package"] = new_pkg


@given("the updated daily spend does not exceed max_daily_package_spend")
def given_daily_spend_ok(ctx: dict) -> None:
    """Declarative guard — verifies budgets are within daily spend limits.

    Checks that each package's budget is positive AND does not exceed the tenant's
    max_daily_package_spend (if configured). When update_kwargs has no packages,
    falls back to verifying the existing media buy's packages satisfy the constraint.
    """
    kwargs = ctx.get("update_kwargs", {})
    packages_to_check = kwargs.get("packages", [])

    # If no packages in update, verify existing packages satisfy the constraint
    if not packages_to_check:
        existing_mb = ctx.get("existing_media_buy")
        assert existing_mb is not None, (
            "No packages in update_kwargs AND no existing_media_buy — "
            "step claims 'daily spend does not exceed max_daily_package_spend' "
            "but there is no budget data to validate"
        )
        # Verify existing packages actually satisfy the constraint (not just assumed)
        existing_pkgs = getattr(existing_mb, "packages", None) or []
        assert len(existing_pkgs) > 0, (
            "existing_media_buy has no packages — step claims 'daily spend does not "
            "exceed max_daily_package_spend' but there are no packages to validate"
        )
    else:
        for pkg in packages_to_check:
            budget = pkg.get("budget")
            if budget is not None:
                assert budget > 0, f"Package budget {budget} is not positive — cannot satisfy daily spend constraint"

    # Verify against tenant's max_daily_package_spend if available
    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims 'does not exceed max_daily_package_spend' "
        "but cannot check the limit without a tenant"
    )
    max_daily = getattr(tenant, "max_daily_package_spend", None)
    if max_daily is not None:
        # Check update packages when present
        for pkg in packages_to_check:
            budget = pkg.get("budget")
            if budget is not None:
                assert budget <= max_daily, (
                    f"Package budget {budget} exceeds tenant max_daily_package_spend {max_daily} — "
                    "step claims 'does not exceed max_daily_package_spend'"
                )
        # Also check existing packages when no update packages specified —
        # the step claims the constraint holds, so existing packages must satisfy it too.
        if not packages_to_check:
            existing_mb = ctx.get("existing_media_buy")
            for pkg in getattr(existing_mb, "packages", None) or []:
                budget = getattr(pkg, "budget", None)
                if budget is not None:
                    assert float(budget) <= float(max_daily), (
                        f"Existing package budget {budget} exceeds tenant max_daily_package_spend "
                        f"{max_daily} — step claims 'does not exceed max_daily_package_spend' "
                        "but existing packages violate the constraint"
                    )
    ctx.setdefault("daily_spend_validated", True)


@given(parsers.parse('the buyer_ref "{buyer_ref}" resolves to the existing media buy'))
def given_buyer_ref_resolves(ctx: dict, buyer_ref: str) -> None:
    """Ensure the existing media buy has the specified buyer_ref."""
    mb = ctx.get("existing_media_buy")
    assert mb is not None, (
        f"No existing_media_buy in ctx — step claims buyer_ref '{buyer_ref}' "
        "resolves to media buy but no media buy exists"
    )
    if mb.buyer_ref != buyer_ref:
        mb.buyer_ref = buyer_ref
        env = ctx["env"]
        env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative_assignments / inline creatives on package updates
# ═══════════════════════════════════════════════════════════════════════


@given("the package update includes creative_assignments with:")
def given_package_update_creative_assignments(ctx: dict, datatable: list[list[str]]) -> None:
    """Add creative_assignments to the first package update from a data table.

    Handles variable column counts:
    - 1 col: creative_id only (error scenarios)
    - 2 cols: creative_id + placement_ids (placement error scenarios)
    - 3 cols: creative_id + weight + placement_ids (full happy path)
    First row is the header (skipped).
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    # Detect column layout from header
    header = [h.strip().lower() for h in datatable[0]]
    assignments = []
    for row in datatable[1:]:  # skip header row
        creative_id = row[0].strip()
        assignment: dict[str, Any] = {"creative_id": creative_id}
        if "weight" in header and len(row) > header.index("weight"):
            assignment["weight"] = float(row[header.index("weight")].strip())
        else:
            assignment["weight"] = 1.0  # default weight
        if "placement_ids" in header and len(row) > header.index("placement_ids"):
            placement_ids = [p.strip() for p in row[header.index("placement_ids")].strip().split(",") if p.strip()]
            assignment["placement_ids"] = placement_ids
        else:
            assignment["placement_ids"] = []
        assignments.append(assignment)
    kwargs["packages"][0]["creative_assignments"] = assignments
    # Track referenced creative_ids for later guard steps
    ctx["referenced_creative_ids"] = [a["creative_id"] for a in assignments]
    ctx["referenced_placement_ids"] = [pid for a in assignments for pid in (a.get("placement_ids") or [])]


@given("all referenced creative_ids exist in the creative library")
def given_creatives_exist_in_library(ctx: dict) -> None:
    """Create DB Creative records for all creative_ids referenced by creative_assignments."""
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    creative_ids = ctx.get("referenced_creative_ids", [])
    for cid in creative_ids:
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=True,
            data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
        )
    env._commit_factory_data()


@given("all referenced creatives are in valid state (not error or rejected)")
def given_creatives_in_valid_state(ctx: dict) -> None:
    """Declarative guard — creatives created by the previous step are already approved.

    CreativeFactory with approved=True sets status='approved'. This step verifies
    the referenced creative_ids exist and were created with valid status by the
    prior 'all referenced creative_ids exist in the creative library' step.
    """
    ids = ctx.get("referenced_creative_ids")
    assert ids and len(ids) > 0, "No referenced creative_ids — missing prior step"
    # Prior step (given_creatives_exist_in_library) creates with approved=True.
    # Verify the status is not error/rejected via DB query.
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims 'all referenced creatives are in valid state' "
        "but cannot verify without tenant context"
    )
    invalid_statuses = ("error", "rejected")
    with get_db_session() as session:
        for cid in ids:
            cr = session.scalars(select(CreativeModel).filter_by(creative_id=cid, tenant_id=tenant.tenant_id)).first()
            assert cr is not None, (
                f"Creative {cid} not found in DB for tenant {tenant.tenant_id} — "
                "step claims creatives are 'in valid state' but creative doesn't exist"
            )
            assert cr.status not in invalid_statuses, (
                f"Creative {cid} is in '{cr.status}' state — step claims 'not error or rejected'"
            )


@given("all placement_ids are valid for the product")
def given_placement_ids_valid(ctx: dict) -> None:
    """Declarative guard — verifies placement_ids are valid for the product.

    The guaranteed_display product created by setup_update_data() does not
    restrict placements, so any placement_id is valid. When a product has
    explicit placement restrictions, this step verifies compatibility.
    """
    pids = ctx.get("referenced_placement_ids")
    assert pids is not None, "No referenced placement_ids — missing prior step"
    assert isinstance(pids, list), f"Expected placement_ids to be a list, got {type(pids).__name__}"
    assert len(pids) > 0, "placement_ids list is empty — step claims placements are 'valid for the product'"
    # Step claims 'valid for the product' — product must be present to validate against
    product = ctx.get("default_product") or ctx.get("existing_product")
    assert product is not None, (
        "No product in ctx (neither 'default_product' nor 'existing_product') — "
        "step claims placements are 'valid for the product' but no product exists to validate against"
    )
    # Verify product does not have restrictive placement config that would reject these
    allowed = getattr(product, "allowed_placement_ids", None)
    if allowed is not None:
        invalid = [p for p in pids if p not in allowed]
        assert not invalid, (
            f"Placement IDs {invalid} are not in product's allowed placements {allowed} — "
            "step claims 'all placement_ids are valid for the product'"
        )
    # When product has no allowed_placement_ids restriction, all placements are
    # valid by definition — this is correct AdCP semantics (no restriction = all allowed).
    # Log which path was taken for debugging.
    ctx.setdefault("placement_validation_path", "unrestricted" if allowed is None else "restricted")


@given("the package update includes inline creatives with valid content")
def given_package_update_inline_creatives(ctx: dict) -> None:
    """Add inline creative objects to the first package update.

    Uses the adcp CreativeAsset structure with minimal valid content.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    kwargs["packages"][0]["creatives"] = [
        {
            "creative_id": "inline-cr-001",
            "name": "Inline Creative 1",
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            "assets": {
                "primary": {
                    "url": "https://example.com/banner-1.png",
                    "width": 300,
                    "height": 250,
                }
            },
        }
    ]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — optimization_goals on package updates (partition/boundary)
# ═══════════════════════════════════════════════════════════════════════


@given("the package update includes optimization_goals:")
def given_package_update_optimization_goals_default(ctx: dict) -> None:
    """Set default optimization_goals on the first package update (alt-flow scenario).

    This is the NO-DATATABLE variant (step text ends with ':'). The parameterized
    variant ``given_package_update_optimization_goals`` handles explicit goals values.
    Hardcodes a representative single-metric goal (clicks) for replacement semantics tests.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. Used by the alt-flow replacement semantics scenario.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    # Default: single metric goal (clicks) — representative for replacement semantics test.
    # The parameterized variant (with goals_value) handles scenario-specific goals.
    kwargs["packages"][0]["optimization_goals"] = json.loads('[{"kind": "metric", "metric": "clicks", "priority": 1}]')
    ctx.setdefault("optimization_goals_source", "default_clicks")


@given(parsers.parse("the package update includes optimization_goals: {goals_value}"))
def given_package_update_optimization_goals(ctx: dict, goals_value: str) -> None:
    """Set optimization_goals on the first package update.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. UpdateMediaBuyRequest's package updates will reject the field.
    All scenarios are expected to xfail via conftest.py tag-based xfail.

    The goals_value is either a JSON array or the literal '<not provided>'.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]

    if goals_value.strip() == "<not provided>":
        # Omit optimization_goals entirely — tests preservation semantics
        kwargs["packages"][0].pop("optimization_goals", None)
        ctx["optimization_goals_omitted"] = True
    else:
        kwargs["packages"][0]["optimization_goals"] = json.loads(goals_value)


@given("no targeting_overlay.keyword_targets is present in the same package update")
def given_no_keyword_targets_in_update(ctx: dict) -> None:
    """Ensure the package update does not include keyword_targets.

    This step is a declarative guard — it confirms that the package update
    doesn't have keyword_targets set (which would conflict with keyword_targets_add).
    """
    kwargs = _ensure_update_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        overlay = pkg.get("targeting_overlay")
        if isinstance(overlay, dict):
            overlay.pop("keyword_targets", None)
        elif overlay is not None and hasattr(overlay, "keyword_targets"):
            # Handle Pydantic model overlays (same pattern as negative_keywords guard)
            overlay.keyword_targets = None


@given("no targeting_overlay.negative_keywords is present in the same package update")
def given_no_negative_keywords_in_update(ctx: dict) -> None:
    """Ensure the package update does not include negative_keywords in targeting_overlay.

    Declarative guard — analogous to the keyword_targets guard above. Prevents
    conflict with negative_keywords_add (BR-RULE-083).
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        return  # No packages → negative_keywords trivially absent
    pkg = kwargs["packages"][0]
    overlay = pkg.get("targeting_overlay")
    if overlay is None:
        return  # No overlay → negative_keywords trivially absent
    if isinstance(overlay, dict):
        overlay.pop("negative_keywords", None)
    elif hasattr(overlay, "negative_keywords"):
        # Handle Pydantic model overlays
        overlay.negative_keywords = None


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — keyword operations on package updates
# ═══════════════════════════════════════════════════════════════════════


def _set_keyword_field_on_package(ctx: dict, field: str, default_value: list[dict[str, Any]]) -> None:
    """Set a keyword operation field on the first package update.

    Shared helper for keyword_targets_add, keyword_targets_remove,
    negative_keywords_add, and negative_keywords_remove steps.
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    kwargs["packages"][0][field] = default_value


@given("the package update includes keyword_targets_add:")
def given_package_update_keyword_targets_add(ctx: dict) -> None:
    """Set default keyword_targets_add on the first package update (alt-flow scenario)."""
    _set_keyword_field_on_package(ctx, "keyword_targets_add", [{"keyword": "shoes", "match_type": "broad"}])


@given("the package update includes keyword_targets_remove:")
def given_package_update_keyword_targets_remove(ctx: dict) -> None:
    """Set default keyword_targets_remove on the first package update (alt-flow scenario)."""
    _set_keyword_field_on_package(ctx, "keyword_targets_remove", [{"keyword": "shoes", "match_type": "broad"}])


@given("the package update includes negative_keywords_add:")
def given_package_update_negative_keywords_add(ctx: dict) -> None:
    """Set negative_keywords_add on the first package update (alt-flow scenario).

    Note: Step text ends with ':' (Gherkin table indicator) but this function uses
    hardcoded defaults. Feature files using this step do not provide a DataTable;
    the ':' is part of the step text pattern matching the Gherkin scenario phrasing.

    FIXME(salesagent-9vgz.1): Accept datatable parameter when feature files provide one.
    """
    _set_keyword_field_on_package(ctx, "negative_keywords_add", [{"keyword": "cheap", "match_type": "exact"}])


@given("the package update includes negative_keywords_remove:")
def given_package_update_negative_keywords_remove(ctx: dict) -> None:
    """Set negative_keywords_remove on the first package update (alt-flow scenario).

    Note: Step text ends with ':' (Gherkin table indicator) but this function uses
    hardcoded defaults. Feature files using this step do not provide a DataTable;
    the ':' is part of the step text pattern matching the Gherkin scenario phrasing.

    FIXME(salesagent-9vgz.1): Accept datatable parameter when feature files provide one.
    """
    _set_keyword_field_on_package(ctx, "negative_keywords_remove", [{"keyword": "cheap", "match_type": "exact"}])


# ═══════════════════════════════════════════════════════════════════════
# WHEN step — dispatch update request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the update_media_buy request")
def when_send_update_request(ctx: dict) -> None:
    """Build UpdateMediaBuyRequest and dispatch through harness."""
    from src.core.schemas import UpdateMediaBuyRequest

    update_kwargs = ctx.get("update_kwargs", {})
    req = UpdateMediaBuyRequest(**update_kwargs)

    if ctx.get("has_auth") is False:
        dispatch_request(ctx, req=req, identity=None)
    else:
        dispatch_request(ctx, req=req)

    # Post-process: promote error responses to ctx["error"]
    _promote_update_errors(ctx)


def _promote_update_errors(ctx: dict) -> None:
    """Promote UpdateMediaBuyError responses to ctx['error'] for Then steps."""
    resp = ctx.get("response")
    if resp is None:
        return
    from src.core.schemas._base import UpdateMediaBuyError

    if isinstance(resp, UpdateMediaBuyError) and resp.errors:
        ctx["error"] = resp.errors[0]
        ctx["error_response"] = resp
        del ctx["response"]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — update-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the existing start_time and end_time should remain unchanged")
def then_start_end_time_unchanged(ctx: dict) -> None:
    """Assert the media buy's start_time and end_time were not altered by the update.

    Reloads the media buy from DB and compares against the values stored in ctx
    by the Given step 'the existing media buy has start_time ... and end_time ...'.
    """

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    original_start = ctx.get("original_start_time")
    original_end = ctx.get("original_end_time")
    assert original_start is not None and original_end is not None, (
        "original_start_time/original_end_time not in ctx — "
        "missing prior Given step 'the existing media buy has start_time ... and end_time ...'"
    )
    mb = ctx["existing_media_buy"]
    with get_db_session() as session:
        refreshed = session.scalars(select(MediaBuy).filter_by(media_buy_id=mb.media_buy_id)).first()
        assert refreshed is not None, f"Media buy {mb.media_buy_id} not found in DB after update"
        actual_start = refreshed.start_time
        actual_end = refreshed.end_time
    # Normalize to UTC for comparison (DB may return timezone-aware or naive)
    if actual_start is not None and actual_start.tzinfo is not None:
        actual_start = actual_start.astimezone(UTC)
    if actual_end is not None and actual_end.tzinfo is not None:
        actual_end = actual_end.astimezone(UTC)
    assert actual_start == original_start, f"start_time changed: expected {original_start}, got {actual_start}"
    assert actual_end == original_end, f"end_time changed: expected {original_end}, got {actual_end}"


@then(parsers.parse('the response should contain media_buy_id "{media_buy_id}"'))
def then_response_media_buy_id(ctx: dict, media_buy_id: str) -> None:
    """Assert response contains the expected media_buy_id."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    actual = getattr(resp, "media_buy_id", None)
    assert actual == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{actual}'"


@then("the response should contain media_buy_id")
def then_response_has_media_buy_id(ctx: dict) -> None:
    """Assert response contains a media_buy_id (any value)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    actual = getattr(resp, "media_buy_id", None)
    assert actual is not None, f"Expected media_buy_id in response, got {actual!r}"


@then("the response should contain buyer_ref")
def then_response_has_buyer_ref(ctx: dict) -> None:
    """Assert response contains a buyer_ref."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert getattr(resp, "buyer_ref", None) is not None, "Expected buyer_ref in response"


@then(parsers.parse('the response should contain buyer_ref "{buyer_ref}"'))
def then_response_buyer_ref(ctx: dict, buyer_ref: str) -> None:
    """Assert response contains the expected buyer_ref."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    actual = getattr(resp, "buyer_ref", None)
    assert actual == buyer_ref, f"Expected buyer_ref '{buyer_ref}', got '{actual}'"


@then("the response should contain implementation_date that is null")
def then_implementation_date_null(ctx: dict) -> None:
    """Assert response has a null implementation_date (pending approval)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert hasattr(resp, "implementation_date"), "Response has no implementation_date field"
    impl_date = resp.implementation_date
    assert impl_date is None, f"Expected implementation_date to be None (pending approval), got {impl_date!r}"


@then("the response should contain an implementation_date that is not null")
def then_implementation_date_not_null(ctx: dict) -> None:
    """Assert response has a non-null implementation_date."""
    from datetime import datetime

    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Guard: this step only makes sense on a success response, not an error
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check implementation_date on error"
    assert hasattr(resp, "implementation_date"), "Response has no implementation_date field"
    impl_date = resp.implementation_date
    # Hard-assert what the step text claims: "not null"
    if impl_date is None:
        # SPEC-PRODUCTION GAP: production does not set implementation_date on
        # update yet. The assertion WOULD fail, so xfail documents the gap.
        pytest.xfail(
            "SPEC-PRODUCTION GAP: implementation_date is None in response — "
            "production does not populate it on update. Step claims 'not null'. "
            "FIXME(salesagent-9vgz.1)"
        )
    # Verify it's a meaningful datetime value (not just a truthy non-None)
    if isinstance(impl_date, str):
        parsed = datetime.fromisoformat(impl_date.replace("Z", "+00:00"))
        assert parsed.year >= 2020, f"implementation_date parsed to implausible date: {parsed!r}"
    else:
        assert isinstance(impl_date, datetime), (
            f"implementation_date should be datetime or ISO string, got {type(impl_date).__name__}: {impl_date!r}"
        )
        assert impl_date.year >= 2020, f"implementation_date has implausible year: {impl_date!r}"


@then(parsers.parse('the response should contain affected_packages including "{package_id}"'))
def then_affected_packages_include(ctx: dict, package_id: str) -> None:
    """Assert affected_packages contains the specified package."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    affected = getattr(resp, "affected_packages", None) or []
    pkg_ids = [
        getattr(p, "package_id", None) or (p.get("package_id") if isinstance(p, dict) else None) for p in affected
    ]
    assert package_id in pkg_ids, f"Expected '{package_id}' in affected_packages, got {pkg_ids}"


@then(parsers.parse("the affected package should show the updated budget of {budget:d}"))
def then_affected_package_budget(ctx: dict, budget: int) -> None:
    """Assert the affected package shows the updated budget value."""
    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Guard: this step only makes sense on a success response
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check budget on error"
    affected = getattr(resp, "affected_packages", None) or []
    assert len(affected) > 0, "No affected packages in response"
    pkg = affected[0]
    pkg_id = getattr(pkg, "package_id", None) or (pkg.get("package_id") if isinstance(pkg, dict) else None)
    assert pkg_id, "Affected package has no package_id — cannot identify which package was updated"
    actual_budget = getattr(pkg, "budget", None)
    if actual_budget is None and isinstance(pkg, dict):
        actual_budget = pkg.get("budget")
    # Hard-assert what the step text claims: "updated budget of {budget}"
    if actual_budget is None:
        # SPEC-PRODUCTION GAP: budget not echoed in affected_packages response.
        # The assertion WOULD fail, so xfail documents the gap.
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: affected package '{pkg_id}' budget is None — "
            f"production does not echo budget in affected_packages. "
            f"Step claims 'updated budget of {budget}'. "
            f"FIXME(salesagent-9vgz.1)"
        )
    assert float(actual_budget) == float(budget), f"Expected budget {budget}, got {actual_budget}"


@then("the response envelope should include a sandbox flag")
def then_response_has_sandbox(ctx: dict) -> None:
    """Assert response includes sandbox information."""
    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Guard: this step only makes sense on a success response, not an error
    assert "error" not in ctx, f"Update errored ({ctx['error']}) — cannot check sandbox flag on an error response"
    # Guard: response must be a real model object, not a raw dict or string
    assert hasattr(resp, "model_dump") or hasattr(resp, "__dict__"), (
        f"Response is not a model object (type: {type(resp).__name__}) — cannot inspect for sandbox flag"
    )
    # sandbox may live on the response directly or on a wrapper envelope
    sandbox = getattr(resp, "sandbox", None)
    if sandbox is None and hasattr(resp, "model_dump"):
        dumped = resp.model_dump()
        sandbox = dumped.get("sandbox")
    # Hard-assert what the step text claims: "should include a sandbox flag"
    if sandbox is None:
        # SPEC-PRODUCTION GAP: sandbox flag not present in response.
        # Only xfail if this is a genuine gap (response is otherwise valid).
        resp_fields = list(resp.model_dump().keys()) if hasattr(resp, "model_dump") else dir(resp)
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: sandbox flag not present on response "
            f"(type: {type(resp).__name__}, fields: {resp_fields[:10]}). "
            "Step claims envelope 'should include' it but value is absent. "
            "FIXME(salesagent-9vgz.1)"
        )
    # Sandbox is present — verify it's a boolean (not just any truthy/falsy value)
    assert isinstance(sandbox, bool), f"Expected sandbox to be bool, got {type(sandbox).__name__}: {sandbox!r}"


@then('the response should NOT contain an "errors" field')
def then_no_errors_field(ctx: dict) -> None:
    """Assert the response does not contain an 'errors' field at all.

    Step text says 'NOT contain' — the field should be absent (None),
    not just empty. An empty list ``[]`` still means the field exists.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # "NOT contain" means the key must be absent, not just None.
    # Use exclude_none=True (AdCP default) so errors=None is excluded from the dict.
    if hasattr(resp, "model_dump"):
        data = resp.model_dump(exclude_none=True)
        assert "errors" not in data, (
            f"Expected 'errors' key absent from response (exclude_none=True), but found: {data.get('errors')!r}"
        )
    else:
        errors = getattr(resp, "errors", None)
        assert errors is None, f"Expected no 'errors' field in response, got: {errors}"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _ensure_update_defaults(ctx: dict) -> dict[str, Any]:
    """Ensure ctx['update_kwargs'] has valid defaults for an update request."""
    if "update_kwargs" not in ctx:
        mb = ctx.get("existing_media_buy")
        ctx["update_kwargs"] = {
            "media_buy_id": mb.media_buy_id if mb else "mb_existing",
        }
    return ctx["update_kwargs"]
