"""WebhookEnv — test environment for deliver_webhook_with_retry.

Patches: requests.post, WebhookURLValidator.validate_webhook_url,
         time.sleep, get_db_session (all in src.core.webhook_delivery)

Usage::

    with WebhookEnv() as env:
        env.set_http_status(200)
        success, result = env.call_deliver(
            webhook_url="https://example.com/hook",
            payload={"event": "delivery.update"},
        )
        assert success is True
        assert result["status"] == "delivered"

Available mocks via env.mock:
    "post"      -- requests.post mock
    "validate"  -- WebhookURLValidator.validate_webhook_url mock
    "sleep"     -- time.sleep mock
    "db"        -- get_db_session mock
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
from tests.harness._base_unit import ImplTestEnv


class WebhookEnv(ImplTestEnv):
    """Test environment for deliver_webhook_with_retry.

    Fluent API:
        set_http_status(code, text)       -- configure single HTTP response
        set_http_sequence(responses)      -- configure sequence of responses
        set_http_error(exception)         -- make requests.post raise
        set_url_invalid(error_msg)        -- make URL validation fail
        call_deliver(...)                 -- call deliver_webhook_with_retry
    """

    MODULE = "src.core.webhook_delivery"

    def _patch_targets(self) -> dict[str, str]:
        return {
            "post": f"{self.MODULE}.requests.post",
            "validate": f"{self.MODULE}.WebhookURLValidator.validate_webhook_url",
            "sleep": f"{self.MODULE}.time.sleep",
            "db": f"{self.MODULE}.get_db_session",
        }

    def _configure_defaults(self) -> None:
        # URL validation: valid by default
        self.mock["validate"].return_value = (True, None)

        # HTTP: 200 OK by default
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        self.mock["post"].return_value = mock_response

        # DB session: no-op context manager
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_ctx.__exit__ = MagicMock(return_value=False)
        self.mock["db"].return_value = mock_ctx

    def set_http_status(self, code: int, text: str = "") -> None:
        """Configure requests.post to return a single response with the given status."""
        mock_response = MagicMock()
        mock_response.status_code = code
        mock_response.text = text or f"Status {code}"
        self.mock["post"].return_value = mock_response
        self.mock["post"].side_effect = None  # Clear any sequence

    def set_http_sequence(self, responses: list[tuple[int, str]]) -> None:
        """Configure requests.post to return a sequence of responses.

        Args:
            responses: List of (status_code, text) tuples.
                       Each call to requests.post returns the next in sequence.

        Example::

            env.set_http_sequence([(503, "Unavailable"), (200, "OK")])
        """
        mocks = []
        for code, text in responses:
            r = MagicMock()
            r.status_code = code
            r.text = text
            mocks.append(r)
        self.mock["post"].side_effect = mocks

    def set_http_error(self, exception: Exception) -> None:
        """Make requests.post raise the given exception."""
        self.mock["post"].side_effect = exception

    def set_url_invalid(self, error_msg: str = "Invalid URL") -> None:
        """Make URL validation fail, short-circuiting delivery."""
        self.mock["validate"].return_value = (False, error_msg)

    def call_deliver(
        self,
        webhook_url: str = "https://example.com/webhook",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        signing_secret: str | None = None,
        max_retries: int = 3,
        timeout: int = 10,
        event_type: str | None = None,
        tenant_id: str | None = None,
        object_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Call deliver_webhook_with_retry with the given parameters."""
        delivery = WebhookDelivery(
            webhook_url=webhook_url,
            payload=payload or {"event": "delivery.update", "media_buy_id": "mb_001"},
            headers=headers or {"Content-Type": "application/json"},
            signing_secret=signing_secret,
            max_retries=max_retries,
            timeout=timeout,
            event_type=event_type,
            tenant_id=tenant_id,
            object_id=object_id,
        )
        return deliver_webhook_with_retry(delivery)

    def call_impl(self, **kwargs: Any) -> Any:
        """Alias for call_deliver to satisfy ImplTestEnv interface."""
        return self.call_deliver(**kwargs)
