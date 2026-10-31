"""The GAM creative placeholder the admin SEEDS follows the product's format declarations.

WHY AN ADMIN TEST. A canonical-only product whose GAM placeholder contradicts its own
declaration is OPERATOR-visible, not buyer-visible: it is the ``implementation_config`` the
admin writes for a seller, which the GAM adapter later traffics. And it is reachable only
through a Flask POST -- the registry's tools are all buyer-facing, there is no
``create_product``, and the BDD harness seeds products through ``ProductFactory``, which
writes the row directly and never runs the handler. So no BDD scenario can reach this and
this module is the grader.

WHAT THIS GRADES THAT THE UNIT TEST CANNOT.
``tests/unit/test_gam_creative_placeholder_derivation.py`` grades the derivation over eight
input shapes, and every one of those would still pass if ``format_options`` were threaded in
from nowhere -- a derivation that matches nothing is green. This module grades the PLUMBING:
that the admin CREATE path actually hands the product's canonical arm to the service. It is
the only test of the set that fails when the wiring is removed and the derivation is left
intact.

THE EDIT PATH IS NOT GRADED HERE, AND THAT IS THE HONEST STATE. A third test used to assert
that an edit changing the declarations reseeded the placeholder. It reached that code by
POSTing a ``line_item_type`` no template submits, which is how the NameError behind that
unsatisfiable guard was found; the guard and its branches are now deleted, so the behaviour
the test asserted does not exist. Re-grounding it on the current behaviour would mean
asserting that an edit leaves the placeholder STALE, which pins a defect. The reseed-on-edit
obligation and the test that belongs with it are tracked as their own work item.

NOT ASSERTED ANYWHERE HERE: 300x250 as the expected placeholder for a product that declared
something else. That was the defect's signature -- every product got it regardless of what
the operator selected -- so an expectation of it would pin the bug. The one assertion about
300x250 is a NEGATIVE one.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from src.core.database.models import Product
from tests.factories import TenantFactory
from tests.factories.core import seed_property_tag_selection
from tests.helpers.admin_product_form import minimal_product_create_form
from tests.helpers.admin_session import admin_auth_session, admin_test_app

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

#: A canonical declaration fixing a size that is NOT the fallback, so no assertion below can
#: be satisfied by the fallback firing. ``core/product-format-declaration.json`` (AdCP 3.2.3)
#: requires ``format_kind``; ``formats/canonical/image.json`` declares ``width``/``height``.
IMAGE_970x250 = {"format_kind": "image", "params": {"width": 970, "height": 250}}

#: What the defect produced for every product, whatever it declared.
FALLBACK_PLACEHOLDER = {"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}

EXPECTED_970x250 = {"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False}


@pytest.fixture
def gam_seller(factory_session):
    """A GAM tenant an operator can author a product for, built by factories only.

    ``ad_server="google_ad_manager"`` because ``add_product`` reads
    ``adapter_type = tenant.ad_server or "mock"`` and only that value reaches the GAM
    branch -- the one that seeds a GAM ``implementation_config``.
    """
    tenant = TenantFactory(ad_server="google_ad_manager")
    selection = seed_property_tag_selection(tenant)
    factory_session.commit()
    return tenant, selection


@pytest.fixture
def operator(gam_seller):
    """A Flask client carrying an authenticated admin session for the GAM tenant."""
    tenant, _ = gam_seller
    app = admin_test_app()
    with app.test_client() as client:
        admin_auth_session(client, tenant.tenant_id)
        yield client


def _stored_placeholders(session, tenant_id: str) -> list[dict]:
    """The seeded placeholders on the one product the tenant now has, read from the row."""
    session.expire_all()
    product = session.scalars(select(Product).filter_by(tenant_id=tenant_id)).first()
    assert product is not None, "the create handler stored no product"
    assert product.implementation_config, "the create handler seeded no implementation_config"
    return product.implementation_config["creative_placeholders"]


def test_a_canonical_only_product_is_seeded_with_the_size_it_declares(operator, gam_seller, factory_session):
    """The whole of defect 2, through the handler that was not passing the arm.

    The product declares image 970x250 and nothing on the deprecated arm. Before the fix
    the admin passed only ``formats`` -- empty here -- so the explicit else-branch assigned
    300x250, contradicting the declaration the operator had just authored.
    """
    tenant, tag_selection = gam_seller
    response = operator.post(
        f"/tenant/{tenant.tenant_id}/products/add",
        data=minimal_product_create_form(
            tag_selection,
            name="Canonically Declared Billboard",
            description="Declares image 970x250 canonically and nothing on the legacy arm",
            format_options=json.dumps([IMAGE_970x250]),
        ),
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.data[:3000]

    placeholders = _stored_placeholders(factory_session, tenant.tenant_id)
    assert placeholders == [EXPECTED_970x250]
    assert FALLBACK_PLACEHOLDER not in placeholders, (
        "the product declared 970x250; a 300x250 placeholder is the dead-derivation fallback"
    )


# DELETED: test_a_legacy_only_product_is_seeded_from_the_entry_it_stores.
#
# It POSTed a legacy-only product to /products/add and expected a redirect. A4
# (salesagent-ol033.6) removed legacy authoring from this surface, so that POST is now
# REFUSED: core/product.json says "Do not author a new product from format_ids alone", and
# a product created here must declare the canonical arm. The test asserted behaviour the
# pin forbids, which this epic deletes rather than re-grounds.
#
# ITS OBLIGATION SURVIVES AND IS OWED BACK, as salesagent-knsx5. The claim that it could
# not be re-homed was WRONG: resolving the callers of generate_default_config finds a third
# one at src/core/tools/media_buy_create.py:3540, inside create_media_buy, which hands the
# legacy arm over for an existing product with no implementation_config. A4 deliberately
# PRESERVES format_ids on existing and ingested rows, so that path is live and untouched by
# this change -- and it is a BUYER tool, so the obligation is gradable as a named scenario
# on a2a/mcp/rest/e2e_rest rather than through a Flask POST no transport reaches. That is a
# better instrument than the one deleted here.
#
# The sibling above still grades the admin plumbing, so this module does not go empty.
