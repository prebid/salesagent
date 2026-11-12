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
from tests.factories import AuthorizedPropertyFactory, ProductFactory, PropertyTagFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_ct_tenant"
PUBLISHER_DOMAIN = "gam-ct.example.com"
REFUSAL = "Custom targeting is not in a form GAM can take"
MALFORMED = {"groups": [{"criteria": [{"keyId": "sport", "values": ["football"]}]}]}
VALID = {"groups": [{"criteria": [{"keyId": "11001", "values": ["448512"]}]}]}


@pytest.fixture
def gam_tenant(bound_factory_session):
    """A GAM tenant with the property tag the add form's tag check reads."""
    tenant = TenantFactory(tenant_id=TENANT_ID, ad_server="google_ad_manager")
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
    AuthorizedPropertyFactory(tenant=tenant, publisher_domain=PUBLISHER_DOMAIN, tags=["all_inventory"])
    return tenant


def _stored_products() -> dict[str, dict]:
    """Each saved product's name mapped to its implementation_config."""
    with ProductUoW(TENANT_ID) as uow:
        return {p.name: p.implementation_config for p in uow.products.list_all()}


def _form(name: str, key_value_pairs: dict) -> dict[str, str]:
    """The product form fields both routes need before they reach custom targeting."""
    return {
        "name": name,
        "format_options": json.dumps([{"format_kind": "image", "params": {"width": 300, "height": 250}}]),
        "pricing_model_0": "cpm_fixed",
        "currency_0": "USD",
        "rate_0": "10.00",
        "property_mode": "tags",
        "selected_property_tags": f"{PUBLISHER_DOMAIN}:all_inventory",
        "delivery_measurement_provider": "publisher",
        "targeting_template": json.dumps({"key_value_pairs": key_value_pairs}),
    }


def _widget_document(client, product_id: str) -> dict:
    """The targeting document the edit page hands the targeting widget in its hidden field."""
    page = client.get(f"/tenant/{TENANT_ID}/products/{product_id}/edit").get_data(as_text=True)
    [value] = re.findall(r"id=\"targeting-data\" name=\"targeting_template\" value='([^']*)'", page)
    return json.loads(html.unescape(value))


def _add_product(client, name: str, key_value_pairs: dict):
    return client.post(f"/tenant/{TENANT_ID}/products/add", data=_form(name, key_value_pairs))


def test_add_refuses_custom_targeting_gam_cannot_take(authenticated_admin_session, gam_tenant):
    response = _add_product(authenticated_admin_session, "Malformed Targeting", MALFORMED)

    assert response.status_code == 200
    assert REFUSAL in response.get_data(as_text=True)
    assert "Malformed Targeting" not in _stored_products()


def test_add_saves_custom_targeting_gam_can_take(authenticated_admin_session, gam_tenant):
    response = _add_product(authenticated_admin_session, "Valid Targeting", VALID)

    assert response.status_code == 302
    assert _stored_products()["Valid Targeting"]["custom_targeting_keys"] == VALID


def test_edit_refusal_leaves_stored_custom_targeting_unchanged(authenticated_admin_session, gam_tenant):
    product = ProductFactory(tenant=gam_tenant, name="Existing", implementation_config={"custom_targeting_keys": VALID})

    response = authenticated_admin_session.post(
        f"/tenant/{TENANT_ID}/products/{product.product_id}/edit",
        data=_form("Existing", MALFORMED),
    )

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
    response = authenticated_admin_session.post(
        f"/tenant/{TENANT_ID}/products/{product.product_id}/edit", data=_form("Legacy", handed)
    )

    assert handed == {"groups": [{"criteria": [{"keyId": "11001", "values": [{"name": "448512"}], "exclude": False}]}]}
    assert response.status_code == 302
    stored = _stored_products()["Legacy"]["custom_targeting_keys"]
    assert stored == handed
    assert CustomTargeting.from_stored(stored) == CustomTargeting.from_stored(legacy)


def test_edit_page_says_when_stored_custom_targeting_cannot_be_shown(authenticated_admin_session, gam_tenant):
    product = ProductFactory(
        tenant=gam_tenant,
        name="Unreadable",
        targeting_template={},
        implementation_config={"custom_targeting_keys": {"sport": "football"}},
    )

    response = authenticated_admin_session.get(f"/tenant/{TENANT_ID}/products/{product.product_id}/edit")

    page = response.get_data(as_text=True)
    assert REFUSAL in page
    assert "stored custom targeting is not shown below, and saving replaces it" in page
    assert _widget_document(authenticated_admin_session, product.product_id)["key_value_pairs"] == {"groups": []}


def test_saving_no_custom_targeting_clears_it(authenticated_admin_session, gam_tenant):
    """The widget sends an empty groups document once the last criterion is removed."""
    product = ProductFactory(tenant=gam_tenant, name="Cleared", implementation_config={"custom_targeting_keys": VALID})

    response = authenticated_admin_session.post(
        f"/tenant/{TENANT_ID}/products/{product.product_id}/edit", data=_form("Cleared", {"groups": []})
    )

    assert response.status_code == 302
    assert _stored_products()["Cleared"]["custom_targeting_keys"] == {"groups": []}
