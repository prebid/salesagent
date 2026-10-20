"""Harness for the admin pages that decide which publishers a product names (#1845).

Sibling of :mod:`tests.harness.admin_accounts` and :mod:`tests.harness.admin_principal`: the
operator's inventory-profile form, the product add and edit forms, and the products page
that marks a product buyers are not offered. A :class:`tests.harness.product.ProductEnv` rather than a standalone env,
because a profile the operator saves is only half the behavior: the other
half is what ``get_products`` then announces for a product linked to it. One env carries
both, so a scenario saves the profile through the real form and reads it back off the wire
on every buyer transport, and the factories, the database and the e2e realization are
ProductEnv's.

The admin transport follows the env's own, through the shared
:class:`tests.harness.admin_client.AdminClient`: in process a Flask test client against the
same database the factories write, over e2e a ``requests`` session against the live stack
at ``e2e_config.base_url``. The client opens on the first admin request and the env's
cleanup registry closes it, so the env adds no ``__enter__``
(``tests/harness/test_harness_base.py`` keeps that list closed).
"""

from __future__ import annotations

import json
from typing import Any

from src.core.database.models import InventoryProfile, Product
from tests.harness.admin_client import AdminClient, AdminResponse, guarded_admin_client
from tests.harness.product import ProductEnv

#: A format the profile form requires. Any id serves: the form stores it verbatim.
_PROFILE_FORMATS = json.dumps([{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}])


class AdminInventoryProfileEnv(ProductEnv):
    """``ProductEnv`` plus the operator's inventory-profile form and products page."""

    _admin: AdminClient | None = None

    # ── requests ───────────────────────────────────────────────────────────

    def create_inventory_profile(self, profile_id: str, **selection: Any) -> AdminResponse:
        """POST the add form for *profile_id* with a ``tags`` or ``property_ids`` *selection*."""
        return self._admin_request("inventory-profiles/add", self._profile_form(profile_id, **selection))

    def edit_inventory_profile(self, profile_id: str, **selection: Any) -> AdminResponse:
        """POST the edit form of the stored profile *profile_id* with a new *selection*."""
        stored = self.stored_inventory_profile(profile_id)
        assert stored is not None, f"no inventory profile {profile_id!r} to edit"
        return self._admin_request(f"inventory-profiles/{stored.id}/edit", self._profile_form(profile_id, **selection))

    def create_product(self, product_id: str, **selection: Any) -> AdminResponse:
        """POST the product add form for *product_id* with a ``tags`` or ``property_ids`` *selection*."""
        return self._admin_request("products/add", self._product_form(product_id, **selection))

    def edit_product(self, product_id: str, **selection: Any) -> AdminResponse:
        """POST the edit form of the stored product *product_id* with a new *selection*."""
        return self._admin_request(f"products/{product_id}/edit", self._product_form(product_id, **selection))

    def admin_page(self, path: str) -> AdminResponse:
        """GET ``/tenant/<tenant>/<path>``."""
        return self._admin_request(path)

    # ── reads ──────────────────────────────────────────────────────────────

    def stored_inventory_profile(self, profile_id: str) -> InventoryProfile | None:
        """The profile row as the form left it, read fresh from the env's database."""
        return self._stored(InventoryProfile, profile_id=profile_id)

    def stored_product(self, product_id: str) -> Product | None:
        """The product row as the form left it, read fresh from the env's database."""
        return self._stored(Product, product_id=product_id)

    def _stored(self, model: type[Any], **key: str) -> Any:
        self.get_session().expire_all()
        return self.get_one(model, tenant_id=self.tenant_id, **key)

    # ── internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _profile_form(profile_id: str, **selection: Any) -> dict[str, Any]:
        return {
            "name": f"Profile {profile_id}",
            "profile_id": profile_id,
            "targeted_ad_unit_ids": "[]",
            "targeted_placement_ids": "[]",
            "formats": _PROFILE_FORMATS,
            **selection,
        }

    @staticmethod
    def _product_form(product_id: str, **selection: Any) -> dict[str, Any]:
        # One fixed-CPM pricing option, which both forms require, and a measurement
        # provider, without which the edit form clears the NOT NULL delivery_measurement.
        return {
            "name": f"Product {product_id}",
            "product_id": product_id,
            "formats": "[]",
            "delivery_measurement_provider": "publisher",
            "pricing_model_0": "cpm_fixed",
            "currency_0": "USD",
            "rate_0": "10.00",
            **selection,
        }

    def _admin_request(self, path: str, form: dict[str, Any] | None = None) -> AdminResponse:
        """GET *path*, or POST *form* to it and follow the redirect to the page showing its flash."""
        url = f"/tenant/{self.tenant_id}/{path}"
        if form is not None:
            self._commit_factory_data()
        if self._admin is None:
            base_url = self.e2e_config.base_url if self.e2e_config is not None else None
            self._admin = guarded_admin_client(self._guard, base_url, self.tenant_id)
        return self._admin.request("get", url) if form is None else self._admin.submit(url, form)
