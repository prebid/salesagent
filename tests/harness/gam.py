"""A Google Ad Manager seller, composable into any env whose tool reaches its adapter through ``get_adapter``.

``GamSellerMixin.sell_through_gam`` makes the env's tenant a GAM seller: from then on the
tool's adapter is production's ``GoogleAdManager``, built from the seeded rows, and the only
stand-in under it is Google's SOAP client (``stub_gam_client``). ``sell_through_tenant_adapter``
is the step it ends in, on its own: for a scenario that needs the tenant's real adapter
without making the tenant a GAM seller.

On e2e the tenant is a seller on the Google Ad Manager network the environment configures
(``ToolingSettings.gam_suite_network``), and the live server builds production's adapter from
the rows this writes. Every reader of what GAM holds has two realizations: the stand-in's
record in process, and the network read back through the GAM API on e2e
(``tests/harness/gam_network.py``). A Given that shapes a network the configured one cannot
be made into declares that, with its own reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from googleads.ad_manager import AdManagerClient as _AD_MANAGER_CLIENT

from src.core.security.outbound_http import send as _send
from tests.harness._realize import E2EUnsupportedSetup, e2e_unsupported, realize_e2e
from tests.helpers.gam_client import (
    GAM_REPORT_DOWNLOAD_TARGET,
    STAND_IN_NETWORK_TIME_ZONE,
    serve_gam_report,
    stub_gam_client,
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from tests.harness.gam_network import ConfiguredGamNetwork

GAM_CLIENT_PATCH: dict[str, str] = {
    # Google's SOAP client on googleads' module, where GAMClientManager constructs it.
    "gam_client": "src.adapters.gam.client.ad_manager.AdManagerClient",
    # The download of a finished GAM report from Google's report host. Inert unless the
    # tenant is a GAM seller. The egress gateway's own handling of this download is graded
    # in test_vendor_egress.py.
    "gam_report_download": GAM_REPORT_DOWNLOAD_TARGET,
}

#: The trafficker and ad unit a GAM seller has when the scenario does not name them.
DEFAULT_GAM_TRAFFICKER_ID = "2000001"
DEFAULT_GAM_AD_UNIT_ID = "4000001"


def _live_server_builds_its_own_adapter(self: Any) -> None:
    """The live server has no adapter patch: it builds every tenant's adapter with ``get_adapter``."""


def _the_configured_network_answers(self: Any, **network_answers: Any) -> None:
    """On e2e every ``AdManagerClient`` this process builds is Google's, for the configured network.

    The client's name on googleads' module is put back to Google's class for the env's life,
    over the stand-in patch: a delegating ``side_effect`` is not enough, because
    ``AdManagerClient.__init__`` calls ``super()`` with that very module name, which would
    then be the mock. A seller step this process runs itself (the seller's approval of a buy,
    of a creative) reaches the network the live server reaches. The report download
    delegates to the egress seam. A stand-in answer cannot be stated to a real network, so
    *network_answers* are refused.
    """
    from unittest.mock import patch

    from googleads import ad_manager

    if network_answers:
        raise E2EUnsupportedSetup(f"a real network gives its own answers, not these: {sorted(network_answers)}")
    google_client = patch.object(ad_manager, "AdManagerClient", _AD_MANAGER_CLIENT)
    google_client.start()
    self._guard("gam client: Google's, on e2e", google_client.stop)
    self.mock["gam_report_download"].side_effect = _send


def _sell_through_the_configured_network(
    self: Any,
    *,
    advertiser_id: str,
    products: Iterable[Any],
    trafficker_id: str = DEFAULT_GAM_TRAFFICKER_ID,
    ad_unit_id: str = DEFAULT_GAM_AD_UNIT_ID,
    network_time_zone: str | None = None,
    network_fault: str | None = None,
    implementation_config: Mapping[str, Any] | None = None,
    product_formats: Mapping[str, Any] | None = None,
) -> None:
    """``sell_through_gam`` on e2e: the tenant sells on the configured network, as the advertiser it configures.

    The scenario's trafficker, ad unit and advertiser name the configured network's: the
    service account's own user (the trafficker the adapter resolves when none is stored),
    the first configured ad unit, the configured advertiser. Read back, each is reported
    under the scenario's id. The key is written encrypted through the factory; a server
    holding another ``ENCRYPTION_KEY`` refuses the row with the typed decrypt error
    (``AdapterConfig.gam_service_account_json``), never with a GAM refusal.
    """
    if network_time_zone is not None:
        raise E2EUnsupportedSetup(
            "Network.timeZone is read-only: the configured network's time zone is its own, so a "
            "scenario stating another cannot be realized on it"
        )
    if network_fault is not None:
        raise E2EUnsupportedSetup("a real network answers getCurrentNetwork as it does: a refusal cannot be injected")
    from src.core.config import ToolingSettings
    from tests.harness.gam_network import NO_GAM_NETWORK, ConfiguredGamNetwork

    network = ToolingSettings().gam_suite_network()
    if network is None:
        raise E2EUnsupportedSetup(NO_GAM_NETWORK)
    gam = ConfiguredGamNetwork(network)
    self._gam_network = gam
    self._guard("gam network: archive the scenario's orders", gam.archive)
    gam.name(network.advertiser_id, advertiser_id)
    gam.name(gam.trafficker_id, trafficker_id)
    gam.name(network.ad_unit_ids[0], ad_unit_id)
    self._become_gam_seller(
        advertiser_id=network.advertiser_id,
        ad_unit_id=network.ad_unit_ids[0],
        products=products,
        implementation_config=implementation_config,
        product_formats=product_formats,
        onboarding={"trafficker_id": None, "network": network, "name_prefix": gam.prefix},
    )


def _book_on_the_configured_network(
    self: Any,
    *,
    line_items: Sequence[tuple[str, str, tuple[int, int]]],
    order_id: str,
    product: Any,
    status: str,
    principal: Any = None,
) -> Any:
    """``book_in_gam`` on e2e: the order and its line items are created on the configured network first.

    Named under the scenario's ids, and archived with the scenario's orders.
    """
    if principal is not None:
        raise E2EUnsupportedSetup(
            "the configured network names one advertiser (GAM_SUITE_ADVERTISER_ID), so another buyer's "
            "GAM advertiser has no order on it"
        )
    network_order_id, booked = self._configured_network().book(
        order_id=order_id, line_items=[(line_item_id, size) for _, line_item_id, size in line_items]
    )
    return self.book_at_ad_server(
        line_items={package_id: booked[line_item_id] for package_id, line_item_id, _ in line_items},
        order_id=network_order_id,
        product=product,
        status=status,
    )


def _hold_on_the_configured_network(self: Any, gam_id: str, image_url: str) -> str:
    """``hold_gam_creative`` on e2e: the creative is created on the configured network, named under *gam_id*."""
    return self._configured_network().hold_creative(creative_id=gam_id, image_url=image_url)


#: The buyer's bundle host on e2e: a tls-proxy alias ``publisher-origin`` serves (see
#: ``tests/harness/publisher_authorization.py``). Bundles live under ``/bundles/<key>/``, a
#: path no adagents.json shares.
_BUNDLE_HOST = "pub-psatest.com"


def _serve_from_the_publisher_origin(self: Any, bundle: bytes | None) -> str:
    """``serve_html5_bundle`` on e2e: the bundle is a file ``publisher-origin`` serves, at a per-scenario path.

    ``publisher-origin`` serves a dialer only the files written under that dialer's address,
    and two processes fetch the bundle: the live server, on create, and this process, on a
    seller's approval it runs itself. So the file is written under both addresses. With no
    *bundle* nothing is written, and the path answers 404 (``publisher_authorization.py``): the
    seller fetches no bundle there either.
    """
    import shutil
    import socket
    import uuid

    from tests.harness.publisher_authorization import PUBLISHER_FILES_DIR

    self._reach_the_bundle_host()
    key = uuid.uuid4().hex
    runner_files = PUBLISHER_FILES_DIR / socket.gethostbyname(socket.gethostname())
    if bundle is not None:
        for files in (self._registered_server_files(), runner_files):
            bundle_file = files / _BUNDLE_HOST / "bundles" / key / "bundle.zip"
            bundle_file.parent.mkdir(parents=True, exist_ok=True)
            bundle_file.write_bytes(bundle)
    self._guard("html5 bundle at this runner's address", lambda: shutil.rmtree(runner_files, ignore_errors=True))
    return f"https://{_BUNDLE_HOST}/bundles/{key}/bundle.zip"


def _a_network_asset_is_held(self: Any, asset: Mapping[str, Any], bundle: bytes) -> bool:
    """``gam_asset_is_the_bundle`` on e2e: GAM holds the asset, by its reference. It does not read the bytes back."""
    return asset.get("assetId") is not None


def _read_a_line_item_on_the_configured_network(self: Any, line_item_id: str) -> Mapping[str, Any]:
    """``gam_line_item`` on e2e: the line item the configured network holds under the scenario's *line_item_id*."""
    return self._configured_network().line_item(line_item_id)


def _read_back_from_the_configured_network(self: Any, service: str, method: str) -> list[Any]:
    """``gam_objects_created`` on e2e: what the configured network holds of the seller's work for this scenario."""
    return self._configured_network().created(service, method, recorded_creative_ids=self._recorded_creative_ids())


def _read_back_what_the_network_holds(self: Any, service: str, method: str) -> list[Any]:
    """``gam_objects_sent`` on e2e: a create is read back as what the network holds; nothing else was recorded."""
    if not method.startswith("create"):
        raise E2EUnsupportedSetup(
            f"a real network keeps no record of what the seller sent it through {service}.{method}, "
            "only of what it holds"
        )
    return _read_back_from_the_configured_network(self, service, method)


class GamSellerMixin:
    """Mixes a Google Ad Manager seller into an env: the setup, and readers of what GAM received.

    A host env composes it in three places:

    * its bases: ``class SomeEnv(GamSellerMixin, ..., IntegrationEnv)``;
    * its ``EXTERNAL_PATCHES``: ``{..., **GAM_CLIENT_PATCH}``, next to an ``"adapter"`` patch on
      the ``get_adapter`` its tool calls, when it patches one;
    * its ``_configure_mocks``: ``self._configure_gam_client()``.

    The client patch is inert for a tenant on the Mock adapter, which never constructs an
    ``AdManagerClient``.
    """

    #: The ad unit :meth:`sell_through_gam` sold the scenario's products on, once it has.
    _gam_ad_unit_id: str | None = None
    #: On e2e, the configured network :meth:`sell_through_gam` made the tenant a seller on.
    _gam_network: ConfiguredGamNetwork | None = None

    if TYPE_CHECKING:
        # Declared, not implemented: this mixin is only ever composed with IntegrationEnv,
        # which owns the patches, the seed and the session (the same statement
        # EgressHatchMixin makes for ``_guard``).
        mock: dict[str, MagicMock]
        _tenant_id: str
        _session: Any
        is_e2e: bool

        def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]: ...

        def _commit_factory_data(self) -> None: ...

        def _guard(self, label: str, cleanup: Any) -> None: ...

        def _registered_server_files(self) -> Any: ...

    @realize_e2e(_the_configured_network_answers)
    def _configure_gam_client(self, **network_answers: Any) -> None:
        """Every ``AdManagerClient(...)`` built during the scenario is the same stand-in client.

        The adapter's, a health check's, the approval and sync services': one scenario has
        one GAM network. A seller whose scenario states no report reads a finished, empty one.
        *network_answers* are ``stub_gam_client``'s keywords.
        """
        self.mock["gam_client"].return_value = stub_gam_client(**network_answers)
        self.serve_gam_report_rows(())

    @realize_e2e(
        e2e_unsupported(
            "a real network's report holds what it delivered: a scenario cannot state the rows of a "
            "report the configured network runs"
        )
    )
    def serve_gam_report_rows(self, rows: Iterable[Mapping[str, object]], *, job_status: str = "COMPLETED") -> None:
        """GAM's report job ends in *job_status*, and the finished report holds *rows*.

        Rows are keyed by GAM's own column names (``Dimension.*``, ``Column.*``), as
        ``serve_gam_report`` writes them.
        """
        self.mock["gam_report_download"].return_value = serve_gam_report(
            self.mock["gam_client"].return_value, rows=list(rows), job_status=job_status
        )

    @realize_e2e(_live_server_builds_its_own_adapter)
    def sell_through_tenant_adapter(self) -> None:
        """From here on the tool's adapter is production's, built by ``get_adapter`` from the tenant's rows.

        For the harness tenant that is the real Mock adapter (``MockAdServer``), which is
        what the live server runs for it, so the in-process transports run the adapter code
        e2e does. ``sell_through_gam`` ends here after making the tenant a GAM seller.

        Ceiling: once this runs, a Given that configures the ``MagicMock`` adapter
        (``self.mock["adapter"].return_value``, the adapter error and rejection injections)
        does nothing, because the patch delegates to production. A host with no ``"adapter"``
        patch already runs production's ``get_adapter``, so there is nothing to redirect.
        """
        self._run_the_tenant_adapter_here()

    def _run_the_tenant_adapter_here(self) -> None:
        """The ``"adapter"`` patch, when the host has one, delegates to production's ``get_adapter``."""
        from src.core.helpers.adapter_helpers import get_adapter

        if "adapter" in self.mock:
            self.mock["adapter"].side_effect = get_adapter

    @realize_e2e(_sell_through_the_configured_network)
    def sell_through_gam(
        self,
        *,
        advertiser_id: str,
        products: Iterable[Any],
        trafficker_id: str = DEFAULT_GAM_TRAFFICKER_ID,
        ad_unit_id: str = DEFAULT_GAM_AD_UNIT_ID,
        network_time_zone: str | None = None,
        network_fault: str | None = None,
        implementation_config: Mapping[str, Any] | None = None,
        product_formats: Mapping[str, Any] | None = None,
    ) -> None:
        """Make this env's tenant a Google Ad Manager seller selling *products* on one ad unit.

        Writes the rows a seller has after GAM onboarding in the Admin UI, and nothing else:
        the onboarding rows (``onboard_gam``), the buyer's principal mapped to its GAM
        advertiser, the synced ad unit (the setup checklist requires one), and each product
        configured for GAM. From then on ``get_adapter`` builds production's ``GoogleAdManager``
        from those rows. Read what the seller sent GAM with ``gam_objects_sent`` and what GAM
        created with ``gam_objects_created``.

        Ceiling: the one ``sell_through_tenant_adapter`` states. A GAM scenario states GAM's
        answers through the stand-in client, and does not combine with the Givens that
        configure the ``MagicMock`` adapter.

        ``network_time_zone`` is the GAM network's own zone (``Network.timeZone``), and
        ``network_fault`` the ``ApiError`` reason with which the network refuses to name it
        (``stub_gam_client``). Given either, they replace the default stand-in network that
        ``_configure_gam_client`` built, through ``_configure_gam_client``, so the replacement
        also serves the finished, empty report; a report the scenario states comes after this
        method. Omitted, the default network (``STAND_IN_NETWORK_TIME_ZONE``) stays. Either way
        the network is in place before any client is constructed, because nothing builds a GAM
        client until this method makes the tenant a GAM seller. A later network answer a
        scenario needs is another keyword of this method passed to the same
        ``_configure_gam_client(...)`` call, so one call carries every answer a scenario states.

        ``implementation_config`` is what the seller wrote over each product's generated GAM
        configuration (``_sell_product_through_gam``): its keys replace the generated ones.

        ``product_formats`` is the format arms each product sells, ``format_ids`` and
        ``format_options`` as ``core/product.json`` names them; an arm it leaves out is none.
        Omitted, every product sells ``display_300x250_image``.
        """
        if network_time_zone is not None or network_fault is not None:
            self._configure_gam_client(
                network_time_zone=network_time_zone or STAND_IN_NETWORK_TIME_ZONE, network_fault=network_fault
            )
        self._become_gam_seller(
            advertiser_id=advertiser_id,
            ad_unit_id=ad_unit_id,
            products=products,
            implementation_config=implementation_config,
            product_formats=product_formats,
            onboarding={"trafficker_id": trafficker_id},
        )

    def _become_gam_seller(
        self,
        *,
        advertiser_id: str,
        ad_unit_id: str,
        products: Iterable[Any],
        implementation_config: Mapping[str, Any] | None,
        product_formats: Mapping[str, Any] | None,
        onboarding: Mapping[str, Any],
    ) -> None:
        """The rows of :meth:`sell_through_gam`, on the network *onboarding* names (``onboard_gam``'s keywords).

        From here on the tool's adapter, and the adapter of every seller step this process
        runs, is production's.
        """
        from tests.factories import GAMInventoryFactory
        from tests.utils.tenant_setup import onboard_gam

        tenant, principal = self.setup_default_data()
        onboard_gam(tenant, **onboarding)
        principal.platform_mappings = {
            **(principal.platform_mappings or {}),
            "google_ad_manager": {"advertiser_id": advertiser_id},
        }
        GAMInventoryFactory(tenant=tenant, inventory_id=ad_unit_id)
        self._gam_ad_unit_id = ad_unit_id
        for product in products:
            self._sell_product_through_gam(product, ad_unit_id, implementation_config or {}, product_formats)
        self._commit_factory_data()
        self._run_the_tenant_adapter_here()

    def sell_product_through_gam(
        self, product: Any, *, product_formats: Mapping[str, Any], implementation_config: Mapping[str, Any]
    ) -> None:
        """Re-sell *product*, already sold through GAM by :meth:`sell_through_gam`, with its own formats and settings.

        For a scenario whose products differ in what they sell: the same configuration
        ``sell_through_gam`` writes for every product, on the ad unit it sold them on, with
        this product's *product_formats* and the keys the seller wrote over its generated
        configuration (*implementation_config*).
        """
        assert self._gam_ad_unit_id is not None, "sell_through_gam() must make the tenant a GAM seller first"
        self._sell_product_through_gam(product, self._gam_ad_unit_id, implementation_config, product_formats)
        self._commit_factory_data()

    @realize_e2e(_book_on_the_configured_network)
    def book_in_gam(
        self,
        *,
        line_items: Sequence[tuple[str, str, tuple[int, int]]],
        order_id: str,
        product: Any,
        status: str,
        principal: Any = None,
    ) -> Any:
        """A buy the seller already booked in GAM as order *order_id*: the env's buyer's, unless *principal* names another.

        The rows :meth:`book_at_ad_server` leaves, one package for each (package id, line
        item id, creative size) of *line_items*; the stand-in network holds each line item,
        accepting creatives of its size.
        """
        from tests.helpers.gam_client import gam_line_item

        media_buy = self.book_at_ad_server(
            line_items={package_id: line_item_id for package_id, line_item_id, _ in line_items},
            order_id=order_id,
            product=product,
            status=status,
            principal=principal,
        )
        for package_id, line_item_id, size in line_items:
            self.mock["gam_client"].return_value.line_items.append(
                gam_line_item(package_id, item_id=line_item_id, sizes=(size,))
            )
        return media_buy

    def book_at_ad_server(
        self,
        *,
        line_items: Mapping[str, str],
        order_id: str,
        product: Any,
        status: str,
        principal: Any = None,
        package_products: Mapping[str, str] | None = None,
    ) -> Any:
        """A buy the seller already booked at its ad server as order *order_id*, whatever the ad server.

        The rows the create path leaves: one package per package id of *line_items*, whose
        ``package_config`` carries the order id and that package's line item id. Each package
        is on *product*, unless *package_products* names another product id for it (one the
        seller may since have removed). The env's buyer's buy, unless *principal* names another.
        """
        from datetime import UTC, datetime

        media_buy = self._seed_buy(
            package_ids=list(line_items),
            product=product,
            principal=principal,
            status=status,
            confirmed_at=datetime.now(UTC),
            package_products=package_products,
        )
        for package in media_buy.packages:
            package.package_config = {
                **package.package_config,
                "platform_order_id": order_id,
                "platform_line_item_id": line_items[package.package_id],
            }
        self._commit_factory_data()
        return media_buy

    def pause_booked_package(self, package_id: str) -> None:
        """Pause *package_id* of the env's buyer's booked buy, in the column ``update_media_buy`` writes."""
        from src.core.database.models import MediaPackage

        package = self.get_one(MediaPackage, package_id=package_id)
        package.package_config = {**package.package_config, "paused": True}
        self._commit_factory_data()

    def hold_unbooked_buy(self, *, package_id: str, product: Any, status: str) -> Any:
        """A buy of the env's buyer, in *status*, that the seller has not booked: nothing of it is in GAM.

        Its package stores no order id and no line item id, and the stand-in network holds
        no line item for it.
        """
        media_buy = self._seed_buy(package_ids=[package_id], product=product, status=status)
        self._commit_factory_data()
        return media_buy

    def _seed_buy(
        self,
        *,
        package_ids: Sequence[str],
        product: Any,
        principal: Any = None,
        package_products: Mapping[str, str] | None = None,
        **buy: Any,
    ) -> Any:
        """The rows a create stores for a buy with those packages, the env's buyer's or *principal*'s.

        Each package is on *product*, or on the product id *package_products* names for it.
        """
        from datetime import UTC, datetime, timedelta

        from tests.factories import MediaBuyFactory
        from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID
        from tests.factories.media_buy import request_package

        tenant, buyer = self.setup_default_data()
        # A flight that has started and ends 30 days from this run: a buy in a serving status is
        # delivering. Relative, not a far-future literal: GAM refuses a line item ending too far out
        # (LineItemFlightDateError.END_DATE_TIME_TOO_LATE), and the seller books this flight there.
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        ends = today + timedelta(days=30, hours=23, minutes=59, seconds=59)
        # A pending_start buy is one whose flight has not started, so its flight starts tomorrow.
        # A started one is not stable: the live server's status scheduler promotes a pending_start
        # buy whose start has passed and whose creatives are approved to active
        # (src/services/media_buy_status_scheduler.py), and whether a sweep lands mid-scenario is
        # timing.
        starts = today + timedelta(days=1) if buy.get("status") == "pending_start" else datetime(2025, 1, 1, tzinfo=UTC)
        return MediaBuyFactory(
            tenant=tenant,
            principal=principal or buyer,
            start_date=starts.date(),
            end_date=ends.date(),
            start_time=starts,
            end_time=ends,
            raw_request={
                "packages": [
                    request_package(
                        product_id=(package_products or {}).get(package_id, product.product_id), package_id=package_id
                    )
                    for package_id in package_ids
                ],
                "account": {"account_id": DEFAULT_TEST_ACCOUNT_ID},
            },
            **buy,
        )

    def approve_creative_as_seller(self, creative_id: str) -> dict[str, Any]:
        """The seller approves *creative_id* in the Admin UI's creative review queue, in this process.

        Returns the route's JSON answer, which is what the seller is told about the approval.
        """
        from tests.harness.admin_client import AdminClient

        self._commit_factory_data()
        admin = AdminClient()
        self._guard("admin_client", admin.close)
        admin.authenticate(self._tenant_id)
        response = admin.request(
            "POST",
            f"/tenant/{self._tenant_id}/creatives/review/{creative_id}/approve",
            json={"approved_by": "seller@example.com"},
        )
        assert response.status_code == 200, f"approving creative {creative_id} answered {response.status_code}"
        answer: dict[str, Any] = response.get_json()
        return answer

    def _sell_product_through_gam(
        self,
        product: Any,
        ad_unit_id: str,
        implementation_config: Mapping[str, Any],
        product_formats: Mapping[str, Any] | None = None,
    ) -> None:
        """Configure *product* for GAM on one ad unit.

        * Its formats are *product_formats*, else ``display_300x250_image`` from the
          reference catalog. The adapter resolves each line item's creative size through
          ``format_resolver.get_format``, and the creative registry serves the checked-in
          reference catalog only while the testing flag's ``reference_formats_only`` holds
          (#2255).
        * Its ``implementation_config`` is ``GAMProductConfigService.generate_default_config``
          for its delivery type and formats, the generator the Admin UI's product form calls,
          plus the ad unit, with *implementation_config* (the keys the seller wrote by hand)
          over both.
        """
        from src.core.format_cache import DEFAULT_AGENT_URL
        from src.services.gam_product_config_service import GAMProductConfigService

        if product_formats is None:
            product.format_ids = [{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"}]
            generated = GAMProductConfigService.generate_default_config(
                product.delivery_type, ["display_300x250_image"]
            )
        else:
            product.format_ids = product_formats.get("format_ids")
            product.format_options = product_formats.get("format_options")
            generated = GAMProductConfigService.generate_default_config(
                product.delivery_type, product.format_ids, format_options=product.format_options
            )
        product.implementation_config = {**generated, "targeted_ad_unit_ids": [ad_unit_id], **implementation_config}

    @realize_e2e(
        e2e_unsupported(
            "a real network refuses only what it refuses: a fault on one line item's associations cannot be injected"
        )
    )
    def refuse_gam_association(self, line_item_id: str, reason: str) -> None:
        """The stand-in GAM network refuses creative associations on *line_item_id*, with this ``ApiError`` reason."""
        self.mock["gam_client"].return_value.refused_line_items[line_item_id] = reason

    @realize_e2e(
        e2e_unsupported("a real network refuses only what it refuses: a fault on creative updates cannot be injected")
    )
    def refuse_gam_creative_updates(self, reason: str | None) -> None:
        """The stand-in GAM network refuses creative updates with this ``ApiError`` reason; None takes them again."""
        self.mock["gam_client"].return_value.creative_update_fault = reason

    @realize_e2e(
        e2e_unsupported("a real network refuses only what it refuses: a fault on reading line items cannot be injected")
    )
    def refuse_gam_line_item_read(self, reason: str) -> None:
        """The stand-in GAM network refuses to read line items, with this ``ApiError`` reason."""
        from tests.helpers.gam_client import gam_fault

        line_items = self.mock["gam_client"].return_value.GetService("LineItemService")
        line_items.getLineItemsByStatement.side_effect = gam_fault(reason)

    def _reach_the_bundle_host(self) -> None:
        """This process may fetch the buyer's bundle host: it trusts the shared test CA and opens the private-range hatch.

        The seller fetches a bundle through the egress seam, and the bundle host is a private
        address on a test certificate. Both released with the env.
        """
        from unittest.mock import patch

        from src.core.config import get_settings
        from tests.helpers.tls_material import trust_test_ca

        trust_test_ca(self._guard)
        hatch = patch.object(get_settings().limits, "adcp_outbound_allow_private", True)
        hatch.start()
        self._guard("html5 bundle egress hatch", hatch.stop)

    @realize_e2e(_serve_from_the_publisher_origin)
    def serve_html5_bundle(self, bundle: bytes | None) -> str:
        """Serve *bundle* as a zip from a loopback origin over TLS; return its URL.

        The buyer's bundle host, which the seller fetches the bundle from (``_reach_the_bundle_host``).
        With no *bundle* the host does not answer: it closes every connection before a status line.
        """
        from tests.helpers.tls_material import start_tls_origin

        self._reach_the_bundle_host()
        origin = start_tls_origin(self._guard, "html5 bundle origin")
        if bundle is None:
            origin.close_without_responding()
        else:
            origin.respond_with(200, body=bundle, content_type="application/zip")
        return f"{origin.base_url}/bundle.zip"

    @realize_e2e(_a_network_asset_is_held)
    def gam_asset_is_the_bundle(self, asset: Mapping[str, Any], bundle: bytes) -> bool:
        """Whether GAM's *asset* of a creative is *bundle*: the stand-in holds the bytes the seller sent."""
        return asset.get("assetByteArray") == bundle

    @realize_e2e(_read_back_what_the_network_holds)
    def gam_objects_sent(self, service: str, method: str) -> list[Any]:
        """Every object the seller sent the stand-in GAM network through ``service.method``, in order.

        As production built them; this concatenates the object arrays of every call. For what
        GAM made of a create, with the ids it assigned, read ``gam_objects_created``.
        """
        calls = getattr(self.mock["gam_client"].return_value.GetService(service), method).call_args_list
        return [obj for call in calls for obj in call.args[0]]

    @realize_e2e(_read_back_from_the_configured_network)
    def gam_objects_created(self, service: str, method: str) -> list[Any]:
        """What the stand-in GAM network created through ``service.method``, as it answered.

        Each sent object with the id the network assigned it, in order
        (``stub_gam_client``'s ``created_objects``, kept for the ``GAM_CREATE_METHODS``).
        """
        return list(self.mock["gam_client"].return_value.created_objects.get((service, method), []))

    @realize_e2e(
        e2e_unsupported(
            "a real network keeps no record of the actions it was asked to perform, only of the state they left"
        )
    )
    def gam_actions_performed(self, service: str, method: str) -> list[Any]:
        """Every call the seller made to the stand-in GAM network's ``service.method``, in order, as ``call.args``."""
        return [
            call.args
            for call in getattr(self.mock["gam_client"].return_value.GetService(service), method).call_args_list
        ]

    @realize_e2e(
        e2e_unsupported(
            "a real network reads an immediate start back as the instant it resolved, and gives an order the "
            "flight of its line items: what the seller sent for a flight is not readable back"
        )
    )
    def gam_flights(self, service: str, method: str, keys: Sequence[str]) -> list[dict[str, Any]]:
        """For each object the stand-in GAM network created through ``service.method``, the flight *keys* the seller sent."""
        return [
            {key: created[key] for key in keys if key in created}
            for created in self.gam_objects_created(service, method)
        ]

    @realize_e2e(_read_a_line_item_on_the_configured_network)
    def gam_line_item(self, line_item_id: str) -> Mapping[str, Any]:
        """The line item the stand-in GAM network holds as *line_item_id*, as it holds it now."""
        (held,) = [
            item
            for item in self.mock["gam_client"]
            .return_value.GetService("LineItemService")
            .getLineItemsByStatement(None)["results"]
            if str(item.get("id")) == line_item_id
        ]
        return held

    @realize_e2e(_hold_on_the_configured_network)
    def hold_gam_creative(self, gam_id: str, image_url: str) -> str:
        """A creative GAM already holds as *gam_id*, showing *image_url*: the id the seller records for it.

        In process the stand-in network holds any id; on e2e the creative is made on the configured network.
        """
        return gam_id

    def _configured_network(self) -> ConfiguredGamNetwork:
        """The configured network :meth:`sell_through_gam` made the tenant a seller on (e2e only)."""
        assert self._gam_network is not None, (
            "sell_through_gam() must make the tenant a seller on the configured network first"
        )
        return self._gam_network

    def _recorded_creative_ids(self) -> list[str]:
        """The GAM ids the seller recorded on the tenant's creatives (``ad_server_creative_id``), read off the server's database."""
        from sqlalchemy import select

        from src.core.database.models import Creative

        self._session.expire_all()
        creatives = self._session.scalars(select(Creative).filter_by(tenant_id=self._tenant_id)).all()
        return [str(c.data["ad_server_creative_id"]) for c in creatives if c.data.get("ad_server_creative_id")]
