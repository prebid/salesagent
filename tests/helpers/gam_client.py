"""Stand-ins for the GAM SOAP client, for tests that drive the GAM adapter.

Two stand-ins, one network:

* ``stub_gam_client`` stands in for ``googleads.ad_manager.AdManagerClient``, Google's
  client. Every way the adapter reaches a GAM service goes through it:
  ``GAMClientManager.get_service`` calls ``client.GetService``, the adapter and its
  targeting manager call ``client.GetService`` directly, and every construction of a client
  (the adapter's, a health check's, the approval and sync services') ends in
  ``ad_manager.AdManagerClient(...)``. ``GamSellerMixin`` (``tests/harness/gam.py``)
  patches the class on that module.
* ``stub_gam_client_manager`` stands in for this repo's ``GAMClientManager`` and serves the
  same client. It exists for tests that build the adapter from a config whose credentials
  could not be built (a key file path that does not exist), so the manager itself cannot
  run.

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

import itertools
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from googleads.errors import GoogleAdsServerFault


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


#: The ``create*`` methods the stand-in network answers as GAM does: with each sent object,
#: an id filled in. ``client.created_objects`` holds their answers, and nothing else.
GAM_CREATE_METHODS: frozenset[tuple[str, str]] = frozenset(
    {
        ("OrderService", "createOrders"),
        ("LineItemService", "createLineItems"),
        ("CreativeService", "createCreatives"),
        ("LineItemCreativeAssociationService", "createLineItemCreativeAssociations"),
    }
)


#: The stand-in network's time zone when a test states none. Neither UTC nor America/New_York,
#: so a test that does not state a zone cannot pass by matching a hard-coded label or an
#: unconverted UTC wall clock.
STAND_IN_NETWORK_TIME_ZONE = "America/Los_Angeles"


def stub_gam_client(
    *,
    line_items: tuple[SoapObject, ...] | list[SoapObject] = (),
    network_time_zone: str = STAND_IN_NETWORK_TIME_ZONE,
    network_fault: str | None = None,
) -> MagicMock:
    """An ``AdManagerClient`` stand-in: one recorded GAM network.

    ``GetService(name)`` returns the same stand-in for a name for the life of the client,
    so a call on a service nobody configured is still recorded on it and readable
    afterwards. Configured answers:

    * ``NetworkService.getCurrentNetwork`` answers a network whose ``timeZone`` is
      ``network_time_zone``. Any other network field raises ``AttributeError``, so a new
      reader of the network record fails loudly instead of reading a ``MagicMock``. Given
      ``network_fault``, an ``ApiError`` reason such as ``"QuotaError.EXCEEDED_QUOTA"``, it
      refuses instead, raising what googleads raises for a SOAP fault: a
      ``GoogleAdsServerFault`` carrying GAM's ``[<reason> @ ]`` text.
    * ``LineItemService.getLineItemsByStatement`` answers ``line_items``.
    * ``OrderService.performOrderAction`` answers ``numChanges: 1``, so the adapter's
      in-request approval takes its success branch (``approve_order``).
    * The ``create*`` methods in ``GAM_CREATE_METHODS`` answer each sent object with a copy
      carrying a fresh NUMERIC id, and keep that answer in ``client.created_objects``, keyed
      by ``(service, method)``: what the network holds after the scenario. The ids are
      numeric because production reads them back and converts: ``create_order`` does
      ``str(created[0]["id"])`` and the approval path then does ``int(order_id)``. The sent
      objects are not mutated. GAM keys a line item creative association by its line item
      and creative and gives it no id; the id this stand-in adds to one is its own, and
      nothing should read it.

    Ceilings:

    * ``getLineItemsByStatement`` answers the configured line items; it does not evaluate
      PQL against what was created. A test that needs GAM to read back a line item states
      it with ``line_items=``.
    * A test must not drive the adapter's background approval path
      (``google_ad_manager.py`` starts it when the in-request approval fails): its daemon
      thread polls for minutes and can construct a client after the test has released the
      patch. The ``numChanges: 1`` answer keeps the in-request approval on its success
      branch, so the thread is not started.
    """
    ids = itertools.count(9000001)
    created: dict[tuple[str, str], list[SoapObject]] = {}

    def _creates(service: str, method: str) -> Callable[..., list[SoapObject]]:
        def _answer(objects: list[Any], *_args: Any, **_kwargs: Any) -> list[SoapObject]:
            answered = [SoapObject(obj, id=next(ids)) for obj in objects]
            created.setdefault((service, method), []).extend(answered)
            return answered

        return _answer

    services: dict[str, MagicMock] = {
        "LineItemService": MagicMock(
            getLineItemsByStatement=MagicMock(return_value=SoapObject(results=list(line_items)))
        ),
        "OrderService": MagicMock(performOrderAction=MagicMock(return_value=SoapObject(numChanges=1))),
        "NetworkService": MagicMock(
            getCurrentNetwork=MagicMock(
                return_value=SoapObject(timeZone=network_time_zone),
                side_effect=None
                if network_fault is None
                else GoogleAdsServerFault(None, message=f"[{network_fault} @ ]"),
            )
        ),
    }
    for service, method in GAM_CREATE_METHODS:
        setattr(services.setdefault(service, MagicMock()), method, MagicMock(side_effect=_creates(service, method)))

    client = MagicMock()
    client.GetService.side_effect = lambda name, *_args, **_kwargs: services.setdefault(name, MagicMock())
    client.created_objects = created
    return client


def stub_gam_client_manager(*, line_items: tuple[SoapObject, ...] | list[SoapObject] = ()) -> MagicMock:
    """A ``GAMClientManager`` instance stand-in serving one ``stub_gam_client`` network.

    ``get_client()`` returns the client and ``get_service(name)`` resolves through its
    ``GetService``, so both of the adapter's access paths reach the same per-service
    stand-ins.
    """
    client = stub_gam_client(line_items=line_items)
    client_manager = MagicMock()
    client_manager.get_client.return_value = client
    client_manager.get_service.side_effect = client.GetService
    return client_manager
