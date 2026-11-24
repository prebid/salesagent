"""BDD step definitions for BR-ADMIN-OPERATOR-REFUSALS.

The routes are driven through ``AdminAccountEnv`` on both admin transports. The text a
refusal is answered with is read off its class, so no scenario restates it.
"""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import (
    _answer_text,
    _datatable_to_form,
    _env,
    _json_error,
    _require_admin_page,
)
from tests.bdd.steps.domain.admin_inventory_profiles import IMAGE_300X250, _modal_form
from tests.bdd.steps.domain.admin_unexpected_errors import _other_tenant_id, script_accept
from tests.harness.admin_client import BROWSER_PAGE_HEADERS
from tests.helpers.admin_product_form import minimal_product_create_form, minimal_product_edit_form
from tests.helpers.operator_refusals import operator_refusal_classes


def _submitted(ctx: dict, datatable: list) -> dict[str, str]:
    """The table as a request body, remembered so a Then can check the answer does not echo it."""
    ctx["admin_submitted"] = _datatable_to_form(datatable)
    return ctx["admin_submitted"]


@given(parsers.parse('the operator is signed in as "{email}", an admin of the tenant'))
def given_signed_in_as_tenant_admin(ctx: dict, email: str) -> None:
    _env(ctx).authenticate_as_tenant_user(email)


@when("the operator submits the create tenant form from the create tenant page with:")
def when_submit_create_tenant_from_page(ctx: dict, datatable: list) -> None:
    env = _env(ctx)
    ctx["admin_page"] = env.submit_create_tenant(_submitted(ctx, datatable), from_page=env.CREATE_TENANT_PAGE)


@when("the operator submits the create tenant form with no page to go back to, with:")
def when_submit_create_tenant_without_page(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_tenant(_submitted(ctx, datatable), from_page=None)


#: How a browser that asks for a page in only one of the two ways sends its form post.
_ONE_WAY_BROWSERS: dict[str, dict[str, str | None]] = {
    "a browser that sends only Sec-Fetch-Mode": {"Sec-Fetch-Mode": "navigate", "Accept": None},
    "a browser that sends only Accept": {"Accept": BROWSER_PAGE_HEADERS["Accept"]},
}


@when(
    parsers.re(
        r"(?P<browser>a browser that sends only (?:Sec-Fetch-Mode|Accept)) submits the create tenant form "
        r"from the create tenant page with:"
    )
)
def when_one_way_browser_submits_create_tenant(ctx: dict, browser: str, datatable: list) -> None:
    env = _env(ctx)
    ctx["admin_page"] = env.submit_create_tenant(
        _submitted(ctx, datatable), from_page=env.CREATE_TENANT_PAGE, browser_headers=_ONE_WAY_BROWSERS[browser]
    )


@when(parsers.parse('a script saves the adapter config with no body, sending Accept "{accept}"'))
def when_script_saves_adapter_config(ctx: dict, accept: str) -> None:
    ctx["admin_page"] = _env(ctx).save_adapter_config_as_script(script_accept(accept))


@when(parsers.parse('the operator submits the create tenant form from the page "{page}" with:'))
def when_submit_create_tenant_from_named_page(ctx: dict, page: str, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_tenant(_submitted(ctx, datatable), from_page=page)


@given("the tenant is onboarded to Google Ad Manager")
def given_tenant_on_gam(ctx: dict) -> None:
    _env(ctx).onboard_gam()


@given(parsers.parse('the tenant is onboarded to Google Ad Manager with the service account "{email}"'))
def given_tenant_on_gam_with_service_account(ctx: dict, email: str) -> None:
    _env(ctx).onboard_gam(service_account_email=email)


@given(parsers.parse('the tenant\'s inventory sync "{sync_id}" is running'))
def given_inventory_sync_running(ctx: dict, sync_id: str) -> None:
    _env(ctx).seed_running_inventory_sync(sync_id)


@given(parsers.parse('a product "{product_id}" built from the profile "{profile_id}"'))
def given_product_built_from_profile(ctx: dict, product_id: str, profile_id: str) -> None:
    _env(ctx).link_product_to_inventory_profile(product_id, profile_id)


@given("that tenant's inventory sync is running")
def given_other_tenant_sync_running(ctx: dict) -> None:
    tenant_id = _other_tenant_id(ctx)
    ctx["admin_running_sync_id"] = f"sync_running_{tenant_id}"
    _env(ctx).seed_running_inventory_sync(ctx["admin_running_sync_id"], tenant_id=tenant_id)


@then("the JSON response names that tenant's running sync")
def then_json_names_running_sync(ctx: dict) -> None:
    sync_id = _require_admin_page(ctx).get_json().get("sync_id")
    assert sync_id == ctx["admin_running_sync_id"], f"Expected the running sync's id, got {sync_id!r}"


@given("that tenant has a product")
def given_other_tenant_has_product(ctx: dict) -> None:
    tenant_id = _other_tenant_id(ctx)
    ctx["admin_product_id"] = f"edit_probe_{tenant_id}"
    _env(ctx).seed_product(ctx["admin_product_id"], tenant_id=tenant_id)


@when("the operator submits that product's edit form from its page with:")
def when_submit_product_edit_from_page(ctx: dict, datatable: list) -> None:
    form = minimal_product_edit_form(**_submitted(ctx, datatable))
    ctx["admin_page"] = _env(ctx).submit_product_edit(_other_tenant_id(ctx), ctx["admin_product_id"], form)


@when("the settings page creates a GAM service account for that tenant")
def when_create_gam_service_account_for_other_tenant(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).create_gam_service_account(_other_tenant_id(ctx))


@when(parsers.parse('the settings page detects that tenant\'s GAM network with the refresh token "{token}"'))
def when_detect_gam_network(ctx: dict, token: str) -> None:
    ctx["admin_page"] = _env(ctx).detect_gam_network(_other_tenant_id(ctx), token)


@when("the settings page saves that tenant's adapter without naming one")
def when_save_adapter_without_naming_one(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).post_adapter_update(_other_tenant_id(ctx), {})


@when(parsers.parse('the sync API is asked for a "{sync_type}" sync of that tenant'))
def when_sync_api_asked_for_sync_type(ctx: dict, sync_type: str) -> None:
    ctx["admin_page"] = _env(ctx).trigger_sync_by_api(_other_tenant_id(ctx), {"sync_type": sync_type})


@when("the settings page starts an inventory sync")
def when_start_inventory_sync(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).start_inventory_sync()


@when("the settings page creates a GAM service account")
def when_create_gam_service_account(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).create_gam_service_account()


@when(parsers.parse('the adapter settings save a "{adapter_type}" config with "{key}" set to "{value}"'))
def when_save_adapter_config(ctx: dict, adapter_type: str, key: str, value: str) -> None:
    ctx["admin_submitted"] = {key: value}
    ctx["admin_page"] = _env(ctx).post_adapter_config({"adapter_type": adapter_type, "config": {key: value}})


@when(parsers.parse('the inventory page deletes the profile "{profile_id}"'))
def when_delete_profile_by_fetch(ctx: dict, profile_id: str) -> None:
    env = _env(ctx)
    ctx["admin_page"] = env.delete_inventory_profile_row(env.inventory_profile_row_id(profile_id))


@when(parsers.parse('a browser form on the profiles list deletes the profile "{profile_id}"'))
def when_delete_profile_by_form(ctx: dict, profile_id: str) -> None:
    ctx["admin_page"] = _env(ctx).submit_inventory_profile_delete(profile_id)


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


#: A row id nothing is stored under: ids come from a sequence that no scenario runs this far.
_ABSENT_ROW_ID = 2_000_000_000


@when("the inventory page deletes a profile that does not exist")
def when_delete_absent_profile(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).delete_inventory_profile_row(_ABSENT_ROW_ID)


#: For each kind of record, the request a page's script sends for one, as (method, path, JSON
#: body), naming a record no scenario stores. ``{tenant}`` is the signed-in tenant.
_ABSENT_RECORD_REQUESTS: dict[str, tuple[str, str, dict[str, str] | None]] = {
    "principal": ("get", "/tenant/{tenant}/principal/bdd_absent_principal", None),
    "product": ("get", "/adapters/mock/config/{tenant}/bdd_absent_product", None),
    "creative": ("post", "/tenant/{tenant}/creatives/review/bdd_absent_creative/approve", {}),
    "signals agent": ("delete", f"/tenant/{{tenant}}/signals-agents/{_ABSENT_ROW_ID}/delete", None),
    "workflow step": ("post", "/tenant/{tenant}/workflows/bdd_absent_context/steps/bdd_absent_step/approve", {}),
    "GAM order": ("get", "/api/tenant/{tenant}/orders/bdd_absent_order", None),
    "sync job": ("get", "/tenant/{tenant}/gam/sync-status/bdd_absent_sync", None),
}


@when(parsers.parse("a page's script asks for a {record} that does not exist"))
def when_script_asks_for_absent_record(ctx: dict, record: str) -> None:
    env = _env(ctx)
    method, path, body = _ABSENT_RECORD_REQUESTS[record]
    ctx["admin_page"] = env.fetch(method, path.format(tenant=env.tenant_id), body)


@then("the answer does not carry the JSON parser's text")
def then_answer_omits_json_parser_text(ctx: dict) -> None:
    """What ``json`` says about the submitted format options, derived by parsing them here."""
    try:
        json.loads(ctx["admin_submitted"]["format_options"])
    except json.JSONDecodeError as e:
        parser_text = e.msg
    else:
        raise AssertionError("the submitted format options parse, so there is no parser text to look for")
    assert parser_text not in _answer_text(ctx), f"The answer carries the parser's text {parser_text!r}"


@then(parsers.parse('the page shows the "{refusal}" refusal'))
def then_page_shows_refusal(ctx: dict, refusal: str) -> None:
    message = operator_refusal_classes()[refusal].MESSAGE
    assert message in _answer_text(ctx), f"The page does not show {refusal}.MESSAGE ({message!r})"


@then(parsers.parse('the JSON error is the "{refusal}" refusal'))
def then_json_error_is_refusal(ctx: dict, refusal: str) -> None:
    error = _json_error(ctx)
    assert error == operator_refusal_classes()[refusal].MESSAGE, f"Expected {refusal}.MESSAGE, got {error!r}"


@then(parsers.parse('the answer does not echo the submitted "{field}"'))
def then_answer_does_not_echo_submitted(ctx: dict, field: str) -> None:
    submitted = ctx["admin_submitted"][field]
    assert submitted not in _answer_text(ctx), f"The answer echoes the refused value {submitted!r}"


@then("the JSON issues are:")
def then_json_issues_are(ctx: dict, datatable: list) -> None:
    """Each refused field and its text, in order, as ``InvalidFormInput`` answers a JSON caller."""
    headers = [str(h) for h in datatable[0]]
    expected = [dict(zip(headers, (str(cell) for cell in row), strict=True)) for row in datatable[1:]]
    issues = _require_admin_page(ctx).get_json().get("issues")
    assert issues == expected, f"Expected issues {expected!r}, got {issues!r}"


@given(parsers.parse('another tenant of this seller, with the Slack webhook "{url}"'))
def given_another_tenant_with_slack_webhook(ctx: dict, url: str) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant(slack_webhook_url=url)


@given(parsers.parse('another tenant of this seller, whose AI configuration names "{provider}" with no key'))
def given_another_tenant_with_keyless_ai(ctx: dict, provider: str) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant(ai_config={"provider": provider})


@given("the operator signed in through SSO")
def given_operator_signed_in_through_sso(ctx: dict) -> None:
    env = _env(ctx)
    env.authenticate(env.tenant_id, auth_method="oidc")


@when("the settings page sends that tenant's Slack test message")
def when_send_slack_test(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "test_slack")


@when("the settings page asks for that tenant's DNS widget token")
def when_ask_for_dns_widget_token(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "settings/approximated-token")


@when("the users page disables that tenant's setup mode")
def when_disable_setup_mode(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "users/disable-setup-mode")


@when(parsers.parse('the creative review page asks for an AI review of that tenant\'s creative "{creative_id}"'))
def when_ask_for_ai_review(ctx: dict, creative_id: str) -> None:
    path = f"creatives/review/{creative_id}/ai-review"
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), path)


@when("the settings page resets that tenant's stuck inventory sync")
def when_reset_stuck_sync(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "gam/reset-stuck-sync")


@when("the admin sends a status change request for an account that does not exist")
def when_status_change_for_absent_account(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).post_status_change("acc_bdd_absent", "active")


@when("the users page adds an authorized domain without naming one")
def when_add_domain_without_naming_one(ctx: dict) -> None:
    env = _env(ctx)
    ctx["admin_page"] = env.fetch_tenant_route(env.tenant_id, "users/domains", {"domain": "  "})


@when("the settings page saves that tenant's GAM configuration with:")
def when_save_gam_configuration(ctx: dict, datatable: list) -> None:
    body = _submitted(ctx, datatable)
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "gam/configure", body)


@then("the JSON details are:")
def then_json_details_are(ctx: dict, datatable: list) -> None:
    """The lines a refusal answers a JSON caller with beside its ``MESSAGE``, in order."""
    expected = [str(row[0]) for row in datatable[1:]]
    details = _require_admin_page(ctx).get_json().get("details")
    assert details == expected, f"Expected details {expected!r}, got {details!r}"


@given(parsers.parse('another tenant of this seller, authorizing the domain "{domain}"'))
def given_another_tenant_authorizing_domain(ctx: dict, domain: str) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant(authorized_domains=[domain])


@when("the settings page sends the Slack test message of a tenant that does not exist")
def when_send_slack_test_for_absent_tenant(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route("bdd_absent_tenant", "test_slack")


@when(parsers.parse('the users page adds the authorized domain "{domain}" to that tenant'))
def when_add_authorized_domain(ctx: dict, domain: str) -> None:
    ctx["admin_page"] = _env(ctx).fetch_tenant_route(_other_tenant_id(ctx), "users/domains", {"domain": domain})


# ── Media buy approval ──


def _parsed_size(size: str) -> tuple[int, int]:
    width, height = size.split("x")
    return int(width), int(height)


@given(
    parsers.re(
        r"another tenant of this seller, holding a media buy awaiting approval"
        r"(?: whose product accepts images of (?P<size>\d+x\d+))?$"
    )
)
def given_other_tenant_with_buy_awaiting_approval(ctx: dict, size: str | None) -> None:
    seeded = _env(ctx).seed_media_buy_awaiting_approval(accepted_image_size=_parsed_size(size) if size else None)
    ctx["admin_other_tenant_id"] = seeded.tenant_id
    ctx["admin_pending_buy"] = seeded


@given(parsers.parse('that media buy\'s package is assigned the approved {size} image creative "{creative_id}"'))
def given_pending_buy_assigned_image_creative(ctx: dict, size: str, creative_id: str) -> None:
    _env(ctx).assign_image_creative_to_pending_buy(ctx["admin_pending_buy"], creative_id, size=_parsed_size(size))


@given("that tenant's ad server fails every media buy it is asked to create")
def given_other_tenant_ad_server_fails_creation(ctx: dict) -> None:
    _env(ctx).fail_ad_server_creation(_other_tenant_id(ctx))


@when("the workflow page approves that media buy's approval step")
def when_workflow_page_approves_step(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).approve_workflow_step(ctx["admin_pending_buy"])


def _assert_details_follow_code_line(ctx: dict, code: str, rest: list[str]) -> None:
    """The refusal's details: the typed error's code line (its code and the code table's message), then *rest*."""
    from src.core.errors.codes import CODE_TABLE

    details = _require_admin_page(ctx).get_json().get("details")
    expected = [f"{code}: {CODE_TABLE[code].message}", *rest]
    assert details == expected, f"Expected details {expected!r}, got {details!r}"


@then(parsers.parse('the JSON details are the "{code}" error\'s line, then:'))
def then_json_details_code_line_then(ctx: dict, code: str, datatable: list) -> None:
    _assert_details_follow_code_line(ctx, code, [str(row[0]) for row in datatable[1:]])


@then(parsers.parse('the JSON details are only the "{code}" error\'s line'))
def then_json_details_only_code_line(ctx: dict, code: str) -> None:
    _assert_details_follow_code_line(ctx, code, [])


@then("that media buy still awaits approval")
def then_pending_buy_still_awaits_approval(ctx: dict) -> None:
    status = _env(ctx).media_buy_status(ctx["admin_pending_buy"].media_buy_id)
    assert status == "pending_approval", f"Expected the media buy to await approval, it is {status!r}"


@given(parsers.parse('the deployment\'s super-admin domain is "{domain}"'))
def given_super_admin_domain(ctx: dict, domain: str) -> None:
    _env(ctx).name_super_admin_domain(domain)


@when(parsers.parse('the settings page\'s form adds the authorized domain "{domain}" to that tenant, from no page'))
def when_settings_form_adds_authorized_domain(ctx: dict, domain: str) -> None:
    ctx["admin_page"] = _env(ctx).submit_tenant_form(_other_tenant_id(ctx), "settings/domains/add", {"domain": domain})


@then(parsers.parse('that tenant authorizes only the domain "{domain}"'))
def then_tenant_authorizes_only(ctx: dict, domain: str) -> None:
    stored = _env(ctx).authorized_domains_of(_other_tenant_id(ctx))
    assert stored == [domain], f"Expected the tenant to authorize only {domain!r}, it authorizes {stored!r}"
