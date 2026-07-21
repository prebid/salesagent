"""Real-wire presence semantics for update_media_buy.revision."""

import json
from unittest.mock import patch

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from src.app import app
from src.core.main import mcp
from src.core.schemas import UpdateMediaBuyRequest, UpdateMediaBuyResult, UpdateMediaBuySuccess
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape

_IDENTITY = PrincipalFactory.make_identity(
    principal_id="revision-boundary-principal",
    tenant_id="revision-boundary-tenant",
    tenant={"tenant_id": "revision-boundary-tenant"},
    protocol="mcp",
)
_VALID_REQUEST = {
    "media_buy_id": "mb-revision-boundary",
    "paused": True,
    "idempotency_key": "revision-boundary-key-0001",
}


def _success_result() -> UpdateMediaBuyResult:
    return UpdateMediaBuyResult(
        response=UpdateMediaBuySuccess(media_buy_id="mb-revision-boundary", affected_packages=[]),
        status="completed",
    )


@pytest.mark.asyncio
async def test_mcp_omitted_revision_reaches_impl() -> None:
    """The real MCP TypeAdapter must preserve omission as the accepted path."""
    with (
        patch("src.core.mcp_auth_middleware.resolve_identity_from_context", return_value=_IDENTITY),
        patch("src.core.tools.media_buy_update._update_media_buy_impl", return_value=_success_result()) as mock_impl,
    ):
        async with Client(mcp) as client:
            result = await client.call_tool("update_media_buy", _VALID_REQUEST, raise_on_error=False)

    assert not result.is_error, result.content
    assert result.structured_content["media_buy_id"] == "mb-revision-boundary"
    mock_impl.assert_called_once_with(
        req=UpdateMediaBuyRequest(**_VALID_REQUEST),
        identity=_IDENTITY,
        context_id=None,
    )


@pytest.mark.asyncio
async def test_mcp_valid_revision_is_rejected_as_unsupported_feature() -> None:
    """A SCHEMA-VALID revision names an unimplemented field — UNSUPPORTED_FEATURE, not INVALID_REQUEST.

    Per the pinned 3.1.1 enum descriptions: a valid ``revision: 5`` violates no
    schema constraint (INVALID_REQUEST's definition) and is verbatim
    UNSUPPORTED_FEATURE's ("a requested feature or field is not supported by
    this seller"). Schema-invalid spellings (null / 0) keep INVALID_REQUEST —
    pinned by the sibling tests below.
    """
    with (
        patch("src.core.mcp_auth_middleware.resolve_identity_from_context", return_value=_IDENTITY),
        patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl,
    ):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "update_media_buy",
                {**_VALID_REQUEST, "revision": 5},
                raise_on_error=False,
            )

    assert result.is_error
    envelope = json.loads(result.content[0].text)
    assert_envelope_shape(
        envelope,
        "UNSUPPORTED_FEATURE",
        recovery="correctable",
        message_substr="does not support optimistic-concurrency control",
    )
    mock_impl.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_explicit_null_revision_is_rejected_before_impl() -> None:
    """A JSON null must survive MCP parsing and hit the shared fail-loud guard."""
    with (
        patch("src.core.mcp_auth_middleware.resolve_identity_from_context", return_value=_IDENTITY),
        patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl,
    ):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "update_media_buy",
                {**_VALID_REQUEST, "revision": None},
                raise_on_error=False,
            )

    assert result.is_error
    envelope = json.loads(result.content[0].text)
    assert_envelope_shape(
        envelope,
        "INVALID_REQUEST",
        recovery="correctable",
        message_substr="does not support optimistic-concurrency control",
    )
    mock_impl.assert_not_called()


def test_rest_omitted_revision_reaches_impl() -> None:
    """The real REST body model must preserve omission as the accepted path."""
    rest_identity = _IDENTITY.model_copy(update={"protocol": "rest"})
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=rest_identity),
        patch("src.core.tools.media_buy_update._update_media_buy_impl", return_value=_success_result()) as mock_impl,
    ):
        client = TestClient(app)
        try:
            response = client.put(
                "/api/v1/media-buys/mb-revision-boundary",
                json={key: value for key, value in _VALID_REQUEST.items() if key != "media_buy_id"},
                headers={"Authorization": "Bearer revision-boundary-token"},
            )
        finally:
            client.close()

    assert response.status_code == 200, response.text
    assert response.json()["media_buy_id"] == "mb-revision-boundary"
    mock_impl.assert_called_once_with(
        req=UpdateMediaBuyRequest(**_VALID_REQUEST),
        identity=rest_identity,
        context_id=None,
    )


def test_rest_explicit_null_revision_is_rejected_before_impl() -> None:
    """A JSON null must not be collapsed into the REST model's default."""
    rest_identity = _IDENTITY.model_copy(update={"protocol": "rest"})
    body = {key: value for key, value in _VALID_REQUEST.items() if key != "media_buy_id"}
    body["revision"] = None
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=rest_identity),
        patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.put(
                "/api/v1/media-buys/mb-revision-boundary",
                json=body,
                headers={"Authorization": "Bearer revision-boundary-token"},
            )
        finally:
            client.close()

    assert response.status_code == 400
    assert_envelope_shape(
        response.json(),
        "INVALID_REQUEST",
        recovery="correctable",
        message_substr="does not support optimistic-concurrency control",
    )
    mock_impl.assert_not_called()
