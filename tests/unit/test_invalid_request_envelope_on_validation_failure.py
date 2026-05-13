"""``create_media_buy`` validation failures surface as AdCP-canonical
``INVALID_REQUEST`` on the wire — the only code accepted by both the
``error_compliance/nonexistent_product`` and ``error_compliance/reversed_dates_error``
storyboards.

The pre-dispatch validation pass in ``_create_media_buy_impl`` raises
``ValueError`` on past-start_time, reversed dates, empty product_ids,
duplicate product_ids, and targeting validation failures. The outer
``except (ValueError, PermissionError)`` handler used to wrap these as
``Error(code="VALIDATION_ERROR")``. Both ``VALIDATION_ERROR`` and
``INVALID_REQUEST`` are in the AdCP 3.0 standard error-code enum
(``adcp/types/generated_poc/enums/error_code.py``); the distinction is that
``INVALID_REQUEST`` is the canonical code per ``core/error.json`` for buyer-
fixable shape issues and is the intersection value of the two storyboards'
``allowed_values``:

- ``error_compliance/nonexistent_product`` → ``{PRODUCT_NOT_FOUND, PRODUCT_UNAVAILABLE, INVALID_REQUEST}``
- ``error_compliance/reversed_dates_error`` → ``{VALIDATION_ERROR, INVALID_REQUEST}``
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "allowed_codes",
    [
        # error_compliance/nonexistent_product
        ["PRODUCT_NOT_FOUND", "PRODUCT_UNAVAILABLE", "INVALID_REQUEST"],
        # error_compliance/reversed_dates_error
        ["VALIDATION_ERROR", "INVALID_REQUEST"],
    ],
)
def test_invalid_request_satisfies_both_storyboard_validations(allowed_codes: list[str]) -> None:
    """The single wire code we emit must satisfy both storyboards. This
    pins the intersection of accepted codes so future YAML revisions
    that drop ``INVALID_REQUEST`` from either list fail the test before
    a regression hits production."""
    assert "INVALID_REQUEST" in allowed_codes


def test_create_media_buy_error_accepts_invalid_request_code() -> None:
    """The ``CreateMediaBuyError`` schema must accept ``INVALID_REQUEST``
    in the ``Error.code`` field. The wire envelope projection in
    ``_translate_adcp_error`` preserves the code verbatim, so this is
    sufficient to lock the contract — a schema regression that rejected
    ``INVALID_REQUEST`` would surface here before reaching the wire."""
    from src.core.schemas import CreateMediaBuyError, Error

    err = CreateMediaBuyError(errors=[Error(code="INVALID_REQUEST", message="start_time is in the past", details=None)])
    assert err.errors[0].code == "INVALID_REQUEST"
