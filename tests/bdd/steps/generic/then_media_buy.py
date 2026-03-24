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
    """Assert the response indicates auto-approval (task status 'completed').

    Production: auto-approved → adapter called synchronously → status=completed.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    status = _get_response_field(resp, "status")
    assert status == "completed", f"Expected auto-approval (status='completed'), got '{status}'"


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
    """Assert the media buy has the expected status."""
    resp = ctx.get("response")
    if resp is not None:
        actual = _get_response_field(resp, "status")
        assert actual == status, f"Expected media buy status '{status}', got '{actual}'"
        return
    # Check existing media buy in DB
    media_buy = ctx.get("existing_media_buy")
    if media_buy is not None:
        env = ctx["env"]
        env._commit_factory_data()
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy

        with get_db_session() as session:
            mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy.media_buy_id)).first()
            assert mb is not None, f"Media buy {media_buy.media_buy_id} not found in DB"
            assert mb.status == status, f"Expected status '{status}', got '{mb.status}'"
        return
    raise AssertionError("No response or existing media buy to check status")


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
    assert mock_slack.notify_media_buy_event.called, "Expected Slack notification to be sent"
    call_args = mock_slack.notify_media_buy_event.call_args
    assert call_args is not None, "Slack notify_media_buy_event called but call_args is None"
    event_type = call_args.args[0] if call_args.args else call_args.kwargs.get("event_type")
    assert event_type, "Slack notification missing event_type argument"
    # Seller-facing events: approval requests go to the seller's Slack channel
    seller_event_types = ("approval_required", "created", "config_approval_required")
    assert event_type in seller_event_types, (
        f"Expected seller-facing event_type (one of {seller_event_types}), "
        f"got '{event_type}' — this may not target the Seller"
    )
    # Verify tenant context is included (Seller = tenant/publisher)
    tenant_name = call_args.kwargs.get("tenant_name")
    if not tenant_name and len(call_args.args) > 4:
        tenant_name = call_args.args[4]
    assert tenant_name, "Slack notification missing tenant_name — cannot confirm it targets the Seller"


@then("the Buyer should be notified via webhook")
def then_webhook_notification(ctx: dict) -> None:
    """Assert buyer webhook notification was sent.

    SPEC-PRODUCTION GAP: The BDD harness does not yet wire push_notification_config
    or the webhook delivery service. When wired, this step should verify:
    1. The webhook was POSTed to the buyer's push_notification_config URL
    2. The payload includes the media_buy_id and event details
    3. The notification targets the Buyer (not the Seller)

    FIXME(salesagent-9vgz.1): Wire webhook service mock in harness to replace xfail.
    """
    import pytest

    # Verify preconditions: a response or error must exist (the event to notify about)
    resp = ctx.get("response")
    error = ctx.get("error")
    assert resp is not None or error is not None, "No response or error in ctx — nothing to notify the Buyer about"
    # Verify the media_buy_id is present (webhook payload needs it)
    if resp is not None:
        media_buy_id = _get_response_field(resp, "media_buy_id")
        assert media_buy_id, (
            "Response has no media_buy_id — webhook cannot notify Buyer without identifying the media buy"
        )
    # Verify a state change occurred that WOULD trigger a buyer webhook notification.
    # Webhooks fire on status transitions (rejected, approved, delivered, etc.).
    if resp is not None:
        status = _get_response_field(resp, "status")
        assert status is not None, "Response has no status — cannot verify a webhook-triggering state change occurred"
        webhook_triggering_statuses = {
            "rejected",
            "approved",
            "active",
            "delivered",
            "completed",
            "pending_approval",
        }
        assert status in webhook_triggering_statuses, (
            f"Status '{status}' is not a webhook-triggering status. Expected one of {webhook_triggering_statuses}"
        )
    # SPEC-PRODUCTION GAP: xfail when harness lacks webhook mock.
    # Preconditions above verify the trigger event occurred; this gap is about
    # verifying actual webhook delivery, which requires a wired mock.
    webhook_mock = ctx.get("webhook_mock") or ctx.get("notification_mock")
    if webhook_mock is None:
        pytest.xfail(
            "SPEC-PRODUCTION GAP: Webhook notification service not wired in BDD harness. "
            "Preconditions verified (event occurred, media_buy_id present, status is "
            "webhook-triggering), but cannot verify actual webhook delivery. "
            "FIXME(salesagent-9vgz.1)"
        )
    # --- Assertions below only run when webhook mock IS wired ---
    webhook_mock.assert_called_once()
    call_args = webhook_mock.call_args
    # Payload must include media_buy_id so the Buyer can correlate the notification
    payload = call_args.kwargs if call_args.kwargs else {}
    if call_args.args:
        # Positional arg[0] is often the payload dict
        payload = call_args.args[0] if isinstance(call_args.args[0], dict) else payload
    assert "media_buy_id" in payload, (
        "Webhook was called but payload has no media_buy_id — Buyer cannot correlate notification to a media buy"
    )
    # Verify notification targets the Buyer (event_type should be buyer-facing)
    if "event_type" in payload:
        buyer_events = {"rejected", "approved", "status_changed", "delivered", "completed"}
        assert payload["event_type"] in buyer_events, (
            f"Webhook event_type '{payload['event_type']}' is not a buyer-facing event. Expected one of {buyer_events}"
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
        if tenant is not None:
            assert mb.tenant_id == tenant.tenant_id, f"Expected tenant_id '{tenant.tenant_id}', got '{mb.tenant_id}'"
        assert mb.status is not None, f"Media buy {media_buy_id} persisted with no status"


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
    """Assert the campaign is immediately activating: auto-approved AND start_time near now."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Auto-approved means status == "completed"
    status = _get_response_field(resp, "status")
    assert status == "completed", f"Expected 'completed' for immediate activation, got '{status}'"
    # "Immediately activating" also means start_time is near-now (ASAP resolved)
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
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
    Verifies via DB that resolution happened, then xfails the response-level claim.
    """
    from datetime import UTC, datetime

    import pytest
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    # Check response first — if start_time is present on response, verify it
    resp_start_time = _get_response_field(resp, "start_time")
    if resp_start_time is not None:
        assert str(resp_start_time) != "asap", "Response start_time is literal 'asap', not resolved"
        return

    # SPEC-PRODUCTION GAP: Response lacks start_time field.
    # Step text claims "the response should include resolved start_time" but
    # CreateMediaBuySuccess has no top-level start_time field.
    # Verify via DB that start_time WAS resolved (behavior is correct, just
    # not exposed in the response), then xfail the response-level claim.
    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found"
        assert mb.start_time is not None, "No start_time persisted — 'asap' was not resolved"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 30, f"Persisted start_time {mb.start_time} not resolved to current UTC"
    pytest.xfail(
        "SPEC-PRODUCTION GAP: CreateMediaBuySuccess response lacks start_time field. "
        "DB confirms 'asap' was correctly resolved to current UTC, but step text claims "
        "'response should include resolved start_time' — response does not satisfy this. "
        "FIXME(salesagent-9vgz.1)"
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
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
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
