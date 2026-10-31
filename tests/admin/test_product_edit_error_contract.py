"""What the product-edit handler tells an operator when something unexpected fails.

THE DISEASE THIS GRADES. ``edit_product`` wrapped its whole body in
``except Exception`` and answered with ``flash(f"Error editing product: {str(e)}")`` plus a
redirect. So a programming error -- a ``NameError``, a ``KeyError`` -- was presented to the
operator as a description of THEIR input, their edit was discarded, and the response was a
302 that monitoring records as a success. The defect got no half-life: it was not noticed and
fixed, it was re-encountered as a mystery. That is exactly how the ``NameError`` behind the
unreachable ``line_item_type`` guard survived for the life of that branch.

It is the same shape as the buyer-facing instance recorded on salesagent-q28ss, where a
``KeyError`` in the creative-upload path became SERVICE_UNAVAILABLE with
``recovery=transient`` -- "retry this" for a request that could never succeed. A broad handler
converting a programming error into a plausible-looking answer is one defect with two
surfaces.

WHY ``KeyError`` IS THE INJECTED FAULT: it is the exception class of that recorded instance,
and it is injected at ``parse_pricing_options_from_form``, whose call site guards only
``ValueError`` -- so it reaches the outer handler the way a real programming error would,
rather than being routed by a local ``except``.

WHY AN ADMIN TEST. The contract under test is the HTTP answer a Flask handler gives, which
only a request can observe. No registry tool edits a product, so no BDD transport reaches it.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from werkzeug.exceptions import NotFound

from tests.factories import (
    PricingOptionFactory,
    ProductFactory,
    TenantFactory,
)
from tests.factories.core import seed_property_tag_selection
from tests.helpers.admin_product_form import minimal_product_create_form, minimal_product_edit_form
from tests.helpers.admin_session import admin_auth_session, admin_test_app

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

#: Text from the injected exception that must never reach the operator. A Python exception's
#: ``str()`` is a diagnostic, and presenting one as an error message asserts that the
#: operator's input caused it.
FAULT_TEXT = "pricing_option_internals"


@pytest.fixture
def editable_product(factory_session):
    """A tenant with one editable product, and the property selection its forms require.

    The authorized property is needed by the CREATE form, not the edit one: ``add_product``
    refuses a product with no format arm and no properties BEFORE it parses pricing, so a
    create POST missing either returns the re-rendered form (200) and never reaches the fault
    this module injects.
    """
    tenant = TenantFactory(ad_server="mock")
    selection = seed_property_tag_selection(tenant)
    product = ProductFactory(tenant=tenant, product_id="edit_me", property_tags=["all_inventory"])
    PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)
    factory_session.commit()
    tenant.property_tag_selection = selection
    return tenant


@pytest.fixture
def operator(editable_product):
    app = admin_test_app()
    with app.test_client() as client:
        admin_auth_session(client, editable_product.tenant_id)
        yield client


def _post_edit(client, tenant_id):
    return client.post(
        f"/tenant/{tenant_id}/products/edit_me/edit", data=minimal_product_edit_form(), follow_redirects=False
    )


def test_a_programming_error_is_not_answered_as_an_operator_mistake(operator, editable_product):
    """A ``KeyError`` must not become "Error editing product: <python text>" plus a 302.

    The status code is the half the monitoring sees and the message is the half the operator
    sees; both were wrong, so both are asserted.
    """
    with patch(
        "src.admin.blueprints.products.parse_pricing_options_from_form",
        side_effect=KeyError(FAULT_TEXT),
    ):
        response = _post_edit(operator, editable_product.tenant_id)

    assert response.status_code == 500, (
        f"a programming error answered {response.status_code}; a 302 tells monitoring the edit succeeded"
    )
    assert FAULT_TEXT not in response.get_data(as_text=True), (
        "the injected exception's text reached the operator, which asserts their input caused it"
    )


def test_an_http_exception_raised_inside_the_handler_keeps_its_status(operator, editable_product):
    """``abort(404)`` inside the body must stay a 404, not become a flash message.

    The broad handler caught Werkzeug's own control flow too, so any ``abort()`` reached from
    inside this handler was converted into "Error editing product: 404 Not Found" with a 302.
    ``src/admin/utils/helpers.py:386`` guards the same way for 403/404, which is the
    precedent this follows.
    """
    with patch(
        "src.admin.blueprints.products.parse_pricing_options_from_form",
        side_effect=NotFound(),
    ):
        response = _post_edit(operator, editable_product.tenant_id)

    assert response.status_code == 404, (
        f"an HTTPException from inside the handler answered {response.status_code}, losing its own status"
    )


def test_the_create_handler_does_not_answer_a_programming_error_either(operator, editable_product):
    """``add_product`` carries the identical net, so it gets the identical contract.

    Fixing one of a twinned pair and not the other is arbitrary: both are the product-authoring
    surface, both wrap their whole body in one broad handler, and both flashed ``str(e)``. The
    create path re-renders the form instead of redirecting, which is better for a real input
    error and exactly as misleading for a ``KeyError``.
    """
    with patch(
        "src.admin.blueprints.products.parse_pricing_options_from_form",
        side_effect=KeyError(FAULT_TEXT),
    ):
        response = operator.post(
            f"/tenant/{editable_product.tenant_id}/products/add",
            data=minimal_product_create_form(
                editable_product.property_tag_selection,
                description="Reaches the pricing parse",
                format_options=json.dumps([{"format_kind": "image", "params": {"width": 300, "height": 250}}]),
            ),
            follow_redirects=False,
        )

    assert response.status_code == 500, f"a programming error on the create path answered {response.status_code}"
    assert FAULT_TEXT not in response.get_data(as_text=True)


def test_an_operator_input_error_still_flashes_and_redirects(operator, editable_product):
    """The path that WAS correct stays correct: a ValueError is the operator's to fix.

    ``parse_pricing_options_from_form`` raises ``ValueError`` for a malformed pricing block,
    and its call site catches exactly that, flashes the message and redirects. Without this
    assertion, narrowing the outer handler could turn every input mistake into a 500 and the
    two tests above would still pass.
    """
    with patch(
        "src.admin.blueprints.products.parse_pricing_options_from_form",
        side_effect=ValueError("rate must be a positive number"),
    ):
        response = _post_edit(operator, editable_product.tenant_id)

    assert response.status_code in (302, 303), (
        f"an operator input error answered {response.status_code} instead of redirecting with a message"
    )
