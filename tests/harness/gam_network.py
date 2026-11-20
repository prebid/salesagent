"""The configured Google Ad Manager suite network, as one e2e scenario uses it.

On e2e the live server builds production's ``GoogleAdManager`` from the tenant's
``adapter_config`` row, and the row names the network the environment configures
(``ToolingSettings.gam_suite_network``). What a GAM Then grades is then what that network
holds, read back through the GAM API here, by the scenario's own objects:

* **orders**: every order whose name starts with the scenario's prefix. The seller's naming
  templates lead every order and line item name with it (``onboard_gam(name_prefix=...)``),
  so an order the seller created and failed to record is read back too;
* **line items**: every line item of those orders;
* **creatives**: the ids the seller recorded on the tenant's creatives
  (``ad_server_creative_id``), read off the live server's database by the env;
* **associations**: every association on those line items.

Objects this harness put on the network for a Given (a booked order and its line items, a
creative GAM already holds) are the network's state before the scenario, not the seller's
work, so they are read as such and never reported as created.

The network names objects with its own ids, and a scenario names them with the ids it
wrote (an advertiser "3000001", a booked line item "9000001"). Each id the harness
realized for a scenario id is reported under the scenario's id, so one assertion grades
both realizations. An id the scenario did not name is reported as the network gave it.

GAM's answer is read as the stand-in records what was sent: an unset field (``None``, or an
empty list) is absent, and so is the ``isAspectRatio: false`` GAM may fill in on a size sent
without it.

Isolation and cleanup: the prefix is one per test process and scenario
(:data:`RUN_PREFIX`), and :meth:`ConfiguredGamNetwork.archive` archives the scenario's
orders and line items when the env exits. GAM has no creative archive; creatives stay.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.core.config import GamSuiteNetwork

logger = logging.getLogger(__name__)

#: The declaration of every GAM test in an environment with no GAM setting at all
#: (``ToolingSettings.gam_suite_network`` is ``None``): an e2e scenario's E2EUnsupportedSetup
#: reason, a requires_gam test's xfail reason.
NO_GAM_NETWORK = "this environment configures no GAM network"

#: This test process's part of every name it gives a GAM object.
RUN_PREFIX = f"sa-e2e-{uuid.uuid4().hex[:8]}"

#: The keys of a GAM object that hold another object's id.
_ID_KEYS = frozenset({"id", "advertiserId", "traffickerId", "adUnitId", "orderId", "lineItemId", "creativeId"})

#: Where each create method of the seller's is read back, and the statement's subject.
_READ_BACK = {
    ("OrderService", "createOrders"): "orders",
    ("LineItemService", "createLineItems"): "line_items",
    ("CreativeService", "createCreatives"): "creatives",
    ("LineItemCreativeAssociationService", "createLineItemCreativeAssociations"): "associations",
}


def _plain(value: Any) -> Any:
    """GAM's answer as plain data: a SOAP object is a dict of its set fields, a size has no default aspect flag."""
    if isinstance(value, list):
        return [_plain(item) for item in value]
    values = getattr(value, "__values__", None)
    if values is None:
        return value
    fields = {key: _plain(item) for key, item in values.items() if item is not None and item != []}
    if set(fields) <= {"width", "height", "isAspectRatio"} and fields.get("isAspectRatio") is False:
        del fields["isAspectRatio"]
    return fields


def scenario_named(objects: Sequence[Any], scenario_ids: Mapping[str, str]) -> list[dict[str, Any]]:
    """GAM's *objects* as plain data, each carrying its ``xsi_type``, with every id in *scenario_ids* under the scenario's id."""

    def renamed(value: Any) -> Any:
        if isinstance(value, list):
            return [renamed(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: scenario_ids.get(str(item), item) if key in _ID_KEYS else renamed(item) for key, item in value.items()
        }

    return [{**renamed(_plain(obj)), "xsi_type": obj._xsd_type.name} for obj in objects]


class ConfiguredGamNetwork:
    """The configured GAM network the suite books against, for one scenario: its client, its name prefix, its ids."""

    def __init__(self, network: GamSuiteNetwork) -> None:
        from scripts.gam_prerequisites_check import network_client_manager

        self.network = network
        self.prefix = f"{RUN_PREFIX}-{uuid.uuid4().hex[:6]}"
        self._manager = network_client_manager(network)
        #: The id the scenario wrote, by the network's id for the object it names.
        self._scenario_ids: dict[str, str] = {}
        #: The ids of the objects this harness created for a Given.
        self._harness_ids: set[str] = set()
        user = self._service("UserService").getCurrentUser()
        #: The service account's own user: the trafficker the adapter resolves when none is configured.
        self.trafficker_id = str(user["id"])
        current = self._service("NetworkService").getCurrentNetwork()
        self._time_zone = str(current["timeZone"])
        self._currency = str(current["currencyCode"])
        # A record of the network this scenario ran against, compared with nothing.
        logger.info("GAM e2e scenario %s on configured network %s", self.prefix, network.network_code)

    def name(self, network_id: str, scenario_id: str) -> None:
        """Report the network's *network_id* under the id the scenario wrote for it."""
        self._scenario_ids[str(network_id)] = scenario_id

    def _service(self, name: str) -> Any:
        return self._manager.get_service(name)

    def _select(self, service: str, method: str, where: str) -> list[Any]:
        """Every object ``service.method`` answers for *where*, all pages, in id order unless stated."""
        from googleads import ad_manager

        statement = ad_manager.StatementBuilder().Where(where)
        if " ORDER BY " not in where:
            statement.OrderBy("id", ascending=True)
        found: list[Any] = []
        while True:
            page = getattr(self._service(service), method)(statement.ToStatement())
            results = list(page["results"] or []) if "results" in page else []
            found += results
            statement.offset += statement.limit
            if len(results) < statement.limit:
                return found

    def _orders(self) -> list[Any]:
        return self._select("OrderService", "getOrdersByStatement", f"name LIKE '{self.prefix}%'")

    def _line_items(self, order_ids: Iterable[str]) -> list[Any]:
        ids = ",".join(order_ids)
        return self._select("LineItemService", "getLineItemsByStatement", f"orderId IN ({ids})") if ids else []

    def _scenario_named(self, objects: Sequence[Any]) -> list[dict[str, Any]]:
        return scenario_named(objects, self._scenario_ids)

    def created(self, service: str, method: str, *, recorded_creative_ids: Sequence[str]) -> list[dict[str, Any]]:
        """What the seller created through ``service.method``, as the network holds it now.

        *recorded_creative_ids* are the GAM ids the seller recorded on the tenant's creatives.
        """
        from tests.harness._realize import E2EUnsupportedSetup

        subject = _READ_BACK.get((service, method))
        if subject is None:
            raise E2EUnsupportedSetup(
                f"a real network keeps no record of what the seller sent it through {service}.{method}, "
                "only of what it holds"
            )
        orders = self._orders()
        if subject == "orders":
            return self._scenario_named([o for o in orders if str(o["id"]) not in self._harness_ids])
        line_items = self._line_items(str(o["id"]) for o in orders)
        if subject == "line_items":
            return self._scenario_named([li for li in line_items if str(li["id"]) not in self._harness_ids])
        if subject == "creatives":
            ids = ",".join(i for i in recorded_creative_ids if i not in self._harness_ids)
            return self._scenario_named(
                self._select("CreativeService", "getCreativesByStatement", f"id IN ({ids})") if ids else []
            )
        line_item_ids = ",".join(str(li["id"]) for li in line_items)
        if not line_item_ids:
            return []
        return self._scenario_named(
            self._select(
                "LineItemCreativeAssociationService",
                "getLineItemCreativeAssociationsByStatement",
                f"lineItemId IN ({line_item_ids}) ORDER BY lineItemId ASC",
            )
        )

    def line_item(self, scenario_id: str) -> dict[str, Any]:
        """The line item the scenario named *scenario_id*, as the network holds it now."""
        (network_id,) = [network for network, named in self._scenario_ids.items() if named == scenario_id]
        (item,) = self._select("LineItemService", "getLineItemsByStatement", f"id = {int(network_id)}")
        return self._scenario_named([item])[0]

    def book(self, *, order_id: str, line_items: Sequence[tuple[str, tuple[int, int]]]) -> tuple[str, dict[str, str]]:
        """An order of the configured advertiser, with one line item per (scenario id, size), on the network.

        The network's ids, named under the scenario's: the order's, and each line item's by
        its scenario id. A line item accepts creatives of its size, on the configured ad unit,
        over a flight that starts now and ends in 30 days.
        """
        (order,) = self._service("OrderService").createOrders(
            [
                {
                    "name": f"{self.prefix} booked {order_id}",
                    "advertiserId": self.network.advertiser_id,
                    "traffickerId": self.trafficker_id,
                }
            ]
        )
        end = datetime.now(ZoneInfo(self._time_zone)) + timedelta(days=30)
        created = self._service("LineItemService").createLineItems(
            [
                {
                    "name": f"{self.prefix} booked {line_item_id}",
                    "orderId": order["id"],
                    "targeting": {
                        "inventoryTargeting": {
                            "targetedAdUnits": [{"adUnitId": self.network.ad_unit_ids[0], "includeDescendants": True}]
                        }
                    },
                    "creativePlaceholders": [{"size": {"width": width, "height": height, "isAspectRatio": False}}],
                    "lineItemType": "PRICE_PRIORITY",
                    "priority": 12,
                    "costType": "CPM",
                    "costPerUnit": {"currencyCode": self._currency, "microAmount": 1_000_000},
                    "primaryGoal": {"goalType": "NONE"},
                    "creativeRotationType": "EVEN",
                    "startDateTimeType": "IMMEDIATELY",
                    "endDateTime": {
                        "date": {"year": end.year, "month": end.month, "day": end.day},
                        "hour": 23,
                        "minute": 59,
                        "second": 0,
                        "timeZoneId": self._time_zone,
                    },
                }
                for line_item_id, (width, height) in line_items
            ]
        )
        booked = {scenario_id: str(item["id"]) for (scenario_id, _), item in zip(line_items, created, strict=True)}
        self._harness_ids |= {str(order["id"]), *booked.values()}
        self.name(str(order["id"]), order_id)
        for scenario_id, network_id in booked.items():
            self.name(network_id, scenario_id)
        return str(order["id"]), booked

    def hold_creative(self, *, creative_id: str, image_url: str) -> str:
        """A 300x250 image creative of the configured advertiser the network already holds, named *creative_id*."""
        (creative,) = self._service("CreativeService").createCreatives(
            [
                {
                    "xsi_type": "ImageRedirectCreative",
                    "name": f"{self.prefix} held {creative_id}",
                    "advertiserId": self.network.advertiser_id,
                    "size": {"width": 300, "height": 250, "isAspectRatio": False},
                    "imageUrl": image_url,
                    "destinationUrl": image_url,
                }
            ]
        )
        self._harness_ids.add(str(creative["id"]))
        self.name(str(creative["id"]), creative_id)
        return str(creative["id"])

    def archive(self) -> None:
        """Archive the scenario's orders and their line items: the network's own cleanup."""
        from googleads import ad_manager

        order_ids = ",".join(str(order["id"]) for order in self._orders())
        if not order_ids:
            return
        self._service("LineItemService").performLineItemAction(
            {"xsi_type": "ArchiveLineItems"},
            ad_manager.StatementBuilder().Where(f"orderId IN ({order_ids})").ToStatement(),
        )
        self._service("OrderService").performOrderAction(
            {"xsi_type": "ArchiveOrders"}, ad_manager.StatementBuilder().Where(f"id IN ({order_ids})").ToStatement()
        )
