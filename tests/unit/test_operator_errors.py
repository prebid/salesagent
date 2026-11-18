"""The operator-facing rendering of a caught exception carries none of its text.

``safe_error_message`` / ``json_error`` are what an admin handler answers with
from a blanket ``except``. The contract: the exception (with its traceback) is
logged under a short reference, and the returned text names what failed and the
reference, never anything the exception carries.
"""

import logging
import re

import pytest
from flask import Flask
from sqlalchemy.exc import OperationalError

from src.admin.utils.operator_errors import json_error, safe_error_message

_SECRET = "postgres://admin:hunter2@db/prod DETAIL: Key (email)=(a@b.c)"
_REFERENCE = re.compile(r"\(reference ([0-9a-f]{8})\)\.$")


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:
        return caught


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError(_SECRET),
        ValueError(_SECRET),
        OperationalError("SELECT 1", {"p": _SECRET}, Exception(_SECRET)),
    ],
    ids=["runtime", "value", "database"],
)
def test_no_exception_text_reaches_the_message(exc):
    message = safe_error_message(_raised(exc), "Error saving GAM configuration")

    assert _SECRET not in message
    assert "hunter2" not in message
    assert message.startswith("Error saving GAM configuration. The details have been logged (reference ")
    assert _REFERENCE.search(message)


def test_default_text_without_context():
    assert safe_error_message(_raised(RuntimeError(_SECRET))).startswith("An unexpected error occurred. ")
    db_error = OperationalError("SELECT 1", {}, Exception(_SECRET))
    assert safe_error_message(_raised(db_error)).startswith("A database error occurred. ")


def test_exception_is_logged_with_traceback_under_the_same_reference(caplog):
    exc = _raised(RuntimeError(_SECRET))

    with caplog.at_level(logging.ERROR, logger="src.admin.utils.operator_errors"):
        message = safe_error_message(exc, "Error syncing inventory")

    reference = _REFERENCE.search(message).group(1)
    (record,) = caplog.records
    assert record.getMessage() == f"Error syncing inventory (reference {reference})"
    assert record.exc_info is not None
    assert record.exc_info[1] is exc
    assert _SECRET in caplog.text  # the details stay on the server


def test_references_differ_per_call():
    exc = _raised(RuntimeError("boom"))
    first = _REFERENCE.search(safe_error_message(exc)).group(1)
    second = _REFERENCE.search(safe_error_message(exc)).group(1)
    assert first != second


def test_json_error_keeps_the_route_shape():
    with Flask(__name__).app_context():
        response, status = json_error(_raised(RuntimeError(_SECRET)), "Connection failed", success=False)

    assert status == 500
    body = response.get_json()
    assert set(body) == {"success", "error"}
    assert body["success"] is False
    assert body["error"].startswith("Connection failed. The details have been logged (reference ")
    assert _SECRET not in body["error"]


def test_json_error_status_code_and_extra_fields():
    with Flask(__name__).app_context():
        response, status = json_error(_raised(ValueError(_SECRET)), status_code=400, sync_id="s1", status="failed")

    assert status == 400
    body = response.get_json()
    assert body["sync_id"] == "s1"
    assert body["status"] == "failed"
    assert _SECRET not in body["error"]
