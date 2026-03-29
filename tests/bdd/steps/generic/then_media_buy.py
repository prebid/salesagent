"""Then steps for create_media_buy response assertions.

Asserts on ``ctx["response"]`` (CreateMediaBuyResult or CreateMediaBuySuccess)
and ``ctx["error"]`` (AdCPError or CreateMediaBuyError).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ═══════════════════════════════════════════════════════════════════════
# Response success assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the response should succeed")
def then_response_succeeds(ctx: dict) -> None:
    """Assert the response is a success (no error, has response object)."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"


@then("the pricing validation should pass")
def then_pricing_validation_passes(ctx: dict) -> None:
    """Assert pricing validation passed — no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected pricing validation to pass but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found (pricing validation may have failed silently)"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response — pricing validation passed but no media buy created"


@then("the budget validation should pass")
def then_budget_validation_passes(ctx: dict) -> None:
    """Assert budget validation passed — no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected budget validation to pass but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found (budget validation may have failed silently)"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response — budget validation passed but no media buy created"


@then("the date validation should pass")
def then_date_validation_passes(ctx: dict) -> None:
    """Assert date validation passed — no error, response has media_buy_id."""
    assert "error" not in ctx, f"Expected date validation to pass but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found (date validation may have failed silently)"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "Expected media_buy_id in response — date validation passed but no media buy created"


@then(parsers.parse('the response should include a "{field}"'))
def then_response_includes_field(ctx: dict, field: str) -> None:
    """Assert response includes the specified field with a non-None value."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    # Check on the response object — may be CreateMediaBuyResult wrapping a Success
    value = _get_response_field(resp, field)
    assert value is not None, f"Expected '{field}' in response, got None"


@then(parsers.parse('the response should include "{field}" matching "{value}"'))
def then_response_field_matches(ctx: dict, field: str, value: str) -> None:
    """Assert response field matches the expected value."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    actual = _get_response_field(resp, field)
    assert str(actual) == value, f"Expected {field}='{value}', got '{actual}'"


@then("the response should include packages with allocations")
def then_response_has_packages(ctx: dict) -> None:
    """Assert response includes packages array with allocated packages (product_id assigned)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    packages = _get_response_field(resp, "packages")
    assert packages is not None, "Expected 'packages' in response"
    assert len(packages) > 0, "Expected at least one package in response"
    # "with allocations" means each package has a product_id (allocation to a product)
    for i, pkg in enumerate(packages):
        pkg_dict = pkg if isinstance(pkg, dict) else (pkg.model_dump() if hasattr(pkg, "model_dump") else vars(pkg))
        assert pkg_dict.get("product_id"), f"Package {i} missing product_id — not allocated"


@then("each package should include product_id, budget, and pricing details")
def then_packages_have_details(ctx: dict) -> None:
    """Assert each package has product_id, budget, AND pricing details."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    packages = _get_response_field(resp, "packages")
    assert packages, "No packages in response"
    for i, pkg in enumerate(packages):
        pkg_dict = pkg if isinstance(pkg, dict) else (pkg.model_dump() if hasattr(pkg, "model_dump") else vars(pkg))
        assert "product_id" in pkg_dict, f"Package {i} missing product_id"
        assert "budget" in pkg_dict, f"Package {i} missing budget"
        # Step text claims "pricing details" — verify pricing_option_id is present
        assert pkg_dict.get("pricing_option_id"), f"Package {i} missing pricing_option_id (pricing details)"


# ═══════════════════════════════════════════════════════════════════════
# Approval workflow assertions (BR-RULE-017)
# ═══════════════════════════════════════════════════════════════════════


@then("the approval path should be auto-approved")
def then_approval_auto(ctx: dict) -> None:
    """Assert the media buy was auto-approved — no manual approval step required.

    Auto-approval means:
    1. status='completed' (full pipeline ran synchronously)
    2. No workflow_step_id on response packages (no pending approval task created)

    This distinguishes auto-approved from manually-approved-then-completed,
    where a workflow step would have been created even if it was later resolved.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    status = _get_response_field(resp, "status")
    assert status == "completed", (
        f"Expected auto-approval (status='completed' per BR-RULE-017), got '{status}'. "
        "Auto-approved media buys complete the full pipeline synchronously."
    )
    # Auto-approved means no manual approval workflow step was created.
    # Check that no package carries a workflow_step_id (which would indicate
    # a pending approval task was created, even if it was later completed).
    inner = getattr(resp, "response", resp)
    pkgs = getattr(inner, "packages", None) or []
    for pkg in pkgs:
        wf_step_id = getattr(pkg, "workflow_step_id", None)
        assert wf_step_id is None, (
            f"Package {getattr(pkg, 'package_id', '?')} has workflow_step_id={wf_step_id} — "
            "auto-approved media buys should not create approval workflow steps"
        )


@then("the media buy should proceed to adapter execution")
def then_adapter_executed(ctx: dict) -> None:
    """Assert the adapter's create_media_buy was called exactly once (auto-approval path)."""
    env = ctx["env"]
    adapter_mock = env.mock["adapter"].return_value
    assert adapter_mock.create_media_buy.call_count == 1, (
        f"Expected adapter.create_media_buy to be called exactly once (auto-approval path), "
        f"but it was called {adapter_mock.create_media_buy.call_count} time(s)"
    )
    # Verify the adapter received a request argument (not called with empty args)
    call_args = adapter_mock.create_media_buy.call_args
    assert call_args is not None, "adapter.create_media_buy was called but call_args is None"
    assert len(call_args.args) > 0 or len(call_args.kwargs) > 0, "adapter.create_media_buy was called with no arguments"


@then("the approval path should be manual")
def then_approval_manual(ctx: dict) -> None:
    """Assert the response indicates manual approval (task status 'submitted').

    Production: manual approval → DB status=pending_approval, task status=submitted.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    status = _get_response_field(resp, "status")
    assert status == "submitted", f"Expected manual approval (status='submitted'), got '{status}'"


@then("the media buy should enter pending state")
def then_pending_state(ctx: dict) -> None:
    """Assert the media buy was persisted with status 'pending_approval' in DB."""
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    resp = ctx.get("response")
    assert resp is not None, "Expected a response to find media_buy_id"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        assert mb.status == "pending_approval", f"Expected DB status 'pending_approval', got '{mb.status}'"


# ═══════════════════════════════════════════════════════════════════════
# Status and workflow assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the media buy status should be "{status}"'))
def then_media_buy_status(ctx: dict, status: str) -> None:
    """Assert the media buy has the expected status.

    Checks response first (preferred), then falls back to DB query.
    Both paths must assert the exact status — no silent fallthrough.
    """
    resp = ctx.get("response")
    media_buy = ctx.get("existing_media_buy")
    assert resp is not None or media_buy is not None, (
        "No response or existing media buy to check status — "
        f"step claims status should be '{status}' but nothing to verify against"
    )
    if resp is not None:
        actual = _get_response_field(resp, "status")
        assert actual == status, f"Expected media buy status '{status}' in response, got '{actual}'"
        return
    # Fallback: check existing media buy in DB (explicit path, not silent)
    env = ctx["env"]
    env._commit_factory_data()
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy.media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy.media_buy_id} not found in DB"
        assert mb.status == status, f"Expected DB status '{status}', got '{mb.status}'"


# ═══════════════════════════════════════════════════════════════════════
# Notification assertions
# ═══════════════════════════════════════════════════════════════════════


@then("a Slack notification should be sent to the Seller")
def then_slack_notification_sent(ctx: dict) -> None:
    """Assert Slack notifier was called with seller-facing event details.

    Step text claims 'sent to the Seller'. Slack notifications go to the
    tenant/publisher (the Seller). The event_type must be seller-relevant
    (approval_required, created, config_approval_required) and include
    tenant context (tenant_name).
    """
    env = ctx["env"]
    mock_slack = env.mock["slack"].return_value
    # assert_called_once() ensures exactly one notification — .called allows multiple
    mock_slack.notify_media_buy_event.assert_called_once()
    call_args = mock_slack.notify_media_buy_event.call_args
    assert call_args is not None, "Slack notify_media_buy_event called but call_args is None"
    # Extract all args upfront for combined verification
    event_type = call_args.args[0] if call_args.args else call_args.kwargs.get("event_type")
    media_buy_id = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("media_buy_id")
    assert event_type, "Slack notification missing event_type argument"
    assert media_buy_id, "Slack notification missing media_buy_id — cannot confirm it references the correct media buy"
    # Seller-facing events: events where the Seller (publisher/tenant) is the audience.
    # - "created": notifies the seller that a buyer submitted a new media buy for
    #   their inventory — seller needs to know a new order arrived, even if auto-approved.
    # - "approval_required": seller must review and approve the media buy.
    # - "config_approval_required": seller must configure adapter settings.
    # Buyer-facing events (rejected, approved, status_changed) are NOT seller events
    # and should never appear here.
    seller_event_types = ("approval_required", "created", "config_approval_required")
    assert event_type in seller_event_types, (
        f"Expected seller-facing event_type (one of {seller_event_types}), "
        f"got '{event_type}' — this event type does not target the Seller. "
        f"Buyer-facing events (rejected, approved, status_changed) should not "
        f"be sent to the Seller's Slack channel."
    )
    # Verify the notification references the CORRECT media buy from this scenario
    resp = ctx.get("response")
    if resp is not None:
        expected_mb_id = _get_response_field(resp, "media_buy_id")
        if expected_mb_id:
            assert media_buy_id == expected_mb_id, (
                f"Slack notification sent for media_buy_id '{media_buy_id}' but scenario "
                f"created '{expected_mb_id}' — notification targets the wrong media buy"
            )
    # Verify tenant context is included (Seller = tenant/publisher).
    # Production calls notify_media_buy_event with tenant_name as a keyword argument
    # (see media_buy_create.py: tenant_name=tenant.get("name", "Unknown")).
    # Assert it was passed as a kwarg — positional fallback is fragile and speculative.
    assert "tenant_name" in call_args.kwargs, (
        "Slack notification missing tenant_name keyword argument — production passes "
        "tenant_name as a kwarg; if it arrived positionally the call signature has diverged"
    )
    tenant_name = call_args.kwargs["tenant_name"]
    assert tenant_name, "Slack notification has empty tenant_name — cannot confirm it targets the Seller"
    # Verify the notification targets the correct seller (tenant from this scenario)
    tenant = ctx.get("tenant")
    if tenant is not None:
        expected_tenant_name = getattr(tenant, "name", None)
        if expected_tenant_name:
            assert tenant_name == expected_tenant_name, (
                f"Slack notification tenant_name '{tenant_name}' does not match scenario "
                f"tenant name '{expected_tenant_name}' — notification targets the wrong Seller"
            )


@then("the Buyer should be notified via webhook")
def then_webhook_notification(ctx: dict) -> None:
    """Assert buyer webhook notification pipeline is configured.

    Buyer notifications use push_notification_config — a webhook URL the buyer
    provides on the request. Production stores this config in PushNotificationConfig;
    a background worker later POSTs to the URL. This step verifies the config
    was persisted (proves the buyer WILL be notified), not that the HTTP POST
    happened (that's async, tested in integration/e2e).
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import PushNotificationConfig

    # --- Extract media_buy_id and tenant ---
    resp = ctx.get("response")
    existing_mb = ctx.get("existing_media_buy")
    assert resp is not None or existing_mb is not None, (
        "No response or existing media buy in ctx — nothing to notify the Buyer about"
    )

    media_buy_id = None
    if resp is not None:
        media_buy_id = _get_response_field(resp, "media_buy_id")
    elif existing_mb is not None:
        media_buy_id = getattr(existing_mb, "media_buy_id", None)
    assert media_buy_id, "No media_buy_id — cannot verify notification config"

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify notification config scoping"
    tenant_id = getattr(tenant, "tenant_id", None) or (tenant.get("tenant_id") if isinstance(tenant, dict) else None)

    # --- Check push_notification_config was provided on request ---
    push_config = ctx.get("push_notification_config")
    if push_config is None:
        request_kwargs = ctx.get("request_kwargs", {})
        push_config = request_kwargs.get("push_notification_config")
    assert push_config is not None, (
        "push_notification_config not provided on request — scenario should include "
        "a Given step that sets push_notification_config. Without it, production has "
        "no webhook URL to store, and the step claim 'the Buyer should be notified "
        "via webhook' cannot be verified. This is a scenario setup issue, not a "
        "production gap."
    )

    # --- Verify PushNotificationConfig was persisted ---
    with get_db_session() as session:
        configs = session.scalars(select(PushNotificationConfig).filter_by(tenant_id=tenant_id)).all()
        assert len(configs) > 0, (
            f"No PushNotificationConfig records for tenant {tenant_id} — "
            "production did not persist the buyer's webhook config"
        )
        # Verify the URL matches what the buyer provided
        expected_url = push_config.get("url") if isinstance(push_config, dict) else None
        if expected_url:
            stored_urls = [getattr(c, "url", None) for c in configs]
            assert expected_url in stored_urls, (
                f"Expected webhook URL '{expected_url}' not found in stored configs. Stored URLs: {stored_urls}"
            )


# ═══════════════════════════════════════════════════════════════════════
# Persistence assertions
# ═══════════════════════════════════════════════════════════════════════


@then("no media buy record should be persisted in the database")
@then("no media buy record should be persisted")
def then_no_media_buy_persisted(ctx: dict) -> None:
    """Assert no new media buy was created in the database."""
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        count = session.scalar(select(func.count()).select_from(MediaBuy).filter_by(tenant_id=tenant.tenant_id))
        # Allow existing media buys created by Given steps
        existing_count = 1 if ctx.get("existing_media_buy") else 0
        assert count == existing_count, f"Expected {existing_count} media buy(s) in DB, found {count}"


@then("the media buy record should be persisted in the database")
@then("the media buy record should be persisted")
def then_media_buy_persisted(ctx: dict) -> None:
    """Assert a media buy was persisted in the database with correct field values."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response to check persistence"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response to verify persistence"

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in database"
        # Verify key field values are populated (not just existence)
        tenant = ctx.get("tenant")
        assert tenant is not None, "No tenant in ctx — cannot verify tenant_id on persisted media buy"
        assert mb.tenant_id == tenant.tenant_id, f"Expected tenant_id '{tenant.tenant_id}', got '{mb.tenant_id}'"
        assert mb.status is not None, f"Media buy {media_buy_id} persisted with no status"
        # Verify buyer_ref from the request was persisted correctly
        request_kwargs = ctx.get("request_kwargs", {})
        expected_buyer_ref = request_kwargs.get("buyer_ref")
        if expected_buyer_ref:
            assert mb.buyer_ref == expected_buyer_ref, (
                f"Expected buyer_ref '{expected_buyer_ref}' on persisted media buy, got '{mb.buyer_ref}'"
            )
        # Verify principal linkage
        principal = ctx.get("principal")
        if principal is not None:
            assert mb.principal_id is not None, (
                f"Media buy {media_buy_id} persisted without principal_id — "
                "step claims record is 'persisted' but identity linkage is missing"
            )


@then(parsers.parse('the media buy record should be persisted with status "{status}"'))
def then_media_buy_persisted_with_status(ctx: dict, status: str) -> None:
    """Assert media buy is persisted with expected status."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found"
        assert mb.status == status, f"Expected status '{status}', got '{mb.status}'"


@then("the package records should be persisted")
def then_package_records_persisted(ctx: dict) -> None:
    """Assert media buy packages were persisted in the database with correct count."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response to check package persistence"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response to verify package persistence"

    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    with get_db_session() as session:
        count = session.scalar(select(func.count()).select_from(MediaPackage).filter_by(media_buy_id=media_buy_id))
        assert count and count > 0, f"No package records found for media buy {media_buy_id}"
        # Verify count matches the number of packages in the request
        request_kwargs = ctx.get("request_kwargs", {})
        expected_count = len(request_kwargs.get("packages", []))
        if expected_count > 0:
            assert count == expected_count, (
                f"Expected {expected_count} package record(s) for media buy {media_buy_id}, found {count}"
            )


@then("no package records should be persisted")
def then_no_package_records_persisted(ctx: dict) -> None:
    """Assert no package records were created for the tenant."""
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, MediaPackage

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with get_db_session() as session:
        count = session.scalar(
            select(func.count())
            .select_from(MediaPackage)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .filter(MediaBuy.tenant_id == tenant.tenant_id)
        )
        # Allow existing packages created by Given steps
        existing_count = 0
        if ctx.get("existing_media_buy"):
            existing_mb = ctx["existing_media_buy"]
            existing_count = len(getattr(existing_mb, "packages", []) or [])
        assert count == existing_count, f"Expected {existing_count} package record(s) in DB, found {count}"


@then(parsers.parse("the package budget should be persisted as {budget:d}"))
def then_package_budget_persisted(ctx: dict, budget: int) -> None:
    """Assert the package budget was persisted in the database with the expected value.

    Queries the real DB for the package referenced in the update request and
    verifies its budget matches the expected value. This checks actual persistence,
    not just the response payload.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaPackage

    # Determine package_id from the update request or existing package
    update_kwargs = ctx.get("update_kwargs", {})
    packages = update_kwargs.get("packages", [])
    if packages:
        package_id = packages[0].get("package_id")
    else:
        pkg = ctx.get("existing_package")
        assert pkg is not None, "No package in update request or ctx — cannot verify budget"
        package_id = pkg.package_id

    assert package_id, "No package_id found to verify budget persistence"

    with get_db_session() as session:
        db_pkg = session.scalars(select(MediaPackage).filter_by(package_id=package_id)).first()
        assert db_pkg is not None, f"Package {package_id} not found in DB"
        assert db_pkg.budget == budget, (
            f"Expected package budget {budget}, got {db_pkg.budget} — "
            "BR-RULE-020 INV-1: adapter success should persist changes"
        )


@then("the creative assignment records should be persisted")
def then_creative_assignment_records_persisted(ctx: dict) -> None:
    """Assert creative assignment records were persisted in the database.

    Verifies: (1) records exist, and (2) count matches the total number of
    creative_ids across all packages in the request.
    If no creative_ids were requested, this step passes (no assignments expected).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response to check creative assignment persistence"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    # Count expected creative assignments from the request
    request_kwargs = ctx.get("request_kwargs", {})
    expected_count = sum(len(pkg.get("creative_ids", []) or []) for pkg in request_kwargs.get("packages", []))
    if expected_count == 0:
        return  # No creative assignments expected

    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CreativeAssignment

    with get_db_session() as session:
        actual_count = session.scalar(
            select(func.count()).select_from(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
        )
        assert actual_count and actual_count > 0, f"No creative assignment records found for media buy {media_buy_id}"
        assert actual_count == expected_count, (
            f"Expected {expected_count} creative assignment record(s) for media buy {media_buy_id} "
            f"(matching creative_ids in request), found {actual_count}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Response field rejection
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the response should include "rejection_reason" containing "{text}"'))
def then_rejection_reason_contains(ctx: dict, text: str) -> None:
    """Assert rejection_reason field contains expected text."""
    resp = ctx.get("response")
    if resp is None:
        resp = ctx.get("existing_media_buy")
    assert resp is not None, "No response or media buy to check"
    reason = _get_response_field(resp, "rejection_reason") or ""
    assert text.lower() in reason.lower(), f"Expected '{text}' in rejection_reason: {reason}"


# ═══════════════════════════════════════════════════════════════════════
# ASAP start_time resolution assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the system should resolve start_time to current UTC")
def then_start_time_resolved_to_utc(ctx: dict) -> None:
    """Assert the persisted media buy has start_time close to now (ASAP resolved)."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    resp = ctx.get("response")
    assert resp is not None, "Expected a response to find media_buy_id"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        assert mb.start_time is not None, "start_time not set on persisted media buy"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 30, f"start_time {mb.start_time} is {delta}s from now — expected within 30s for ASAP"


@then("the campaign should be immediately activating")
def then_campaign_immediately_activating(ctx: dict) -> None:
    """Assert the campaign is immediately activating: auto-approved AND start_time near now.

    "Immediately activating" means:
    1. Task status is "completed" (workflow succeeded, not stuck in manual approval)
    2. DB media buy status is NOT "pending_approval" (bypassed manual approval)
    3. start_time is near-now (ASAP was resolved to current UTC)
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Task status "completed" means the create_media_buy workflow step finished
    # successfully — the adapter was called (not held for manual approval).
    # "submitted" would indicate manual approval pending.
    status = _get_response_field(resp, "status")
    assert status == "completed", (
        f"Expected task status 'completed' for immediate activation (got '{status}'). "
        f"'submitted' would mean manual approval is pending; 'failed' means adapter error."
    )
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        # DB status must NOT be "pending_approval" — that means manual approval was required,
        # contradicting "immediately activating"
        assert mb.status != "pending_approval", (
            f"Media buy {media_buy_id} has DB status 'pending_approval' — "
            f"campaign is waiting for manual approval, not 'immediately activating'"
        )
        # start_time must be near-now (ASAP resolved to current UTC)
        assert mb.start_time is not None, "start_time not set — campaign cannot be 'immediately activating'"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 60, (
            f"start_time {mb.start_time} is {delta}s from now — expected within 60s for 'immediately activating'"
        )


@then('the response should include resolved start_time (not literal "asap")')
def then_response_includes_resolved_start_time(ctx: dict) -> None:
    """Assert the response contains a resolved start_time, not the literal 'asap'.

    SPEC-PRODUCTION GAP: CreateMediaBuySuccess has no top-level start_time field,
    and Package.start_time / PlannedDelivery are not populated by production code.
    If production doesn't expose start_time in the response, the SCENARIO should be
    xfailed in conftest.py (not the step body). See salesagent-12nd.
    """
    from datetime import UTC, datetime

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    # Check response first — if start_time is present on response, verify it's
    # a resolved datetime (not literal "asap") and within a reasonable window.
    resp_start_time = _get_response_field(resp, "start_time")
    if resp_start_time is not None:
        resp_str = str(resp_start_time)
        assert resp_str != "asap", "Response start_time is literal 'asap', not resolved"
        # Verify it parses as a real datetime (not some other non-datetime string)
        if isinstance(resp_start_time, str):
            parsed = datetime.fromisoformat(resp_start_time.replace("Z", "+00:00"))
            delta = abs((parsed - datetime.now(UTC)).total_seconds())
            assert delta < 60, (
                f"Response start_time {resp_start_time} is {delta:.1f}s from now — "
                "expected within 60s of current UTC for 'asap' resolution"
            )
        return

    # Check package-level start_time before falling back to DB
    inner = getattr(resp, "response", resp)
    pkgs = getattr(inner, "packages", None) or getattr(resp, "packages", None) or []
    for pkg in pkgs:
        pkg_start = getattr(pkg, "start_time", None)
        if pkg_start is not None and str(pkg_start) != "asap":
            # Package has a resolved start_time — step claim satisfied at package level
            if isinstance(pkg_start, str):
                parsed = datetime.fromisoformat(pkg_start.replace("Z", "+00:00"))
                delta = abs((parsed - datetime.now(UTC)).total_seconds())
                assert delta < 60, (
                    f"Package start_time {pkg_start} is {delta:.1f}s from now — "
                    "expected within 60s for 'asap' resolution"
                )
            return

    # Step text claims "response should include resolved start_time" — hard assert.
    # No DB fallback: the step tests the RESPONSE, not the database.
    # If production doesn't expose start_time in the response, the SCENARIO
    # should be xfailed in conftest.py. See salesagent-12nd.
    raise AssertionError(
        f"Response has no resolved start_time — checked top-level and {len(pkgs)} package(s). "
        "Step text claims 'response should include resolved start_time (not literal asap)'."
    )


# ═══════════════════════════════════════════════════════════════════════
# Atomic response shape assertions (BR-RULE-018)
# ═══════════════════════════════════════════════════════════════════════


@then("the response should have success fields")
def then_response_has_success_fields(ctx: dict) -> None:
    """Assert response contains success-only fields with valid values.

    Success fields for BR-RULE-018: media_buy_id (non-empty string),
    packages (non-empty list), and status (valid completion status).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a success response but none found"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert isinstance(media_buy_id, str) and len(media_buy_id) > 0, (
        f"Expected non-empty string media_buy_id in success response, got: {media_buy_id!r}"
    )
    packages = _get_response_field(resp, "packages")
    assert isinstance(packages, list), f"Expected packages to be a list, got: {type(packages).__name__}"
    assert len(packages) > 0, "Expected at least one package in success response"
    status = _get_response_field(resp, "status")
    assert status is not None, "Expected status field in success response"
    valid_statuses = ("completed", "submitted", "pending_approval", "activating", "pending_activation")
    assert status in valid_statuses, f"Expected valid success status (one of {valid_statuses}), got: {status!r}"


@then('the response should NOT have an "errors" field')
def then_response_no_errors_field(ctx: dict) -> None:
    """Assert the success response has no errors field or errors is None/absent.

    Step says 'NOT have an "errors" field' — the field should be absent or None,
    not merely an empty list (which would mean the field IS present but empty).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Check the inner response (unwrap CreateMediaBuyResult)
    inner = getattr(resp, "response", resp)
    if isinstance(inner, dict):
        assert "errors" not in inner or inner["errors"] is None, (
            f'Expected no "errors" field on success response, got: {inner.get("errors")}'
        )
    else:
        errors = getattr(inner, "errors", None)
        assert errors is None, f'Expected "errors" field to be absent/None on success response, got: {errors!r}'


@then('the response should have an "errors" array')
def then_response_has_errors_array(ctx: dict) -> None:
    """Assert the response contains a non-empty errors array."""
    error_response = ctx.get("error_response")
    assert error_response is not None, (
        "Expected error_response in ctx (dispatch promotes errors from CreateMediaBuyError)"
    )
    errors = getattr(error_response, "errors", None)
    assert errors and len(errors) > 0, f"Expected non-empty errors array, got: {errors}"


@then("the response should NOT have success fields (media_buy_id, packages)")
def then_response_no_success_fields(ctx: dict) -> None:
    """Assert the error response has no success fields (media_buy_id, packages)."""
    # On error path, ctx["response"] is deleted by dispatch — only ctx["error_response"] remains
    resp = ctx.get("response")
    if resp is not None:
        raise AssertionError("Expected no success response, but ctx['response'] is present")
    error_response = ctx.get("error_response")
    if error_response is not None:
        media_buy_id = getattr(error_response, "media_buy_id", None)
        assert not media_buy_id, f"Expected no media_buy_id on error response, got: {media_buy_id}"
        packages = getattr(error_response, "packages", None)
        assert not packages, f"Expected no packages on error response, got: {packages}"


@then('each error should include "suggestion" field')
def then_each_error_has_suggestion(ctx: dict) -> None:
    """Assert every error in the errors array includes a suggestion field."""
    error_response = ctx.get("error_response")
    assert error_response is not None, "Expected error_response in ctx"
    errors = getattr(error_response, "errors", [])
    assert errors, "Expected non-empty errors array"
    for i, err in enumerate(errors):
        suggestion = getattr(err, "suggestion", None)
        assert suggestion, f"Error[{i}] missing 'suggestion' field: {err}"


@then('the error should include "retry_after" field')
def then_error_has_retry_after(ctx: dict) -> None:
    """Assert the error includes a retry_after hint (transient error recovery).

    Step claims the field should be 'included' — verify it exists and contains
    a positive numeric value (retry delay in seconds).

    Checks both ctx["error"] (AdCPError from dispatch) and ctx["error_response"]
    (structured error response) to match the dispatch contract.
    """
    # Check both error keys to match the dispatch contract used by other error steps
    error = ctx.get("error") or ctx.get("error_response")
    assert error is not None, (
        "No error recorded in ctx (checked both 'error' and 'error_response') — "
        "step claims error should include retry_after but no error was captured"
    )
    # AdCPError stores retry_after in details dict
    from src.core.exceptions import AdCPError

    retry_after = None
    if isinstance(error, AdCPError):
        assert error.details is not None, "Expected error details with retry_after"
        assert "retry_after" in error.details, f"Expected 'retry_after' in error details, got: {error.details}"
        retry_after = error.details["retry_after"]
    else:
        # adcp.types.Error model — check for retry_after attribute
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None and hasattr(error, "details"):
            retry_after = (error.details or {}).get("retry_after")
    assert retry_after is not None, f"Expected retry_after field on error, but it is absent: {error}"
    # retry_after should be a positive number (seconds to wait before retrying)
    assert isinstance(retry_after, (int, float)), (
        f"Expected retry_after to be a number (seconds), got {type(retry_after).__name__}: {retry_after!r}"
    )
    assert retry_after > 0, f"Expected positive retry_after value, got {retry_after}"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _get_response_field(resp: object, field: str) -> object:
    """Extract a field from a response object, handling wrapper types.

    CreateMediaBuyResult wraps CreateMediaBuySuccess — check both levels.
    """
    # Direct attribute
    if hasattr(resp, field):
        return getattr(resp, field)
    # CreateMediaBuyResult wraps .response
    inner = getattr(resp, "response", None)
    if inner is not None and hasattr(inner, field):
        return getattr(inner, field)
    # Dict fallback
    if isinstance(resp, dict):
        return resp.get(field)
    return None
