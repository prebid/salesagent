"""Persisted media-buy revision counter on the wire (AdCP 3.1.0-beta.3 fields).

``revision`` is a persisted monotonic counter (``media_buys.revision``), not a
timestamp-derived value: two updates within the same second MUST still yield
strictly increasing revisions, because buyers treat the field as an
optimistic-concurrency token. ``confirmed_at`` on the create response and on
get_media_buys items both report the same persisted ``created_at``.

Full production paths against real PostgreSQL: _create_media_buy_impl →
_update_media_buy_impl → _get_media_buys_impl via the harness dual env.
"""

from __future__ import annotations

import pytest

from src.core.schemas import UpdateMediaBuyRequest
from src.core.schemas._base import (
    CreateMediaBuySuccess,
    GetMediaBuysRequest,
    UpdateMediaBuySuccess,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _create_buy(env, product) -> CreateMediaBuySuccess:
    """Drive a real create through the impl; returns the success response."""
    return env.create_default_buy(product, brand_domain="revision-test.example")


def _update_budget(env, media_buy_id: str, budget: float) -> UpdateMediaBuySuccess:
    """Drive a real budget update through the impl; returns the success response."""
    req = UpdateMediaBuyRequest(media_buy_id=media_buy_id, budget=budget)
    result = env.call_impl(req=req)
    assert isinstance(result, UpdateMediaBuySuccess), f"update must succeed, got {result!r}"
    return result


def _get_buy(env, media_buy_id: str):
    """Fetch the buy back through the real get_media_buys impl."""
    from src.core.tools.media_buy_list import _get_media_buys_impl

    response = _get_media_buys_impl(
        req=GetMediaBuysRequest(media_buy_ids=[media_buy_id]),
        identity=env.identity,
        include_snapshot=False,
    )
    assert len(response.media_buys) == 1
    return response.media_buys[0]


@pytest.mark.requires_db
class TestPersistedRevisionOnTheWire:
    """create → update → get report the persisted counter consistently."""

    def test_create_reports_revision_1_and_persisted_confirmed_at(self, integration_db):
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)

            assert created.revision == 1
            assert created.confirmed_at is not None

            item = _get_buy(env, created.media_buy_id)
            assert item.revision == 1
            # Same persisted source everywhere: create's confirmed_at is the
            # row's created_at, and get_media_buys echoes both.
            assert item.confirmed_at == item.created_at
            assert item.confirmed_at == created.confirmed_at

    def test_create_then_update_shows_1_then_2_across_tools(self, integration_db):
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)
            assert created.revision == 1

            updated = _update_budget(env, created.media_buy_id, 6000.0)
            assert updated.revision == 2

            item = _get_buy(env, created.media_buy_id)
            assert item.revision == 2
            # confirmed_at is stable after set — an update never moves it.
            assert item.confirmed_at == created.confirmed_at

    def test_single_update_touching_budget_and_dates_bumps_once(self, integration_db):
        """One accepted update that changes budget AND dates advances revision by
        exactly 1 — not once per changed field group.

        Regression for #1544: the update path bumped independently for the
        package set, the budget, and the date range (3 sites), so a combined
        update jumped the revision by 2-3. AdCP 3.1.0-beta.3 ``revision`` is a per-resource
        version token, so one update must advance it by exactly one.
        """
        from datetime import UTC, datetime, timedelta

        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)
            assert created.revision == 1

            new_start = datetime.now(UTC) + timedelta(days=2)
            new_end = new_start + timedelta(days=20)
            req = UpdateMediaBuyRequest(
                media_buy_id=created.media_buy_id,
                budget=8000.0,
                start_time=new_start.isoformat(),
                end_time=new_end.isoformat(),
            )
            result = env.call_impl(req=req)
            assert isinstance(result, UpdateMediaBuySuccess), f"update must succeed, got {result!r}"
            # Budget + dates in one call → exactly one increment.
            assert result.revision == 2

            item = _get_buy(env, created.media_buy_id)
            assert item.revision == 2

    def test_rapid_consecutive_updates_yield_strictly_increasing_revisions(self, integration_db):
        """Two back-to-back updates (same wall-clock second) must not collide.

        A time-derived formula (e.g. 1 + whole seconds between created_at and
        updated_at) returns the SAME revision for updates landing within one
        second — this pins the persisted counter's strict monotonicity.
        """
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)

            first = _update_budget(env, created.media_buy_id, 6000.0)
            second = _update_budget(env, created.media_buy_id, 7000.0)

            assert first.revision is not None and second.revision is not None
            assert created.revision is not None
            assert created.revision < first.revision < second.revision
            assert (first.revision, second.revision) == (2, 3)

            # And the read tool agrees with the last write.
            item = _get_buy(env, created.media_buy_id)
            assert item.revision == second.revision

    def test_pause_bumps_persisted_revision(self, integration_db):
        """A campaign-level pause through the real impl bumps the persisted counter.

        The value the buyer sees is produced by PostgreSQL (the server-side
        increment), not echoed from a mock — the real-DB half of the pause-bump
        contract whose plumbing half lives in
        ``tests/unit/test_update_media_buy_behavioral.py`` (#1544 round-2 TQ-03).
        """
        from src.core.database.repositories import MediaBuyUoW
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)

            # 'pause' is only a valid action for an 'active' buy — transition the
            # setup state there (itself a bump), then capture the pre-pause value.
            with MediaBuyUoW(tenant.tenant_id) as uow:
                uow.media_buys.update_status(created.media_buy_id, "active")
            pre_pause = _get_buy(env, created.media_buy_id).revision

            result = env.call_impl(req=UpdateMediaBuyRequest(media_buy_id=created.media_buy_id, paused=True))
            assert isinstance(result, UpdateMediaBuySuccess), f"pause must succeed, got {result!r}"

            # Pause advanced the revision by exactly one, and the read tool agrees.
            assert result.revision == pre_pause + 1
            assert _get_buy(env, created.media_buy_id).revision == result.revision


@pytest.mark.requires_db
class TestRevisionOptimisticConcurrency:
    """req.revision gate: mismatch MUST reject with CONFLICT; match/absent proceed.

    AdCP 3.1.0-beta.3 update-media-buy-request.json ``properties.revision``:
    "When provided, sellers MUST reject the update with CONFLICT if the media
    buy's current revision does not match." (Schema MUST; no conformance
    storyboard step grades it — ungraded. #1544 round-6 review item 1.)
    """

    def test_stale_revision_rejected_with_conflict_and_nothing_written(self, integration_db):
        from src.core.exceptions import AdCPConflictError
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)  # persisted revision 1

            with pytest.raises(AdCPConflictError) as exc_info:
                env.call_impl(req=UpdateMediaBuyRequest(media_buy_id=created.media_buy_id, budget=9000.0, revision=5))

            # The typed CONFLICT carries both sides of the mismatch so the buyer
            # can re-read and retry (spec: "Obtain from get_media_buys or the
            # most recent update response").
            assert exc_info.value.error_code == "CONFLICT"
            assert exc_info.value.details is not None
            assert exc_info.value.details["expected_version"] == 5
            assert exc_info.value.details["current_version"] == 1
            assert exc_info.value.details["resource_id"] == created.media_buy_id

            # Rejected update wrote nothing: revision unchanged on a fresh read.
            assert _get_buy(env, created.media_buy_id).revision == 1

    def test_terminal_and_stale_revision_prefers_conflict_over_gone(self, integration_db):
        """CONFLICT precedence: a stale token against a buy that has since reached a
        terminal state MUST reject with CONFLICT (refetch-and-retry), not GONE.

        The pinned update-media-buy-request schema mandates CONFLICT on ANY revision
        mismatch unconditionally, so the optimistic-concurrency gate runs before the
        terminal-state gate. Pre-fix the terminal check ran first and returned GONE,
        masking the stale write and denying the buyer the refetch recovery. #1544.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPConflictError
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)  # persisted revision 1

            # Drive the buy into a terminal state out-of-band (revision 1 → 2)
            # through the repository/UoW seam (commits on context exit).
            with MediaBuyUoW(env.identity.tenant_id) as uow:
                uow.media_buys.update_status(created.media_buy_id, "completed")

            # Buyer sends a STALE token (1) against the now-terminal buy (revision 2).
            with pytest.raises(AdCPConflictError) as exc_info:
                env.call_impl(req=UpdateMediaBuyRequest(media_buy_id=created.media_buy_id, budget=9000.0, revision=1))

            assert exc_info.value.error_code == "CONFLICT"
            assert exc_info.value.details is not None
            assert exc_info.value.details["expected_version"] == 1
            assert exc_info.value.details["current_version"] == 2

    def test_matching_revision_proceeds_and_increments(self, integration_db):
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)  # persisted revision 1

            result = env.call_impl(
                req=UpdateMediaBuyRequest(media_buy_id=created.media_buy_id, budget=9000.0, revision=1)
            )
            assert isinstance(result, UpdateMediaBuySuccess), f"matching revision must succeed, got {result!r}"
            assert result.revision == 2

    def test_absent_revision_keeps_last_write_wins(self, integration_db):
        """Omitting the token preserves LWW semantics — the gate only fires when provided."""
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            created = _create_buy(env, product)

            result = _update_budget(env, created.media_buy_id, 9000.0)
            assert result.revision == 2
