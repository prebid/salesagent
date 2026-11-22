"""BDD step definitions for BR-ADMIN-UNEXPECTED-ERRORS.

The fault is the database's own, installed by ``AdminAccountEnv.fail_writes_of``: a trigger
that refuses one row's write with :data:`_FAULT_TEXT`. The routes are driven through
``AdminAccountEnv`` on both admin transports, and the answer the error handler gives is
graded on the response itself; the trigger's own counter shows it was this fault.
"""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, then, when

from src.core.operator_errors import DB_ERROR_OPERATOR_MESSAGE, GENERIC_OPERATOR_MESSAGE, logged_failure_pattern
from tests.bdd.steps.domain.admin_accounts import (
    _answer_text,
    _datatable_to_form,
    _env,
    _json_error,
    _require_admin_page,
)

# What the database says when it refuses the write: plainly internal, so its appearance
# anywhere in an answer is a leak. str() of the driver's error carries it, with the driver's
# class name and the failing statement beside it.
_FAULT_TEXT = "BDD admin fault: postgres://admin:s3cr3t-admin-fault@10.0.0.9:5432/prod_shadow"
_DRIVER_TEXT = "psycopg2"


def _assert_json_error_is_logged_failure(ctx: dict, context: str) -> None:
    """The JSON ``error`` is exactly what ``safe_error_message`` answers for *context*, with a reference."""
    error = _json_error(ctx)
    assert isinstance(error, str) and logged_failure_pattern(context).fullmatch(error), (
        f"Expected {context!r} with a reference, got {error!r}"
    )


@given(parsers.parse('the database refuses to store an account named "{name}"'))
def given_database_refuses_account_named(ctx: dict, name: str) -> None:
    _env(ctx).fail_writes_of("accounts", column="name", value=name, message=_FAULT_TEXT)


@given(parsers.parse('the database refuses to change account "{name}"'))
def given_database_refuses_account_change(ctx: dict, name: str) -> None:
    account_id = ctx[f"account_id:{name}"]
    _env(ctx).fail_writes_of("accounts", column="account_id", value=account_id, message=_FAULT_TEXT)


@when("the operator submits the create account form from the create account page with:")
def when_submit_create_account_from_page(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_account(_datatable_to_form(datatable), from_create_page=True)


@when("the operator submits the create account form with no page to go back to, with:")
def when_submit_create_account_without_page(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).submit_create_account(_datatable_to_form(datatable), from_create_page=False)


@then("the page is the error page")
def then_page_is_error_page(ctx: dict) -> None:
    response = _require_admin_page(ctx)
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/html"), f"Expected the HTML error page, got {content_type!r}"
    assert "Go Home" in _answer_text(ctx), "The page is not error.html (it has no way home)"


@then("the page shows a database error with a reference")
def then_page_shows_database_error(ctx: dict) -> None:
    page = _answer_text(ctx)
    assert logged_failure_pattern(DB_ERROR_OPERATOR_MESSAGE).search(page), (
        f"The page does not show {DB_ERROR_OPERATOR_MESSAGE!r} with a reference: {page[:2000]!r}"
    )


@then("the JSON error is a database error with a reference")
def then_json_error_is_database_error(ctx: dict) -> None:
    _assert_json_error_is_logged_failure(ctx, DB_ERROR_OPERATOR_MESSAGE)


@then("the answer does not carry the database fault's text")
def then_answer_does_not_carry_fault(ctx: dict) -> None:
    answer = _answer_text(ctx)
    assert _FAULT_TEXT not in answer, "The answer carries the database's own error text"
    assert _DRIVER_TEXT not in answer, "The answer carries the database driver's error"


@then("the database refused the write once")
def then_database_refused_once(ctx: dict) -> None:
    raised = _env(ctx).database_faults_raised()
    assert raised == 1, f"The injected fault refused {raised} writes; the route did not fail on it"


@then(parsers.parse('the database contains no account named "{name}"'))
def then_no_account_named(ctx: dict, name: str) -> None:
    assert _env(ctx).get_account_from_db(name=name) is None, f"An account named {name!r} was stored"


# ── API callers, the business-rules fetch, sync jobs ────────────────────────


def _other_tenant_id(ctx: dict) -> str:
    return ctx["admin_other_tenant_id"]


@given("another tenant of this seller")
def given_another_tenant(ctx: dict) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant()


@given("another tenant of this seller, onboarded to Google Ad Manager")
def given_another_gam_tenant(ctx: dict) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant(gam=True)


@given("another tenant of this seller, with no ad server chosen")
def given_another_tenant_without_ad_server(ctx: dict) -> None:
    ctx["admin_other_tenant_id"] = _env(ctx).seed_tenant(ad_server=None)


def script_accept(accept: str) -> str | None:
    """The ``Accept`` a scenario's "sending Accept" names: ``(none)`` sends no ``Accept`` at all."""
    return None if accept == "(none)" else accept


@when(parsers.parse('a script starts that tenant\'s inventory sync with no body, sending Accept "{accept}"'))
def when_script_starts_inventory_sync(ctx: dict, accept: str) -> None:
    ctx["admin_page"] = _env(ctx).start_inventory_sync_as_script(_other_tenant_id(ctx), script_accept(accept))


@given(parsers.parse("the AI provider answers the connection test with HTTP {status:d}"))
def given_ai_provider_answers_with_status(ctx: dict, status: int) -> None:
    ctx["admin_ai_api_key"] = _env(ctx).script_ai_connection_test({"fail_status": status})


@when("the settings page tests the AI connection with that key")
def when_test_ai_connection_with_scripted_key(ctx: dict) -> None:
    body = {"provider": "gemini", "api_key": ctx["admin_ai_api_key"]}
    ctx["admin_page"] = _env(ctx).post_ai_connection_test(body)


@given("the database refuses to change that tenant")
def given_database_refuses_tenant_change(ctx: dict) -> None:
    _env(ctx).fail_writes_of("tenants", column="tenant_id", value=_other_tenant_id(ctx), message=_FAULT_TEXT)


@given(parsers.parse('the database refuses to store the order name template "{template}"'))
def given_database_refuses_order_name_template(ctx: dict, template: str) -> None:
    _env(ctx).fail_writes_of("tenants", column="order_name_template", value=template, message=_FAULT_TEXT)


@given("the database refuses to mark that tenant's sync jobs running")
def given_database_refuses_running_sync_jobs(ctx: dict) -> None:
    where = {"tenant_id": _other_tenant_id(ctx), "status": "running"}
    _env(ctx).fail_writes_of_rows("sync_jobs", where=where, message=_FAULT_TEXT)


@when("a script deletes that tenant through the tenant management API, accepting any format")
def when_script_deletes_tenant(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).delete_tenant_by_management_api(_other_tenant_id(ctx))


@when("the settings page posts the business rules form by fetch with:")
def when_settings_page_posts_business_rules(ctx: dict, datatable: list) -> None:
    ctx["admin_page"] = _env(ctx).post_business_rules_by_fetch(_datatable_to_form(datatable))


@when("the sync API is asked to sync that tenant's inventory")
def when_sync_api_syncs_inventory(ctx: dict) -> None:
    body = {"sync_type": "inventory", "force": True}
    ctx["admin_page"] = _env(ctx).trigger_sync_by_api(_other_tenant_id(ctx), body)


@then("the JSON error is that tenant's inventory sync failure with a reference")
def then_json_error_is_sync_failure(ctx: dict) -> None:
    # The context recording_failure's caller names (GAMSyncManager.sync_inventory).
    _assert_json_error_is_logged_failure(ctx, f"Inventory sync failed for tenant {_other_tenant_id(ctx)}")


@then("the sync API reports the answered sync job as failed, with the answered error")
def then_sync_job_recorded_failed(ctx: dict) -> None:
    answer = _require_admin_page(ctx).get_json()
    status = _env(ctx).sync_status_by_api(answer["sync_id"])
    assert status.status_code == 200, f"The sync API does not know job {answer['sync_id']!r}: {status.status_code}"
    job = status.get_json()
    assert job["status"] == "failed", f"The job is {job['status']!r}, so the failure was not recorded"
    assert job.get("error") == answer["error"], f"The job stores {job.get('error')!r}, not {answer['error']!r}"


@when(parsers.parse('the settings page tests the AI connection with the JSON body "{body}"'))
def when_test_ai_connection_with_body(ctx: dict, body: str) -> None:
    ctx["admin_page"] = _env(ctx).post_ai_connection_test(json.loads(body))


@then("the JSON error is an unexpected error with a reference")
def then_json_error_is_unexpected_error(ctx: dict) -> None:
    _assert_json_error_is_logged_failure(ctx, GENERIC_OPERATOR_MESSAGE)


# ── The audit row of a failed action, as the activity feed shows it ─────────


def _failed_create_account_activities(ctx: dict) -> list[dict]:
    activities = _require_admin_page(ctx).get_json()["activities"]
    return [a for a in activities if a["operation"].endswith("create_account") and not a["success"]]


@when("the operator reads the tenant's recent activity")
def when_read_recent_activity(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).get_recent_activities()


@then("the activity feed shows the create account action as failed with the fixed audit text")
def then_feed_shows_failed_action(ctx: dict) -> None:
    from src.admin.utils.audit_decorator import _RAISED_MESSAGE

    failed = _failed_create_account_activities(ctx)
    assert len(failed) == 1, f"Expected one failed create_account activity, got {failed!r}"
    assert failed[0]["full_details"].get("error_details") == _RAISED_MESSAGE, (
        f"Expected the audit row's fixed text {_RAISED_MESSAGE!r}, got {failed[0]['full_details']!r}"
    )


@then("the activity feed does not carry the database fault's text")
def then_feed_does_not_carry_fault(ctx: dict) -> None:
    feed = json.dumps(_require_admin_page(ctx).get_json())
    assert _FAULT_TEXT not in feed, "The activity feed carries the database's own error text"
    assert _DRIVER_TEXT not in feed, "The activity feed carries the database driver's error"
