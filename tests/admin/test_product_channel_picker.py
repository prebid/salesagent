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
  The refusal is ``InvalidFormInput``, so the admin error handler answers it on the page the
  form came from.
"""

from __future__ import annotations

import json

import pytest
from adcp.types._generated import MediaChannel
from bs4 import BeautifulSoup

from src.core.database.repositories.uow import ProductUoW
from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory
from tests.factories.core import seed_property_tag_selection
from tests.harness.admin_client import BROWSER_PAGE_HEADERS
from tests.helpers.admin_product_form import minimal_product_create_form, minimal_product_edit_form
from tests.helpers.admin_session import admin_auth_session
from tests.integration._egress_ingest_helpers import flashes

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "channel_picker_t1"
_REFUSAL = "is not an AdCP media channel"

#: The create handler refuses a product with no format arm before it reads the channels.
_IMAGE_DECLARATION = {"format_kind": "image", "params": {"width": 300, "height": 250}}


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


def test_add_product_refuses_a_channel_outside_the_enum(admin_client, tenant, factory_session):
    """Re-rendered form (200) naming the refusal, and no row -- not the 302 a stored product gives."""
    selection = seed_property_tag_selection(tenant)
    factory_session.commit()
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.post(
        f"/tenant/{_TENANT_ID}/products/add",
        data=minimal_product_create_form(
            selection, product_id="picker_add", format_options=json.dumps([_IMAGE_DECLARATION]), channels="video"
        ),
        follow_redirects=False,
    )

    assert response.status_code == 200, response.data[:2000]
    # The create path re-renders, which consumes the flash into the page; read it there.
    assert _REFUSAL in response.get_data(as_text=True)
    with ProductUoW(_TENANT_ID) as uow:
        assert uow.products is not None
        assert uow.products.get_by_id("picker_add") is None


def test_edit_product_refuses_a_channel_outside_the_enum_and_keeps_the_stored_value(admin_client, tenant):
    product = ProductFactory(tenant=tenant, product_id="picker_edit", channels=["olv"])
    PricingOptionFactory(product=product)
    admin_auth_session(admin_client, _TENANT_ID)

    edit_path = f"/tenant/{_TENANT_ID}/products/picker_edit/edit"
    response = admin_client.post(
        edit_path,
        # A browser's form post from the edit page: the error handler answers a refusal there.
        headers={**BROWSER_PAGE_HEADERS, "Referer": f"https://localhost{edit_path}"},
        data=minimal_product_edit_form(channels="video"),
        follow_redirects=False,
    )

    assert response.status_code == 302, response.data[:2000]
    # Back to the edit page -- success goes to the list.
    assert response.headers["Location"].endswith(edit_path)
    assert [category for category, message in flashes(admin_client) if _REFUSAL in message] == ["error"]
    assert _stored_channels("picker_edit") == ["olv"]
