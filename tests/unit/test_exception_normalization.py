import pytest
from pydantic import ValidationError

from src.core.exceptions import (
    WIRE_STANDARD_CODES,
    AdCPValidationError,
    normalize_to_adcp_error,
    to_wire_error_code,
)
from src.core.validation_helpers import adcp_validation_boundary
from tests.helpers import assert_no_raw_validation_leak


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
    leaky = RuntimeError("SELECT token FROM secret_table WHERE tenant='acme'")

    normalized = normalize_to_adcp_error(leaky)

    generic = WIRE_STANDARD_CODES[to_wire_error_code("INTERNAL_ERROR")]["message"]
    assert normalized.error_code == "INTERNAL_ERROR"
    assert normalized.message == generic
    assert "secret_table" not in normalized.message
    assert "SELECT" not in normalized.message


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

    leaky = RuntimeError("SELECT token FROM secret_table -- /var/secrets/db.key")

    err = _internal_error_for("message processing", leaky)

    # The parseable prefix survives (storyboard runners key off it).
    assert err.message.startswith("message processing failed: ")
    # ...but the raw exception text does not reach the wire message.
    assert "secret_table" not in err.message
    assert "/var/secrets/db.key" not in err.message
    # The envelope half stays generic too (this was already correct). The code is
    # the WIRE value for INTERNAL_ERROR (derived, not hardcoded: INTERNAL_ERROR is
    # internal-only and normalizes to a wire-standard code).
    assert err.data["adcp_error"]["code"] == to_wire_error_code("INTERNAL_ERROR")
    assert "secret_table" not in err.data["errors"][0]["message"]


@pytest.mark.asyncio
async def test_a2a_push_config_endpoint_does_not_leak_raw_exception_to_the_wire():
    """End-to-end through a real A2A entry point: the wire error carries no raw detail.

    The sibling test above pins ``_internal_error_for`` directly; this one drives an
    actual JSON-RPC method (``on_get_task_push_notification_config``) so the
    sanitized message is proven to reach the wire through the production path,
    not just through the helper in isolation.

    The failure is injected at identity resolution, before any database work, so
    this stays a unit test. That is also the realistic shape: an untyped
    SQLAlchemy/OS error escaping early is exactly what put SQL text on the wire.
    """
    from unittest.mock import MagicMock

    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="a-token")
    handler._resolve_a2a_identity = MagicMock(
        side_effect=RuntimeError("SELECT token FROM secret_table -- /var/secrets/db.key")
    )

    params = MagicMock()
    params.task_id = "task-1"
    params.get = lambda _k: "cfg-1"

    with pytest.raises(Exception) as exc_info:  # noqa: B017 - shape asserted below
        await handler.on_get_task_push_notification_config(params, MagicMock())

    raised = exc_info.value
    wire_message = getattr(raised, "message", None) or str(raised)
    # Pin that we went through _internal_error_for (not some unrelated failure),
    # so the leak assertions below are grading the sink we care about.
    assert wire_message.startswith("get push notification config failed: "), (
        f"expected the canonical A2A internal-error shape, got {wire_message!r}"
    )
    assert "secret_table" not in wire_message, f"raw SQL reached the wire: {wire_message!r}"
    assert "/var/secrets/db.key" not in wire_message, f"raw path reached the wire: {wire_message!r}"
