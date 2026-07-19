"""Delivery-service regressions for the shared pinned webhook transport."""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import pytest

from src.core.database.repositories.push_notification_config import PushNotificationTarget
from src.core.security.webhook_http import UnsafeWebhookTargetError, post_webhook_status, webhook_host_header
from src.services.webhook_delivery_service import CircuitBreaker, WebhookDeliveryService, WebhookQueue


def _queue_target(
    *,
    secret: str | None = None,
    payload: dict[str, Any] | None = None,
) -> WebhookQueue:
    queue = WebhookQueue()
    queue.enqueue(
        {
            "config": PushNotificationTarget(
                url="https://buyer.example/webhook",
                authentication_type=None,
                authentication_token=None,
                webhook_secret=secret,
                auth_blocked_at=None,
            ),
            "payload": payload if payload is not None else {"event": "delivery", "nested": {"value": 1}},
            "timestamp": datetime(2026, 7, 17, tzinfo=UTC),
        }
    )
    return queue


def _session_context() -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = False
    return context, session


def test_unsafe_delivery_target_is_not_retried():
    service = WebhookDeliveryService()
    breaker = CircuitBreaker()
    session_context, session = _session_context()

    with (
        patch(
            "src.services.webhook_delivery_service.create_pinned_webhook_session",
            return_value=session_context,
        ),
        patch(
            "src.services.webhook_delivery_service.post_webhook_status",
            side_effect=UnsafeWebhookTargetError("private target"),
        ) as post,
    ):
        assert service._deliver_with_backoff("tenant:url", breaker, _queue_target()) is False

    post.assert_called_once_with(
        session,
        "https://buyer.example/webhook",
        body=ANY,
        headers=ANY,
        timeout=10.0,
    )
    assert breaker.failure_count == 1


@pytest.mark.parametrize("status_code", [301, 302, 307, 308, 400, 404])
def test_redirects_and_client_errors_are_non_retryable(status_code):
    service = WebhookDeliveryService()
    breaker = CircuitBreaker()
    session_context, session = _session_context()

    with (
        patch(
            "src.services.webhook_delivery_service.create_pinned_webhook_session",
            return_value=session_context,
        ),
        patch(
            "src.services.webhook_delivery_service.post_webhook_status",
            return_value=status_code,
        ) as post,
    ):
        assert service._deliver_with_backoff("tenant:url", breaker, _queue_target()) is False

    post.assert_called_once_with(
        session,
        "https://buyer.example/webhook",
        body=ANY,
        headers=ANY,
        timeout=10.0,
    )


def test_hmac_covers_the_exact_transmitted_body_bytes():
    service = WebhookDeliveryService()
    breaker = CircuitBreaker()
    secret = "s" * 32
    session_context, session = _session_context()

    with (
        patch(
            "src.services.webhook_delivery_service.create_pinned_webhook_session",
            return_value=session_context,
        ),
        patch(
            "src.services.webhook_delivery_service.post_webhook_status",
            return_value=200,
        ) as post,
    ):
        assert service._deliver_with_backoff("tenant:url", breaker, _queue_target(secret=secret)) is True

    post.assert_called_once_with(
        session,
        "https://buyer.example/webhook",
        body=ANY,
        headers=ANY,
        timeout=10.0,
    )
    body = post.call_args.kwargs["body"]
    headers = post.call_args.kwargs["headers"]
    signed = headers["X-ADCP-Timestamp"].encode() + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    assert headers["X-ADCP-Signature"] == expected


def test_post_helper_transmits_exact_body_and_closes_response():
    """The last HTTP seam sends the signed bytes verbatim and never buffers a response."""
    session = MagicMock()
    response = MagicMock(status_code=202)
    session.post.return_value = response
    body = b'{"event":"delivery","non_ascii":"\xc3\xa9"}'
    headers = {"Content-Type": "application/json", "X-ADCP-Signature": "signed"}

    status_code = post_webhook_status(
        session,
        "https://buyer.example:8443/webhook",
        body=body,
        headers=headers,
        timeout=10.0,
    )

    assert status_code == 202
    session.post.assert_called_once_with(
        "https://buyer.example:8443/webhook",
        data=body,
        headers={**headers, "Host": "buyer.example:8443"},
        timeout=10.0,
        allow_redirects=False,
        stream=True,
    )
    response.close.assert_called_once_with()


def test_host_header_uses_requests_idna_canonicalization() -> None:
    """A valid Unicode callback host is emitted as ASCII punycode."""
    assert webhook_host_header("https://例え.テスト:8443/webhook") == "xn--r8jz45g.xn--zckzah:8443"


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_delivery_payload_is_rejected_before_http(non_finite):
    """Delivery never signs or transmits Python's non-standard NaN/Infinity tokens."""
    service = WebhookDeliveryService()
    breaker = CircuitBreaker()

    with (
        patch("src.services.webhook_delivery_service.create_pinned_webhook_session") as create_session,
        patch("src.services.webhook_delivery_service.post_webhook_status") as post,
    ):
        result = service._deliver_with_backoff(
            "tenant:url",
            breaker,
            _queue_target(payload={"spend": non_finite}),
        )

    assert result is False
    create_session.assert_not_called()
    post.assert_not_called()
    assert breaker.failure_count == 1
