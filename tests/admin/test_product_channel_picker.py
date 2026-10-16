"""The admin product forms offer, and accept, exactly the AdCP ``MediaChannel`` enum (GH #1532).

The shared picker ``templates/components/channel_selector.html`` used to hard-code nine
options, four of them outside the enum, and both product handlers stored whatever the form
posted. Three facts are graded here through the real Flask app, because the template guard
in tests/unit cannot see them:

* the add page renders the picker from ``inject_context`` -- a context processor that forgot
  the key would render an EMPTY picker silently (Flask's default Jinja ``Undefined`` iterates
  as nothing);
* the create handler refuses a value outside the enum and stores no product;
* the edit handler refuses one and leaves the stored channels untouched -- the stale-tab
  path, where a form opened before a deploy re-posts a value the repair migration removed.
"""

from __future__ import annotations

import pytest
from adcp.types._generated import MediaChannel
from bs4 import BeautifulSoup
from werkzeug.datastructures import MultiDict

from src.core.database.repositories.uow import ProductUoW
from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory
from tests.helpers.admin_session import admin_auth_session
from tests.integration._egress_ingest_helpers import flashes

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "channel_picker_t1"
_REFUSAL = "is not a valid MediaChannel"

#: The proven create payload of ``test_product_creation_integration.py``, minus the fields
#: this module varies. ``property_mode=none`` bypasses property-tag validation, which is not
#: what is graded here.
_ADD_FORM = [
    ("product_id", "picker_add"),
    ("name", "Picker Add"),
    ("description", "channel picker create path"),
    ("delivery_type", "non_guaranteed"),
    ("pricing_model_0", "cpm_auction"),
    ("currency_0", "USD"),
    ("floor_0", "5.0"),
    ("min_spend_0", "1000"),
    ("property_mode", "none"),
]


@pytest.fixture
def tenant(factory_session):
    return TenantFactory(tenant_id=_TENANT_ID)


def _picker_options(response) -> dict[str, bool]:
    """``value -> selected`` for every option of the page's channel picker."""
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    select = soup.select_one("select#channels")
    assert select is not None, "the page carries no channel picker"
    return {option["value"]: option.has_attr("selected") for option in select.find_all("option")}


def _stored_channels(product_id: str) -> list[str] | None:
    with ProductUoW(_TENANT_ID) as uow:
        assert uow.products is not None
        product = uow.products.get_by_id(product_id)
        return None if product is None else product.channels


def test_add_product_page_offers_exactly_the_media_channel_enum(admin_client, tenant):
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.get(f"/tenant/{_TENANT_ID}/products/add")

    assert response.status_code == 200
    assert set(_picker_options(response)) == {member.value for member in MediaChannel}


def test_add_product_refuses_a_channel_outside_the_enum(admin_client, tenant):
    """Re-rendered form (200) naming the refusal, and no row -- not the 302 a stored product gives."""
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.post(
        f"/tenant/{_TENANT_ID}/products/add",
        data=MultiDict([*_ADD_FORM, ("channels", "video")]),
        follow_redirects=False,
    )

    assert response.status_code == 200
    # The create path re-renders, which consumes the flash into the page; read it there.
    assert _REFUSAL in response.get_data(as_text=True)
    with ProductUoW(_TENANT_ID) as uow:
        assert uow.products is not None
        assert uow.products.get_by_id("picker_add") is None


def test_edit_product_refuses_a_channel_outside_the_enum_and_keeps_the_stored_value(admin_client, tenant):
    product = ProductFactory(tenant=tenant, product_id="picker_edit", channels=["olv"])
    PricingOptionFactory(product=product)
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.post(
        f"/tenant/{_TENANT_ID}/products/picker_edit/edit",
        data=MultiDict(
            [
                ("name", product.name),
                ("description", product.description),
                ("delivery_type", "guaranteed"),
                # NOT NULL column the handler nulls when the field is absent; omitting it
                # would turn the failure into an IntegrityError on the list redirect.
                ("delivery_measurement_provider", "publisher"),
                ("pricing_model_0", "cpm"),
                ("currency_0", "USD"),
                ("rate_0", "10"),
                ("channels", "video"),
            ]
        ),
        follow_redirects=False,
    )

    assert response.status_code == 302
    # Back to the edit page -- the list is where both success and the outer except go.
    assert response.headers["Location"].endswith(f"/tenant/{_TENANT_ID}/products/picker_edit/edit")
    assert [category for category, message in flashes(admin_client) if _REFUSAL in message] == ["error"]
    assert _stored_channels("picker_edit") == ["olv"]
