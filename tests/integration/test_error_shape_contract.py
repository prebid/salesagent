"""L0-02 guard: the AdCPError REST response wire shape is frozen.

Every AdCP REST endpoint that raises an :class:`~src.core.exceptions.AdCPError`
is translated to a JSON body by the handler in ``src/app.py`` (``adcp_error_handler``).
The body shape is a stable contract with buyer-agent clients:

    {
        "error_code": <str>,
        "message":    <str>,
        "recovery":   <str — one of transient|correctable|terminal>,
        "details":    <dict | null>,
    }

This test triggers representative errors (missing identity → 400, missing
auth → 401) and asserts both the HTTP status family and the JSON body
shape. It does NOT pin exact messages (they evolve), only the shape.

See ``.claude/notes/flask-to-fastapi/L0-implementation-plan.md`` §L0-02.
"""

from __future__ import annotations

from typing import Any

import pytest

EXPECTED_ADCP_ERROR_KEYS = frozenset({"error_code", "message", "recovery", "details"})
EXPECTED_RECOVERY_VALUES = frozenset({"transient", "correctable", "terminal"})


def _assert_adcp_error_shape(body: Any, *, expected_status: int, actual_status: int) -> None:
    """Assert ``body`` matches the AdCPError wire shape."""
    assert actual_status == expected_status, f"expected HTTP {expected_status}, got {actual_status}; body={body!r}"
    assert isinstance(body, dict), f"expected JSON object, got {type(body).__name__}: {body!r}"
    assert set(body.keys()) == EXPECTED_ADCP_ERROR_KEYS, (
        f"AdCPError body keys drifted.\n"
        f"  expected: {sorted(EXPECTED_ADCP_ERROR_KEYS)}\n"
        f"  observed: {sorted(body.keys())}"
    )
    assert (
        isinstance(body["error_code"], str) and body["error_code"]
    ), f"error_code must be a non-empty string, got {body['error_code']!r}"
    assert isinstance(body["message"], str), f"message must be a string, got {type(body['message']).__name__}"
    assert (
        body["recovery"] in EXPECTED_RECOVERY_VALUES
    ), f"recovery must be one of {sorted(EXPECTED_RECOVERY_VALUES)}, got {body['recovery']!r}"
    assert body["details"] is None or isinstance(
        body["details"], dict
    ), f"details must be null or a dict, got {type(body['details']).__name__}"


@pytest.mark.integration
class TestAdcpErrorShapeContract:
    """Representative AdCP error responses match the frozen wire shape."""

    def test_validation_error_shape(self, boot_client):
        """Missing identity in get_products produces AdCPValidationError (400)."""
        response = boot_client.post("/api/v1/products", json={"brief": "test"})
        _assert_adcp_error_shape(response.json(), expected_status=400, actual_status=response.status_code)
        assert response.json()["error_code"] == "VALIDATION_ERROR"
        assert response.json()["recovery"] == "correctable"

    def test_authentication_error_shape(self, boot_client):
        """Missing auth on create_media_buy produces AdCPAuthenticationError (401)."""
        response = boot_client.post(
            "/api/v1/media-buys",
            json={
                "buyer_ref": "x",
                "product_ids": ["p1"],
                "total_budget": 100,
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "daily_budget": 10,
            },
        )
        _assert_adcp_error_shape(response.json(), expected_status=401, actual_status=response.status_code)
        assert response.json()["error_code"] == "AUTH_TOKEN_INVALID"
        assert response.json()["recovery"] == "terminal"

    def test_content_type_is_json_for_adcp_errors(self, boot_client):
        """AdCP error responses are served as application/json."""
        response = boot_client.post("/api/v1/products", json={"brief": "test"})
        ct = response.headers.get("content-type", "")
        assert ct.startswith("application/json"), f"expected application/json, got {ct!r}"

    def test_planted_drift_is_detected(self):
        """Meta-test: a body missing a required key must fail the shape check."""
        missing_key = {"error_code": "X", "message": "m", "recovery": "terminal"}  # no 'details'
        with pytest.raises(AssertionError, match="keys drifted"):
            _assert_adcp_error_shape(missing_key, expected_status=400, actual_status=400)

    def test_planted_drift_wrong_type_is_detected(self):
        """Meta-test: a body with wrong types for a field must fail."""
        wrong_type = {"error_code": "X", "message": 42, "recovery": "terminal", "details": None}
        with pytest.raises(AssertionError, match="message must be a string"):
            _assert_adcp_error_shape(wrong_type, expected_status=400, actual_status=400)

    def test_planted_drift_invalid_recovery_is_detected(self):
        """Meta-test: a body with an unknown recovery value must fail."""
        bad_recovery = {"error_code": "X", "message": "m", "recovery": "BOGUS", "details": None}
        with pytest.raises(AssertionError, match="recovery must be one of"):
            _assert_adcp_error_shape(bad_recovery, expected_status=400, actual_status=400)
