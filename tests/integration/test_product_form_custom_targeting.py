"""Integration tests: the admin product form refuses custom targeting GAM cannot take.

A GAM product's ``targeting_template.key_value_pairs`` is stored as
``implementation_config["custom_targeting_keys"]`` and parsed by
``CustomTargeting.from_stored`` when a media buy books it. The form runs the same parser
on save, so a seller fixes a malformed config on the product instead of a buyer's media
buy failing later.

Real PostgreSQL, real Flask app, real form POST. Nothing on these paths calls GAM.
"""

import html
import json
import re

import pytest

from src.adapters.gam_implementation_config_schema import CustomTargeting
from src.core.database.repositories.uow import ProductUoW
from tests.factories import ProductFactory, TenantFactory
from tests.factories.core import seed_property_tag_selection
from tests.helpers.admin_product_form import minimal_product_create_form, minimal_product_edit_form

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_ct_tenant"
REFUSAL = "Custom targeting is not in a form GAM can take"
MALFORMED = {"groups": [{"criteria": [{"keyId": "sport", "values": ["football"]}]}]}
VALID = {"groups": [{"criteria": [{"keyId": "11001", "values": ["448512"]}]}]}
#: The add form refuses a product with no format declaration.
IMAGE_300X250 = json.dumps([{"format_kind": "image", "params": {"width": 300, "height": 250}}])


@pytest.fixture
def gam_tenant(bound_factory_session):
    return TenantFactory(tenant_id=TENANT_ID, ad_server="google_ad_manager")


def _stored_products() -> dict[str, dict]:
    """Each saved product's name mapped to its implementation_config."""
    with ProductUoW(TENANT_ID) as uow:
        return {p.name: p.implementation_config for p in uow.products.list_all()}


def _targeting(key_value_pairs: dict) -> str:
    """The targeting widget's hidden field, carrying ``key_value_pairs``."""
    return json.dumps({"key_value_pairs": key_value_pairs})


def _widget_document(client, product_id: str) -> dict:
    """The targeting document the edit page hands the targeting widget in its hidden field."""
    page = client.get(f"/tenant/{TENANT_ID}/products/{product_id}/edit").get_data(as_text=True)
    [value] = re.findall(r"id=\"targeting-data\" name=\"targeting_template\" value='([^']*)'", page)
    return json.loads(html.unescape(value))


def _add_product(client, tenant, name: str, key_value_pairs: dict):
    form = minimal_product_create_form(
        seed_property_tag_selection(tenant),
        name=name,
        format_options=IMAGE_300X250,
        targeting_template=_targeting(key_value_pairs),
    )
    return client.post(f"/tenant/{TENANT_ID}/products/add", data=form)


def _edit_product(client, product, key_value_pairs: dict):
    form = minimal_product_edit_form(name=product.name, targeting_template=_targeting(key_value_pairs))
    return client.post(f"/tenant/{TENANT_ID}/products/{product.product_id}/edit", data=form)


def test_add_refuses_custom_targeting_gam_cannot_take(authenticated_admin_session, gam_tenant):
    response = _add_product(authenticated_admin_session, gam_tenant, "Malformed Targeting", MALFORMED)

    assert response.status_code == 200
    assert REFUSAL in response.get_data(as_text=True)
    assert "Malformed Targeting" not in _stored_products()


def test_add_saves_custom_targeting_gam_can_take(authenticated_admin_session, gam_tenant):
    response = _add_product(authenticated_admin_session, gam_tenant, "Valid Targeting", VALID)

    assert response.status_code == 302
    assert (
        _stored_products()["Valid Targeting"]["custom_targeting_keys"] == CustomTargeting.from_stored(VALID).to_stored()
    )


def test_edit_refusal_leaves_stored_custom_targeting_unchanged(authenticated_admin_session, gam_tenant):
    product = ProductFactory(tenant=gam_tenant, name="Existing", implementation_config={"custom_targeting_keys": VALID})

    response = _edit_product(authenticated_admin_session, product, MALFORMED)

    assert response.status_code == 302
    with authenticated_admin_session.session_transaction() as flask_session:
        assert any(REFUSAL in message for _, message in flask_session["_flashes"])
    assert response.headers["Location"].endswith(f"/tenant/{TENANT_ID}/products/{product.product_id}/edit")
    assert _stored_products()["Existing"]["custom_targeting_keys"] == VALID


def test_a_legacy_name_of_digits_stays_a_name_through_the_edit_page(authenticated_admin_session, gam_tenant):
    """The widget edits what the model wrote, so saving it back cannot turn the name into a value ID."""
    legacy = {"11001": "448512"}
    product = ProductFactory(
        tenant=gam_tenant, name="Legacy", targeting_template={}, implementation_config={"custom_targeting_keys": legacy}
    )

    handed = _widget_document(authenticated_admin_session, product.product_id)["key_value_pairs"]
    response = _edit_product(authenticated_admin_session, product, handed)

    assert handed == {"groups": [{"criteria": [{"keyId": "11001", "values": [{"name": "448512"}], "exclude": False}]}]}
    assert response.status_code == 302
    stored = _stored_products()["Legacy"]["custom_targeting_keys"]
    assert stored == handed
    assert CustomTargeting.from_stored(stored) == CustomTargeting.from_stored(legacy)


def test_saving_the_edit_page_keeps_custom_targeting_it_cannot_show(authenticated_admin_session, gam_tenant):
    """The page hands the widget nothing for a config GAM cannot take, and saving does not clear it."""
    unreadable = {"sport": "football"}
    product = ProductFactory(
        tenant=gam_tenant,
        name="Unreadable",
        targeting_template={},
        implementation_config={"custom_targeting_keys": unreadable},
    )

    page = authenticated_admin_session.get(f"/tenant/{TENANT_ID}/products/{product.product_id}/edit").get_data(
        as_text=True
    )
    handed = _widget_document(authenticated_admin_session, product.product_id)["key_value_pairs"]
    response = _edit_product(authenticated_admin_session, product, handed)

    assert "stored custom targeting is not shown below. Saving keeps it as it is" in page
    assert handed == {"groups": []}
    assert response.status_code == 302
    assert _stored_products()["Unreadable"]["custom_targeting_keys"] == unreadable


def test_saving_no_custom_targeting_clears_it(authenticated_admin_session, gam_tenant):
    """The widget sends an empty groups document once the last criterion is removed."""
    product = ProductFactory(tenant=gam_tenant, name="Cleared", implementation_config={"custom_targeting_keys": VALID})

    response = _edit_product(authenticated_admin_session, product, {"groups": []})

    assert response.status_code == 302
    assert _stored_products()["Cleared"]["custom_targeting_keys"] == {"groups": []}
