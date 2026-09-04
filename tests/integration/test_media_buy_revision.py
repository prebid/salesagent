"""The `revision` optimistic-concurrency token, enforced by the update flow.

The pinned update-media-buy-request.json says of `revision`:

    Expected current revision for optimistic concurrency. ... When provided, sellers
    MUST reject the update with CONFLICT if the media buy's current revision does not
    match, and MUST enforce that comparison atomically with the write.

and of the value it reports back, the pinned update-media-buy-response.json says:

    Revision number after this update.

These tests grade the impl layer, plus the one cross-transport pin that belongs with
the arithmetic it grades (``test_a_field_writing_update_emits_one_advance_on_every_wire``):
a request that supplies a token AND writes a field must advance the counter exactly
once, and every wire must carry that value. The rest of the wire-level assertions -- what
each transport accepts, and the conflict shape -- live in
test_update_media_buy_revision_validation_wire.py.
"""

import threading
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from src.core.config_loader import set_current_tenant
from src.core.database.repositories import MediaBuyUoW
from src.core.exceptions import AdCPGoneError, AdCPRevisionConflictError
from src.core.helpers.adapter_helpers import get_adapter as _real_get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import AdCPPackageUpdate, UpdateMediaBuyRequest, UpdateMediaBuySubmitted
from src.core.tools import media_buy_update
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory
from tests.factories.creative_asset import build_assets, image_spec
from tests.harness.media_buy_dual import MediaBuyDualEnv
from tests.harness.transport import WIRE_TRANSPORTS
from tests.helpers.media_buy_write_seam import assert_revision_advanced, read_media_buy_state

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "test_revision_tenant"
PRINCIPAL_ID = "test_revision_principal"
TOKEN = "test_revision_token_abc"

#: A far-future end_time for the dates branch, chosen so the update is a real change the
#: seeded row does not already carry.
_FUTURE_END = datetime(2035, 1, 1, tzinfo=UTC)


@pytest.fixture
def revision_tenant(integration_db, bound_factory_session):
    """Tenant + principal, built by the shared factories.

    TenantFactory already provisions the USD CurrencyLimit that budget validation
    requires, so this must not create a second one.
    """
    tenant = TenantFactory(
        tenant_id=TENANT_ID,
        name="Revision Tenant",
        subdomain="revision-tenant",
        ad_server="mock",
    )
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=PRINCIPAL_ID,
        name="Revision Advertiser",
        access_token=TOKEN,
        platform_mappings={"mock": {"id": "adv_revision"}},
    )
    bound_factory_session.commit()

    set_current_tenant(
        {
            "tenant_id": TENANT_ID,
            "name": "Revision Tenant",
            "subdomain": "revision-tenant",
            "ad_server": "mock",
            "is_active": True,
        }
    )

    return tenant, principal


def _identity(*, dry_run: bool = False) -> ResolvedIdentity:
    return PrincipalFactory.make_identity(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        auth_token=TOKEN,
        dry_run=dry_run,
    )


@pytest.fixture
def seed_media_buy(revision_tenant, bound_factory_session):
    """Return a helper that persists a media buy owned by the fixture's principal.

    The principal OBJECT is passed, not its id: MediaBuyFactory declares ``principal``
    as a SubFactory, so supplying only ``principal_id`` mints a SECOND principal and
    the buy ends up owned by someone the test never authenticates as.

    ``revision`` is a repository-managed seam field that ``MediaBuy.__init__`` refuses
    outright; the factory assigns it the way the repository does, so a test can start
    from a row in a state production actually reaches. The commit below closes the
    factory's own transaction so the seeded row is visible to the separate unit of work
    the update flow opens, rather than being pinned behind an uncommitted writer.
    """
    tenant, principal = revision_tenant

    def _seed(media_buy_id: str, *, status: str = "active", revision: int = 1):
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            status=status,
            revision=revision,
        )
        bound_factory_session.commit()
        return media_buy

    return _seed


def move_revision_on(media_buy_id: str) -> None:
    """Advance the revision the way any other writer would -- through the repository.

    Deliberately not another update_media_buy call: not every update action bumps the
    counter (a bare pause does not), so driving the setup through the tool would make
    the fixture depend on which action happens to write.
    """
    with MediaBuyUoW(TENANT_ID) as uow:
        uow.media_buys.update_fields(media_buy_id, order_name="moved on by another writer")


def _force_manual_approval_adapter(*args, **kwargs):
    """Return the real adapter with manual approval forced on for update_media_buy.

    Mutates the genuine adapter (rather than substituting a Mock) so every other
    call the submission branch makes on it -- ``property_list_unsupported_advisories``
    included -- keeps its real behaviour. Calls the source ``get_adapter`` (not the
    ``media_buy_update`` binding this is patched onto) to avoid recursing into itself.
    """
    adapter = _real_get_adapter(*args, **kwargs)
    adapter.manual_approval_required = True
    adapter.manual_approval_operations = {"update_media_buy"}
    return adapter


def _spying_get_adapter(spies: list) -> Any:
    """A ``get_adapter`` replacement that wraps ``update_media_buy`` in a spy.

    Returns the genuine adapter (so every other call it makes keeps its real
    behaviour) with only ``update_media_buy`` wrapped, so a test can assert whether the
    ad server was touched. Calls the source ``get_adapter`` (not the ``media_buy_update``
    binding this patches) to avoid recursing into itself; appends each wrapped method to
    ``spies`` so the caller can read it back.
    """

    def _factory(*args, **kwargs):
        adapter = _real_get_adapter(*args, **kwargs)
        adapter.update_media_buy = mock.Mock(wraps=adapter.update_media_buy)
        spies.append(adapter.update_media_buy)
        return adapter

    return _factory


def _move_the_row_then_resolve(mbid: str, moved: list[int]):
    """Patch target: on the first resolve, a SECOND connection advances the row.

    Opens the mid-request window that B1 is about -- the compare-only early check has
    already passed, and this lands another writer's advance BEFORE the branch reaches its
    own atomic claim. ``moved`` records the post-move revision so the test can assert the
    window actually opened (an unexercised window would grade nothing).
    """
    real_resolve = media_buy_update.resolve_principal_or_raise

    def _resolve(*args, **kwargs):
        principal = real_resolve(*args, **kwargs)
        if not moved:
            mover = threading.Thread(target=move_revision_on, args=(mbid,))
            mover.start()
            mover.join(timeout=30)
            assert not mover.is_alive(), "the second connection never finished its write"
            moved.append(read_media_buy_state(TENANT_ID, mbid).revision)
        return principal

    return _resolve


class TestRevisionEnforcedByUpdateFlow:
    def test_matching_token_succeeds_and_advances_the_revision(self, seed_media_buy):
        seed_media_buy("mb_rev_match")
        before = read_media_buy_state(TENANT_ID, "mb_rev_match").revision

        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_rev_match", paused=True, revision=before),
            identity=_identity(),
        )

        assert result.response.media_buy_id == "mb_rev_match"
        after = read_media_buy_state(TENANT_ID, "mb_rev_match").revision
        assert after > before, "an honoured token must be spent -- the revision has to move"
        # The buyer is told the value it must send next, so the reported revision has to
        # be the persisted one, not the token it just sent.
        assert result.response.revision == after

    def test_a_tokened_pause_advances_the_same_as_an_untokened_pause(self, seed_media_buy):
        """Supplying a token must not change how far the counter moves.

        The pause branch used to advance the revision only when a token was present
        (the two-phase claim ran there but a bare pause wrote nothing), so a tokened
        pause and an untokened pause moved the counter by different amounts and the
        divergence was asserted as correct. Under the single-advance design both are one
        mutating request and advance by exactly the same amount. Graded as an equality
        between the two shapes, not against a literal, so a design that moved both by two
        would still be caught if they ever diverged.
        """
        seed_media_buy("mb_pause_untokened")
        untokened_before = read_media_buy_state(TENANT_ID, "mb_pause_untokened").revision
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_pause_untokened", paused=True),
            identity=_identity(),
        )
        untokened_advance = read_media_buy_state(TENANT_ID, "mb_pause_untokened").revision - untokened_before

        seed_media_buy("mb_pause_tokened")
        tokened_before = read_media_buy_state(TENANT_ID, "mb_pause_tokened").revision
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_pause_tokened", paused=True, revision=tokened_before),
            identity=_identity(),
        )
        tokened_advance = read_media_buy_state(TENANT_ID, "mb_pause_tokened").revision - tokened_before

        assert tokened_advance == untokened_advance, (
            f"a tokened pause advanced the revision by {tokened_advance} and an untokened "
            f"pause by {untokened_advance}; a token must not change how far the counter moves"
        )

    def test_a_field_writing_update_advances_the_revision_exactly_once(self, seed_media_buy):
        """Honouring a token must not cost the buyer a second revision, nor none.

        The response field is defined as "Revision number after this update" --
        singular -- and the buyer sends the reported value back as its next token.

        Graded against the SAME request without a token, not against a literal: the
        obligation is that supplying a token changes nothing about how far the
        counter moves, and a hardcoded 8 would still pass if both shapes moved two.

        Both the UNDER-count (a honoured token that leaves the counter still) and the
        OVER-count (a token that advances twice) fail the exact-delta assertion. The
        single-advance design has one advance point per request -- the atomic
        ``advance_revision`` -- so there is no second advance to cancel and no prepayment
        ledger to lose. The exact-delta is asserted through the shared
        ``assert_revision_advanced(..., bumps=1)`` owner, applied identically to the
        tokened and untokened shape, so a token cannot change how far the counter moves.
        """
        seed_media_buy("mb_rev_untokened")
        untokened_before = read_media_buy_state(TENANT_ID, "mb_rev_untokened")
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_rev_untokened", budget=9000.0),
            identity=_identity(),
        )
        assert_revision_advanced(untokened_before, read_media_buy_state(TENANT_ID, "mb_rev_untokened"), bumps=1)

        seed_media_buy("mb_rev_tokened")
        before = read_media_buy_state(TENANT_ID, "mb_rev_tokened")
        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_rev_tokened", budget=9000.0, revision=before.revision),
            identity=_identity(),
        )

        after = read_media_buy_state(TENANT_ID, "mb_rev_tokened")
        assert_revision_advanced(before, after, bumps=1)
        assert result.response.revision == after.revision, (
            f"the response reported revision {result.response.revision} while the row holds "
            f"{after.revision} -- the buyer's next token must be the value AFTER this update"
        )

    def test_a_dry_run_with_a_token_reports_the_current_revision_and_moves_nothing(self, seed_media_buy):
        """A simulation applies nothing -- including the token spend.

        The compare-only ``_reject_stale_revision`` runs before the dry-run early return,
        deliberately: a simulated update that WOULD be rejected has to report the
        rejection. But it takes no advance, so a matching-token dry run leaves the
        counter exactly where it found it rather than handing the buyer a token for a
        state the seller never entered.
        """
        seed_media_buy("mb_rev_dry")
        before = read_media_buy_state(TENANT_ID, "mb_rev_dry").revision

        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_rev_dry", budget=9000.0, revision=before),
            identity=_identity(dry_run=True),
        )

        assert read_media_buy_state(TENANT_ID, "mb_rev_dry").revision == before, (
            f"the dry run moved the persisted revision to {read_media_buy_state(TENANT_ID, 'mb_rev_dry').revision}; a "
            f"simulation must leave the row exactly as it found it"
        )
        assert result.response.revision == before

    def test_a_dry_run_still_rejects_a_stale_token(self, seed_media_buy):
        """Not advancing must not become not comparing.

        A simulation of an update that would be rejected has to report the rejection,
        or dry_run becomes a way to get a 200 for a request the seller would refuse.
        """
        seed_media_buy("mb_rev_dry_stale")
        move_revision_on("mb_rev_dry_stale")

        with pytest.raises(AdCPRevisionConflictError) as exc_info:
            _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_dry_stale", budget=9000.0, revision=1),
                identity=_identity(dry_run=True),
            )
        assert exc_info.value.details["expected_version"] == 1

    def test_absent_token_still_updates(self, seed_media_buy):
        """revision is optional; omitting it skips the check rather than failing."""
        seed_media_buy("mb_rev_absent")
        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id="mb_rev_absent", paused=True),
            identity=_identity(),
        )
        assert result.response.media_buy_id == "mb_rev_absent"

    def test_stale_token_is_rejected_with_conflict_naming_both_versions(self, seed_media_buy):
        seed_media_buy("mb_rev_stale")
        # Someone else writes first, moving the revision past the buyer's token.
        move_revision_on("mb_rev_stale")
        current = read_media_buy_state(TENANT_ID, "mb_rev_stale").revision
        assert current > 1

        with pytest.raises(AdCPRevisionConflictError) as exc_info:
            _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_stale", paused=False, revision=1),
                identity=_identity(),
            )

        exc = exc_info.value
        assert exc.wire_error_code == "CONFLICT"
        assert exc.details == {
            "resource_id": "mb_rev_stale",
            "expected_version": 1,
            "current_version": current,
        }

    def test_rejected_update_is_not_applied(self, seed_media_buy):
        """A CONFLICT must be a no-op, not a partial write."""
        seed_media_buy("mb_rev_noop")
        move_revision_on("mb_rev_noop")
        after_first = read_media_buy_state(TENANT_ID, "mb_rev_noop").revision

        with pytest.raises(AdCPRevisionConflictError):
            _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_noop", paused=False, revision=1),
                identity=_identity(),
            )

        assert read_media_buy_state(TENANT_ID, "mb_rev_noop").revision == after_first

    def test_stale_token_on_a_terminal_buy_yields_conflict_not_gone(self, seed_media_buy):
        """Order matters: the CONFLICT check runs BEFORE the terminal-state gate.

        A buyer holding a stale token against a completed buy has a stale-token
        problem first. CONFLICT names both versions and says "re-read and retry";
        the terminal answer (INVALID_STATE via AdCPGoneError) hides the version pair
        and misdescribes the cause.
        """
        seed_media_buy("mb_rev_terminal", status="completed", revision=4)

        with pytest.raises(AdCPRevisionConflictError) as exc_info:
            _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_terminal", paused=True, revision=1),
                identity=_identity(),
            )
        assert exc_info.value.details["current_version"] == 4

    def test_terminal_gate_still_applies_when_the_token_matches(self, seed_media_buy):
        """Running the CONFLICT check first must not disable the terminal gate.

        With a CURRENT token there is no conflict to report, so the buy's terminal
        state is the real answer and must still be given.
        """
        seed_media_buy("mb_rev_terminal_ok", status="completed")
        current = read_media_buy_state(TENANT_ID, "mb_rev_terminal_ok").revision

        with pytest.raises(AdCPGoneError):
            _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_terminal_ok", paused=True, revision=current),
                identity=_identity(),
            )

    def test_submission_with_a_matching_token_submits_without_spending_the_revision(self, seed_media_buy):
        """Manual approval defers the update, so a good token must NOT be spent.

        UpdateMediaBuySubmitted carries no successor revision: the update is not
        applied yet. Advancing the counter here would hand the buyer a token for a
        state the seller has not entered and leave its held token dead, so submission
        compares WITHOUT advancing. The persisted revision must be exactly what it
        was before the request.
        """
        seed_media_buy("mb_rev_submit_match")
        before = read_media_buy_state(TENANT_ID, "mb_rev_submit_match").revision

        with mock.patch.object(media_buy_update, "get_adapter", _force_manual_approval_adapter):
            result = _update_media_buy_impl(
                req=UpdateMediaBuyRequest(media_buy_id="mb_rev_submit_match", paused=True, revision=before),
                identity=_identity(),
            )

        assert isinstance(result, UpdateMediaBuySubmitted)
        assert read_media_buy_state(TENANT_ID, "mb_rev_submit_match").revision == before, (
            f"submission moved the revision from {before} to {read_media_buy_state(TENANT_ID, 'mb_rev_submit_match').revision}; a "
            f"deferred update must not spend the token, which has no successor value to report"
        )

    def test_submission_with_a_stale_token_still_conflicts(self, seed_media_buy):
        """Not spending the token must not become not comparing it.

        A stale token against a manual-approval update still has to be rejected with
        CONFLICT -- deferral is not a way to smuggle a stale update into the approval
        queue.
        """
        seed_media_buy("mb_rev_submit_stale")
        move_revision_on("mb_rev_submit_stale")
        current = read_media_buy_state(TENANT_ID, "mb_rev_submit_stale").revision
        assert current > 1

        with mock.patch.object(media_buy_update, "get_adapter", _force_manual_approval_adapter):
            with pytest.raises(AdCPRevisionConflictError) as exc_info:
                _update_media_buy_impl(
                    req=UpdateMediaBuyRequest(media_buy_id="mb_rev_submit_stale", paused=True, revision=1),
                    identity=_identity(),
                )

        assert exc_info.value.wire_error_code == "CONFLICT"
        assert exc_info.value.details["expected_version"] == 1
        assert exc_info.value.details["current_version"] == current

    @pytest.mark.parametrize(
        "branch, req_kwargs, kind",
        [
            # MID-REQUEST MOVE: a second connection moves the row AFTER this request read
            # it but BEFORE the write. The compare-only early check has already passed, so
            # only the atomic advance (UPDATE ... WHERE revision = :expected) can turn that
            # into CONFLICT, and it does -- the whole transaction rolls back.
            pytest.param("budget", {"budget": 9000.0}, "atomic", id="budget"),
            pytest.param("dates", {"end_time": _FUTURE_END}, "atomic", id="dates"),
            # STALE ON ARRIVAL: the token is already stale before this request reads the
            # row, so _reject_stale_revision rejects it up front -- before the adapter is
            # even acquired. That is the protection for a doomed pause/package update: the
            # ad server is never touched. The test asserts get_adapter was never called.
            pytest.param("pause", {"paused": True}, "failfast", id="pause"),
            pytest.param(
                "package",
                {"packages": [AdCPPackageUpdate(package_id="pkg_window", paused=True)]},
                "failfast",
                id="package",
            ),
        ],
    )
    def test_a_row_moved_after_the_early_check_is_caught_per_branch(self, seed_media_buy, branch, req_kwargs, kind):
        """Each write branch that honours ``revision`` is graded for the protection it
        actually has -- the atomic advance, or the compare-only fail-fast -- never a
        guarantee it does not provide.

        The compare-only early check takes no lock, so it cannot cover a row moved AFTER
        it runs. For the atomic branches (budget, dates) a SECOND connection moves the row
        after the early check and before the write, and only the atomic advance
        (UPDATE ... WHERE revision = :expected) turns that into CONFLICT. For the
        stale-on-arrival branches (pause, package) the token is already stale when the
        request reads the row, so the early check rejects it before the adapter is
        acquired -- so those assert ``get_adapter`` was never called.
        """
        mbid = f"mb_rev_window_{branch}"
        seed_media_buy(mbid)
        before = read_media_buy_state(TENANT_ID, mbid).revision
        req = UpdateMediaBuyRequest(media_buy_id=mbid, revision=before, **req_kwargs)

        if kind == "atomic":
            moved: list[int] = []
            real_resolve = media_buy_update.resolve_principal_or_raise

            def move_the_row_then_resolve(*args, **kwargs):
                principal = real_resolve(*args, **kwargs)
                if not moved:
                    mover = threading.Thread(target=move_revision_on, args=(mbid,))
                    mover.start()
                    mover.join(timeout=30)
                    assert not mover.is_alive(), "the second connection never finished its write"
                    moved.append(read_media_buy_state(TENANT_ID, mbid).revision)
                return principal

            with mock.patch.object(media_buy_update, "resolve_principal_or_raise", move_the_row_then_resolve):
                with pytest.raises(AdCPRevisionConflictError) as exc_info:
                    _update_media_buy_impl(req=req, identity=_identity())

            assert moved == [before + 1], (
                "the window was never opened -- the other writer has to land between the "
                f"early comparison and the write for this test to grade anything (saw {moved})"
            )
            assert exc_info.value.wire_error_code == "CONFLICT"
            assert exc_info.value.details == {
                "resource_id": mbid,
                "expected_version": before,
                "current_version": before + 1,
            }
            # The rejected request wrote nothing: the other writer's revision stands and
            # its marker survives, so the whole transaction rolled back.
            with MediaBuyUoW(TENANT_ID) as uow:
                survivor = uow.media_buys.get_by_id_or_raise(mbid)
                assert survivor.revision == before + 1
                assert survivor.order_name == "moved on by another writer"
                if branch == "budget":
                    assert float(survivor.budget or 0) != 9000.0
        else:  # failfast
            # A second connection moves the row before this request reads it, so the token
            # is already stale when _reject_stale_revision compares -- the "stale on
            # arrival" case the compare-only early check exists for.
            move_revision_on(mbid)
            assert read_media_buy_state(TENANT_ID, mbid).revision == before + 1, (
                "the second connection never moved the row"
            )

            with pytest.raises(AdCPRevisionConflictError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

            assert exc_info.value.wire_error_code == "CONFLICT"
            assert exc_info.value.details == {
                "resource_id": mbid,
                "expected_version": before,
                "current_version": before + 1,
            }
            # Buyer-observable, not a mock-call assertion: the stale-on-arrival token was
            # rejected and NOTHING was applied. Re-reading the row shows the other writer's
            # revision still standing and its marker intact -- the request never wrote (and
            # so never reached the ad server), which is what "reject with CONFLICT, apply
            # nothing" means on the wire. Dropping the early compare would let the write run.
            with MediaBuyUoW(TENANT_ID) as uow:
                survivor = uow.media_buys.get_by_id_or_raise(mbid)
                assert survivor.revision == before + 1
                assert survivor.order_name == "moved on by another writer"
                if branch == "budget":
                    assert float(survivor.budget or 0) != 9000.0

    @pytest.mark.parametrize(
        "branch, package_update, needs_package",
        [
            pytest.param("campaign_pause", None, False, id="campaign-pause"),
            pytest.param("package_pause", {"paused": True}, False, id="package-pause"),
            pytest.param("package_budget", {"budget": 9000.0}, True, id="package-budget"),
        ],
    )
    def test_a_mid_request_move_conflicts_before_the_adapter_is_touched(
        self, seed_media_buy, revision_tenant, bound_factory_session, branch, package_update, needs_package
    ):
        """B1: the atomic claim runs BEFORE the adapter, so a token that goes stale in the
        window AFTER the compare-only early check is CONFLICT before the ad server is touched.

        These branches are adapter-first: the adapter effects the change on the ad server,
        then it is persisted. If the claim ran after the adapter (the pre-fix ordering), a
        concurrent writer advancing the row in the window would let the early check pass,
        the adapter change the ad server, then the advance fail -- the buyer told CONFLICT
        while the ad server was already changed. The claim is a distinct step ahead of the
        adapter, so a stale token stops the request before ``update_media_buy`` is called.

        The spy records every ``update_media_buy`` call; asserting it was NOT called is the
        mutation oracle -- restore the post-adapter ordering and the adapter IS called, so
        "reject with CONFLICT, apply nothing" would be violated.
        """
        _tenant, _principal = revision_tenant
        mbid = f"mb_mw_{branch}"
        buy = seed_media_buy(mbid)
        if needs_package:
            MediaPackageFactory(
                media_buy=buy,
                package_id="pkg_mw",
                package_config={"package_id": "pkg_mw", "product_id": "prod_mw"},
            )
            bound_factory_session.commit()

        before = read_media_buy_state(TENANT_ID, mbid).revision
        if branch == "campaign_pause":
            req = UpdateMediaBuyRequest(media_buy_id=mbid, paused=True, revision=before)
        else:
            req = UpdateMediaBuyRequest(
                media_buy_id=mbid,
                revision=before,
                packages=[AdCPPackageUpdate(package_id="pkg_mw", **package_update)],
            )

        moved: list[int] = []
        spies: list = []
        with (
            mock.patch.object(media_buy_update, "resolve_principal_or_raise", _move_the_row_then_resolve(mbid, moved)),
            mock.patch.object(media_buy_update, "get_adapter", _spying_get_adapter(spies)),
        ):
            with pytest.raises(AdCPRevisionConflictError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

        assert moved == [before + 1], (
            f"the mid-request window never opened -- the other writer has to land between the "
            f"early comparison and the claim for this test to grade anything (saw {moved})"
        )
        assert exc_info.value.wire_error_code == "CONFLICT"
        assert exc_info.value.details == {
            "resource_id": mbid,
            "expected_version": before,
            "current_version": before + 1,
        }
        # The claim ran BEFORE the adapter, so the stale token stopped the request before
        # the ad server was ever touched: no update_media_buy call was recorded.
        assert spies, "get_adapter was never called, so the branch never reached the adapter path"
        for spy in spies:
            spy.assert_not_called()
        # Buyer-observable: the other writer's revision still stands and its marker survives,
        # so the whole transaction (including this request's claim) rolled back.
        survivor = read_media_buy_state(TENANT_ID, mbid)
        assert survivor.revision == before + 1

    def test_a_mid_request_move_conflicts_before_the_inline_creative_sync(self, seed_media_buy):
        """B1, worst case: ``_sync_creatives_impl`` commits through its OWN CreativeUoW, so
        its writes are NOT inside the update's transaction to roll back. The claim MUST
        precede it, or a stale token leaves committed creatives behind a CONFLICT.

        The sync spy asserts the independently-committing write never ran; the creative
        store is then checked directly (buyer-observable) to prove nothing was committed.
        Restoring the post-sync ordering would call the sync and commit the creative.
        """
        mbid = "mb_mw_creatives"
        seed_media_buy(mbid)
        before = read_media_buy_state(TENANT_ID, mbid).revision
        req = UpdateMediaBuyRequest(
            media_buy_id=mbid,
            revision=before,
            packages=[
                AdCPPackageUpdate(
                    package_id="pkg_mw",
                    creatives=[
                        {
                            "creative_id": "mw_cr1",
                            "name": "Mid-window creative",
                            "format_id": {
                                "agent_url": "https://creative.adcontextprotocol.org",
                                "id": "display_300x250",
                            },
                            "assets": build_assets(image_spec("banner")),
                        }
                    ],
                )
            ],
        )

        moved: list[int] = []
        sync_spy = mock.Mock(wraps=media_buy_update._sync_creatives_impl)
        with (
            mock.patch.object(media_buy_update, "resolve_principal_or_raise", _move_the_row_then_resolve(mbid, moved)),
            mock.patch.object(media_buy_update, "_sync_creatives_impl", sync_spy),
        ):
            with pytest.raises(AdCPRevisionConflictError) as exc_info:
                _update_media_buy_impl(req=req, identity=_identity())

        assert moved == [before + 1], f"the mid-request window never opened (saw {moved})"
        assert exc_info.value.details == {
            "resource_id": mbid,
            "expected_version": before,
            "current_version": before + 1,
        }
        # The claim ran BEFORE the sync, so the independently-committing creative write never ran.
        sync_spy.assert_not_called()
        # Buyer-observable: no creative was committed behind the CONFLICT.
        with MediaBuyUoW(TENANT_ID) as uow:
            assert uow.creatives is not None
            assert uow.creatives.get_by_id("mw_cr1", PRINCIPAL_ID) is None, (
                "a creative was committed behind a CONFLICT -- the inline sync ran before the claim"
            )


#: A distinctive seed, so the assertion below discriminates the three wrong answers
#: this arithmetic can give: the ``UpdateMediaBuySuccess.revision`` schema default (1),
#: the pre-write value (7) and the double advance (9).
_WIRE_SEED_REVISION = 7

#: How each transport renders a JSON number. A2A carries its payload through a
#: protobuf Struct, whose only numeric type is ``double``, so it emits 8.0 where MCP
#: and REST emit 8 -- both conformant under draft-07 ``integer``. Mirrors the table in
#: test_update_media_buy_revision_validation_wire.py; the comparison below is by value,
#: so the fork does not need repeating here.


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS)
def test_a_field_writing_update_emits_one_advance_on_every_wire(integration_db, transport):
    """The post-write revision is a protocol contract, so it is graded on wire bytes.

    The impl-level pin above cannot see a boundary that re-serializes the field or
    substitutes the schema default, and the existing wire pin in
    test_update_media_buy_revision_validation_wire.py drives a bare pause -- which
    writes no row at all, so it never reaches the second advance that a field write
    used to take. This is the shape that did: token honoured, then a budget written.
    """
    media_buy_id = "mb_rev_wire_post_write"
    with MediaBuyDualEnv() as env:
        env.seed_existing_media_buy(media_buy_id, revision=_WIRE_SEED_REVISION)
        result = env.call_via(
            transport,
            media_buy_id=media_buy_id,
            budget=9000.0,
            revision=_WIRE_SEED_REVISION,
        )

    assert not result.is_error, f"{transport} rejected a matching token: {result.wire_error_envelope or result.error!r}"
    wire = result.require_wire()
    assert wire["revision"] == _WIRE_SEED_REVISION + 1, (
        f"{transport} emitted revision={wire['revision']!r} for an update of a buy at "
        f"{_WIRE_SEED_REVISION}. The pinned update-media-buy-response.json defines the field as "
        f"the revision AFTER this update, so it must be {_WIRE_SEED_REVISION + 1}: "
        f"{_WIRE_SEED_REVISION} is the pre-write value, {_WIRE_SEED_REVISION + 2} is a double "
        f"advance (a field write bumping on top of the request's one advance), and 1 is the schema default"
    )
