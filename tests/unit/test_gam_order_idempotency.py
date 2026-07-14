"""GAM order creation is idempotent for crash-recoverable approval resumes (#1637).

A mid-finalize crash can re-invoke ``execute_approved_media_buy`` with the SAME deterministic
order name (the default template keys on media_buy_id). ``create_order`` must reuse an existing
non-archived order for the advertiser instead of creating a duplicate remote order — this closes
the "adapter created the order, then the process died before platform_order_id persisted" window
that the DB-side ``platform_order_id`` guard cannot.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from src.adapters.gam.managers.orders import GAMOrdersManager

_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = datetime(2026, 12, 31, tzinfo=UTC)


def _manager(order_service: MagicMock) -> GAMOrdersManager:
    client = MagicMock()
    client.get_service = MagicMock(return_value=order_service)
    return GAMOrdersManager(client, advertiser_id="123", trafficker_id="456", dry_run=False)


def test_reuses_existing_non_archived_order_instead_of_creating_duplicate():
    order_service = MagicMock()
    order_service.getOrdersByStatement.return_value = {"results": [{"id": 555, "isArchived": False}]}

    order_id = _manager(order_service).create_order("Camp - mb_1 - 2026", 5000.0, _START, _END)

    assert order_id == "555"
    order_service.createOrders.assert_not_called()  # NO duplicate remote order


def test_creates_when_no_existing_order():
    order_service = MagicMock()
    order_service.getOrdersByStatement.return_value = {"results": []}
    order_service.createOrders.return_value = [{"id": 777}]

    order_id = _manager(order_service).create_order("Camp - mb_2 - 2026", 5000.0, _START, _END)

    assert order_id == "777"
    assert order_service.createOrders.call_count == 1


def test_archived_order_is_not_reused():
    order_service = MagicMock()
    order_service.getOrdersByStatement.return_value = {"results": [{"id": 555, "isArchived": True}]}
    order_service.createOrders.return_value = [{"id": 888}]

    order_id = _manager(order_service).create_order("Camp - mb_3 - 2026", 5000.0, _START, _END)

    assert order_id == "888"  # archived match ignored → a fresh order is created
    assert order_service.createOrders.call_count == 1


def test_lookup_failure_falls_through_to_create():
    order_service = MagicMock()
    order_service.getOrdersByStatement.side_effect = RuntimeError("GAM lookup transient error")
    order_service.createOrders.return_value = [{"id": 999}]

    order_id = _manager(order_service).create_order("Camp - mb_4 - 2026", 5000.0, _START, _END)

    assert order_id == "999"  # a lookup failure must never block creation
    assert order_service.createOrders.call_count == 1
