"""A Google Ad Manager seller, composable into any env whose tool reaches its adapter through ``get_adapter``.

``GamSellerMixin.sell_through_gam`` makes the env's tenant a GAM seller: from then on the
tool's adapter is production's ``GoogleAdManager``, built from the seeded rows, and the only
stand-in under it is Google's SOAP client (``stub_gam_client``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.helpers.gam_client import stub_gam_client

if TYPE_CHECKING:
    from unittest.mock import MagicMock

GAM_CLIENT_PATCH: dict[str, str] = {
    # Google's SOAP client on googleads' module, where GAMClientManager constructs it.
    "gam_client": "src.adapters.gam.client.ad_manager.AdManagerClient",
}


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

    if TYPE_CHECKING:
        # Declared, not implemented: this mixin is only ever composed with IntegrationEnv,
        # which owns the patches, the seed and the session (the same statement
        # EgressHatchMixin makes for ``_guard``).
        mock: dict[str, MagicMock]
        _tenant_id: str

        def setup_default_data(self, **tenant_kwargs: Any) -> tuple[Any, Any]: ...

        def _commit_factory_data(self) -> None: ...

        def _guard(self, label: str, cleanup: Any) -> None: ...

    def _configure_gam_client(self) -> None:
        """Every ``AdManagerClient(...)`` built during the scenario is the same stand-in client.

        The adapter's, a health check's, the approval and sync services': one scenario has
        one GAM network.
        """
        self.mock["gam_client"].return_value = stub_gam_client()

    @realize_e2e(
        e2e_unsupported(
            "the live server has no stand-in Google Ad Manager network: a GAM tenant there would "
            "call Google, so its tenants run the Mock adapter"
        )
    )
    def sell_through_gam(
        self, *, trafficker_id: str, advertiser_id: str, ad_unit_id: str, products: Iterable[Any]
    ) -> None:
        """Make this env's tenant a Google Ad Manager seller selling *products* on one ad unit.

        Writes the rows a seller has after GAM onboarding in the Admin UI, and nothing else:
        the onboarding rows (``onboard_gam``), the buyer's principal mapped to its GAM
        advertiser, the synced ad unit (the setup checklist requires one), and each product
        configured for GAM. From then on ``get_adapter`` builds production's ``GoogleAdManager``
        from those rows. Read what the seller sent GAM with ``gam_objects_sent`` and what GAM
        created with ``gam_objects_created``.

        Ceiling: once this runs, a Given that configures the ``MagicMock`` adapter
        (``self.mock["adapter"].return_value``, the adapter error and rejection injections)
        does nothing, because the patch delegates to production. A GAM scenario states GAM's
        answers through the stand-in client, and does not combine with those Givens.
        """
        from src.core.helpers.adapter_helpers import get_adapter
        from tests.factories import GAMInventoryFactory
        from tests.utils.tenant_setup import onboard_gam

        tenant, principal = self.setup_default_data()
        onboard_gam(tenant, trafficker_id=trafficker_id)
        principal.platform_mappings = {
            **(principal.platform_mappings or {}),
            "google_ad_manager": {"advertiser_id": advertiser_id},
        }
        GAMInventoryFactory(tenant=tenant, inventory_id=ad_unit_id)
        for product in products:
            self._sell_product_through_gam(product, ad_unit_id)
        self._commit_factory_data()
        if "adapter" in self.mock:
            self.mock["adapter"].side_effect = get_adapter

    def book_in_gam(
        self,
        *,
        package_id: str,
        line_item_id: str,
        size: tuple[int, int],
        order_id: str,
        product: Any,
        status: str,
        principal: Any = None,
    ) -> Any:
        """A buy the seller already booked in GAM as order *order_id*: the env's buyer's, unless *principal* names another.

        The rows the create path leaves: one package on *product*, whose ``package_config``
        carries the order id and its line item id. The stand-in network holds that line
        item, accepting creatives of *size*.
        """
        from datetime import UTC, datetime

        from tests.helpers.gam_client import gam_line_item

        media_buy = self._seed_buy(
            package_id=package_id, product=product, principal=principal, status=status, confirmed_at=datetime.now(UTC)
        )
        (package,) = media_buy.packages
        package.package_config = {
            **package.package_config,
            "platform_order_id": order_id,
            "platform_line_item_id": line_item_id,
        }
        self._commit_factory_data()
        self.mock["gam_client"].return_value.line_items.append(
            gam_line_item(package_id, item_id=line_item_id, sizes=(size,))
        )
        return media_buy

    def hold_unbooked_buy(self, *, package_id: str, product: Any, status: str) -> Any:
        """A buy of the env's buyer, in *status*, that the seller has not booked: nothing of it is in GAM.

        Its package stores no order id and no line item id, and the stand-in network holds
        no line item for it.
        """
        media_buy = self._seed_buy(package_id=package_id, product=product, status=status)
        self._commit_factory_data()
        return media_buy

    def _seed_buy(self, *, package_id: str, product: Any, principal: Any = None, **buy: Any) -> Any:
        """The rows a create stores for a buy with one package on *product*, the env's buyer's or *principal*'s."""
        from datetime import UTC, datetime

        from tests.factories import MediaBuyFactory
        from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID
        from tests.factories.media_buy import request_package

        tenant, buyer = self.setup_default_data()
        return MediaBuyFactory(
            tenant=tenant,
            principal=principal or buyer,
            # The flight a create resolves and stores, on the factory's own flight dates.
            start_time=datetime(2025, 1, 1, tzinfo=UTC),
            end_time=datetime(2027, 12, 31, 23, 59, 59, tzinfo=UTC),
            raw_request={
                "packages": [request_package(product_id=product.product_id, package_id=package_id)],
                "account": {"account_id": DEFAULT_TEST_ACCOUNT_ID},
            },
            **buy,
        )

    def approve_creative_as_seller(self, creative_id: str) -> None:
        """The seller approves *creative_id* in the Admin UI's creative review queue, in this process."""
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

    def _sell_product_through_gam(self, product: Any, ad_unit_id: str) -> None:
        """Configure *product* for GAM on one ad unit.

        * Its format is ``display_300x250_image`` from the reference catalog. The adapter
          resolves each line item's creative size through ``format_resolver.get_format``,
          and the creative registry serves the checked-in reference catalog only while the
          testing flag's ``reference_formats_only`` holds (#2255).
        * Its ``implementation_config`` is ``GAMProductConfigService.generate_default_config``
          for its delivery type and format, the generator the Admin UI's product form calls,
          plus the ad unit.
        """
        from src.core.format_cache import DEFAULT_AGENT_URL
        from src.services.gam_product_config_service import GAMProductConfigService

        product.format_ids = [{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"}]
        product.implementation_config = {
            **GAMProductConfigService.generate_default_config(product.delivery_type, ["display_300x250_image"]),
            "targeted_ad_unit_ids": [ad_unit_id],
        }

    def gam_objects_sent(self, service: str, method: str) -> list[Any]:
        """Every object the seller sent the stand-in GAM network through ``service.method``, in order.

        As production built them; this concatenates the object arrays of every call. For what
        GAM made of a create, with the ids it assigned, read ``gam_objects_created``.
        """
        calls = getattr(self.mock["gam_client"].return_value.GetService(service), method).call_args_list
        return [obj for call in calls for obj in call.args[0]]

    def gam_objects_created(self, service: str, method: str) -> list[Any]:
        """What the stand-in GAM network created through ``service.method``, as it answered.

        Each sent object with the id the network assigned it, in order
        (``stub_gam_client``'s ``created_objects``, kept for the ``GAM_CREATE_METHODS``).
        """
        return list(self.mock["gam_client"].return_value.created_objects.get((service, method), []))
