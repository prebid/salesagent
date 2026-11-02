"""Stand-ins for the GAM SOAP client, for tests that drive the GAM adapter.

The adapter has no dry-run mode: every read and write it makes is a GAM call. It used
to fabricate its own test data when ``dry_run`` was set — ``_get_line_item_info``
returned a line item map naming "mock_package" / "package_1" / "test_package" with
invented ``creativePlaceholders`` — so the creative-size check compared a test's
creative against sizes production itself made up.

That branch is STILL PRESENT in ``src/adapters/gam/managers/creatives.py`` (the ``else``
of ``if line_item_service:``); only the ``dry_run`` half of its guard was removed, and
GH #2245 tracks deleting it. It is unreachable — ``add_creative_assets`` always passes
the service it just asked the client manager for — so nothing here may depend on it: a
test that needs a line item states one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


class SoapObject(dict):
    """A GAM (Zeep) object stand-in: production reads these by key AND by attribute."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def gam_line_item(name: str, *, item_id: str = "1001", sizes: tuple[tuple[int, int], ...] = ((728, 90),)) -> SoapObject:
    """One line item in a GAM order, carrying the creative sizes it accepts."""
    return SoapObject(
        name=name,
        id=item_id,
        creativePlaceholders=[
            SoapObject(size=SoapObject(width=width, height=height), creativeSizeType="PIXEL") for width, height in sizes
        ],
    )


def stub_gam_client_manager(
    *,
    line_items: tuple[SoapObject, ...] | list[SoapObject] = (),
    created_creative_id: str = "gam_creative_1",
    created_order_id: str = "9000001",
    created_line_item_id: str = "9100001",
) -> MagicMock:
    """A ``GAMClientManager`` instance stand-in serving the order's line items.

    Every service the adapter asks for resolves: the ones a test states behaviour for
    are configured, and any other is a bare mock so a call reaching it neither fails
    nor pretends to have done anything.

    ``createOrders`` and ``createLineItems`` answer with NUMERIC ids because production
    reads them back and converts: ``create_order`` does ``str(created[0]["id"])`` and the
    approval path then does ``int(order_id)``. A bare mock satisfies the subscript (every
    MagicMock does) and yields ``"<MagicMock ...>"``, which fails much later as
    ``invalid literal for int()`` -- a create that never touched a real client looking
    like an ad-server fault.
    """
    services = {
        "OrderService": MagicMock(createOrders=MagicMock(return_value=[{"id": int(created_order_id)}])),
        "LineItemService": MagicMock(
            getLineItemsByStatement=MagicMock(return_value=SoapObject(results=list(line_items))),
            createLineItems=MagicMock(return_value=[{"id": int(created_line_item_id)}]),
        ),
        "CreativeService": MagicMock(createCreatives=MagicMock(return_value=[{"id": created_creative_id}])),
    }
    client_manager = MagicMock()
    client_manager.get_service.side_effect = lambda name: services.get(name) or MagicMock()
    return client_manager
