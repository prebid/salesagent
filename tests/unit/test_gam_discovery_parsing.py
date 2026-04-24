"""Tests for GAM discovery parsing — Order and LineItem from_gam_object.

Verifies parse behavior for well-formed and malformed GAM API dicts.
The bare except in discover_orders()/discover_line_items() silently drops
parse failures — these tests verify what from_gam_object does and doesn't
tolerate.

GH #1078 H6.
"""

import pytest

from src.adapters.gam_orders_discovery import LineItem, Order, OrderStatus

pytestmark = pytest.mark.unit


class TestOrderFromGamObject:
    """Order.from_gam_object parsing behavior."""

    def test_minimal_valid_order(self):
        """Minimal valid dict parses successfully."""
        order = Order.from_gam_object({"id": 123, "name": "Test Order", "status": "DRAFT"})
        assert order.order_id == "123"
        assert order.name == "Test Order"
        assert order.status == OrderStatus.DRAFT

    def test_missing_id_raises(self):
        """Missing 'id' field raises KeyError — this is what the bare except swallows."""
        with pytest.raises(KeyError, match="id"):
            Order.from_gam_object({"name": "No ID Order", "status": "DRAFT"})

    def test_missing_name_raises(self):
        """Missing 'name' field raises KeyError."""
        with pytest.raises(KeyError, match="name"):
            Order.from_gam_object({"id": 456, "status": "DRAFT"})

    def test_missing_status_uses_default(self):
        """Missing 'status' falls back to DRAFT via safe_enum_conversion."""
        order = Order.from_gam_object({"id": 789, "name": "No Status"})
        assert order.status == OrderStatus.DRAFT

    def test_full_order_with_budget(self):
        """Order with budget and advertiser info parses correctly."""
        order = Order.from_gam_object(
            {
                "id": 100,
                "name": "Full Order",
                "status": "APPROVED",
                "advertiserId": 5001,
                "advertiserName": "Acme Corp",
                "totalBudget": {"microAmount": 50000000, "currencyCode": "USD"},
            }
        )
        assert order.advertiser_id == "5001"
        assert order.advertiser_name == "Acme Corp"
        assert order.total_budget == 50.0
        assert order.currency_code == "USD"


class TestLineItemFromGamObject:
    """LineItem.from_gam_object parsing behavior."""

    def test_minimal_valid_line_item(self):
        """Minimal valid dict parses successfully."""
        li = LineItem.from_gam_object(
            {
                "id": 200,
                "orderId": 100,
                "name": "Test Line Item",
                "lineItemType": "STANDARD",
                "status": "READY",
            }
        )
        assert li.line_item_id == "200"
        assert li.order_id == "100"
        assert li.name == "Test Line Item"

    def test_missing_id_raises(self):
        """Missing 'id' field raises KeyError — silently swallowed in discover_line_items."""
        with pytest.raises(KeyError, match="id"):
            LineItem.from_gam_object({"orderId": 100, "name": "No ID", "lineItemType": "STANDARD"})

    def test_missing_order_id_raises(self):
        """Missing 'orderId' field raises KeyError."""
        with pytest.raises(KeyError, match="orderId"):
            LineItem.from_gam_object({"id": 300, "name": "No OrderId", "lineItemType": "STANDARD"})
