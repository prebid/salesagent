"""BDD step definitions for BR-ADMIN-OPERATOR-REFUSALS.

The routes are driven through ``AdminAccountEnv`` on both admin transports. The text a
refusal is answered with is read off its class, so no scenario restates it.
"""

from __future__ import annotations

import html
import json

from pytest_bdd import given, parsers, then, when

from src.core.http_utils import InvalidVirtualHost
from src.core.operator_errors import InvalidFormInput, OperatorRefusal
from src.services.auth_config_service import DiscoveryUrlRequired
from tests.bdd.steps.domain.admin_accounts import _datatable_to_form, _env, _require_admin_page
from tests.bdd.steps.domain.admin_inventory_profiles import IMAGE_300X250, _modal_form
from tests.helpers.admin_product_form import minimal_product_create_form

_REFUSALS: dict[str, type[OperatorRefusal]] = {
    cls.__name__: cls for cls in (InvalidVirtualHost, DiscoveryUrlRequired, InvalidFormInput)
}


def _submitted(ctx: dict, datatable: list) -> dict[str, str]:
    """The table as a request body, remembered so a Then can check the answer does not echo it."""
    ctx["admin_submitted"] = _datatable_to_form(datatable)
    return ctx["admin_submitted"]


@given(parsers.parse('the operator is signed in as "{email}", an admin of the tenant'))
def given_signed_in_as_tenant_admin(ctx: dict, email: str) -> None:
    _env(ctx).authenticate_as_tenant_user(email)


@when("the operator submits the create tenant form from the create tenant page with:")
def when_submit_create_tenant_from_page(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_tenant(_submitted(ctx, datatable), from_create_page=True)


@when("the operator submits the create tenant form with no page to go back to, with:")
def when_submit_create_tenant_without_page(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_tenant(_submitted(ctx, datatable), from_create_page=False)


@when("the operator saves an OIDC configuration with:")
def when_save_oidc_config(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).post_oidc_config(_submitted(ctx, datatable))


@when("the tenant management API is asked to create a tenant with:")
def when_management_api_creates_tenant(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).post_management_api_tenant(_submitted(ctx, datatable))


@when("the operator submits the add product form from its page with:")
def when_submit_add_product_from_page(ctx: dict, datatable: list) -> None:
    """The add form at its minimum, declaring an image so the form reaches what the table sets."""
    form = minimal_product_create_form(
        "pub-psatest.com:all_inventory", format_options=json.dumps(IMAGE_300X250), **_submitted(ctx, datatable)
    )
    ctx["admin_page"] = _env(ctx).submit_create_product(form)


@when(
    parsers.parse(
        'the operator saves the edit form of profile "{profile_id}" from its page with format options "{raw}"'
    )
)
def when_save_profile_edit_with_raw_format_options(ctx: dict, profile_id: str, raw: str) -> None:
    env = _env(ctx)
    form = _modal_form(env, profile_id, None) | {"format_options": raw}
    ctx["admin_submitted"] = form
    ctx["admin_page"] = env.submit_inventory_profile_edit(profile_id, form)


#: A row id no profile is stored under: ids come from a sequence that no scenario runs this far.
_ABSENT_PROFILE_ROW = 2_000_000_000


@when("the inventory page deletes a profile that does not exist")
def when_delete_absent_profile(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).delete_inventory_profile_row(_ABSENT_PROFILE_ROW)


@then("the page is the add product form")
def then_page_is_add_product_form(ctx: dict) -> None:
    page = _require_admin_page(ctx).data.decode()
    assert "Add New Product" in page, "The page is not the add product form"


@then("the answer does not carry the JSON parser's text")
def then_answer_omits_json_parser_text(ctx: dict) -> None:
    """What ``json`` says about the submitted format options, derived by parsing them here."""
    try:
        json.loads(ctx["admin_submitted"]["format_options"])
    except json.JSONDecodeError as e:
        parser_text = e.msg
    else:
        raise AssertionError("the submitted format options parse, so there is no parser text to look for")
    answer = html.unescape(_require_admin_page(ctx).data.decode())
    assert parser_text not in answer, f"The answer carries the parser's text {parser_text!r}"


@then("the page is the create tenant form")
def then_page_is_create_tenant_form(ctx: dict) -> None:
    page = _require_admin_page(ctx).data.decode()
    assert 'name="virtual_host"' in page, "The page has no virtual_host input, so it is not the create tenant form"


@then(parsers.parse('the page shows the "{refusal}" refusal'))
def then_page_shows_refusal(ctx: dict, refusal: str) -> None:
    message = _REFUSALS[refusal].MESSAGE
    page = html.unescape(_require_admin_page(ctx).data.decode())
    assert message in page, f"The page does not show {refusal}.MESSAGE ({message!r})"


@then(parsers.parse('the JSON error is the "{refusal}" refusal'))
def then_json_error_is_refusal(ctx: dict, refusal: str) -> None:
    error = _require_admin_page(ctx).get_json().get("error")
    assert error == _REFUSALS[refusal].MESSAGE, f"Expected {refusal}.MESSAGE, got {error!r}"


@then("the answer does not echo the submitted virtual host")
def then_answer_does_not_echo_virtual_host(ctx: dict) -> None:
    submitted = ctx["admin_submitted"]["virtual_host"]
    answer = html.unescape(_require_admin_page(ctx).data.decode())
    assert submitted not in answer, f"The answer echoes the refused value {submitted!r}"
