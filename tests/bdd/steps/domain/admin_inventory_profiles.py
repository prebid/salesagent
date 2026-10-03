"""Steps for BR-ADMIN-INVENTORY-PROFILE-publishers: the operator's selections and their publishers (#1845).

The harness is ``AdminInventoryProfileEnv`` (``ctx["env"]``): the real inventory-profile
and product forms and the products page, in process or over the live stack, against the database the factories
write. Pages land in ``ctx["admin_page"]``, the key the shared admin page steps
(``the page contains ...``, ``tests/bdd/steps/domain/admin_accounts.py``) read.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.bdd.steps.generic._table import comma_list
from tests.factories import InventoryProfileFactory

if TYPE_CHECKING:
    from tests.harness.admin_inventory_profile import AdminInventoryProfileEnv


def _env(ctx: dict) -> AdminInventoryProfileEnv:
    return ctx["env"]


# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('an inventory profile "{profile_id}" exists'))
def given_profile_exists(ctx: dict, profile_id: str) -> None:
    InventoryProfileFactory(tenant=ctx["tenant"], profile_id=profile_id)


# ── When steps ──────────────────────────────────────────────────────


@when(parsers.parse('the operator creates inventory profile "{profile_id}" selecting tags "{tags}"'))
def when_create_profile_by_tags(ctx: dict, profile_id: str, tags: str) -> None:
    ctx["admin_page"] = _env(ctx).create_inventory_profile(profile_id, property_mode="tags", property_tags=tags)


@when(parsers.parse('the operator creates inventory profile "{profile_id}" selecting properties "{property_ids}"'))
def when_create_profile_by_ids(ctx: dict, profile_id: str, property_ids: str) -> None:
    ctx["admin_page"] = _env(ctx).create_inventory_profile(
        profile_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids)
    )


@when(parsers.parse('the operator edits inventory profile "{profile_id}" to select properties "{property_ids}"'))
def when_edit_profile_by_ids(ctx: dict, profile_id: str, property_ids: str) -> None:
    ctx["admin_page"] = _env(ctx).edit_inventory_profile(
        profile_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids)
    )


@when(parsers.parse('the operator creates product "{product_id}" selecting tags "{selections}"'))
def when_create_product_by_tags(ctx: dict, product_id: str, selections: str) -> None:
    ctx["admin_page"] = _env(ctx).create_product(
        product_id, property_mode="tags", selected_property_tags=comma_list(selections)
    )


@when(parsers.parse('the operator creates product "{product_id}" selecting properties "{property_ids}"'))
def when_create_product_by_ids(ctx: dict, product_id: str, property_ids: str) -> None:
    ctx["admin_page"] = _env(ctx).create_product(
        product_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids)
    )


@when(parsers.parse('the operator edits product "{product_id}" to select tags "{selections}"'))
def when_edit_product_by_tags(ctx: dict, product_id: str, selections: str) -> None:
    ctx["admin_page"] = _env(ctx).edit_product(
        product_id, property_mode="tags", selected_property_tags=comma_list(selections)
    )


@when(parsers.parse('the operator edits product "{product_id}" to select properties "{property_ids}"'))
def when_edit_product_by_ids(ctx: dict, product_id: str, property_ids: str) -> None:
    ctx["admin_page"] = _env(ctx).edit_product(
        product_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids)
    )


@when(parsers.parse('the operator opens the edit form of product "{product_id}"'))
def when_open_product_edit_form(ctx: dict, product_id: str) -> None:
    ctx["admin_page"] = _env(ctx).admin_page(f"products/{product_id}/edit")


@when("the operator opens the add inventory profile form")
def when_open_add_form(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).admin_page("inventory-profiles/add")


@when(parsers.parse('the operator opens the edit form of inventory profile "{profile_id}"'))
def when_open_edit_form(ctx: dict, profile_id: str) -> None:
    env = _env(ctx)
    stored = env.stored_inventory_profile(profile_id)
    assert stored is not None, f"no inventory profile {profile_id!r}"
    ctx["admin_page"] = env.admin_page(f"inventory-profiles/{stored.id}/edit")


@when("the operator opens the products page")
def when_open_products_page(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).admin_page("products/")


# ── Then steps ──────────────────────────────────────────────────────


def _assert_stored_selectors(ctx: dict, row: Any, field: str, expected: str) -> None:
    """*row*'s selector column *field* equals the *expected* JSON, the row having been stored."""
    page = _require_admin_page(ctx)
    assert row is not None, f"nothing stored; the form answered {page.status_code}"
    stored = getattr(row, field)
    assert stored == json.loads(expected), f"stored {stored!r}"


@then(parsers.parse('inventory profile "{profile_id}" stores publisher_properties {expected}'))
def then_profile_stores(ctx: dict, profile_id: str, expected: str) -> None:
    _assert_stored_selectors(ctx, _env(ctx).stored_inventory_profile(profile_id), "publisher_properties", expected)


@then(parsers.parse('product "{product_id}" stores publisher_properties {expected}'))
def then_product_stores(ctx: dict, product_id: str, expected: str) -> None:
    _assert_stored_selectors(ctx, _env(ctx).stored_product(product_id), "properties", expected)


@then(parsers.parse('no product "{product_id}" is stored'))
def then_product_not_stored(ctx: dict, product_id: str) -> None:
    assert _env(ctx).stored_product(product_id) is None, f"product {product_id!r} was stored"


@then(parsers.parse('the form preselects exactly the properties "{property_ids}"'))
def then_form_preselects(ctx: dict, property_ids: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the form answered {page.status_code}"
    checkboxes = re.findall(r'<input[^>]*name="selected_property_ids" value="([^"]+)"([^>]*)>', page.data.decode())
    assert checkboxes, "the form offers no property checkboxes"
    checked = {value for value, attributes in checkboxes if re.search(r"\bchecked\b", attributes)}
    assert checked == set(comma_list(property_ids)), f"the form preselects {sorted(checked)}"


@then(parsers.parse('no inventory profile "{profile_id}" is stored'))
def then_profile_not_stored(ctx: dict, profile_id: str) -> None:
    assert _env(ctx).stored_inventory_profile(profile_id) is None, f"profile {profile_id!r} was stored"


@then("the page does not name the seller's own host")
def then_page_omits_agent_host(ctx: dict) -> None:
    page = _require_admin_page(ctx)
    host = ctx["tenant"].virtual_host_name
    assert page.status_code == 200, f"the page answered {page.status_code}"
    assert host not in page.data.decode(), f"the page offers the seller's own host {host!r}"


@then(parsers.parse('the products page marks exactly the products "{product_ids}" as not offered to buyers'))
def then_products_marked_not_offered(ctx: dict, product_ids: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the products page answered {page.status_code}"
    marked = set(re.findall(r'data-not-offered="([^"]+)"', page.data.decode()))
    assert marked == set(comma_list(product_ids)), f"the page marks {sorted(marked)} as not offered"
