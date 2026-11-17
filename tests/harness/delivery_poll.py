"""DeliveryPollEnv — integration test environment for _get_media_buy_delivery_impl.

Patches: get_adapter, and Google's GAM SOAP client and report download for a GAM seller.
``report_through_gam`` (``GamSellerMixin.sell_through_gam``) turns the adapter patch into
production's ``get_adapter``, so a GAM seller runs the real ``GoogleAdManager`` adapter.
Real: MediaBuyUoW and the buy's MediaPackage rows (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with DeliveryPollEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])
            assert response.aggregated_totals.impressions == 5000.0

Available mocks via env.mock:
    "adapter"             -- get_adapter mock
    "gam_client"          -- Google's AdManagerClient (``stub_gam_client``)
    "gam_report_download" -- the GAM report download from Google's report host

The get_media_buy_delivery dispatch itself lives in ``DeliveryDispatchMixin`` so a
composite env (``MediaBuyCreateListEnv``) dispatches the same verb through the same code.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse, GetMediaBuyDeliveryRequest, GetMediaBuyDeliveryResponse
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from tests.harness._base import IntegrationEnv, rest_body_from_kwargs
from tests.harness._mixins import DeliveryPollMixin
from tests.harness.gam import GAM_CLIENT_PATCH, GamSellerMixin
from tests.harness.transport import DeliverResult
from tests.helpers.gam_client import gam_delivery_row

#: The request fields the delivery route's body accepts, for a dispatch in the flat form.
_DELIVERY_BODY_FIELDS = (
    "media_buy_ids",
    "status_filter",
    "start_date",
    "end_date",
    "reporting_dimensions",
    "attribution_window",
    "include_package_daily_breakdown",
    "account",
)


class DeliveryDispatchMixin:
    """get_media_buy_delivery dispatch across impl/A2A/MCP/REST, in both call shapes.

    A step dispatches the verb either as flat request fields or as a built
    ``req=GetMediaBuyDeliveryRequest(...)``; every transport sends the same request for
    both. Private names only, the ``MediaBuyListDispatchMixin`` convention: on a composite
    env none of them shadows the primary verb's public ``call_*`` / ``deliver_*`` /
    ``build_rest_body`` by MRO. The env whose primary verb this is binds them to the
    public names.
    """

    #: The route get_media_buy_delivery answers on (``POST /api/v1/media-buys/delivery``).
    DELIVERY_REST_ENDPOINT = "/api/v1/media-buys/delivery"

    @staticmethod
    def is_delivery_request(kwargs: dict[str, Any]) -> bool:
        """Whether this dispatch is get_media_buy_delivery, by the request's type."""
        return isinstance(kwargs.get("req"), GetMediaBuyDeliveryRequest)

    def _call_delivery_impl(self, **kwargs: Any) -> GetMediaBuyDeliveryResponse:
        """Call _get_media_buy_delivery_impl with the env's identity.

        A ``req`` kwarg is the request; flat request fields beside it are two requests for
        one call and raise ``TypeError``.
        """
        self._commit_factory_data()  # type: ignore[attr-defined]

        # Pop identity — it's injected by call_via for transport dispatch
        # but is not a GetMediaBuyDeliveryRequest field.
        # Sentinel distinguishes "not provided" from "explicitly None".
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        raw_identity = kwargs.pop("identity", NO_IDENTITY_OVERRIDE)
        identity = self.identity if raw_identity is NO_IDENTITY_OVERRIDE else raw_identity  # type: ignore[attr-defined]

        req = kwargs.pop("req", None)
        # The four filters a caller leaves as None mean "not sent"; other fields pass as given.
        for key in ("media_buy_ids", "start_date", "end_date", "status_filter"):
            if key in kwargs and kwargs[key] is None:
                del kwargs[key]
        if req is None:
            req = GetMediaBuyDeliveryRequest(**kwargs)
        elif kwargs:
            raise TypeError(f"dispatch carries both req= and request fields {sorted(kwargs)}; send one request")
        return _get_media_buy_delivery_impl(req, identity)

    # FIXME(#2012): parsed with the LOCAL model, not through the client core. The core's
    # UNWRAP parses into the PINNED GetMediaBuyDeliveryResponse, whose by_package items
    # REQUIRE pricing_model, rate and currency; production omitted all three, so every
    # response failed that parse. The env binding these keeps its `_KNOWN_DELIVER_OVERRIDES`
    # entries until #2012 lands.
    def _deliver_delivery_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real FastMCP Client pipeline."""
        return self._run_mcp_client("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)  # type: ignore[attr-defined]

    def _deliver_delivery_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real A2A handler pipeline."""
        return self._run_a2a_handler("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)  # type: ignore[attr-defined]

    def _build_delivery_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """The ``GetMediaBuyDeliveryBody`` for either call shape (``rest_body_from_kwargs``)."""
        return rest_body_from_kwargs(kwargs, _DELIVERY_BODY_FIELDS)

    def _parse_delivery_rest_response(self, data: dict[str, Any]) -> GetMediaBuyDeliveryResponse:
        """Parse a get_media_buy_delivery REST body, through ``revive`` as the base does."""
        return GetMediaBuyDeliveryResponse.revive(data)  # type: ignore[return-value]


class DeliveryPollEnv(GamSellerMixin, DeliveryDispatchMixin, DeliveryPollMixin, IntegrationEnv):
    """Integration test environment for _get_media_buy_delivery_impl.

    Only mocks the adapter (external ad server), or, for a GAM seller, Google's SOAP client
    and report download under the real GoogleAdManager adapter. Everything else is real:
    - Real MediaBuyUoW -> real DB queries, the buy's MediaPackage rows included
    - The principal comes off the identity; nothing looks one up

    Fluent API (from DeliveryPollMixin):
        set_adapter_response(...)  -- configure adapter return for a media_buy_id
        set_adapter_error(exc)     -- make the adapter raise an exception
        call_impl(...)             -- call _get_media_buy_delivery_impl with real DB
    """

    RESPONSE_MODEL = GetMediaBuyDeliveryResponse

    # FIXME(#2012): JUSTIFIED OVERRIDE — deliberately does NOT declare
    # MCP_TOOL/A2A_SKILL, so it does not take the base's client-core delegation.
    # See DeliveryDispatchMixin; delete both overrides and their
    # `_KNOWN_DELIVER_OVERRIDES` entries when #2012 lands.
    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real FastMCP Client pipeline."""
        return self._deliver_delivery_mcp(**kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real A2A handler pipeline."""
        return self._deliver_delivery_a2a(**kwargs)

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_delivery.get_adapter",
        **GAM_CLIENT_PATCH,
    }
    REST_ENDPOINT = DeliveryDispatchMixin.DELIVERY_REST_ENDPOINT

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}

    def _configure_mocks(self) -> None:
        self._configure_adapter_mock()
        self._configure_gam_client()

    def call_impl(self, **kwargs: Any) -> GetMediaBuyDeliveryResponse:
        return self._call_delivery_impl(**kwargs)

    def report_through_gam(
        self,
        *,
        advertiser_id: str,
        impressions: int | None = None,
        spend: float | None = None,
        job_status: str = "COMPLETED",
    ) -> None:
        """Make the tenant a GAM seller whose network reports for the buyer's *advertiser_id*.

        GAM's report job ends in *job_status*. When *impressions* and *spend* are given, the
        finished report holds one row with them (``gam_delivery_row``). Without them the report
        has no rows: GAM has reported nothing yet.
        """
        self.sell_through_gam(advertiser_id=advertiser_id, products=())
        rows = []
        if impressions is not None and spend is not None:
            rows.append(
                gam_delivery_row(
                    advertiser_id=advertiser_id,
                    order_id="5000001",
                    line_item_id="6000001",
                    impressions=impressions,
                    spend=spend,
                )
            )
        self.serve_gam_report_rows(rows, job_status=job_status)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuyDeliveryBody shape for REST POST."""
        return self._build_delivery_rest_body(**kwargs)

    # parse_rest_response: the base's, which revives RESPONSE_MODEL.
