"""Steps for the inventory page's profile modal, through the admin env's own transports."""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.helpers.admin_product_form import minimal_product_create_form

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


@then(parsers.parse('no product "{product_id}" is stored'))
def then_no_product(ctx: dict, product_id: str) -> None:
    assert not ctx["env"].product_is_stored(product_id), f"product {product_id!r} was stored"


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
def then_profile_stores(ctx: dict, profile_id: str, width: int, height: int) -> None:
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
