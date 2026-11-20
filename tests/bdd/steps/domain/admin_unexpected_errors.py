"""BDD step definitions for BR-ADMIN-UNEXPECTED-ERRORS.

The fault is the database's own, installed by ``AdminAccountEnv.fail_writes_of``: a trigger
that refuses one row's write with :data:`_FAULT_TEXT`. The routes are driven through
``AdminAccountEnv`` on both admin transports, and the answer the error handler gives is
graded on the response itself; the trigger's own counter shows it was this fault.
"""

from __future__ import annotations

import html
import re

from pytest_bdd import given, parsers, then, when

from src.core.operator_errors import DB_ERROR_OPERATOR_MESSAGE
from tests.bdd.steps.domain.admin_accounts import _datatable_to_form, _env, _require_admin_page

# What the database says when it refuses the write: plainly internal, so its appearance
# anywhere in an answer is a leak. str() of the driver's error carries it, with the driver's
# class name and the failing statement beside it.
_FAULT_TEXT = "BDD admin fault: postgres://admin:s3cr3t-admin-fault@10.0.0.9:5432/prod_shadow"
_DRIVER_TEXT = "psycopg2"

_DATABASE_ERROR_WITH_REFERENCE = re.compile(
    re.escape(DB_ERROR_OPERATOR_MESSAGE) + r"\. The details have been logged \(reference [0-9a-f]{8}\)\."
)


def _answer_text(ctx: dict) -> str:
    return html.unescape(_require_admin_page(ctx).data.decode())


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


@then("the page is the create account form")
def then_page_is_create_account_form(ctx: dict) -> None:
    assert 'name="brand_domain"' in _answer_text(ctx), "The page has no brand_domain input, so it is not the form"


@then("the page is the error page")
def then_page_is_error_page(ctx: dict) -> None:
    response = _require_admin_page(ctx)
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/html"), f"Expected the HTML error page, got {content_type!r}"
    assert "Go Home" in _answer_text(ctx), "The page is not error.html (it has no way home)"


@then("the page shows a database error with a reference")
def then_page_shows_database_error(ctx: dict) -> None:
    page = _answer_text(ctx)
    assert _DATABASE_ERROR_WITH_REFERENCE.search(page), (
        f"The page does not show {DB_ERROR_OPERATOR_MESSAGE!r} with a reference: {page[:2000]!r}"
    )


@then("the JSON error is a database error with a reference")
def then_json_error_is_database_error(ctx: dict) -> None:
    error = _require_admin_page(ctx).get_json().get("error")
    assert isinstance(error, str) and _DATABASE_ERROR_WITH_REFERENCE.fullmatch(error), (
        f"Expected {DB_ERROR_OPERATOR_MESSAGE!r} with a reference, got {error!r}"
    )


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
