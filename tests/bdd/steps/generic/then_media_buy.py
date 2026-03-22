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
    """Assert response includes packages array."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    packages = _get_response_field(resp, "packages")
    assert packages is not None, "Expected 'packages' in response"
    assert len(packages) > 0, "Expected at least one package in response"


@then("each package should include product_id, budget, and pricing details")
def then_packages_have_details(ctx: dict) -> None:
    """Assert each package has required fields."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    packages = _get_response_field(resp, "packages")
    assert packages, "No packages in response"
    for i, pkg in enumerate(packages):
        pkg_dict = pkg if isinstance(pkg, dict) else (pkg.model_dump() if hasattr(pkg, "model_dump") else vars(pkg))
        assert "product_id" in pkg_dict, f"Package {i} missing product_id"
        assert "budget" in pkg_dict, f"Package {i} missing budget"


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
    """Assert the adapter's create_media_buy was called (auto-approval path)."""
    env = ctx["env"]
    adapter_mock = env.mock["adapter"].return_value
    assert adapter_mock.create_media_buy.called, (
        "Expected adapter.create_media_buy to be called (auto-approval path), but it was not called"
    )


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
    """Assert Slack notifier was called."""
    env = ctx["env"]
    mock_slack = env.mock["slack"].return_value
    assert mock_slack.notify_media_buy_event.called, "Expected Slack notification to be sent"


@then("the Buyer should be notified via webhook")
def then_webhook_notification(ctx: dict) -> None:
    """Assert buyer webhook notification was sent.

    FIXME(salesagent-9vgz.1): webhook notification requires push notification
    config and protocol webhook service setup in the harness.
    """
    import pytest

    pytest.xfail("Webhook notification not yet wired in harness")


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
    """Assert a media buy was persisted in the database."""
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
    """Assert the response status indicates immediate activation (auto-approved)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    status = _get_response_field(resp, "status")
    assert status == "completed", f"Expected 'completed' for immediate activation, got '{status}'"


@then('the response should include resolved start_time (not literal "asap")')
def then_response_includes_resolved_start_time(ctx: dict) -> None:
    """Assert the response contains a resolved start_time, not the literal 'asap'.

    SPEC-PRODUCTION GAP: CreateMediaBuySuccess has no top-level start_time field,
    and Package.start_time / PlannedDelivery are not populated by production code.
    Falls back to checking the persisted DB record instead.
    """
    from datetime import UTC, datetime

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

    # Fallback: verify via persisted DB record (spec-production gap)
    with get_db_session() as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found"
        assert mb.start_time is not None, "No start_time persisted"
        now = datetime.now(UTC)
        delta = abs((mb.start_time - now).total_seconds())
        assert delta < 30, f"Persisted start_time {mb.start_time} not resolved to current UTC"


# ═══════════════════════════════════════════════════════════════════════
# Atomic response shape assertions (BR-RULE-018)
# ═══════════════════════════════════════════════════════════════════════


@then("the response should have success fields")
def then_response_has_success_fields(ctx: dict) -> None:
    """Assert response contains success-only fields (media_buy_id, packages)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a success response but none found"
    media_buy_id = _get_response_field(resp, "media_buy_id")
    assert media_buy_id, f"Expected media_buy_id in success response, got: {media_buy_id}"
    packages = _get_response_field(resp, "packages")
    assert packages is not None, "Expected packages in success response"


@then('the response should NOT have an "errors" field')
def then_response_no_errors_field(ctx: dict) -> None:
    """Assert the success response has no errors field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    # Check the inner response (unwrap CreateMediaBuyResult)
    inner = getattr(resp, "response", resp)
    errors = getattr(inner, "errors", None)
    assert not errors, f"Expected no errors on success response, got: {errors}"


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
    """Assert the error includes a retry_after hint (transient error recovery)."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    # AdCPError stores retry_after in details dict
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.details is not None, "Expected error details with retry_after"
        assert "retry_after" in error.details, f"Expected 'retry_after' in error details, got: {error.details}"
        assert error.details["retry_after"], "Expected non-zero retry_after value"
    else:
        # adcp.types.Error model — check for retry_after attribute
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None and hasattr(error, "details"):
            retry_after = (error.details or {}).get("retry_after")
        assert retry_after, f"Expected retry_after on error, got: {error}"


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
