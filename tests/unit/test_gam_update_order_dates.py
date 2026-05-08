"""Tests for `GAMOrdersManager.update_order_dates`.

Covers tescoboy issues #157 and #150:

- #157: `_update_media_buy_impl` previously wrote new flight bounds to
  Postgres only. Approved updates left the DB and ad server out of sync.
  The fix adds `update_order_dates` to the GAM adapter and wires it
  into the impl after the DB write.

- #150: GAM rejects LineItem mutations with `ForecastingError.NO_FORECAST_YET`
  during the ~60 min forecast warmup. The fix wraps the
  `LineItemService.updateLineItems` call in `update_order_dates` with a
  bounded retry loop matching the existing pattern in
  `update_line_item_budget` and `approve_order`.

These tests target the adapter helper directly using a mocked GAM
client. Wire-shape assertions live in `test_gam_payload_shape.py`; here
we focus on the call surface, partial-failure semantics, and retry
behavior.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from src.adapters.gam.managers.orders import GAMOrdersManager


def _make_manager(client_manager=None, dry_run=False):
    return GAMOrdersManager(
        client_manager=client_manager,
        advertiser_id="adv_1",
        trafficker_id="tr_1",
        dry_run=dry_run,
    )


def _client_with_services(order_results, line_item_results, update_orders_result, update_lis_result):
    order_service = MagicMock()
    order_service.getOrdersByStatement.return_value = {"results": order_results}
    order_service.updateOrders.return_value = update_orders_result

    lis_service = MagicMock()
    lis_service.getLineItemsByStatement.return_value = {"results": line_item_results}
    lis_service.updateLineItems.return_value = update_lis_result

    client_manager = MagicMock()
    client_manager.get_service.side_effect = lambda name: {
        "OrderService": order_service,
        "LineItemService": lis_service,
    }[name]
    return client_manager, order_service, lis_service


class TestUpdateOrderDatesDryRun:
    def test_dry_run_skips_gam_calls(self):
        client_manager = MagicMock()
        manager = _make_manager(client_manager=client_manager, dry_run=True)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        )

        assert ok is True
        client_manager.get_service.assert_not_called()


class TestUpdateOrderDatesNoOp:
    def test_no_dates_returns_true_without_calling_gam(self):
        client_manager = MagicMock()
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(order_id="123", start_time=None, end_time=None)

        assert ok is True
        client_manager.get_service.assert_not_called()


class TestUpdateOrderDatesSuccess:
    def test_patches_order_and_all_line_items(self):
        order = {"id": 123, "name": "Order"}
        li_a = {"id": 1, "name": "LI-A"}
        li_b = {"id": 2, "name": "LI-B"}
        client_manager, order_service, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[li_a, li_b],
            update_orders_result=[order],
            update_lis_result=[li_a, li_b],
        )
        manager = _make_manager(client_manager=client_manager)

        start = datetime(2026, 5, 7, 15, 0, tzinfo=UTC)  # 11:00 EDT
        end = datetime(2026, 5, 14, 15, 0, tzinfo=UTC)
        ok = manager.update_order_dates(order_id="123", start_time=start, end_time=end)

        assert ok is True
        # Order was patched with tz-converted wall-clock fields. Order has
        # no timeZoneId field in GAM; only LineItems carry it.
        assert order["startDateTime"]["hour"] == 11
        assert order["startDateTime"]["date"] == {"year": 2026, "month": 5, "day": 7}
        assert "timeZoneId" not in order["startDateTime"]
        order_service.updateOrders.assert_called_once_with([order])

        # Both line items patched and updateLineItems called once with the batch.
        assert li_a["startDateTime"]["timeZoneId"] == "America/New_York"
        assert li_b["startDateTime"]["timeZoneId"] == "America/New_York"
        assert li_a["endDateTime"]["hour"] == 11
        lis_service.updateLineItems.assert_called_once_with([li_a, li_b])

    def test_only_end_time_supplied_leaves_start_unchanged(self):
        order = {"id": 123, "startDateTime": "ORIGINAL"}
        li = {"id": 1, "startDateTime": "ORIGINAL"}
        client_manager, *_ = _client_with_services(
            order_results=[order],
            line_item_results=[li],
            update_orders_result=[order],
            update_lis_result=[li],
        )
        manager = _make_manager(client_manager=client_manager)

        end = datetime(2026, 5, 14, 15, 0, tzinfo=UTC)
        ok = manager.update_order_dates(order_id="123", start_time=None, end_time=end)

        assert ok is True
        assert order["startDateTime"] == "ORIGINAL"
        assert order["endDateTime"]["date"] == {"year": 2026, "month": 5, "day": 14}
        assert li["startDateTime"] == "ORIGINAL"
        assert li["endDateTime"]["hour"] == 11

    def test_order_with_no_line_items_still_succeeds(self):
        order = {"id": 123}
        client_manager, _, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[],
            update_orders_result=[order],
            update_lis_result=[],
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )

        assert ok is True
        lis_service.updateLineItems.assert_not_called()


class TestUpdateOrderDatesFailure:
    def test_order_not_found_returns_false(self):
        client_manager, *_ = _client_with_services(
            order_results=[],
            line_item_results=[],
            update_orders_result=None,
            update_lis_result=None,
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_update_orders_returns_empty_returns_false(self):
        order = {"id": 123}
        client_manager, *_ = _client_with_services(
            order_results=[order],
            line_item_results=[{"id": 1}],
            update_orders_result=None,
            update_lis_result=[{"id": 1}],
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_partial_line_item_update_returns_false(self):
        order = {"id": 123}
        client_manager, _, lis_service = _client_with_services(
            order_results=[order],
            line_item_results=[{"id": 1}, {"id": 2}],
            update_orders_result=[order],
            update_lis_result=[{"id": 1}],  # only 1 of 2 echoed back
        )
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_get_orders_raises_returns_false(self):
        client_manager = MagicMock()
        order_service = MagicMock()
        order_service.getOrdersByStatement.side_effect = RuntimeError("network down")
        client_manager.get_service.return_value = order_service
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False

    def test_line_item_update_raises_returns_false(self):
        order = {"id": 123}
        order_service = MagicMock()
        order_service.getOrdersByStatement.return_value = {"results": [order]}
        order_service.updateOrders.return_value = [order]

        lis_service = MagicMock()
        lis_service.getLineItemsByStatement.side_effect = RuntimeError("forecast error")

        client_manager = MagicMock()
        client_manager.get_service.side_effect = lambda name: {
            "OrderService": order_service,
            "LineItemService": lis_service,
        }[name]
        manager = _make_manager(client_manager=client_manager)

        ok = manager.update_order_dates(
            order_id="123",
            start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
            end_time=None,
        )
        assert ok is False


class TestUpdateOrderDatesNoForecastRetry:
    """Issue #150 — retry on ForecastingError.NO_FORECAST_YET."""

    def _build_client_with_intermittent_lis(self, lis_responses):
        """Return a client whose LineItemService.updateLineItems plays a
        scripted sequence of side effects in order. Each entry is either
        an exception class to raise or a return value.
        """
        order = {"id": 123}
        order_service = MagicMock()
        order_service.getOrdersByStatement.return_value = {"results": [order]}
        order_service.updateOrders.return_value = [order]

        lis_service = MagicMock()
        lis_service.getLineItemsByStatement.return_value = {"results": [{"id": 1}]}
        lis_service.updateLineItems.side_effect = lis_responses

        client_manager = MagicMock()
        client_manager.get_service.side_effect = lambda name: {
            "OrderService": order_service,
            "LineItemService": lis_service,
        }[name]
        return client_manager, lis_service

    def test_retries_then_succeeds(self):
        # Two NO_FORECAST_YET faults, then success on the third attempt.
        client_manager, lis_service = self._build_client_with_intermittent_lis(
            [
                Exception("ForecastingError.NO_FORECAST_YET"),
                Exception("[ForecastingError.NO_FORECAST_YET @ id; trigger:'1']"),
                [{"id": 1}],
            ]
        )
        manager = _make_manager(client_manager=client_manager)

        with patch("time.sleep") as mock_sleep:
            ok = manager.update_order_dates(
                order_id="123",
                start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
                end_time=None,
            )

        assert ok is True
        assert lis_service.updateLineItems.call_count == 3
        # First two attempts slept 5s + 10s per the documented backoff
        assert [c.args[0] for c in mock_sleep.call_args_list] == [5, 10]

    def test_retries_exhausted_returns_false(self):
        # Eight NO_FORECAST_YET faults — exceeds the bounded retry budget.
        from src.adapters.gam.managers.orders import NO_FORECAST_RETRY_BACKOFF

        client_manager, lis_service = self._build_client_with_intermittent_lis(
            [Exception("NO_FORECAST_YET")] * len(NO_FORECAST_RETRY_BACKOFF)
        )
        manager = _make_manager(client_manager=client_manager)

        with patch("time.sleep") as mock_sleep:
            ok = manager.update_order_dates(
                order_id="123",
                start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
                end_time=None,
            )

        assert ok is False
        # All 8 attempts ran; last attempt did not sleep (no further retry).
        assert lis_service.updateLineItems.call_count == len(NO_FORECAST_RETRY_BACKOFF)
        assert mock_sleep.call_count == len(NO_FORECAST_RETRY_BACKOFF) - 1

    def test_non_forecast_error_does_not_retry(self):
        # UPDATE_RESERVATION_NOT_ALLOWED is not transient; surface immediately.
        client_manager, lis_service = self._build_client_with_intermittent_lis(
            [Exception("UPDATE_RESERVATION_NOT_ALLOWED")]
        )
        manager = _make_manager(client_manager=client_manager)

        with patch("time.sleep") as mock_sleep:
            ok = manager.update_order_dates(
                order_id="123",
                start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
                end_time=None,
            )

        assert ok is False
        assert lis_service.updateLineItems.call_count == 1
        mock_sleep.assert_not_called()

    def test_backoff_pattern_matches_documented_sequence(self):
        # When all 8 attempts fail with NO_FORECAST_YET, the sleeps
        # between them must match the documented [5, 10, 20, 30, 30, 30, 30].
        # Note: the LAST attempt has no follow-up sleep (no further retry).
        from src.adapters.gam.managers.orders import NO_FORECAST_RETRY_BACKOFF

        client_manager, _ = self._build_client_with_intermittent_lis(
            [Exception("NO_FORECAST_YET")] * len(NO_FORECAST_RETRY_BACKOFF)
        )
        manager = _make_manager(client_manager=client_manager)

        with patch("time.sleep") as mock_sleep:
            manager.update_order_dates(
                order_id="123",
                start_time=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
                end_time=None,
            )

        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleeps == list(NO_FORECAST_RETRY_BACKOFF[:-1])
