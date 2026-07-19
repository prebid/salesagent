"""Transport guards for buyer callback URL registration (#1546).

Every registration surface must reject a callback that delivery would refuse,
before the request reaches business logic or persistence.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import InvalidParamsError, TaskPushNotificationConfig
from adcp import PushNotificationConfig
from adcp.types import ReportingWebhook
from flask import Flask
from starlette.testclient import TestClient

from src.app import app
from src.core.exceptions import AdCPValidationError
from tests.factories import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import create_test_media_buy_request_dict

_INVALID_CALLBACK_URLS = (
    pytest.param("http://example.com/callback", id="plain-http"),
    pytest.param("https://user:password@example.com/callback", id="url-userinfo"),
)
_CALLBACK_FIELDS = ("push_notification_config", "reporting_webhook")
_IDENTITY = PrincipalFactory.make_identity(
    principal_id="callback-principal",
    tenant_id="callback-tenant",
    tenant={"tenant_id": "callback-tenant"},
    auth_token="callback-token",
    protocol="rest",
)


def _callback_config(field: str, url: str, *, typed: bool) -> object:
    if field == "push_notification_config":
        wire = {"url": url}
        return PushNotificationConfig.model_validate(wire) if typed else wire
    wire = {
        "url": url,
        "authentication": {"schemes": ["Bearer"], "credentials": "c" * 32},
        "reporting_frequency": "daily",
    }
    return ReportingWebhook.model_validate(wire) if typed else wire


@pytest.mark.parametrize("field", _CALLBACK_FIELDS)
@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
@pytest.mark.asyncio
async def test_mcp_create_rejects_unsafe_callback_before_impl(field: str, url: str):
    """The typed MCP boundary must stop both callback fields before core."""
    from src.core.tools.media_buy_create import create_media_buy

    request_data = create_test_media_buy_request_dict()
    kwargs = {field: _callback_config(field, url, typed=True)}
    with patch("src.core.tools.media_buy_create._create_media_buy_impl", new_callable=AsyncMock) as mock_impl:
        with pytest.raises(AdCPValidationError) as exc_info:
            await create_media_buy(
                brand=request_data["brand"],
                packages=request_data["packages"],
                start_time=request_data["start_time"],
                end_time=request_data["end_time"],
                idempotency_key=request_data["idempotency_key"],
                **kwargs,
            )

    assert exc_info.value.field == f"{field}.url"
    mock_impl.assert_not_awaited()


@pytest.mark.parametrize("field", _CALLBACK_FIELDS)
@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
@pytest.mark.asyncio
async def test_a2a_raw_create_rejects_unsafe_callback_before_impl(field: str, url: str):
    """The A2A/raw boundary must stop wire dictionaries before core."""
    from src.core.tools.media_buy_create import create_media_buy_raw

    request_data = create_test_media_buy_request_dict()
    kwargs = {field: _callback_config(field, url, typed=False)}
    with patch("src.core.tools.media_buy_create._create_media_buy_impl", new_callable=AsyncMock) as mock_impl:
        with pytest.raises(AdCPValidationError) as exc_info:
            await create_media_buy_raw(
                brand=request_data["brand"],
                packages=request_data["packages"],
                start_time=request_data["start_time"],
                end_time=request_data["end_time"],
                idempotency_key=request_data["idempotency_key"],
                identity=MagicMock(),
                **kwargs,
            )

    assert exc_info.value.field == f"{field}.url"
    mock_impl.assert_not_awaited()


@pytest.mark.parametrize("field", _CALLBACK_FIELDS)
@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
def test_update_shared_boundary_rejects_unsafe_callback_before_impl(field: str, url: str):
    """Update cannot use either callback field to bypass the shared policy."""
    from src.core.tools.media_buy_update import update_media_buy_raw

    kwargs = {field: _callback_config(field, url, typed=False)}
    with patch("src.core.tools.media_buy_update._update_media_buy_impl") as mock_impl:
        with pytest.raises(AdCPValidationError) as exc_info:
            update_media_buy_raw(
                media_buy_id="mb_callback_policy",
                paused=True,
                idempotency_key="callback-update-key-0001",
                identity=MagicMock(),
                **kwargs,
            )

    assert exc_info.value.field == f"{field}.url"
    mock_impl.assert_not_called()


@pytest.mark.parametrize("field", _CALLBACK_FIELDS)
@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
def test_rest_create_rejects_unsafe_callback_before_impl(field: str, url: str):
    """The real REST wire returns VALIDATION_ERROR without entering core."""
    request_data = create_test_media_buy_request_dict()
    body = {
        "brand": request_data["brand"],
        "packages": request_data["packages"],
        "start_time": request_data["start_time"],
        "end_time": request_data["end_time"],
        "idempotency_key": request_data["idempotency_key"],
        field: _callback_config(field, url, typed=False),
    }
    client = TestClient(app)
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_IDENTITY),
        patch("src.core.tools.media_buy_create._create_media_buy_impl", new_callable=AsyncMock) as mock_impl,
    ):
        response = client.post(
            "/api/v1/media-buys",
            json=body,
            headers={"Authorization": "Bearer callback-token"},
        )

    assert response.status_code == 400, response.text
    assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")
    assert response.json()["errors"][0]["field"] == f"{field}.url"
    mock_impl.assert_not_awaited()


@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
@pytest.mark.asyncio
async def test_a2a_push_config_endpoint_rejects_before_uow(url: str):
    """The dedicated A2A push-config registration endpoint never opens its UoW."""
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value="callback-token")
    handler._resolve_a2a_identity = MagicMock(return_value=_IDENTITY)
    handler._make_tool_context = MagicMock(
        return_value=SimpleNamespace(tenant_id="callback-tenant", principal_id="callback-principal")
    )
    with patch("src.a2a_server.adcp_a2a_server.PushNotificationConfigUoW") as mock_uow:
        with pytest.raises(InvalidParamsError, match="Invalid callback url"):
            await handler.on_create_task_push_notification_config(
                TaskPushNotificationConfig(url=url),
                ServerCallContext(),
            )

    mock_uow.assert_not_called()


@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
def test_admin_push_config_registration_rejects_before_db(url: str):
    """The admin registration form uses callback policy before persistence."""
    from src.admin.blueprints.principals import register_webhook

    undecorated_register = register_webhook.__wrapped__.__wrapped__
    flask_app = Flask(__name__)
    flask_app.secret_key = "callback-policy-test"
    with (
        flask_app.test_request_context(
            method="POST",
            data={"url": url, "auth_type": "none"},
        ),
        patch("src.admin.blueprints.principals.get_db_session") as mock_get_db_session,
        patch("src.admin.blueprints.principals.url_for", return_value="/manage-webhooks"),
    ):
        response = undecorated_register("callback-tenant", "callback-principal")

    assert response.status_code == 302
    mock_get_db_session.assert_not_called()
