"""Shared media-buy step plumbing for the UC-003 / UC-019 BDD suites.

Both suites need the same three primitives against the harness's factory-bound
session: resolve a Gherkin label to the real ``media_buy_id``, drive a real
create through the tool and load the persisted ORM row, and advance the
persisted revision through the real ``bump_revision`` seam. Keeping one copy
here means a fix (a tz bug, a seam rename) lands once instead of drifting
between the two step modules (#1544 round-2 DRY-02/03).
"""

from __future__ import annotations

from typing import Any


def resolve_media_buy_id(ctx: dict, label: str) -> str:
    """Resolve a Gherkin media-buy label to the real database id.

    Falls back to the label itself so scenarios where the label and the real ID
    coincide (legacy) continue to work.
    """
    return ctx.get("media_buy_labels", {}).get(label, label)


def _media_buy_repo(ctx: dict) -> Any:
    """Build a tenant-scoped MediaBuyRepository on the harness's bound session."""
    from src.core.database.repositories.media_buy import MediaBuyRepository

    return MediaBuyRepository(ctx["env"]._session, ctx["tenant"].tenant_id)


def load_real_buy(ctx: dict, media_buy_id: str) -> Any:
    """Load the persisted ORM ``MediaBuy`` row through the repository."""
    media_buy = _media_buy_repo(ctx).get_by_id(media_buy_id)
    assert media_buy is not None, f"media buy {media_buy_id!r} not found in DB"
    return media_buy


def create_and_load_real_buy(ctx: dict) -> Any:
    """Drive a real default create through the tool; return the loaded ORM row.

    The Gherkin label→real-id registration differs per suite, so callers register
    the returned row themselves — this shares only the identical create-then-load
    half.
    """
    created = ctx["env"].create_default_buy(ctx["default_product"])
    return load_real_buy(ctx, created.media_buy_id)


def advance_revision_to(ctx: dict, media_buy: Any, revision: int) -> Any:
    """Advance the persisted revision to *revision* via real repository bumps.

    Each bump is a real mutation through the production seam
    (``MediaBuyRepository.bump_revision``), never a seeded column value, so the
    precondition itself exercises the counter's strict monotonicity. Returns the
    re-loaded row at the target revision.
    """
    repo = _media_buy_repo(ctx)
    current = media_buy.revision or 1
    assert current <= revision, f"cannot lower persisted revision from {current} to {revision}"
    while (media_buy.revision or 1) < revision:
        media_buy = repo.bump_revision(media_buy.media_buy_id)
    ctx["env"]._commit_factory_data()
    assert media_buy.revision == revision, f"expected persisted revision {revision}, got {media_buy.revision}"
    return media_buy
