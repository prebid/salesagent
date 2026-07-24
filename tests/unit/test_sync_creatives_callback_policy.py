"""Callback-policy guards for sync_creatives transport boundaries (#1546)."""

from unittest.mock import MagicMock, patch

import pytest
from adcp import PushNotificationConfig
from starlette.testclient import TestClient

from src.app import app
from src.core.exceptions import AdCPValidationError
from tests.factories import PrincipalFactory
from tests.helpers import assert_envelope_shape

_INVALID_CALLBACK_URLS = (
    pytest.param("http://example.com/callback", id="plain-http"),
    pytest.param("https://user:password@example.com/callback", id="url-userinfo"),
)
_IDEMPOTENCY_KEY = "creative-callback-key-0001"
_IDENTITY = PrincipalFactory.make_identity(
    principal_id="creative-callback-principal",
    tenant_id="creative-callback-tenant",
    tenant={"tenant_id": "creative-callback-tenant"},
    auth_token="creative-callback-token",
    protocol="rest",
)


@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
@pytest.mark.asyncio
async def test_mcp_sync_rejects_unsafe_callback_before_impl(url: str):
    from src.core.tools.creatives.sync_wrappers import sync_creatives

    config = PushNotificationConfig.model_validate({"url": url})
    with patch("src.core.tools.creatives.sync_wrappers._sync_creatives_impl") as mock_impl:
        with pytest.raises(AdCPValidationError) as exc_info:
            await sync_creatives(
                creatives=[],
                idempotency_key=_IDEMPOTENCY_KEY,
                push_notification_config=config,
            )

    assert exc_info.value.field == "push_notification_config.url"
    mock_impl.assert_not_called()


@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
def test_a2a_raw_sync_rejects_unsafe_callback_before_impl(url: str):
    from src.core.tools.creatives.sync_wrappers import sync_creatives_raw

    with patch("src.core.tools.creatives.sync_wrappers._sync_creatives_impl") as mock_impl:
        with pytest.raises(AdCPValidationError) as exc_info:
            sync_creatives_raw(
                creatives=[],
                idempotency_key=_IDEMPOTENCY_KEY,
                push_notification_config={"url": url},
                identity=MagicMock(),
            )

    assert exc_info.value.field == "push_notification_config.url"
    mock_impl.assert_not_called()


@pytest.mark.parametrize("url", _INVALID_CALLBACK_URLS)
def test_rest_sync_rejects_unsafe_callback_before_impl(url: str):
    client = TestClient(app)
    with (
        patch("src.core.resolved_identity.resolve_identity", return_value=_IDENTITY),
        patch("src.core.tools.creatives.sync_wrappers._sync_creatives_impl") as mock_impl,
    ):
        response = client.post(
            "/api/v1/creatives/sync",
            json={
                "creatives": [],
                "idempotency_key": _IDEMPOTENCY_KEY,
                "push_notification_config": {"url": url},
            },
            headers={"Authorization": "Bearer creative-callback-token"},
        )

    assert response.status_code == 400, response.text
    assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")
    assert response.json()["errors"][0]["field"] == "push_notification_config.url"
    mock_impl.assert_not_called()
