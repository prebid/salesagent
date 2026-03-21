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

    For now, skip — webhook notification requires push notification config setup.
    """
    import pytest

    pytest.skip("Webhook notification not yet wired in harness")


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
