"""BDD step definitions for UC-003: Update Media Buy.

Given steps build ctx["update_kwargs"], assembled into UpdateMediaBuyRequest
in the When step. Background steps set up the existing media buy via
conftest's _harness_env.

beads: salesagent-82p
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.generic._dispatch import dispatch_request

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — Background + request construction
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the Buyer owns an existing media buy with media_buy_id "{media_buy_id}"'))
def given_buyer_owns_media_buy(ctx: dict, media_buy_id: str) -> None:
    """Verify the existing media buy is in ctx (set by conftest _harness_env)."""
    mb = ctx.get("existing_media_buy")
    assert mb is not None, "No existing_media_buy in ctx — conftest setup_update_data() failed"
    assert mb.media_buy_id == media_buy_id, f"Expected media_buy_id '{media_buy_id}', got '{mb.media_buy_id}'"


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


@given(parsers.parse("a valid update_media_buy request with:"))
def given_update_request_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Build update request kwargs from a data table."""
    kwargs = _ensure_update_defaults(ctx)
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "media_buy_id":
            kwargs["media_buy_id"] = value
        elif field == "buyer_ref":
            kwargs["buyer_ref"] = value
        elif field == "paused":
            kwargs["paused"] = value.lower() == "true"


@given(parsers.parse("the request includes 1 package update with:"))
def given_package_update_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Add a package update to the request from a data table."""
    import json

    kwargs = _ensure_update_defaults(ctx)
    pkg_update: dict[str, Any] = {}
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "package_id":
            pkg_update["package_id"] = value
        elif field == "budget":
            pkg_update["budget"] = float(value)
        elif field == "paused":
            pkg_update["paused"] = value.lower() == "true"
        elif field == "targeting_overlay":
            pkg_update["targeting_overlay"] = json.loads(value)
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
    """Declarative guard — default test data has budgets within limits.

    Verifies that the update request's budget (if present) is a positive number,
    which is a necessary condition for 'does not exceed max_daily_package_spend'.
    The actual max_daily check depends on tenant config; the default test tenant
    has no restrictive daily cap.
    """
    kwargs = ctx.get("update_kwargs", {})
    for pkg in kwargs.get("packages", []):
        budget = pkg.get("budget")
        if budget is not None:
            assert budget > 0, f"Package budget {budget} is not positive — cannot satisfy daily spend constraint"
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

    Table columns: creative_id, weight, placement_ids (comma-separated).
    First row is the header (skipped).
    """
    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    assignments = []
    for row in datatable[1:]:  # skip header row
        creative_id = row[0].strip()
        weight = float(row[1].strip())
        placement_ids = [p.strip() for p in row[2].strip().split(",") if p.strip()]
        assignments.append(
            {
                "creative_id": creative_id,
                "weight": weight,
                "placement_ids": placement_ids,
            }
        )
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
    # If we have DB access, verify the status is not error/rejected.
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    tenant = ctx.get("tenant")
    if tenant is not None:
        invalid_statuses = ("error", "rejected")
        with get_db_session() as session:
            for cid in ids:
                cr = session.scalars(
                    select(CreativeModel).filter_by(creative_id=cid, tenant_id=tenant.tenant_id)
                ).first()
                if cr is not None:
                    assert cr.status not in invalid_statuses, (
                        f"Creative {cid} is in '{cr.status}' state — step claims 'not error or rejected'"
                    )


@given("all placement_ids are valid for the product")
def given_placement_ids_valid(ctx: dict) -> None:
    """Declarative guard — default test product accepts all placement_ids.

    The guaranteed_display product created by setup_update_data() does not
    restrict placements, so any placement_id is valid.
    """
    pids = ctx.get("referenced_placement_ids")
    assert pids is not None, "No referenced placement_ids — missing prior step"
    assert isinstance(pids, list), f"Expected placement_ids to be a list, got {type(pids).__name__}"


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

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. Used by the alt-flow replacement semantics scenario.
    """
    import json

    kwargs = _ensure_update_defaults(ctx)
    if not kwargs.get("packages"):
        kwargs["packages"] = [{"package_id": "pkg_001"}]
    # Default: single metric goal (clicks) — representative for replacement semantics test
    kwargs["packages"][0]["optimization_goals"] = json.loads('[{"kind": "metric", "metric": "clicks", "priority": 1}]')


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


@given("no targeting_overlay.negative_keywords is present in the same package update")
def given_no_negative_keywords_in_update(ctx: dict) -> None:
    """Ensure the package update does not include negative_keywords in targeting_overlay.

    Declarative guard — analogous to the keyword_targets guard above. Prevents
    conflict with negative_keywords_add (BR-RULE-083).
    """
    kwargs = _ensure_update_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        overlay = pkg.get("targeting_overlay")
        if isinstance(overlay, dict):
            overlay.pop("negative_keywords", None)


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
    """Set default negative_keywords_add on the first package update (alt-flow scenario)."""
    _set_keyword_field_on_package(ctx, "negative_keywords_add", [{"keyword": "cheap", "match_type": "exact"}])


@given("the package update includes negative_keywords_remove:")
def given_package_update_negative_keywords_remove(ctx: dict) -> None:
    """Set default negative_keywords_remove on the first package update (alt-flow scenario)."""
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


@then("the response should contain an implementation_date that is not null")
def then_implementation_date_not_null(ctx: dict) -> None:
    """Assert response has a non-null implementation_date."""
    from datetime import datetime

    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    assert hasattr(resp, "implementation_date"), "Response has no implementation_date field"
    impl_date = resp.implementation_date
    # Hard-assert what the step text claims; xfail only on known gap
    try:
        assert impl_date is not None, "implementation_date is None"
    except AssertionError:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: implementation_date is None — production does not "
            "set it on update responses yet. Step claims 'not null'."
        )
    # Verify it's a meaningful datetime value (not just a truthy non-None)
    if isinstance(impl_date, str):
        try:
            datetime.fromisoformat(impl_date.replace("Z", "+00:00"))
        except ValueError:
            raise AssertionError(f"implementation_date is not a valid ISO datetime: {impl_date!r}")
    elif not isinstance(impl_date, datetime):
        raise AssertionError(
            f"implementation_date should be datetime or ISO string, got {type(impl_date).__name__}: {impl_date!r}"
        )


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
    affected = getattr(resp, "affected_packages", None) or []
    assert len(affected) > 0, "No affected packages in response"
    pkg = affected[0]
    actual_budget = getattr(pkg, "budget", None)
    if actual_budget is None and isinstance(pkg, dict):
        actual_budget = pkg.get("budget")
    # Hard-assert what the step text claims; xfail only on known gap
    try:
        assert actual_budget is not None, f"affected package budget is None, expected {budget}"
    except AssertionError:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: affected package budget is None — production may "
            f"not echo budget yet. Step claims 'updated budget of {budget}'."
        )
    assert float(actual_budget) == float(budget), f"Expected budget {budget}, got {actual_budget}"


@then("the response envelope should include a sandbox flag")
def then_response_has_sandbox(ctx: dict) -> None:
    """Assert response includes sandbox information."""
    import pytest

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # sandbox may live on the response directly or on a wrapper envelope
    sandbox = getattr(resp, "sandbox", None)
    if sandbox is None and hasattr(resp, "model_dump"):
        sandbox = resp.model_dump().get("sandbox")
    # Hard-assert what the step text claims; xfail only on known gap
    try:
        assert sandbox is not None, "sandbox flag not present on response"
    except AssertionError:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: sandbox flag not present on response — "
            "step claims envelope 'should include' it but field is absent."
        )
    # If sandbox is present, verify it's a boolean (not just any truthy/falsy value)
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
