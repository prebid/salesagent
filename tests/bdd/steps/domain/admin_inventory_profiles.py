"""Steps for the admin inventory-profile and product forms.

BR-ADMIN-INVENTORY-PROFILE-publishers: the operator's selections and their publishers (#1845).

The harness is ``AdminInventoryProfileEnv`` (``ctx["env"]``): the real inventory-profile
and product forms and the products page, in process or over the live stack, against the database the factories
write. Pages land in ``ctx["admin_page"]``, the key the shared admin page steps
(``the page contains ...``, ``tests/bdd/steps/domain/admin_accounts.py``) read.

BR-ADMIN-INVENTORY-PROFILES: the inventory page's profile modal, through the admin env's own
transports (``AdminAccountEnv``); its answers land in ``ctx["admin_response"]``.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.bdd.steps.generic._table import comma_list
from tests.factories import InventoryProfileFactory
from tests.helpers.admin_product_form import minimal_product_create_form

if TYPE_CHECKING:
    from tests.harness.admin_inventory_profile import AdminInventoryProfileEnv


def _env(ctx: dict) -> AdminInventoryProfileEnv:
    return ctx["env"]


# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('an inventory profile "{profile_id}" exists'))
def given_profile_exists(ctx: dict, profile_id: str) -> None:
    InventoryProfileFactory(tenant=ctx["tenant"], profile_id=profile_id)


@given(parsers.parse('an inventory profile "{profile_id}" storing publisher_properties {selectors}'))
def given_profile_storing(ctx: dict, profile_id: str, selectors: str) -> None:
    InventoryProfileFactory(tenant=ctx["tenant"], profile_id=profile_id, publisher_properties=json.loads(selectors))


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


@when(parsers.parse('the operator creates product "{product_id}" in full mode selecting properties "{property_ids}"'))
def when_create_product_full_mode(ctx: dict, product_id: str, property_ids: str) -> None:
    ctx["admin_page"] = _env(ctx).create_product(
        product_id, property_mode="full", full_property_ids=comma_list(property_ids)
    )


@when(
    parsers.parse(
        'the operator creates inventory profile "{profile_id}" in JSON mode storing publisher_properties {selectors}'
    )
)
def when_create_profile_json_mode(ctx: dict, profile_id: str, selectors: str) -> None:
    ctx["admin_page"] = _env(ctx).create_inventory_profile(
        profile_id, property_mode="full", publisher_properties=selectors
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


@when(parsers.parse('the operator renames product "{product_id}" through its edit form, changing nothing else'))
def when_rename_product(ctx: dict, product_id: str) -> None:
    env = _env(ctx)
    ctx["selection_before_save"] = _product_selection(env.stored_product(product_id))
    ctx["admin_page"] = env.rename_product_through_its_form(product_id)


@when(
    parsers.parse('the operator renames inventory profile "{profile_id}" through its edit form, changing nothing else')
)
def when_rename_profile(ctx: dict, profile_id: str) -> None:
    env = _env(ctx)
    ctx["selection_before_save"] = _profile_selection(env.stored_inventory_profile(profile_id))
    ctx["admin_page"] = env.rename_profile_through_its_form(profile_id)


@when("the operator opens the add product form")
def when_open_add_product_form(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).admin_page("products/add")


@when(parsers.parse('the operator opens the edit form of product "{product_id}"'))
def when_open_product_edit_form(ctx: dict, product_id: str) -> None:
    ctx["admin_page"] = _env(ctx).open_product_edit_form(product_id)


@when(parsers.parse('the seller\'s property "{property_id}" becomes verified'))
def when_property_verified(ctx: dict, property_id: str) -> None:
    _env(ctx).verify_property(property_id)


@when(
    parsers.re(
        r'the operator saves the open edit form of (?P<kind>product|inventory profile) "(?P<subject>[^"]+)", '
        r"changing only its name"
    )
)
def when_save_open_form(ctx: dict, kind: str, subject: str) -> None:
    env = _env(ctx)
    stored = env.stored_product(subject) if kind == "product" else env.stored_inventory_profile(subject)
    ctx["selection_before_save"] = (_product_selection if kind == "product" else _profile_selection)(stored)
    ctx["admin_page"] = env.save_open_form(f"Renamed {subject}")


@when(
    parsers.parse('the operator saves the open edit form of product "{product_id}", also checking tags "{selections}"')
)
def when_save_open_form_checking_tags(ctx: dict, product_id: str, selections: str) -> None:
    ctx["admin_page"] = _env(ctx).save_open_form(f"Renamed {product_id}", selected_property_tags=comma_list(selections))


@when(
    parsers.parse(
        'the operator saves the open edit form of product "{product_id}", declaring format option "{option_id}" '
        "under the seller's own host"
    )
)
def when_save_open_product_form_declaring_agent_host_option(ctx: dict, product_id: str, option_id: str) -> None:
    env = _env(ctx)
    ctx["admin_page"] = env.save_open_form_with(format_options=json.dumps(_agent_host_option(env, option_id)))


@when("the operator opens the add inventory profile form")
def when_open_add_form(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).admin_page("inventory-profiles/add")


@when(parsers.parse('the operator opens the edit form of inventory profile "{profile_id}"'))
def when_open_edit_form(ctx: dict, profile_id: str) -> None:
    ctx["admin_page"] = _env(ctx).open_profile_edit_form(profile_id)


@when("the operator opens the products page")
def when_open_products_page(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).admin_page("products/")


# ── Then steps ──────────────────────────────────────────────────────


def _product_selection(row: Any) -> str:
    """The column a product's selection is stored in, as the bytes a comparison can hold."""
    return json.dumps(row.properties)


def _profile_selection(row: Any) -> str:
    return json.dumps(row.publisher_properties)


def _assert_renamed_with_selection_unchanged(ctx: dict, row: Any, renamed: str, selection: str) -> None:
    """The save landed (the name changed), and the selection is what it was before the save."""
    assert row.name == renamed, f"the save did not land: the name is {row.name!r}"
    assert selection == ctx["selection_before_save"], f"stored {selection}, was {ctx['selection_before_save']}"


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


@then(parsers.parse('product "{product_id}" is renamed and its stored selection is unchanged'))
def then_product_renamed_unchanged(ctx: dict, product_id: str) -> None:
    row = _env(ctx).stored_product(product_id)
    _assert_renamed_with_selection_unchanged(ctx, row, f"Renamed {product_id}", _product_selection(row))


@then(parsers.parse('inventory profile "{profile_id}" is renamed and its stored selection is unchanged'))
def then_profile_renamed_unchanged(ctx: dict, profile_id: str) -> None:
    row = _env(ctx).stored_inventory_profile(profile_id)
    _assert_renamed_with_selection_unchanged(ctx, row, f"Renamed {profile_id}", _profile_selection(row))


@then(parsers.parse('no product "{product_id}" is stored'))
def then_product_not_stored(ctx: dict, product_id: str) -> None:
    # Both admin envs answer it: AdminInventoryProfileEnv and AdminAccountEnv (the profile modal).
    assert not ctx["env"].product_is_stored(product_id), f"product {product_id!r} was stored"


@then(parsers.parse('the form preselects exactly the properties "{property_ids}"'))
def then_form_preselects(ctx: dict, property_ids: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the form answered {page.status_code}"
    checkboxes = re.findall(r'<input[^>]*name="selected_property_ids" value="([^"]+)"([^>]*)>', page.data.decode())
    assert checkboxes, "the form offers no property checkboxes"
    checked = {value for value, attributes in checkboxes if re.search(r"\bchecked\b", attributes)}
    assert checked == set(comma_list(property_ids)), f"the form preselects {sorted(checked)}"


@then(parsers.parse('the form shows "{choice}" kept as stored, with the status "{status}"'))
def then_form_shows_kept(ctx: dict, choice: str, status: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the form answered {page.status_code}"
    soup = BeautifulSoup(page.data, "html.parser")
    kept = {box["data-kept"]: box["data-kept-status"] for box in soup.find_all("input", attrs={"data-kept": True})}
    assert kept.get(choice) == status, f"the form keeps {kept}"
    assert all(box.has_attr("disabled") for box in soup.find_all("input", attrs={"data-kept": True})), (
        "a kept box is editable"
    )


@then(parsers.parse('the form offers exactly the properties "{property_ids}"'))
def then_form_offers(ctx: dict, property_ids: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the form answered {page.status_code}"
    offered = set(re.findall(r'<input[^>]*name="selected_property_ids" value="([^"]+)"', page.data.decode()))
    assert offered == set(comma_list(property_ids)), f"the form offers {sorted(offered)}"


@then(parsers.parse('the form offers no choice of publisher "{domain}"'))
def then_form_offers_no_choice_of(ctx: dict, domain: str) -> None:
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the form answered {page.status_code}"
    assert domain not in page.data.decode(), f"the form offers a choice of {domain!r}"


@then(parsers.parse('no inventory profile "{profile_id}" is stored'))
def then_profile_not_stored(ctx: dict, profile_id: str) -> None:
    assert _env(ctx).stored_inventory_profile(profile_id) is None, f"profile {profile_id!r} was stored"


@then(parsers.parse('product "{product_id}" stores no format option under the seller\'s own host'))
def then_product_stores_no_agent_host_option(ctx: dict, product_id: str) -> None:
    env = _env(ctx)
    stored = env.stored_product(product_id)
    assert stored is not None, f"product {product_id!r} is not stored"
    host = env.agent_host()
    named = [d for d in stored.format_options or [] if d.get("publisher_domain") == host]
    assert named == [], f"product {product_id!r} stores format options under the seller's own host: {named}"


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


# ── BR-ADMIN-INVENTORY-PROFILES: the inventory page's profile modal ─────

IMAGE_300X250 = [{"format_kind": "image", "params": {"width": 300, "height": 250}}]
FETCH_HEADERS = {"Accept": "application/json"}


def _modal_form(env: Any, profile_id: str, format_options: list[dict[str, Any]] | None) -> dict[str, str]:
    """The FormData ``inventory_profile_editor.html`` builds, field for field."""
    form = {
        "name": profile_id,
        "profile_id": profile_id,
        "description": "",
        "targeted_ad_unit_ids": "[]",
        "targeted_placement_ids": "[]",
        "publisher_properties": json.dumps(
            [{"publisher_domain": "pub.example", "property_tags": ["all_inventory"], "selection_type": "by_tag"}]
        ),
        "property_mode": "all",
    }
    if format_options is not None:
        form["format_options"] = json.dumps(format_options)
    return form


def _image(width: int, height: int) -> list[dict[str, Any]]:
    return [{"format_kind": "image", "params": {"width": width, "height": height}}]


def _post(ctx: dict, path: str, form: dict[str, str], headers: dict[str, str] | None) -> None:
    ctx["admin_response"] = ctx["env"].client.request("post", path, data=form, headers=headers)


@when(parsers.parse('the modal submits a new profile "{profile_id}" declaring an image of {width:d}x{height:d}'))
def when_modal_creates(ctx: dict, profile_id: str, width: int, height: int) -> None:
    env = ctx["env"]
    _post(ctx, env.inventory_profiles_path("add"), _modal_form(env, profile_id, _image(width, height)), FETCH_HEADERS)


@when(parsers.parse('the modal submits a new profile "{profile_id}" declaring no format options'))
def when_modal_creates_without_formats(ctx: dict, profile_id: str) -> None:
    env = ctx["env"]
    _post(ctx, env.inventory_profiles_path("add"), _modal_form(env, profile_id, None), FETCH_HEADERS)


@when(parsers.parse('a browser form submits a new profile "{profile_id}" declaring no format options'))
def when_form_creates_without_formats(ctx: dict, profile_id: str) -> None:
    env = ctx["env"]
    _post(ctx, env.inventory_profiles_path("add"), _modal_form(env, profile_id, None), {"Accept": "text/html"})


@given(parsers.parse('the seller holds a verified property of publisher "{domain}"'))
def given_verified_publisher(ctx: dict, domain: str) -> None:
    """The publisher the modal's selector names, verified, so the form accepts the selector."""
    ctx["env"].authorize_publisher(domain)


@given(parsers.parse('a stored profile "{profile_id}" declaring an image of {width:d}x{height:d}'))
def given_stored_profile(ctx: dict, profile_id: str, width: int, height: int) -> None:
    env = ctx["env"]
    form = _modal_form(env, profile_id, _image(width, height))
    response = env.client.request(
        "post", env.inventory_profiles_path("add"), data=form, headers={"Accept": "text/html"}
    )
    assert response.status_code in (301, 302, 303), f"the Given's profile was not created: {response.status_code}"
    assert env.inventory_profile_format_options(profile_id) == _image(width, height)


@when(parsers.parse('the modal edits the profile "{profile_id}" to declare an image of {width:d}x{height:d}'))
def when_modal_edits(ctx: dict, profile_id: str, width: int, height: int) -> None:
    env = ctx["env"]
    path = env.inventory_profiles_path(f"{env.inventory_profile_row_id(profile_id)}/edit")
    _post(ctx, path, _modal_form(env, profile_id, _image(width, height)), FETCH_HEADERS)


def _agent_host_option(env: Any, option_id: str) -> list[dict[str, Any]]:
    """One image declaration naming the seller's own host as the publisher of *option_id*."""
    return [
        {
            "format_kind": "image",
            "format_option_id": option_id,
            "publisher_domain": env.agent_host(),
            "params": {"width": 300, "height": 250},
        }
    ]


@when(
    parsers.parse(
        'the modal submits a new profile "{profile_id}" declaring format option "{option_id}" under the seller\'s own host'
    )
)
def when_modal_creates_agent_host_option(ctx: dict, profile_id: str, option_id: str) -> None:
    env = ctx["env"]
    form = _modal_form(env, profile_id, _agent_host_option(env, option_id))
    _post(ctx, env.inventory_profiles_path("add"), form, FETCH_HEADERS)


@when(
    parsers.parse(
        'the modal edits the profile "{profile_id}" to declare format option "{option_id}" under the seller\'s own host'
    )
)
def when_modal_edits_agent_host_option(ctx: dict, profile_id: str, option_id: str) -> None:
    env = ctx["env"]
    path = env.inventory_profiles_path(f"{env.inventory_profile_row_id(profile_id)}/edit")
    _post(ctx, path, _modal_form(env, profile_id, _agent_host_option(env, option_id)), FETCH_HEADERS)


@when(
    parsers.parse(
        'the operator submits the product form for "{product_id}" declaring format option "{option_id}" '
        "under the seller's own host"
    )
)
def when_product_form_declares_agent_host_option(ctx: dict, product_id: str, option_id: str) -> None:
    env = ctx["env"]
    form = minimal_product_create_form(
        "pub.example:all_inventory",
        product_id=product_id,
        format_options=json.dumps(_agent_host_option(env, option_id)),
    )
    ctx["admin_response"] = env.client.request("post", env.products_path("add"), data=form)


@then("the refusal names the seller's own host as the format option's publisher")
def then_refusal_names_agent_host(ctx: dict) -> None:
    response = ctx["admin_response"]
    body = response.data.decode()
    assert response.status_code in (200, 400), f"{response.status_code}: {body[:200]!r}"
    host = ctx["env"].agent_host()
    assert f"name {host}, the host this sales agent answers at" in body, (
        f"the refusal does not name {host}: {body[:2000]}"
    )


@when("the inventory page is opened")
def when_inventory_page_opened(ctx: dict) -> None:
    env = ctx["env"]
    ctx["admin_response"] = env.client.request("get", f"/tenant/{env.tenant_id}/inventory")


@then("the modal's answer follows through to a page")
def then_modal_answer_redirect_to_page(ctx: dict) -> None:
    """A success is the redirect ``fetch`` follows, where a refusal is a 400 (next scenario)."""
    response = ctx["admin_response"]
    assert response.status_code in (301, 302, 303), f"{response.status_code}: {response.data[:200]!r}"


@then("the modal is answered 400 with the reason as JSON")
def then_modal_answered_400(ctx: dict) -> None:
    response = ctx["admin_response"]
    assert response.status_code == 400, f"{response.status_code}: {response.data[:200]!r}"
    assert "format_options" in response.get_json()["error"], response.get_json()


@then("the form is redirected")
def then_form_redirected(ctx: dict) -> None:
    assert ctx["admin_response"].status_code in (301, 302, 303)


@then(parsers.parse('the profile "{profile_id}" stores an image of {width:d}x{height:d}'))
def then_profile_stores_image(ctx: dict, profile_id: str, width: int, height: int) -> None:
    stored = ctx["env"].inventory_profile_format_options(profile_id)
    assert stored is not None, f"no profile {profile_id!r} was stored"
    assert [(option["format_kind"], option["params"]["width"], option["params"]["height"]) for option in stored] == [
        ("image", width, height)
    ]


@then(parsers.parse('no profile "{profile_id}" is stored'))
def then_no_profile(ctx: dict, profile_id: str) -> None:
    assert ctx["env"].inventory_profile_format_options(profile_id) is None


@then("the page's modal has a format_options field and no formats field")
def then_modal_fields(ctx: dict) -> None:
    response = ctx["admin_response"]
    assert response.status_code == 200, f"{response.status_code}: {response.data[:200]!r}"
    page = response.data.decode()
    assert 'name="format_options"' in page, "the rendered page has no canonical editor"
    assert 'name="formats"' not in page, "the rendered page still carries the field the handler ignores"
    # What the script appends is text in the served page, and that much can be read here. Running
    # it takes a browser, which this suite does not have.
    assert "formPayload.append('format_options'" in page, "the modal's script does not send format_options"
    assert "formPayload.append('formats'" not in page, "the modal's script still sends formats"
