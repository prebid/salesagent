"""Real A2A-wire regressions for update_media_buy request guards."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.app import app
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.unit.test_a2a_transport_contract import (
    _build_jsonrpc,
    _extract_artifact_data,
    _extract_jsonrpc_result,
)

_MOCK_IDENTITY = PrincipalFactory.make_identity(
    principal_id="update-boundary-principal",
    tenant_id="update-boundary-tenant",
    tenant={"tenant_id": "update-boundary-tenant"},
    protocol="a2a",
)


@pytest.mark.parametrize(
    ("parameters", "code", "message"),
    [
        pytest.param(
            {"media_buy_id": "mb-1", "paused": True},
            "VALIDATION_ERROR",
            "idempotency_key is required",
            id="omitted-idempotency-key",
        ),
        pytest.param(
            {"media_buy_id": "mb-1", "paused": True, "idempotency_key": 123},
            "VALIDATION_ERROR",
            "idempotency_key must be a string",
            id="non-string-idempotency-key",
        ),
        pytest.param(
            {
                "media_buy_id": "mb-1",
                "paused": True,
                "idempotency_key": "a2a-update-key-0001",
                "revision": 7,
            },
            # A2A's JSON-RPC/protobuf layer coerces the integer 7 to a float, but
            # the guard classifies on numeric VALUE (not Python type), so this
            # converges with MCP/REST on UNSUPPORTED_FEATURE — a schema-valid
            # revision names a field this seller does not implement.
            "UNSUPPORTED_FEATURE",
            "does not support optimistic-concurrency control",
            id="unsupported-revision",
        ),
        pytest.param(
            {
                "media_buy_id": "mb-1",
                "paused": True,
                "idempotency_key": "a2a-update-key-0001",
                "revision": 0,
            },
            # Below minimum:1 is schema-invalid -> INVALID_REQUEST (BR-UC-003 below_min).
            "INVALID_REQUEST",
            "must be an integer",
            id="below-minimum-revision",
        ),
    ],
)
def test_update_media_buy_rejects_invalid_protocol_fields_before_core_call(
    parameters: dict, code: str, message: str
) -> None:
    """A2A exposes buyer-correctable envelopes and never invokes the core write."""
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
        patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_core,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.post(
                "/a2a",
                json=_build_jsonrpc("update_media_buy", parameters),
                headers={
                    "Authorization": "Bearer update-boundary-token",
                    "Content-Type": "application/json",
                    "A2A-Version": "1.0",
                },
            )
        finally:
            client.close()

    assert response.status_code == 200
    data = _extract_artifact_data(_extract_jsonrpc_result(response))
    assert_envelope_shape(data, code, recovery="correctable", message_substr=message)
    mock_core.assert_not_called()


@pytest.mark.parametrize(
    "revision_field",
    [
        pytest.param({}, id="omitted-revision"),
        pytest.param({"revision": None}, id="explicit-null-revision"),
    ],
)
def test_update_media_buy_accepts_omitted_or_null_revision_before_core_call(revision_field: dict) -> None:
    """Omitted revision AND explicit JSON null both proceed to core (null == omission).

    The SDK models revision as ``int | None = None``, so a conformant client
    that never set it serializes null; the guard treats null identically to
    omission and must not reject it. The omitted case also proves the real A2A
    path doesn't collapse omission into null.
    """
    from src.core.schemas import UpdateMediaBuyRequest, UpdateMediaBuyResult, UpdateMediaBuySuccess

    success = UpdateMediaBuyResult(
        response=UpdateMediaBuySuccess(media_buy_id="mb-1", affected_packages=[]),
        status="completed",
    )
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
        patch("src.core.tools.media_buy_update._update_media_buy_impl", return_value=success) as mock_core,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.post(
                "/a2a",
                json=_build_jsonrpc(
                    "update_media_buy",
                    {
                        "media_buy_id": "mb-1",
                        "paused": True,
                        "idempotency_key": "a2a-update-key-0001",
                        **revision_field,
                    },
                ),
                headers={
                    "Authorization": "Bearer update-boundary-token",
                    "Content-Type": "application/json",
                    "A2A-Version": "1.0",
                },
            )
        finally:
            client.close()

    assert response.status_code == 200
    data = _extract_artifact_data(_extract_jsonrpc_result(response))
    assert data["media_buy_id"] == "mb-1"
    mock_core.assert_called_once_with(
        req=UpdateMediaBuyRequest(
            media_buy_id="mb-1",
            paused=True,
            idempotency_key="a2a-update-key-0001",
        ),
        identity=_MOCK_IDENTITY,
        context_id=None,
    )
