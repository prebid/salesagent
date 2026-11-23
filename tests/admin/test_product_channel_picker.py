"""The admin product forms offer, and accept, exactly the AdCP ``MediaChannel`` enum (GH #1532).

The shared picker ``templates/components/channel_selector.html`` used to hard-code nine
options, four of them outside the enum, and both product handlers stored whatever the form
posted. These facts are graded here through the real Flask app, posted as a browser posts
(``AdminClient``), because the template guard in tests/unit cannot see them:

* the add page renders the picker from ``inject_context`` -- a context processor that forgot
  the key would render an EMPTY picker silently (Flask's default Jinja ``Undefined`` iterates
  as nothing);
* the create handler refuses a value outside the enum and stores no product;
* the edit handler refuses one and leaves the stored channels untouched -- the stale-tab
  path, where a form opened before a deploy re-posts a value the repair migration removed.
  The refusal is ``InvalidFormInput``, so the admin error handler answers it on the page the
  form came from;
* saving the edit form unchanged writes the stored channels back as they were.
"""

from __future__ import annotations

import json

import pytest
from adcp.types._generated import MediaChannel

from src.core.database.repositories.uow import ProductUoW
from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory
from tests.factories.core import seed_property_tag_selection
from tests.harness.admin_client import AdminClient
from tests.helpers.admin_product_form import minimal_product_create_form, minimal_product_edit_form
from tests.helpers.channel_picker import picker_options

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "channel_picker_t1"
_REFUSAL = "is not an AdCP media channel"

#: The create handler refuses a product with no format arm before it reads the channels.
_IMAGE_DECLARATION = {"format_kind": "image", "params": {"width": 300, "height": 250}}


@pytest.fixture
def tenant(factory_session):
    return TenantFactory(tenant_id=_TENANT_ID)


@pytest.fixture
def operator(tenant):
    """The tenant's operator in a browser, signed in."""
    client = AdminClient()
    client.authenticate(_TENANT_ID)
    yield client
    client.close()


def _stored_channels(product_id: str) -> list[str] | None:
    with ProductUoW(_TENANT_ID) as uow:
        assert uow.products is not None
        product = uow.products.get_by_id(product_id)
        return None if product is None else product.channels


def _selected(page) -> list[str]:
    return [value for value, selected in picker_options(page.data.decode()) if selected]


def test_add_product_page_offers_exactly_the_media_channel_enum(operator):
    page = operator.request("get", f"/tenant/{_TENANT_ID}/products/add")

    assert page.status_code == 200
    assert {value for value, _ in picker_options(page.data.decode())} == {member.value for member in MediaChannel}


def test_add_product_refuses_a_channel_outside_the_enum(operator, tenant, factory_session):
    """The form re-rendered with the refusal flashed, and no row -- a stored product goes to the list."""
    selection = seed_property_tag_selection(tenant)
    factory_session.commit()

    page = operator.submit(
        f"/tenant/{_TENANT_ID}/products/add",
        minimal_product_create_form(
            selection, product_id="picker_add", format_options=json.dumps([_IMAGE_DECLARATION]), channels="video"
        ),
    )

    assert page.status_code == 200, page.data[:2000]
    assert [category for category, message in page.flashes() if _REFUSAL in message] == ["error"]
    with ProductUoW(_TENANT_ID) as uow:
        assert uow.products is not None
        assert uow.products.get_by_id("picker_add") is None


def test_edit_product_refuses_a_channel_outside_the_enum_and_keeps_the_stored_value(operator, tenant):
    product = ProductFactory(tenant=tenant, product_id="picker_edit", channels=["olv"])
    PricingOptionFactory(product=product)
    edit_path = f"/tenant/{_TENANT_ID}/products/picker_edit/edit"

    page = operator.submit(edit_path, minimal_product_edit_form(channels="video"), from_page=edit_path)

    # Back on the edit page with the stored channel selected: success goes to the list,
    # which has no picker.
    assert _selected(page) == ["olv"]
    assert [category for category, message in page.flashes() if _REFUSAL in message] == ["error"]
    assert _stored_channels("picker_edit") == ["olv"]


def test_saving_the_edit_form_unchanged_keeps_the_stored_channels(operator, tenant):
    """Open the edit page and post back what its picker selects: the stored array does not move.

    Seeded out of enum order on purpose. The column's rule stores enum order, which is the
    order the picker renders and a browser posts, so a stored product survives a no-op save.
    """
    product = ProductFactory(tenant=tenant, product_id="picker_noop", channels=["retail_media", "olv"])
    PricingOptionFactory(product=product)
    stored = _stored_channels("picker_noop")
    edit_path = f"/tenant/{_TENANT_ID}/products/picker_noop/edit"

    selected = _selected(operator.request("get", edit_path))
    page = operator.submit(edit_path, minimal_product_edit_form(channels=selected), from_page=edit_path)

    # Saved: a refusal goes back to the edit page and writes nothing.
    assert [category for category, _ in page.flashes()] == ["success"]
    assert stored == [MediaChannel.olv.value, MediaChannel.retail_media.value]
    assert _stored_channels("picker_noop") == stored
