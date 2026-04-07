"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes (DB state, response fields) instead of
interactions (mock.call_count), making assertions work across all transports
including E2E where mocks live in the test process but the adapter runs in Docker.

Usage in Then steps:
    from tests.bdd.steps._outcome_helpers import assert_adapter_executed, is_e2e

    @then('the media buy should proceed to adapter execution')
    def then_adapter_executed(ctx):
        assert_adapter_executed(ctx)
        if not is_e2e(ctx):
            # bonus mock check for in-process transports
            ...
"""

from __future__ import annotations

from tests.bdd.steps._harness_db import db_session


def _get_response_field(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types."""
    if hasattr(resp, field):
        return getattr(resp, field)
    inner = getattr(resp, "response", None)
    if inner is not None and hasattr(inner, field):
        return getattr(inner, field)
    if isinstance(resp, dict):
        return resp.get(field)
    return None


def is_e2e(ctx: dict) -> bool:
    """Check if the current transport is E2E (Docker-based)."""
    transport = ctx.get("transport")
    return transport is not None and hasattr(transport, "value") and str(transport.value).startswith("e2e_")


def assert_media_buy_created(ctx: dict, media_buy_id: str | None = None) -> object:
    """Verify media buy exists in DB -- proves adapter executed.

    Returns the MediaBuy ORM instance for further assertions.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    if media_buy_id is None:
        resp = ctx.get("response")
        if resp is not None:
            media_buy_id = _get_response_field(resp, "media_buy_id")

    assert media_buy_id is not None, "No media_buy_id available to verify creation"

    with db_session(ctx) as session:
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not in DB -- adapter may not have executed"
        return mb


def assert_adapter_executed(ctx: dict) -> object:
    """Verify adapter ran by checking DB state (not mock call count).

    A media buy that reaches a non-draft status proves the adapter was invoked.
    Returns the MediaBuy ORM instance for further assertions.
    """
    mb = assert_media_buy_created(ctx)
    executed_statuses = ("active", "completed", "pending_approval", "pending_activation", "submitted")
    assert mb.status in executed_statuses, (
        f"Media buy status '{mb.status}' does not confirm adapter execution. Expected one of {executed_statuses}."
    )
    return mb
