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

``serve_gam_report`` configures a ``stub_gam_client``'s ``ReportService`` to run one report
job, and returns the report body Google's report host serves (``gam_report_csv_dump``).

The adapter has no dry-run mode: every read and write it makes is a GAM call, so a test
that needs a line item states one.
"""

from __future__ import annotations

import csv
import gzip
import io
import itertools
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from unittest.mock import MagicMock

from googleads.errors import GoogleAdsServerFault

from src.core.security.egress.response import OutboundResult


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


def gam_fault(reason: str) -> GoogleAdsServerFault:
    """What googleads raises for a SOAP fault: GAM's ``[<reason> @ ]`` text, for an ``ApiError`` reason."""
    return GoogleAdsServerFault(None, message=f"[{reason} @ ]")


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
      refuses instead, raising ``gam_fault``.
    * ``LineItemService.getLineItemsByStatement`` answers the line items the network holds,
      read at call time: ``client.line_items`` (seeded from ``line_items``; a test adds held
      line items there), then the line items it created.
    * ``OrderService.performOrderAction`` answers ``numChanges: 1``, so the adapter's
      in-request approval takes its success branch (``approve_order``).
    * The ``create*`` methods in ``GAM_CREATE_METHODS`` answer each sent object with a copy
      carrying a fresh NUMERIC id, and keep that answer in ``client.created_objects``, keyed
      by ``(service, method)``: what the network holds after the scenario. The ids are
      numeric because production reads them back and converts: ``create_order`` does
      ``str(created[0]["id"])`` and the approval path then does ``int(order_id)``. The sent
      objects are not mutated. GAM keys a line item creative association by its line item
      and creative and gives it no id; the id this stand-in adds to one is its own, and
      nothing should read it. A line item in ``client.refused_line_items`` (line item id ->
      ``ApiError`` reason; a test adds them) refuses its association with ``gam_fault``.
    * ``CreativeService.updateCreatives`` replaces each sent creative in
      ``client.created_objects``, by the ``id`` GAM requires on an update, and answers it. An id
      the network does not hold raises ``gam_fault``. Given a reason in
      ``client.creative_update_fault`` it refuses instead, with ``gam_fault``. Ceiling: nobody has
      tried this call against real GAM; the stand-in does not model which fields GAM lets an
      update change (``Creative.size`` is read-only after creation).
    * ``LineItemCreativeAssociationService.getLineItemCreativeAssociationsByStatement``
      answers the associations the network created, read at call time.

    Ceilings:

    * ``getLineItemsByStatement`` and ``getLineItemCreativeAssociationsByStatement`` answer
      every line item and every association the network holds; they do not evaluate PQL.
    * A test must not drive the adapter's background approval path
      (``google_ad_manager.py`` starts it when the in-request approval fails): its daemon
      thread polls for minutes and can construct a client after the test has released the
      patch. The ``numChanges: 1`` answer keeps the in-request approval on its success
      branch, so the thread is not started.
    """
    ids = itertools.count(9000001)
    created: dict[tuple[str, str], list[SoapObject]] = {}
    client = MagicMock()
    client.line_items = list(line_items)
    client.refused_line_items = {}
    client.creative_update_fault = None

    def _creates(service: str, method: str) -> Callable[..., list[SoapObject]]:
        def _answer(objects: list[Any], *_args: Any, **_kwargs: Any) -> list[SoapObject]:
            answered = [SoapObject(obj, id=next(ids)) for obj in objects]
            created.setdefault((service, method), []).extend(answered)
            return answered

        return _answer

    services: dict[str, MagicMock] = {
        "LineItemService": MagicMock(
            getLineItemsByStatement=MagicMock(
                side_effect=lambda *_args: SoapObject(
                    results=[*client.line_items, *created.get(("LineItemService", "createLineItems"), [])]
                )
            )
        ),
        "OrderService": MagicMock(performOrderAction=MagicMock(return_value=SoapObject(numChanges=1))),
        "NetworkService": MagicMock(
            getCurrentNetwork=MagicMock(
                return_value=SoapObject(timeZone=network_time_zone),
                side_effect=None if network_fault is None else gam_fault(network_fault),
            )
        ),
    }
    for service, method in GAM_CREATE_METHODS:
        setattr(services.setdefault(service, MagicMock()), method, MagicMock(side_effect=_creates(service, method)))

    def _update_creatives(objects: list[Any], *_args: Any, **_kwargs: Any) -> list[SoapObject]:
        if client.creative_update_fault is not None:
            raise gam_fault(client.creative_update_fault)
        held = created.setdefault(("CreativeService", "createCreatives"), [])
        answered = []
        for obj in objects:
            index = next((i for i, creative in enumerate(held) if creative["id"] == obj["id"]), None)
            if index is None:
                raise gam_fault("CommonError.NOT_FOUND")
            held[index] = SoapObject(obj)
            answered.append(held[index])
        return answered

    services["CreativeService"].updateCreatives = MagicMock(side_effect=_update_creatives)
    lica = "LineItemCreativeAssociationService"
    _associate = _creates(lica, "createLineItemCreativeAssociations")

    def _associate_unless_refused(objects: list[Any], *args: Any, **kwargs: Any) -> list[SoapObject]:
        for obj in objects:
            reason = client.refused_line_items.get(str(obj["lineItemId"]))
            if reason is not None:
                raise gam_fault(reason)
        return _associate(objects, *args, **kwargs)

    services[lica].createLineItemCreativeAssociations = MagicMock(side_effect=_associate_unless_refused)
    services[lica].getLineItemCreativeAssociationsByStatement = MagicMock(
        side_effect=lambda *_args: SoapObject(
            results=list(created.get((lica, "createLineItemCreativeAssociations"), []))
        )
    )

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


#: Where the stand-in ``ReportService`` says a finished report can be downloaded: a host
#: ``ReportingConfig.ALLOWED_DOMAINS`` accepts, so the seller's provenance check runs for real.
GAM_REPORT_DOWNLOAD_URL = "https://storage.googleapis.com/gam-reports/report.csv.gz"

#: The download of a finished GAM report from Google's report host, by the name
#: ``src.adapters.gam_reporting_service`` calls it: the egress seam's entry point. The patch target for a
#: test that serves the report body without an origin.
GAM_REPORT_DOWNLOAD_TARGET = "src.adapters.gam_reporting_service.send"


def gam_report_csv_dump(rows: Sequence[Mapping[str, object]]) -> bytes:
    """The gzipped ``CSV_DUMP`` body GAM serves for a finished report holding *rows*.

    Rows are keyed by GAM's own column names (``Dimension.*``, ``Column.*``) and written
    verbatim under a header taken from the first row. No column has a default: a caller
    states exactly the columns it means. No rows is a header-less, empty report.
    """
    text = io.StringIO()
    if rows:
        writer = csv.DictWriter(text, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return gzip.compress(text.getvalue().encode())


def gam_delivery_row(
    *,
    advertiser_id: str,
    order_id: str,
    line_item_id: str,
    impressions: int,
    spend: float,
    date: str = "2025-01-15",
) -> dict[str, object]:
    """One row of a GAM delivery report, keyed by GAM's own column names.

    *spend* is in currency; GAM reports revenue in micros, so the row carries it that way.
    """
    return {
        "Dimension.DATE": date,
        "Dimension.ADVERTISER_ID": advertiser_id,
        "Dimension.ORDER_ID": order_id,
        "Dimension.LINE_ITEM_ID": line_item_id,
        "Column.AD_SERVER_IMPRESSIONS": impressions,
        "Column.AD_SERVER_CLICKS": 0,
        "Column.AD_SERVER_CPM_AND_CPC_REVENUE": round(spend * 1_000_000),
    }


def serve_gam_report(
    client: MagicMock,
    *,
    rows: Sequence[Mapping[str, object]] = (),
    job_status: str = "COMPLETED",
    download_url: str = GAM_REPORT_DOWNLOAD_URL,
) -> OutboundResult:
    """Make *client*'s ``ReportService`` run a report job that ends in *job_status*.

    ``runReportJob`` answers a job with an id, ``getReportJobStatus`` answers
    *job_status*, and ``getReportDownloadURL`` answers *download_url*. Returns what Google's
    report host serves for that URL: the gzipped ``CSV_DUMP`` of *rows* as the egress seam
    returns it. Patch the seller's download with it; a caller serving the body from its own
    origin passes ``download_url`` and ignores the return value.

    Ceiling: the stand-in does not evaluate the report query. It answers the configured rows
    whatever dimensions, dates and filter the seller sent.
    """
    report_service = client.GetService("ReportService")
    report_service.runReportJob.return_value = SoapObject(id=8000001)
    report_service.getReportJobStatus.return_value = job_status
    report_service.getReportDownloadURL.return_value = download_url
    return OutboundResult(
        http_status=200, headers={}, content=gam_report_csv_dump(rows), attempts=1, duration_seconds=0.0
    )
