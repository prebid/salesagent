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

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal


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


def _custom_targeting_values(values: dict[tuple[int, str], int]) -> Callable[[dict[str, str]], SoapObject]:
    """``getCustomTargetingValuesByStatement`` answering the key id and name it is filtered on.

    Production looks a value up by ``customTargetingKeyId = <id> AND name = '<name>'`` and
    reads ``results[0].id``. An unknown pair answers no results.
    """

    def lookup(statement: dict[str, str]) -> SoapObject:
        query = statement["query"]
        key_id = int(query.split("customTargetingKeyId = ", 1)[1].split(" ", 1)[0])
        name = query.split("name = '", 1)[1].rstrip("'")
        found = [SoapObject(id=values[(key_id, name)])] if (key_id, name) in values else []
        return SoapObject(results=found)

    return lookup


def gam_criterion(key_id: int, value_ids: list[int], operator: str = "IS") -> dict[str, Any]:
    """One ``CustomCriteria`` as the targeting manager builds it for GAM."""
    return {"xsi_type": "CustomCriteria", "keyId": key_id, "operator": operator, "valueIds": value_ids}


def gam_criteria_set(operator: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    """One ``CustomCriteriaSet`` (``"AND"`` or ``"OR"``) as the targeting manager builds it for GAM."""
    return {"xsi_type": "CustomCriteriaSet", "logicalOperator": operator, "children": children}


def stub_gam_client_manager(
    *,
    line_items: tuple[SoapObject, ...] | list[SoapObject] = (),
    created_creative_id: str = "gam_creative_1",
    created_order_id: str = "9000001",
    created_line_item_id: str = "9100001",
    custom_targeting_values: dict[tuple[int, str], int] | None = None,
) -> MagicMock:
    """A ``GAMClientManager`` instance stand-in serving the order's line items.

    Every service the adapter asks for resolves: the ones a test states behaviour for
    are configured, and any other is a bare mock so a call reaching it neither fails
    nor pretends to have done anything. The same services answer whether production asks
    the manager (``get_service``) or the raw client it hands out (``get_client().GetService``),
    which is how the targeting manager reaches them.

    ``custom_targeting_values`` maps ``(key_id, value_name)`` to the id GAM holds for that
    value, for the targeting paths that resolve value names. The stand-in does not create
    values: a name it does not hold fails the create call instead of being given an id,
    so a test states every value its targeting resolves.

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
        "CustomTargetingService": MagicMock(
            getCustomTargetingValuesByStatement=MagicMock(
                side_effect=_custom_targeting_values(custom_targeting_values or {})
            ),
            createCustomTargetingValues=MagicMock(
                side_effect=RuntimeError("the GAM stand-in creates no custom targeting values")
            ),
        ),
    }
    client_manager = MagicMock()
    client_manager.get_service.side_effect = lambda name: services.get(name) or MagicMock()
    client_manager.get_client.return_value.GetService.side_effect = client_manager.get_service.side_effect
    return client_manager


def build_gam_adapter(
    client_manager: MagicMock,
    *,
    principal: Principal | None = None,
    config: dict[str, Any] | None = None,
    tenant_id: str = "test_tenant",
) -> GoogleAdManager:
    """The real GAM adapter, built over ``client_manager`` in place of the GAM client.

    By default the principal is mapped to GAM advertiser 123 and the config names network
    123456 and trafficker ``trafficker_123``; a test passes only what it needs to differ.
    The advertiser is the principal's ``google_ad_manager`` mapping, as production reads it.
    """
    principal = principal or Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
    )
    config = config or {
        "network_code": "123456",
        "service_account_key_file": "/path/to/key.json",
        "trafficker_id": "trafficker_123",
    }
    with patch("src.adapters.google_ad_manager.GAMClientManager", return_value=client_manager):
        return GoogleAdManager(
            config=config,
            principal=principal,
            network_code=config["network_code"],
            advertiser_id=principal.platform_mappings["google_ad_manager"]["advertiser_id"],
            trafficker_id=config["trafficker_id"],
            tenant_id=tenant_id,
            tenant_ai_config=None,
        )
