"""A completed update_media_buy carries THIS update's instant as implementation_date.

Spec: CONFIRMED -- ``.venv/lib/python3.12/site-packages/adcp/_schemas/3.1/media-buy/
update-media-buy-response.json``, ``/oneOf/0/properties/implementation_date``:
"ISO 8601 timestamp when changes take effect (null if pending approval)". Optional on
the branch (``/oneOf/0/required`` is ``["media_buy_id", "revision"]``) and nullable.

Graded: BR-UC-003 POST-S3 -- ``tests/bdd/features/BR-UC-003-update-media-buy.feature``
asserts "the response should contain an implementation_date that is not null" on the
auto-applied scenarios, and "should NOT contain" it on the submitted envelope.

The field names the instant THIS update took effect, which is why the value comes from
``_applied_instant()`` and not from ``media_buys.updated_at``. That column is the parent
ROW's last-modified stamp; a ``packages`` entry carrying only ``package_id`` and
``creative_ids``, or a ``creative_assignments`` entry, writes assignment rows and leaves
the parent row untouched on an active buy, so the column holds an EARLIER update's
instant. Both tests below therefore seed a row stamped in the PAST: a response carrying
that stamp is the defect, and the assertions name it.

The pending-approval half of the obligation (the submitted envelope carries no
implementation_date) is graded by
``tests/unit/test_media_buy.py::test_implementation_date_null_when_pending``.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from src.adapters.base import AdapterUpdateResult
from src.core.schemas import Account, UpdateMediaBuyRequest, UpdateMediaBuySuccess
from src.core.tools._wire import to_wire
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories.principal import PrincipalFactory

#: What this update's own instant is, pinned through the one seam that states it.
APPLIED_AT = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)

#: What the parent ROW's last-modified column holds: an earlier update's instant. A
#: response carrying THIS value is the bug, so it is deliberately not APPLIED_AT.
ROW_STAMPED_AT = datetime(2026, 2, 27, 9, 15, tzinfo=UTC)


def _account_identity():
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id="test_principal", tenant_id="test_tenant"),
        Account(account_id="acct_test", name="Test Account", status="active"),
    )


def _row():
    """The persisted buy as the update path re-reads it, stamped by an EARLIER update."""
    buy = MagicMock()
    buy.media_buy_id = "mb_resolved"
    buy.tenant_id = "test_tenant"
    buy.principal_id = "test_principal"
    buy.status = "active"
    buy.is_paused = True
    buy.revision = 4
    buy.currency = "USD"
    buy.start_time = datetime(2026, 3, 1, tzinfo=UTC)
    buy.end_time = datetime(2026, 3, 31, tzinfo=UTC)
    buy.start_date = buy.start_time.date()
    buy.end_date = buy.end_time.date()
    buy.updated_at = ROW_STAMPED_AT
    buy.raw_request = {}
    return buy


def _uow(row):
    uow = MagicMock()
    uow.idempotency_attempts.find_by_key.return_value = None
    uow.idempotency_attempts.count_inserts_since.return_value = (0, None)
    uow.idempotency_attempts.count_active.return_value = (0, None)
    uow.session = MagicMock()
    uow.media_buys = MagicMock()
    uow.media_buys.get_by_id.return_value = row
    uow.media_buys.get_by_id_or_raise.return_value = row
    uow.media_buys.update_fields.return_value = row
    uow.media_buys.get_package.return_value = None
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return uow


def _adapter():
    adapter = MagicMock()
    adapter.manual_approval_required = False
    adapter.manual_approval_operations = []
    adapter.update_media_buy.return_value = AdapterUpdateResult(
        media_buy_id="mb_resolved",
        affected_packages=[],
    )
    return adapter


def _run(req):
    ctx_mgr = MagicMock()
    ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_1")
    ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")

    with (
        patch("src.core.tools.media_buy_update.get_context_manager", return_value=ctx_mgr),
        patch("src.core.tools.media_buy_update.MediaBuyUoW", return_value=_uow(_row())),
        patch("src.core.tools.media_buy_update.get_audit_logger", return_value=MagicMock()),
        patch("src.core.tools.media_buy_update._verify_principal"),
        patch("src.core.tools.media_buy_update.get_adapter", return_value=_adapter()),
        patch("src.core.tools.media_buy_update._applied_instant", return_value=APPLIED_AT),
    ):
        return _update_media_buy_impl(req=req, identity=_account_identity())


def _assert_carries_this_updates_instant(result):
    assert isinstance(result, UpdateMediaBuySuccess)
    assert result.implementation_date == APPLIED_AT
    # The row's own stamp must NOT be what the buyer is told: that is an earlier
    # update's instant, and reporting it is the defect this grades.
    assert result.implementation_date != ROW_STAMPED_AT
    # On the wire it is an ISO 8601 string, which is what the pinned field declares
    # ("type": ["string", "null"], "format": "date-time"). The Z spelling is this
    # repo's one datetime serializer; both halves are asserted so neither the literal
    # nor the instant can drift.
    wired = to_wire(result)["implementation_date"]
    assert wired == "2026-03-02T14:30:00Z"
    assert datetime.fromisoformat(wired) == APPLIED_AT


def test_pause_update_carries_this_updates_instant():
    """The pause/resume success site: a write that DOES move the row still reports its own instant."""
    _assert_carries_this_updates_instant(
        _run(
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-impl-date",
                media_buy_id="mb_resolved",
                paused=True,
            )
        )
    )


def test_creative_assignment_only_update_carries_this_updates_instant():
    """The final success site, on the shape that writes NO media_buys row.

    ``packages=[{package_id, creative_ids: []}]`` reaches the creative_ids branch and,
    with an empty list, writes nothing at all — so ``media_buys.updated_at`` cannot have
    moved and the row's stamp is provably an earlier update's. This is the shape the
    row-derived value got wrong.
    """
    _assert_carries_this_updates_instant(
        _run(
            UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key="test-idem-key-impl-date-2",
                media_buy_id="mb_resolved",
                packages=[{"package_id": "pkg_001", "creative_ids": []}],
            )
        )
    )
