import pytest
from pydantic import ValidationError

from src.core.exceptions import (
    GENERIC_INTERNAL_ERROR_MESSAGE,
    AdCPValidationError,
    normalize_to_adcp_error,
    to_wire_error_code,
)
from src.core.validation_helpers import adcp_validation_boundary
from tests.helpers import (
    RAW_EXCEPTION_LEAK_SENTINEL,
    assert_no_raw_exception_leak,
    assert_no_raw_validation_leak,
)
from tests.helpers.envelope_assertions import _RAW_EXCEPTION_LEAK_TOKENS


def test_pydantic_validation_error_normalization_is_structured_and_sanitized():
    error = ValidationError.from_exception_data(
        title="call[create_media_buy]",
        line_errors=[
            {
                "type": "missing",
                "loc": ("packages", 0, "product_id"),
                "input": {"secret": "buyer-input"},
            }
        ],
    )

    normalized = normalize_to_adcp_error(error)

    assert isinstance(normalized, AdCPValidationError)
    assert normalized.message == "Field required"
    assert normalized.field == "packages[0].product_id"
    assert normalized.details == {
        "validation_errors": [
            {
                "loc": ["packages", 0, "product_id"],
                "msg": "Field required",
                "type": "missing",
            }
        ]
    }
    assert "buyer-input" not in normalized.message
    assert_no_raw_validation_leak(normalized.message)


def test_a2a_validation_boundary_preserves_contextual_error_format():
    error = ValidationError.from_exception_data(
        title="CreateMediaBuyRequest",
        line_errors=[
            {
                "type": "missing",
                "loc": ("packages", 0, "product_id"),
                "input": {"secret": "buyer-input"},
            }
        ],
    )

    with pytest.raises(AdCPValidationError) as exc_info:
        with adcp_validation_boundary():
            raise error

    assert "Invalid parameters:" in exc_info.value.message
    assert "packages.0.product_id: Required field is missing" in exc_info.value.message
    assert exc_info.value.field == "packages[0].product_id"
    assert exc_info.value.suggestion == ("Provide the required 'packages.0.product_id' field and resend the request.")
    assert exc_info.value.details == {
        "validation_errors": [
            {
                "loc": ["packages", 0, "product_id"],
                "msg": "Field required",
                "type": "missing",
            }
        ]
    }
    assert "buyer-input" not in exc_info.value.message
    assert_no_raw_validation_leak(exc_info.value.message)


def test_untyped_exception_message_is_generic_not_raw():
    """An untyped exception normalizes to a base INTERNAL_ERROR whose buyer-facing
    message is the generic wire message — never the raw str(exc), which can carry
    SQL fragments, table names, or filesystem paths that reach the wire envelope
    and the A2A failed-Task webhook body. Deletion oracle: reverting the sink to
    ``AdCPError(str(exc))`` leaks 'secret_table' here.
    """
    leaky = RuntimeError(RAW_EXCEPTION_LEAK_SENTINEL)

    normalized = normalize_to_adcp_error(leaky)

    assert normalized.error_code == "INTERNAL_ERROR"
    assert normalized.message == GENERIC_INTERNAL_ERROR_MESSAGE
    assert_no_raw_exception_leak(normalized.message)


@pytest.mark.parametrize("token", _RAW_EXCEPTION_LEAK_TOKENS)
def test_each_raw_exception_leak_token_is_individually_enforced(token):
    """Every entry in the oracle's token tuple actually fails the oracle.

    ``assert_no_raw_exception_leak`` is the only mechanism grading sanitization at
    the untyped sinks, and until now its own token tuple was pinned by nothing:
    dropping an entry, or replacing the tuple with ``()``, left the whole suite
    green while the helper quietly stopped checking. The import-time asserts
    beside the constants cover the empty-tuple and absent-from-the-sentinel
    mutations; this covers a token that is still listed but no longer enforced
    (e.g. a loop that stops early or filters the tuple).
    """
    with pytest.raises(AssertionError):
        assert_no_raw_exception_leak(f"boundary message: {token}")

    # ...and the oracle is not simply raising on everything it is handed.
    assert_no_raw_exception_leak(GENERIC_INTERNAL_ERROR_MESSAGE)


def test_generic_internal_error_message_is_non_empty():
    """The buyer-facing generic INTERNAL_ERROR message is never a blank string.

    The wire ``message`` field is schema-required but has no minimum length, so an
    empty string would be schema-valid. Non-emptiness is a stricter repo choice;
    pin it once, here at the constant's definition, so the per-sink tests can
    assert plain equality against it without re-litigating emptiness tautologically.
    """
    assert GENERIC_INTERNAL_ERROR_MESSAGE
    assert GENERIC_INTERNAL_ERROR_MESSAGE.strip()


def test_normalize_is_a_pure_mapping_and_does_not_log(caplog):
    """``normalize_to_adcp_error`` is a pure type→AdCPError mapping with no logging
    side effect.

    Server-side capture of the raw untyped exception is the transport boundary's
    job: every boundary passes the raw exception to ``record_boundary_error``
    (MCP ``_handle_tool_exception``; A2A ``on_message_send``, the skill loop, and
    the four push-config methods), which logs the untyped fallthrough with
    ``exc_info``. Logging inside this helper — invoked at every boundary — would
    double-log there. Deletion oracle: restoring ``logger.error(...)`` in the sink
    reddens this.
    """
    import logging

    leaky = RuntimeError(RAW_EXCEPTION_LEAK_SENTINEL)

    with caplog.at_level(logging.DEBUG, logger="src.core.exceptions"):
        normalize_to_adcp_error(leaky)

    assert caplog.records == [], (
        f"normalize_to_adcp_error is a pure mapping and must not log; it emitted "
        f"{[r.getMessage() for r in caplog.records]!r}"
    )


def test_a2a_internal_error_message_is_sanitized_not_raw():
    """The A2A JSON-RPC ``error.message`` must not carry the raw exception either.

    ``_internal_error_for`` builds the InternalError returned by the top-level
    ``on_message_send`` failure path and the four push-notification-config
    methods — both reachable without authentication. It previously interpolated
    the raw ``exc`` into ``message`` while sanitizing only ``data``, so a
    SQLAlchemy/OS error put SQL text or a filesystem path on the wire at a sink
    the shared normalization fix did not reach.

    Deletion oracle: restore ``message=f"{operation} failed: {exc}"`` and the
    two leak assertions below go red while the ``data`` assertions stay green —
    which is exactly the asymmetry that hid this.
    """
    from src.a2a_server.adcp_a2a_server import _internal_error_for

    leaky = RuntimeError(RAW_EXCEPTION_LEAK_SENTINEL)

    err = _internal_error_for("message processing", leaky)

    # The shared prefix survives (it keeps the five raise sites uniform).
    assert err.message.startswith("message processing failed: ")
    # ...but the raw exception text does not reach the wire message.
    assert_no_raw_exception_leak(err.message)
    # The envelope half stays generic too (this was already correct). The code is
    # the WIRE value for INTERNAL_ERROR (derived, not hardcoded: INTERNAL_ERROR is
    # internal-only and normalizes to a wire-standard code).
    assert err.data["adcp_error"]["code"] == to_wire_error_code("INTERNAL_ERROR")
    assert_no_raw_exception_leak(err.data["errors"][0]["message"])


@pytest.mark.asyncio
async def test_a2a_push_config_endpoint_does_not_leak_raw_exception_to_the_wire():
    """Drive a real A2A JSON-RPC handler and assert the *raised* InternalError is sanitized.

    The sibling test above pins ``_internal_error_for`` directly; this one drives an
    actual JSON-RPC method (``on_get_task_push_notification_config``) through handler
    dispatch, then reads ``.message`` off the ``InternalError`` it raises. It grades
    the production dispatch path in process — it does NOT serialize the error to the
    JSON-RPC wire, so "reaches the wire" is out of scope here (that is graded by the
    transport-blind wire scenario, tracked separately).

    The failure is injected at identity resolution, before any database work, so
    this stays a unit test. That is also the realistic shape: an untyped
    SQLAlchemy/OS error escaping early is exactly what put SQL text on the wire.
    """
    from unittest.mock import MagicMock

    from a2a.types import InternalError

    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="a-token")
    handler._resolve_a2a_identity = MagicMock(side_effect=RuntimeError(RAW_EXCEPTION_LEAK_SENTINEL))

    params = MagicMock()
    params.task_id = "task-1"
    params.get = lambda _k: "cfg-1"

    with pytest.raises(InternalError) as exc_info:
        await handler.on_get_task_push_notification_config(params, MagicMock())

    message = exc_info.value.message
    # Pin that we went through _internal_error_for (not some unrelated failure),
    # so the leak assertion below is grading the sink we care about.
    assert message.startswith("get push notification config failed: "), (
        f"expected the canonical A2A internal-error shape, got {message!r}"
    )
    assert_no_raw_exception_leak(message)
