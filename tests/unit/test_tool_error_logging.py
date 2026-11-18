"""``record_boundary_error`` writes one record per failure, and the cause is in it.

The boundary is the one place a failure is recorded (salesagent-3cs7o.24). A typed error
that carries a cause -- raised ``from`` it, or handed to ``internal_detail`` without
``from`` -- gets exactly one WARNING record whose ``exc_info`` is the typed error itself,
so the formatted record holds the cause's traceback once. An untyped exception gets one
ERROR record with its own traceback. Nothing else logs the cause: ``adcp_error_for`` used
to write ``internal_detail`` a second time, and the GAM ``with_retry`` path used to raise
the mapped error bare, which left the boundary a typed error with no cause at all.
"""

from __future__ import annotations

import logging

import pytest

from src.adapters.gam.utils.error_handler import RetryConfig, with_retry
from src.core.errors.details import ValidationDetails
from src.core.exceptions import AdCPAdapterError, AdCPSalesAgentError, AdCPValidationError, adcp_error_for
from src.core.tool_error_logging import record_boundary_error

_BOUNDARY_LOGGER = "src.core.tool_error_logging"


def _boundary_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == _BOUNDARY_LOGGER]


def _record_through_the_boundary(error: Exception) -> None:
    """The two boundary calls in ``failure_response`` order, with no transport around them."""
    record_boundary_error("t", "op", error)
    adcp_error_for(error)


def test_typed_error_raised_from_a_cause_is_one_record_carrying_the_chain(caplog):
    caplog.set_level(logging.DEBUG)
    try:
        try:
            raise ValueError("upstream")
        except ValueError as cause:
            raise AdCPAdapterError(internal_detail=cause) from cause
    except AdCPAdapterError as err:
        typed = err
        _record_through_the_boundary(err)

    (record,) = _boundary_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None
    assert record.exc_info[1] is typed
    assert typed.__cause__ is not None
    assert caplog.text.count("ValueError: upstream") == 1


def test_typed_error_carrying_internal_detail_without_from_is_still_recorded_once(caplog):
    """The raise site passed the caught exception but did not ``raise from`` it."""
    caplog.set_level(logging.DEBUG)
    try:
        try:
            raise ValueError("handed over, not chained")
        except ValueError as cause:
            raise AdCPAdapterError(internal_detail=cause)
    except AdCPAdapterError as err:
        assert err.__cause__ is None
        _record_through_the_boundary(err)

    (record,) = _boundary_records(caplog)
    assert record.exc_info is not None
    assert caplog.text.count("ValueError: handed over, not chained") == 1


def test_gam_with_retry_fault_reaches_the_boundary_record_with_its_upstream_text(caplog):
    caplog.set_level(logging.DEBUG)

    @with_retry(retry_config=RetryConfig(max_attempts=1))
    def call_gam() -> None:
        raise RuntimeError("gam upstream text")

    with pytest.raises(AdCPSalesAgentError) as excinfo:
        call_gam()
    _record_through_the_boundary(excinfo.value)

    (record,) = _boundary_records(caplog)
    assert record.exc_info is not None
    # The exception line of the cause's traceback; the raise site's source line, which
    # the traceback also prints, does not spell it this way.
    assert caplog.text.count("RuntimeError: gam upstream text") == 1


def test_typed_error_without_any_cause_is_one_record_with_no_traceback(caplog):
    caplog.set_level(logging.DEBUG)
    _record_through_the_boundary(AdCPAdapterError())

    (record,) = _boundary_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


def test_untyped_exception_is_one_error_record_with_its_traceback(caplog):
    caplog.set_level(logging.DEBUG)
    try:
        raise RuntimeError("untyped")
    except RuntimeError as err:
        untyped = err
        _record_through_the_boundary(err)

    (record,) = _boundary_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[1] is untyped
    # One traceback, no chain: the record's message names the exception, and the
    # traceback below it ends on the same line, so the exception line is counted
    # from the traceback header instead.
    assert caplog.text.count("Traceback (most recent call last)") == 1
    assert "RuntimeError: untyped" in caplog.text


def test_two_refusals_sharing_one_code_are_distinguishable_in_the_record(caplog):
    """The record names ``field`` and ``details``, because ``message`` cannot.

    Post-ADR-010 ``message`` is a read-only property over ``CODE_TABLE`` -- a function of
    the CODE, not of the raise site -- so ``create_media_buy``'s twenty-odd
    ``AdCPValidationError`` sites all log the identical sentence. Four media_buy
    storyboard checks failed indistinguishably on exactly that (salesagent-basxl). The
    two values that vary per raise site are ``field`` and ``details``, and both are
    already wire-bound, so naming them adds no exposure.
    """
    caplog.set_level(logging.DEBUG)
    record_boundary_error("t", "create_media_buy", AdCPValidationError(field="packages"))
    record_boundary_error(
        "t", "create_media_buy", AdCPValidationError(details=ValidationDetails(reasons=["no inventory matched"]))
    )

    packages, reasons = (record.getMessage() for record in _boundary_records(caplog))
    assert "field=packages" in packages, packages
    assert "no inventory matched" in reasons, reasons
    assert packages != reasons, "two different refusals must not produce the same record"
