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

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from src.core.database.models import AuthorizedProperty, InventoryProfile, Product, Tenant
from tests.harness.admin_client import AdminResponse
from tests.harness.product import ProductEnv

#: The canonical declaration both create forms require (core/product.json: "New 3.2 products
#: MUST carry format_options"); the legacy ``formats`` field is no longer authored.
_FORMAT_OPTIONS = json.dumps([{"format_kind": "image", "params": {"width": 300, "height": 250}}])


#: Input types a form submits only as its submitter, or not as text at all.
_UNSUBMITTED_INPUT_TYPES = frozenset({"submit", "button", "reset", "image", "file"})


def rendered_form(page: AdminResponse) -> dict[str, list[str]]:
    """Every field *page*'s POST form submits when saved as it rendered, as a browser builds it.

    The HTML form data set: each enabled, named ``input``, ``select`` and ``textarea``; a
    checkbox or radio only when checked; a select's selected options, or its first option when
    a single select marks none. Fields hidden by CSS are submitted, disabled ones are not. The
    fields the page's own script lays out from data it embeds are added by
    :func:`_script_built_fields`, since neither admin transport runs JavaScript.
    """
    assert page.status_code == 200, f"the form answered {page.status_code}"
    soup = BeautifulSoup(page.data, "html.parser")
    forms = soup.find_all("form", method=re.compile(r"^post$", re.IGNORECASE))
    assert len(forms) == 1, f"the page has {len(forms)} POST forms"
    fields: dict[str, list[str]] = {}
    for element in forms[0].find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name or element.has_attr("disabled"):
            continue
        for value in _submitted_values(element):
            fields.setdefault(name, []).append(value)
    for name, values in _script_built_fields(soup).items():
        fields.setdefault(name, []).extend(values)
    return fields


def _submitted_values(element: Any) -> list[str]:
    if element.name == "textarea":
        # A browser drops the one newline that may follow the opening tag.
        return [element.get_text().removeprefix("\n")]
    if element.name == "select":
        options = element.find_all("option")
        chosen = [o for o in options if o.has_attr("selected")]
        if not chosen and not element.has_attr("multiple") and options:
            chosen = options[:1]
        return [o.get("value", o.get_text().strip()) for o in chosen]
    kind = (element.get("type") or "text").lower()
    if kind in _UNSUBMITTED_INPUT_TYPES:
        return []
    if kind in {"checkbox", "radio"}:
        return [element.get("value", "on")] if element.has_attr("checked") else []
    return [element.get("value", "")]


#: The literal the product edit page's script reads its pricing options from.
_EMBEDDED_PRICING = re.compile(r"const existingOptions = (\[.*?\]);", re.DOTALL)


def _script_built_fields(soup: BeautifulSoup) -> dict[str, list[str]]:
    """The pricing inputs the product edit page's ``addPricingOption`` builds from ``existingOptions``.

    ``templates/edit_product.html`` renders no pricing input itself: its script lays one
    option out per embedded entry. This posts the values that script puts in those inputs,
    so a save posts the product's own pricing rather than a default.
    """
    script = next((s.get_text() for s in soup.find_all("script") if "existingOptions" in s.get_text()), None)
    match = _EMBEDDED_PRICING.search(script or "")
    if match is None:
        return {}
    fields: dict[str, list[str]] = {}
    for index, option in enumerate(json.loads(match.group(1))):
        model = option["pricing_model"]
        if model == "cpm":
            model = "cpm_fixed" if option["is_fixed"] else "cpm_auction"
        guidance = option.get("price_guidance") or {}
        parameters = option.get("parameters") or {}
        values = {
            "pricing_model": model,
            "currency": option["currency"],
            "rate": option.get("rate"),
            "floor": guidance.get("floor"),
            "min_spend": option.get("min_spend_per_package"),
            **{p: guidance.get(p) for p in ("p25", "p50", "p75", "p90")},
            **(
                {"demographic": parameters.get("demographic"), "min_points": parameters.get("min_points")}
                if model == "cpp"
                else {}
            ),
            **({"view_threshold": parameters.get("view_threshold")} if model == "cpv" else {}),
        }
        for field, value in values.items():
            fields[f"{field}_{index}"] = ["" if value is None else str(value)]
    return fields


def _legacy_selection_migration() -> Any:
    """The alembic revision that converts legacy publisher selections, loaded as a module."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / _LEGACY_SELECTION_REVISION
    spec = importlib.util.spec_from_file_location("legacy_selection_migration", path)
    assert spec is not None and spec.loader is not None, f"no migration at {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The revision file whose ``legacy_selectors`` a migrated product is built with.
_LEGACY_SELECTION_REVISION = "3c38b1ee241f_legacy_publisher_selections_to_selectors.py"


class AdminInventoryProfileEnv(ProductEnv):
    """``ProductEnv`` plus the operator's inventory-profile form and products page."""

    _open_form: tuple[str, AdminResponse] | None = None

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

    def open_product_edit_form(self, product_id: str) -> AdminResponse:
        """GET the edit page of product *product_id*, held for :meth:`save_open_form`."""
        return self._open(f"products/{product_id}/edit")

    def open_profile_edit_form(self, profile_id: str) -> AdminResponse:
        """GET the edit page of inventory profile *profile_id*, held for :meth:`save_open_form`."""
        stored = self.stored_inventory_profile(profile_id)
        assert stored is not None, f"no inventory profile {profile_id!r} to edit"
        return self._open(f"inventory-profiles/{stored.id}/edit")

    def save_open_form(self, name: str, **checked: list[str]) -> AdminResponse:
        """POST the page last opened as it rendered, with its ``name`` set to *name*, as a browser does.

        *checked* adds boxes the operator checks before saving, by field name.
        """
        return self._save_open_form({"name": [name]}, checked)

    def save_open_form_with(self, **fields: str) -> AdminResponse:
        """POST the page last opened as it rendered, with each of *fields* set to its value."""
        return self._save_open_form({field: [value] for field, value in fields.items()}, {})

    def _save_open_form(self, replaced: dict[str, list[str]], checked: dict[str, list[str]]) -> AdminResponse:
        assert self._open_form is not None, "no edit page is open"
        path, page = self._open_form
        form = {**rendered_form(page), **replaced}
        for field_name, values in checked.items():
            form[field_name] = [*form.get(field_name, []), *values]
        return self._admin_request(path, form)

    def rename_product_through_its_form(self, product_id: str) -> AdminResponse:
        """Open the product's edit page and save it with only the name changed."""
        self.open_product_edit_form(product_id)
        return self.save_open_form(f"Renamed {product_id}")

    def rename_profile_through_its_form(self, profile_id: str) -> AdminResponse:
        """Open the profile's edit page and save it with only the name changed."""
        self.open_profile_edit_form(profile_id)
        return self.save_open_form(f"Renamed {profile_id}")

    def verify_property(self, property_id: str) -> None:
        """Mark the seller's property *property_id* verified, as the verification service does."""
        row = self._stored(AuthorizedProperty, property_id=property_id)
        assert row is not None, f"no authorized property {property_id!r}"
        row.verification_status = "verified"
        self.get_session().commit()

    def migrated_selection(
        self, *, property_tags: list[str] | None = None, property_ids: list[str] | None = None
    ) -> list[dict]:
        """The selectors revision 3c38b1ee241f converts a legacy selection to, over this tenant's properties.

        The migration's own conversion, run on the authorized properties the scenario stored,
        so a scenario grades the product the upgrade leaves behind. A selection the migration
        would refuse fails here, as the upgrade would.
        """
        rows = [
            (row.property_id, row.publisher_domain, row.tags or [])
            for row in self.query(AuthorizedProperty, tenant_id=self.tenant_id)
        ]
        selectors = _legacy_selection_migration().legacy_selectors(property_tags, property_ids, rows)
        assert selectors, f"the migration refuses this selection: it matches no authorized property ({rows})"
        return selectors

    def _open(self, path: str) -> AdminResponse:
        page = self.admin_page(path)
        self._open_form = (path, page)
        return page

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

    def product_is_stored(self, product_id: str) -> bool:
        """Whether this tenant holds a product *product_id* (the step AdminAccountEnv also answers)."""
        return self.stored_product(product_id) is not None

    def agent_host(self) -> str:
        """The hostname this tenant's agent answers at (``Tenant.virtual_host_name``)."""
        tenant = self._stored_tenant()
        return tenant.virtual_host_name

    def _stored_tenant(self) -> Tenant:
        self.get_session().expire_all()
        tenant = self.get_one(Tenant, tenant_id=self.tenant_id)
        assert tenant is not None, f"no tenant {self.tenant_id!r}"
        return tenant

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
            "format_options": _FORMAT_OPTIONS,
            **selection,
        }

    @staticmethod
    def _product_form(product_id: str, **selection: Any) -> dict[str, Any]:
        # One fixed-CPM pricing option, which both forms require, and a measurement
        # provider, without which the edit form clears the NOT NULL delivery_measurement.
        return {
            "name": f"Product {product_id}",
            "product_id": product_id,
            "format_options": _FORMAT_OPTIONS,
            "delivery_measurement_provider": "publisher",
            "pricing_model_0": "cpm_fixed",
            "currency_0": "USD",
            "rate_0": "10.00",
            **selection,
        }

    def _admin_request(self, path: str, form: dict[str, Any] | None = None) -> AdminResponse:
        """GET *path*, or POST *form* to it and follow the redirect to the page showing its flash."""
        url = f"/tenant/{self.tenant_id}/{path}"
        client = self.admin_client(self.tenant_id)
        return client.request("get", url) if form is None else client.submit(url, form)
