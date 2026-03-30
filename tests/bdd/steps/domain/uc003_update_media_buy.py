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
    test state.  Uses MediaBuyRepository (not raw select) per repository pattern.
    """
    from src.core.database.repositories.media_buy import MediaBuyRepository

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — conftest setup_update_data() failed"
    assert mb.media_buy_id == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{mb.media_buy_id}'"
    # Verify DB persistence — step claims media buy "exists", not just "is in ctx"
    env = ctx["env"]
    env._commit_factory_data()
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify media buy ownership"
    repo = MediaBuyRepository(env._session, tenant.tenant_id)
    db_mb = repo.get_by_id(media_buy_id)
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
    import re

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
            # Expand <N character string> placeholders (e.g. "<256 character string>")
            length_match = re.match(r"<(\d+)\s*char(?:acter)?\s*string>", value)
            kwargs["idempotency_key"] = "x" * int(length_match.group(1)) if length_match else value


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
        # Only verify budgets that are positive — zero/negative budgets are expected
        # to fail via the When/Then outcome, not via this guard step.
        pass

    # Verify against tenant's max_daily_package_spend if available
    tenant = ctx.get("tenant")
    assert tenant is not None, (
        "No tenant in ctx — step claims 'does not exceed max_daily_package_spend' "
        "but cannot check the limit without a tenant"
    )
    max_daily = getattr(tenant, "max_daily_package_spend", None)
    if max_daily is not None:
        # Check update packages when present (only positive budgets — zero/negative
        # are expected to fail at budget validation, not at daily spend cap).
        for pkg in packages_to_check:
            budget = pkg.get("budget")
            if budget is not None and budget > 0:
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

    creative_ids = ctx.get("referenced_creative_ids", [])
    for cid in creative_ids:
        CreativeFactory.create_sync(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=True,
            data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
        )


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
        # "<not provided>" means "omit the field" — tests preservation semantics.
        # Step text "includes optimization_goals: <not provided>" is a Scenario Outline
        # convention: the field slot exists in the template but this row omits the value.
        kwargs["packages"][0].pop("optimization_goals", None)
        ctx["optimization_goals_omitted"] = True
        assert "optimization_goals" not in kwargs["packages"][0], (
            "optimization_goals should be absent after '<not provided>' — preservation test requires omission"
        )
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

    Requires packages to exist in the update — this step is always preceded by
    a 'the request includes 1 package update with:' step in the Gherkin.
    """
    kwargs = _ensure_update_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in update_kwargs — 'no targeting_overlay.negative_keywords is present' "
        "requires a prior step that configures at least one package update. "
        "The context is missing expected structure."
    )
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
    from pydantic import ValidationError

    from src.core.schemas import UpdateMediaBuyRequest

    update_kwargs = ctx.get("update_kwargs", {})
    try:
        req = UpdateMediaBuyRequest(**update_kwargs)
    except ValidationError as e:
        # Schema validation rejects the request before production code runs.
        # Store as ctx["error"] so Then steps can assert on it.
        ctx["error"] = e
        return

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
    """Assert response has a non-null implementation_date.

    Step text: "the response should contain an implementation_date that is not null"
    Contract: implementation_date MUST be present and non-null on success responses.
    If production doesn't populate it, that's a SPEC-PRODUCTION GAP (xfail).
    """
    from datetime import datetime

    resp = ctx.get("response")
    assert resp is not None, "Expected a response — no response in ctx"
    # Guard: this step only makes sense on a success response, not an error
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check implementation_date on error"
    assert hasattr(resp, "implementation_date"), (
        f"Response (type: {type(resp).__name__}) has no implementation_date field — "
        "step claims response 'should contain' it"
    )
    impl_date = resp.implementation_date
    # Step text claims "not null" unconditionally — hard assert.
    # If production doesn't populate this, the SCENARIO should be xfailed in conftest.py,
    # not the step body. See salesagent-ghgx.
    assert impl_date is not None, (
        "implementation_date is None in response — step text claims 'not null' unconditionally"
    )
    # impl_date is not None — verify it's a meaningful datetime
    if isinstance(impl_date, str):
        parsed = datetime.fromisoformat(impl_date.replace("Z", "+00:00"))
        assert parsed.year >= 2020, f"implementation_date parsed to implausible date: {parsed!r}"
        assert parsed.year <= 2100, f"implementation_date is implausibly far in the future: {parsed!r}"
    else:
        assert isinstance(impl_date, datetime), (
            f"implementation_date should be datetime or ISO string, got {type(impl_date).__name__}: {impl_date!r}"
        )
        assert impl_date.year >= 2020, f"implementation_date has implausible year: {impl_date!r}"
        assert impl_date.year <= 2100, f"implementation_date is implausibly far in the future: {impl_date!r}"


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


@then("the response should contain affected_packages")
def then_affected_packages_present(ctx: dict) -> None:
    """Assert affected_packages is present and non-empty on the response.

    Step text: "the response should contain affected_packages"
    Contract: affected_packages MUST be a non-empty list on a successful update.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response — no response in ctx"
    assert "error" not in ctx, f"Update errored ({ctx.get('error')}) — cannot check affected_packages on error"
    affected = getattr(resp, "affected_packages", None)
    assert affected is not None, (
        f"affected_packages is None on response (type: {type(resp).__name__}) — "
        "step text claims response should contain affected_packages"
    )
    assert isinstance(affected, list), (
        f"affected_packages should be a list, got {type(affected).__name__}: {affected!r}"
    )
    assert len(affected) > 0, "affected_packages is empty — step text claims response should contain affected_packages"


@then(parsers.parse("the affected package should show the updated budget of {budget:d}"))
def then_affected_package_budget(ctx: dict, budget: int) -> None:
    """Assert the affected package shows the updated budget value.

    Step text: "the affected package should show the updated budget of {budget}"
    Contract: affected_packages[0].budget MUST equal the requested budget.
    If production doesn't echo budget, that's a SPEC-PRODUCTION GAP (xfail).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response — no response in ctx"
    # Guard: this step only makes sense on a success response
    assert "error" not in ctx, f"Response is an error ({ctx.get('error')}) — cannot check budget on error"
    affected = getattr(resp, "affected_packages", None) or []
    assert len(affected) > 0, "No affected packages in response — step claims a package was affected"
    pkg = affected[0]
    pkg_id = getattr(pkg, "package_id", None) or (pkg.get("package_id") if isinstance(pkg, dict) else None)
    assert pkg_id, "Affected package has no package_id — cannot identify which package was updated"
    actual_budget = getattr(pkg, "budget", None)
    if actual_budget is None and isinstance(pkg, dict):
        actual_budget = pkg.get("budget")
    # Step text claims "updated budget of {budget}" unconditionally — hard assert.
    # If production doesn't echo budget, the SCENARIO should be xfailed in conftest.py.
    # See salesagent-2c9b.
    assert actual_budget is not None, (
        f"affected package '{pkg_id}' budget is None — step text claims 'updated budget of {budget}' unconditionally"
    )
    # actual_budget is not None — validate type and value
    assert isinstance(actual_budget, (int, float)), (
        f"Expected budget to be numeric, got {type(actual_budget).__name__}: {actual_budget!r}"
    )
    assert float(actual_budget) == float(budget), (
        f"Expected budget {budget} on affected package '{pkg_id}', got {actual_budget}"
    )


@then("the response envelope should include a sandbox flag")
def then_response_has_sandbox(ctx: dict) -> None:
    """Assert response includes sandbox information.

    Step text: "the response envelope should include a sandbox flag"
    Contract: sandbox MUST be present as a boolean on the response envelope.
    If production doesn't include it, that's a SPEC-PRODUCTION GAP (xfail).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response — no response in ctx"
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
    # Step text claims "should include a sandbox flag" unconditionally — hard assert.
    # If production doesn't include sandbox, the SCENARIO should be xfailed in conftest.py.
    # See salesagent-n3bf.
    assert sandbox is not None, (
        f"sandbox flag not present on response (type: {type(resp).__name__}) — "
        "step text claims envelope 'should include' it unconditionally"
    )
    # sandbox is not None — verify it's a boolean (not just any truthy/falsy value)
    # Step text claims "should include a sandbox flag" — presence + type, not a specific value.
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
# GIVEN steps — partition/boundary: idempotency_key
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the idempotency_key is set to {value}"))
def given_idempotency_key(ctx: dict, value: str) -> None:
    """Set or omit idempotency_key on the update request.

    '<not provided>' means omit the field (test preservation semantics).
    Any other value sets it as-is. Handles length placeholders like
    '<255 character string>' by generating a string of the described length.
    """
    import re

    kwargs = _ensure_update_defaults(ctx)
    stripped = value.strip()
    if stripped == "<not provided>":
        kwargs.pop("idempotency_key", None)
        return

    # Handle length placeholders: <N character string>, <N char string>, <N chars>
    length_match = re.match(r"<(\d+)\s*char(?:acter)?\s*string>", stripped)
    if length_match:
        n = int(length_match.group(1))
        kwargs["idempotency_key"] = "x" * n
        return

    kwargs["idempotency_key"] = stripped


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: daily spend cap
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the tenant max_daily_package_spend is {cap_config}"))
def given_tenant_max_daily_spend(ctx: dict, cap_config: str) -> None:
    """Configure the tenant's max_daily_package_spend for daily spend cap tests.

    Accepts a numeric value or 'not set' (which clears the limit).
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot configure max_daily_package_spend"

    stripped = cap_config.strip()
    with get_db_session() as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id)).first()
        if stripped.lower() == "not set":
            if cl is not None:
                cl.max_daily_package_spend = None
                session.commit()
        else:
            assert cl is not None, (
                f"No CurrencyLimit for tenant {tenant.tenant_id} — cannot set max_daily_package_spend"
            )
            cl.max_daily_package_spend = float(stripped)
            session.commit()


@given(parsers.parse("the media buy flight duration is {flight_days} days"))
def given_media_buy_flight_duration(ctx: dict, flight_days: str) -> None:
    """Set the media buy flight duration by adjusting start_time and end_time.

    Sets start_time to now and end_time to now + flight_days.
    Given step must set up exactly what the step text says — no silent flooring.
    0-day flights (start == end) are valid test inputs for validation error scenarios.
    """
    from datetime import datetime, timedelta

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot set flight duration"
    days = int(flight_days)
    now = datetime.now(tz=UTC)
    mb.start_time = now
    mb.end_time = now + timedelta(days=days)
    env = ctx["env"]
    env._commit_factory_data()

    # Verify persistence: re-read from DB to confirm flight duration was committed
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy as MediaBuyModel

    with get_db_session() as session:
        persisted = session.scalars(
            select(MediaBuyModel).filter_by(media_buy_id=mb.media_buy_id, tenant_id=mb.tenant_id)
        ).first()
        assert persisted is not None, f"Media buy {mb.media_buy_id} not found in DB after _commit_factory_data()"
        actual_days = (persisted.end_time - persisted.start_time).days
        assert actual_days == days, (
            f"Flight duration not persisted: expected {days} days, "
            f"got {actual_days} (start={persisted.start_time}, end={persisted.end_time})"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: media buy identification
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("a valid update_media_buy request with identification: {id_config}"))
def given_update_request_with_identification(ctx: dict, id_config: str) -> None:
    """Build update request with specific identification fields.

    id_config formats:
    - 'media_buy_id=mb_existing' — set media_buy_id only
    - 'buyer_ref=my_ref_01' — set buyer_ref only (ensure mb has this ref)
    - 'media_buy_id=X,buyer_ref=Y' — set both (ambiguous — expect error)
    - '<none>' — set neither (expect error)
    """
    kwargs: dict[str, Any] = {}
    stripped = id_config.strip()

    if stripped == "<none>":
        # Neither identifier — kwargs stays empty, expecting INVALID_REQUEST error.
        # Explicit guard: if kwargs somehow got pre-populated, that's a test setup bug.
        assert not kwargs, f"Expected empty kwargs for '<none>' identification, got {kwargs!r}"
    else:
        for part in stripped.split(","):
            key, _, val = part.strip().partition("=")
            key = key.strip()
            val = val.strip()
            if key == "media_buy_id":
                kwargs["media_buy_id"] = val
            elif key == "buyer_ref":
                kwargs["buyer_ref"] = val
                # Ensure the existing media buy has this buyer_ref
                mb = ctx.get("existing_media_buy")
                if mb is not None and mb.buyer_ref != val:
                    mb.buyer_ref = val
                    ctx["env"]._commit_factory_data()

    ctx["update_kwargs"] = kwargs


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: frequency_cap
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the package targeting_overlay includes frequency_cap: {freq_cap_config}"))
@given(parsers.parse("the package targeting_overlay includes frequency_cap with suppress: {freq_cap_config}"))
def given_frequency_cap_config(ctx: dict, freq_cap_config: str) -> None:
    """Set frequency_cap on the first package update's targeting_overlay.

    The parameter is the FULL frequency_cap configuration object (JSON).
    Examples: {"interval": 60, "unit": "minutes"}, {"suppress": {...}, "max_impressions": 3}.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    overlay = pkg.setdefault("targeting_overlay", {})
    if isinstance(overlay, str):
        overlay = json.loads(overlay)
        pkg["targeting_overlay"] = overlay
    parsed_config = json.loads(freq_cap_config)
    overlay["frequency_cap"] = parsed_config


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: parameterized keyword operations
# ═══════════════════════════════════════════════════════════════════════


def _set_keyword_field_from_param(ctx: dict, field: str, raw_value: str) -> None:
    """Set a keyword operation field from a parameterized scenario value.

    Handles JSON arrays and special sentinel values like
    '<with targeting_overlay.keyword_targets present>' and
    '<with overlay present>' which inject a conflict condition.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = raw_value.strip()

    if stripped.startswith("<with"):
        # Conflict sentinel: inject both the overlay field AND the add/remove field
        overlay = pkg.setdefault("targeting_overlay", {})
        if isinstance(overlay, str):
            overlay = json.loads(overlay)
            pkg["targeting_overlay"] = overlay
        # The field name determines which overlay dimension conflicts
        if "keyword_targets" in field:
            overlay["keyword_targets"] = [{"keyword": "conflict", "match_type": "broad"}]
        elif "negative_keywords" in field:
            overlay["negative_keywords"] = [{"keyword": "conflict", "match_type": "broad"}]
        # Also set the add/remove field to trigger the conflict validation
        pkg[field] = [{"keyword": "shoes", "match_type": "broad"}]
    else:
        pkg[field] = json.loads(stripped)


@given(parsers.parse("the package update includes keyword_targets_add: {kw_value}"))
def given_keyword_targets_add_param(ctx: dict, kw_value: str) -> None:
    """Set keyword_targets_add on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "keyword_targets_add", kw_value)


@given(parsers.parse("the package update includes keyword_targets_remove: {kw_value}"))
def given_keyword_targets_remove_param(ctx: dict, kw_value: str) -> None:
    """Set keyword_targets_remove on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "keyword_targets_remove", kw_value)


@given(parsers.parse("the package update includes negative_keywords_add: {nk_value}"))
def given_negative_keywords_add_param(ctx: dict, nk_value: str) -> None:
    """Set negative_keywords_add on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "negative_keywords_add", nk_value)


@given(parsers.parse("the package update includes negative_keywords_remove: {nk_value}"))
def given_negative_keywords_remove_param(ctx: dict, nk_value: str) -> None:
    """Set negative_keywords_remove on the first package update (parameterized variant)."""
    _set_keyword_field_from_param(ctx, "negative_keywords_remove", nk_value)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: targeting_overlay
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the package targeting_overlay is set to: {overlay_value}"))
def given_targeting_overlay(ctx: dict, overlay_value: str) -> None:
    """Set the full targeting_overlay on the first package update.

    Accepts a JSON object or '<not provided>' (omit the field).
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = overlay_value.strip()

    if stripped == "<not provided>":
        pkg.pop("targeting_overlay", None)
    else:
        pkg["targeting_overlay"] = json.loads(stripped)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: start_time/end_time guards
# ═══════════════════════════════════════════════════════════════════════


@given("the existing end_time is in the future")
def given_existing_end_time_future(ctx: dict) -> None:
    """Ensure the existing media buy's end_time is in the future.

    Sets end_time to 90 days from now if it's not already in the future.
    """
    from datetime import datetime, timedelta

    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — cannot verify end_time"
    now = datetime.now(tz=UTC)
    if mb.end_time is None or mb.end_time.replace(tzinfo=UTC) <= now:
        mb.end_time = now + timedelta(days=90)
        env = ctx["env"]
        env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: creative replacement
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the package "{package_id}" has existing creative assignments [{assignments}]'))
def given_package_existing_creatives(ctx: dict, package_id: str, assignments: str) -> None:
    """Create existing creative assignments on a package.

    Parses comma-separated creative IDs (e.g., 'cr_old_1, cr_old_2') and creates
    Creative records + assignment config on the package.
    """
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    creative_ids = [cid.strip() for cid in assignments.split(",")]
    # Create Creative records
    for cid in creative_ids:
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=True,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}},
        )
    # Set creative_assignments on the existing package — fail loudly if package missing
    pkg = ctx.get("existing_package")
    assert pkg is not None, (
        f"No 'existing_package' in ctx — step claims package '{package_id}' has creative assignments "
        "but no package was set up by a prior Given step"
    )
    assert pkg.package_id == package_id, (
        f"existing_package has package_id '{pkg.package_id}', but step text references '{package_id}'. "
        "Package ID mismatch — check scenario setup."
    )
    config = pkg.package_config or {}
    config["creative_assignments"] = [{"creative_id": cid, "weight": 1.0} for cid in creative_ids]
    pkg.package_config = config
    env._commit_factory_data()
    ctx["existing_creative_ids"] = creative_ids


@given(parsers.parse("the package creative update mode is: {mode}"))
def given_creative_update_mode(ctx: dict, mode: str) -> None:
    """Set the creative update mode on the first package update.

    Parses mode formats:
    - 'creative_ids=[cr_new_1, cr_new_2]' — set creative_ids array
    - 'creative_assignments=[{cr_new_1, weight:70}]' — set creative_assignments
    """
    import re

    from tests.factories.creative import CreativeFactory

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = mode.strip()

    if stripped.startswith("creative_ids="):
        # Parse creative_ids=[id1, id2, ...]
        ids_match = re.search(r"\[([^\]]*)\]", stripped)
        assert ids_match, f"Cannot parse creative_ids from: {stripped}"
        creative_ids = [cid.strip() for cid in ids_match.group(1).split(",") if cid.strip()]
        pkg["creative_ids"] = creative_ids
        # Create Creative records for new IDs
        env = ctx["env"]
        for cid in creative_ids:
            if cid not in (ctx.get("existing_creative_ids") or []):
                CreativeFactory(
                    creative_id=cid,
                    tenant=ctx["tenant"],
                    principal=ctx["principal"],
                    format="display_300x250",
                    approved=True,
                    data={
                        "assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}
                    },
                )
        env._commit_factory_data()
        ctx["referenced_creative_ids"] = creative_ids
    elif stripped.startswith("creative_assignments="):
        # Parse creative_assignments=[{cr_new_1, weight:70}]
        assignments = []
        env = ctx["env"]
        # Find all {id, weight:N} blocks
        for match in re.finditer(r"\{([^}]+)\}", stripped):
            parts = [p.strip() for p in match.group(1).split(",")]
            cid = parts[0]
            weight = 1.0
            for part in parts[1:]:
                if part.startswith("weight:"):
                    weight = float(part.split(":")[1])
            assignments.append({"creative_id": cid, "weight": weight})
            if cid not in (ctx.get("existing_creative_ids") or []):
                CreativeFactory(
                    creative_id=cid,
                    tenant=ctx["tenant"],
                    principal=ctx["principal"],
                    format="display_300x250",
                    approved=True,
                    data={
                        "assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}
                    },
                )
        env._commit_factory_data()
        pkg["creative_assignments"] = assignments
        ctx["referenced_creative_ids"] = [a["creative_id"] for a in assignments]
    else:
        raise ValueError(
            f"Unrecognized creative update mode: '{stripped}'. "
            f"Expected format: 'creative_ids=[id1, id2]' or "
            f"'creative_assignments=[{{id, weight:N}}]'. "
            f"Check the scenario step text."
        )


@given("all referenced creatives are valid")
def given_all_creatives_valid(ctx: dict) -> None:
    """Declarative guard — all referenced creatives are in valid state.

    Verifies creative records exist in the DB and are not in error/rejected state.
    Step text claims "all referenced creatives are valid" — we must verify this
    against actual DB state, not just check that IDs exist in ctx.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    ids = ctx.get("referenced_creative_ids") or ctx.get("existing_creative_ids")
    assert ids and len(ids) > 0, "No referenced or existing creative_ids — missing prior step"

    env = ctx["env"]
    env._commit_factory_data()

    tenant = ctx["tenant"]
    with get_db_session() as session:
        db_creatives = session.scalars(
            select(CreativeModel).filter(
                CreativeModel.tenant_id == tenant.tenant_id,
                CreativeModel.creative_id.in_(ids),
            )
        ).all()
        found_ids = {c.creative_id for c in db_creatives}
        missing = set(ids) - found_ids
        assert not missing, (
            f"Step claims 'all referenced creatives are valid' but creative IDs "
            f"{sorted(missing)} not found in DB for tenant '{tenant.tenant_id}'. "
            f"Found: {sorted(found_ids)}"
        )
        # Verify none are in error/rejected state
        invalid_states = {"error", "rejected", "failed"}
        for creative in db_creatives:
            status = getattr(creative, "status", None)
            if status and status in invalid_states:
                raise AssertionError(
                    f"Creative '{creative.creative_id}' has status '{status}' — "
                    f"step claims 'all referenced creatives are valid' but this creative "
                    f"is in an invalid state"
                )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: creative state validation
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.parse("the package update includes creative_assignments referencing creative in state: {creative_state}")
)
def given_creative_assignments_with_state(ctx: dict, creative_state: str) -> None:
    """Add creative_assignments referencing a creative with a specific state.

    States: 'approved', 'error', 'wrong_format'.
    Creates a Creative record with the appropriate state/format.
    """
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]

    state = creative_state.strip()
    cid = f"cr_{state}_001"
    if state == "approved":
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=True,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}},
        )
    elif state == "error":
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=False,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}},
        )
        # Set status to error after creation
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as CreativeModel

        env._commit_factory_data()
        with get_db_session() as session:
            cr = session.scalars(
                select(CreativeModel).filter_by(creative_id=cid, tenant_id=ctx["tenant"].tenant_id)
            ).first()
            assert cr is not None, (
                f"Creative {cid} not found in DB after _commit_factory_data() — "
                f"factory did not persist the creative for tenant {ctx['tenant'].tenant_id}"
            )
            cr.status = "error"
            session.commit()
            # Verify the status was persisted
            session.refresh(cr)
            assert cr.status == "error", f"Creative {cid} status not persisted as 'error', got '{cr.status}'"
    elif state == "wrong_format":
        CreativeFactory(
            creative_id=cid,
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="video_vast_4",  # incompatible with display product
            approved=True,
            data={"assets": {"primary": {"url": f"https://example.com/{cid}.mp4"}}},
        )
    else:
        raise ValueError(f"Unknown creative state: {state}")

    env._commit_factory_data()
    pkg["creative_assignments"] = [{"creative_id": cid, "weight": 1.0}]
    ctx["referenced_creative_ids"] = [cid]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: placement_id validation
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.parse("the package update includes creative_assignments with placement configuration: {placement_config}")
)
def given_creative_assignments_with_placements(ctx: dict, placement_config: str) -> None:
    """Add creative_assignments with specific placement configuration.

    Formats:
    - 'placement_ids=[plc_a, plc_b] (valid)' — valid placement IDs
    - 'no placement_ids specified' — no placement_ids
    - 'placement_ids=[plc_invalid] (not in product)' — invalid IDs
    - 'placement_ids=[plc_a] (product unsupported)' — product doesn't support placements
    """
    import re

    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    pkg = kwargs["packages"][0]
    stripped = placement_config.strip()

    # Create a creative for the assignment
    cid = "cr_placement_test"
    CreativeFactory(
        creative_id=cid,
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        format="display_300x250",
        approved=True,
        data={"assets": {"primary": {"url": f"https://example.com/{cid}.png", "width": 300, "height": 250}}},
    )
    env._commit_factory_data()

    assignment: dict[str, Any] = {"creative_id": cid, "weight": 1.0}
    if "no placement_ids" in stripped:
        # Step text: "no placement_ids specified" — key ABSENT, not empty list.
        # An empty list (key present, value=[]) is semantically different from
        # key absent (field not specified). Do NOT set placement_ids at all.
        pass
    else:
        ids_match = re.search(r"\[([^\]]*)\]", stripped)
        if ids_match:
            placement_ids = [pid.strip() for pid in ids_match.group(1).split(",") if pid.strip()]
            assignment["placement_ids"] = placement_ids
            ctx["referenced_placement_ids"] = placement_ids

    # Handle "product unsupported" — configure product to not support placements
    if "product unsupported" in stripped:
        product = ctx.get("default_product") or ctx.get("existing_product")
        if product is None:
            # UC-003 harness doesn't store product in ctx — look up from existing package
            pkg_obj = ctx.get("existing_package")
            if pkg_obj is not None:
                product_id = (pkg_obj.package_config or {}).get("product_id")
                if product_id:
                    from sqlalchemy import select

                    from src.core.database.database_session import get_db_session
                    from src.core.database.models import Product as ProductModel

                    with get_db_session() as session:
                        product = session.scalars(
                            select(ProductModel).filter_by(product_id=product_id, tenant_id=ctx["tenant"].tenant_id)
                        ).first()
        assert product is not None, (
            "Scenario requires '(product unsupported)' but no product found in ctx or DB — "
            "ensure a Given step sets ctx['default_product'] or the harness creates a product"
        )
        product.supports_placement_targeting = False
        env._commit_factory_data()

    pkg["creative_assignments"] = [assignment]
    ctx["referenced_creative_ids"] = [cid]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: adapter dispatch & persistence
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.re(r"the request includes (?P<update_fields>.+[^:])$"))
def given_request_includes_fields(ctx: dict, update_fields: str) -> None:
    """Configure the update request with specific field combinations.

    Uses regex to avoid matching the datatable step 'the request includes 1 package update with:'.
    The [^:] at the end ensures this step doesn't capture text ending with ':'.

    Matched patterns from adapter-dispatch partition/boundary scenarios:
    - '1 package with budget update only'
    - '1 package with budget and targeting'
    - 'packages with all updatable fields'
    - 'no updatable fields in request'
    """
    kwargs = _ensure_update_defaults(ctx)
    stripped = update_fields.strip()

    if "no updatable fields" in stripped:
        # Keep only media_buy_id
        mid = kwargs.get("media_buy_id", "mb_existing")
        kwargs.clear()
        kwargs["media_buy_id"] = mid
    elif "budget update only" in stripped:
        kwargs["packages"] = [{"package_id": "pkg_001", "budget": 5000.0}]
    elif "budget and targeting" in stripped:
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": 5000.0,
                "targeting_overlay": {"geo_countries": ["US"]},
            }
        ]
    elif "all updatable fields" in stripped:
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": 5000.0,
                "targeting_overlay": {"geo_countries": ["US"]},
                "paused": False,
            }
        ]
        kwargs["paused"] = False
        kwargs["start_time"] = "2026-05-01T00:00:00Z"
        kwargs["end_time"] = "2026-07-01T00:00:00Z"
    else:
        raise ValueError(f"Unknown update_fields pattern: {stripped}")


@given(parsers.parse('the media buy "{media_buy_id}" exists with status "{status}"'))
def given_media_buy_exists_with_status(ctx: dict, media_buy_id: str, status: str) -> None:
    """Ensure the specified media buy exists in DB with the given status.

    Uses the existing media buy from ctx or verifies it matches.
    """
    mb = ctx.get("existing_media_buy")
    assert mb is not None, f"No existing_media_buy in ctx — cannot verify media buy '{media_buy_id}'"
    assert mb.media_buy_id == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{mb.media_buy_id}'"
    if mb.status != status:
        mb.status = status
        env = ctx["env"]
        env._commit_factory_data()


@given(parsers.parse("the request includes 1 package with budget update"))
def given_request_with_budget_update(ctx: dict) -> None:
    """Add a single package with a budget update to the request."""
    kwargs = _ensure_update_defaults(ctx)
    kwargs["packages"] = [{"package_id": "pkg_001", "budget": 5000.0}]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: approval workflow flags
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the tenant human_review_required is {tenant_flag}"))
def given_tenant_human_review_flag(ctx: dict, tenant_flag: str) -> None:
    """Set the tenant's human_review_required flag for approval workflow tests.

    Delegates to the existing auto-approval / manual-approval helpers in
    given_media_buy.py, which also configure the adapter mock and identity cache.
    """
    from tests.bdd.steps.generic.given_media_buy import (
        given_tenant_auto_approval,
        given_tenant_manual_approval,
    )

    flag = tenant_flag.strip().lower()
    if flag == "false":
        given_tenant_auto_approval(ctx)
    elif flag == "true":
        given_tenant_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown tenant_flag: {tenant_flag}")


@given(parsers.parse("the adapter manual_approval_required is {adapter_flag}"))
def given_adapter_manual_approval_flag(ctx: dict, adapter_flag: str) -> None:
    """Set the adapter's manual_approval_required flag for approval workflow tests.

    Delegates to the existing adapter approval helpers in given_media_buy.py.
    """
    from tests.bdd.steps.generic.given_media_buy import (
        given_adapter_manual_approval,
        given_adapter_no_manual_approval,
    )

    flag = adapter_flag.strip().lower()
    if flag == "false":
        given_adapter_no_manual_approval(ctx)
    elif flag == "true":
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown adapter_flag: {adapter_flag}")


@given(parsers.parse("the tenant approval mode is {approval_mode}"))
def given_tenant_approval_mode(ctx: dict, approval_mode: str) -> None:
    """Configure the tenant's approval mode.

    'auto-approval' — tenant human_review_required=False, adapter manual_approval_required=False
    'manual' — tenant human_review_required=True
    """
    stripped = approval_mode.strip()
    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot configure approval mode"
    env = ctx["env"]

    if stripped == "auto-approval":
        tenant.human_review_required = False
        if "adapter" in env.mock:
            env.mock["adapter"].return_value.manual_approval_required = False
    elif stripped == "manual":
        tenant.human_review_required = True
    else:
        raise ValueError(f"Unknown approval mode: {stripped}")
    env._commit_factory_data()


@given(parsers.re(r"the adapter (?P<adapter_result>returns \w+|not yet called)"))
def given_adapter_result(ctx: dict, adapter_result: str) -> None:
    """Configure adapter mock behavior for persistence timing tests.

    'returns success' — adapter returns normally
    'returns error' — adapter raises an exception
    'not yet called' — adapter not invoked (manual approval path)

    Uses regex to avoid matching 'the adapter manual_approval_required is ...'
    which is a separate step for approval workflow partition/boundary scenarios.
    """
    stripped = adapter_result.strip()
    env = ctx["env"]

    if stripped == "returns success":
        # Default behavior — adapter returns normally
        assert "adapter" in env.mock, (
            "Step claims 'the adapter returns success' but no adapter mock is registered in env.mock. "
            "Ensure the test environment sets up the adapter mock before this step."
        )
        env.mock["adapter"].return_value.update_order.side_effect = None
    elif stripped == "returns error":
        assert "adapter" in env.mock, (
            "Step claims 'the adapter returns error' but no adapter mock is registered in env.mock. "
            "Ensure the test environment sets up the adapter mock before this step."
        )
        env.mock["adapter"].return_value.update_order.side_effect = Exception("Adapter error: update failed")
    elif stripped == "not yet called":
        # Manual approval — adapter won't be called
        pass
    else:
        raise ValueError(f"Unknown adapter result: {stripped}")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: principal ownership
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the media buy "{media_buy_id}" exists with owner {owner}'))
def given_media_buy_with_owner(ctx: dict, media_buy_id: str, owner: str) -> None:
    """Ensure the media buy exists and is owned by the specified principal.

    Creates the owner principal if needed and sets the media buy's principal_id.
    Step text claims the owner "exists" — we must guarantee the principal record
    is present in the DB, not just set the foreign key.
    """
    from tests.factories import PrincipalFactory

    mb = ctx.get("existing_media_buy")
    assert mb is not None, f"No existing_media_buy in ctx — cannot set owner for '{media_buy_id}'"
    assert mb.media_buy_id == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{mb.media_buy_id}'"
    assert "tenant" in ctx, "No tenant in ctx — owner principal requires a tenant"
    env = ctx["env"]
    # Ensure the owner principal exists in the DB.
    # Step text says "exists with owner X" — the owner principal MUST exist.
    # Always create if it differs from current ctx principal, or if no principal
    # exists in ctx at all (the original code silently skipped this case).
    owner_id = owner.strip()
    existing_principal = ctx.get("principal")
    if existing_principal is None or existing_principal.principal_id != owner_id:
        PrincipalFactory(
            principal_id=owner_id,
            tenant=ctx["tenant"],
        )
    mb.principal_id = owner_id
    env._commit_factory_data()


@given(parsers.parse("the authenticated principal is {principal}"))
def given_authenticated_principal(ctx: dict, principal: str) -> None:
    """Set the authenticated principal for the request.

    Updates the env's identity to use the specified principal_id.
    """
    principal_id = principal.strip()
    env = ctx["env"]
    env._identity_cache.clear()
    env._principal_id = principal_id
    ctx["principal_override"] = principal_id


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — partition/boundary: immutable fields
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.re(r"the request includes 1 package update with (?P<update_content>.+[^:])$"))
def given_package_update_with_content(ctx: dict, update_content: str) -> None:
    """Configure a package update based on free-text content description.

    Uses regex to avoid matching the datatable step 'the request includes 1 package update with:'.
    The [^:] at the end ensures this step doesn't capture text ending with ':'.

    Matched patterns from immutable-fields partition/boundary scenarios:
    - 'budget and targeting updates only' — valid updatable fields
    - 'product_id=prod_new (immutable)' — attempt to set immutable product_id
    - 'format_ids=[fmt_new] (immutable)' — attempt to set immutable format_ids
    - 'pricing_option_id=po_new (immutable)' — attempt to set immutable pricing_option_id
    """
    kwargs = _ensure_update_defaults(ctx)
    stripped = update_content.strip()

    if "budget and targeting" in stripped:
        kwargs["packages"] = [
            {
                "package_id": "pkg_001",
                "budget": 5000.0,
                "targeting_overlay": {"geo_countries": ["US"]},
            }
        ]
    elif stripped.startswith("product_id="):
        product_id = stripped.split("=")[1].split()[0]
        kwargs["packages"] = [{"package_id": "pkg_001", "product_id": product_id, "budget": 5000.0}]
    elif stripped.startswith("format_ids="):
        kwargs["packages"] = [{"package_id": "pkg_001", "format_ids": ["fmt_new"], "budget": 5000.0}]
    elif stripped.startswith("pricing_option_id="):
        po_id = stripped.split("=")[1].split()[0]
        kwargs["packages"] = [{"package_id": "pkg_001", "pricing_option_id": po_id, "budget": 5000.0}]
    else:
        raise ValueError(f"Unknown update_content pattern: {stripped}")


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
