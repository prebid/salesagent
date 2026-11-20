"""The operator-facing rendering of a caught exception carries none of its text.

``safe_error_message`` is what the admin error handler answers an unexpected
exception with. The contract: the exception (with its traceback) is logged under a
short reference, and the returned text names what failed and the reference, never
anything the exception carries. Also here: the other pure functions the handler's
answer is built from.
"""

import logging
import re

import pytest
from sqlalchemy.exc import OperationalError

from src.admin.utils.operator_errors import _same_site_referrer
from src.core.operator_errors import safe_error_message

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

    with caplog.at_level(logging.ERROR, logger="src.core.operator_errors"):
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


@pytest.mark.parametrize(
    ("referrer", "method", "full_path", "expected"),
    [
        ("http://localhost/tenant/t1/products?page=2", "POST", "/x?", "/tenant/t1/products?page=2"),
        ("https://localhost/tenant/t1/settings", "POST", "/x?", "/tenant/t1/settings"),
        # A form that posts to its own page goes back to it; a GET of the failed URL would loop.
        ("http://localhost/create_tenant", "POST", "/create_tenant?", "/create_tenant"),
        ("http://localhost/tenant/t1/settings", "GET", "/tenant/t1/settings?", None),
        (None, "POST", "/x?", None),
        ("", "POST", "/x?", None),
        ("https://elsewhere.example/page", "POST", "/x?", None),
        ("javascript://localhost/page", "POST", "/x?", None),
        ("http://localhost//evil.example/page", "POST", "/x?", None),
        ("http://localhost/\\evil.example/page", "POST", "/x?", None),
        ("http://localhost/page?next=\\evil.example", "POST", "/x?", None),
    ],
    ids=[
        "path-and-query",
        "https",
        "form-posting-to-its-own-page",
        "failed-get-of-itself",
        "no-referrer",
        "empty-referrer",
        "other-host",
        "other-scheme",
        "double-slash-path",
        "backslash-path",
        "backslash-query",
    ],
)
def test_same_site_referrer(referrer, method, full_path, expected):
    assert _same_site_referrer(referrer, "localhost", method, full_path) == expected


def test_an_operator_refusal_must_declare_its_message():
    from src.core.operator_errors import OperatorRefusal

    with pytest.raises(TypeError, match="MESSAGE"):

        class NoMessage(OperatorRefusal):
            pass


def test_operator_refusals_are_not_value_errors():
    """A broad ``except ValueError`` must not absorb a refusal meant to be answered by type.

    Every refusal the admin app and its services declare, collected rather than listed, so a
    new one is checked without anyone adding it here. ``InvalidVirtualHost`` is the one
    exception, kept a ``ValueError`` for the ORM validator (see the test below).
    """
    from src.core.http_utils import InvalidVirtualHost
    from tests.helpers.operator_refusals import operator_refusal_classes

    refusals = operator_refusal_classes()
    # Refusals from a service a route imports inside the function, so collecting from the
    # app's imports alone would miss them, and from the admin app itself.
    assert {"SyncAlreadyRunning", "TenantAdapterConfigMissing", "InventoryProfileInUse"} <= refusals.keys(), refusals
    for refusal in refusals.values():
        if refusal is not InvalidVirtualHost:
            assert not issubclass(refusal, ValueError), refusal
        assert isinstance(refusal.MESSAGE, str) and refusal.MESSAGE, refusal


def test_an_invalid_virtual_host_is_answered_with_fixed_text():
    """The refusal's own text quotes the value; responses use MESSAGE, which does not."""
    from src.core.http_utils import InvalidVirtualHost, validate_virtual_host

    with pytest.raises(InvalidVirtualHost) as raised:
        validate_virtual_host("https://evil.example/<script>")

    assert isinstance(raised.value, ValueError), "the ORM validator still handles it as a ValueError"
    assert "evil.example" not in InvalidVirtualHost.MESSAGE
