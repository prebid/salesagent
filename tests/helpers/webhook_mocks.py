"""Shared mock setup for ``WebhookDeliveryService`` unit tests.

Every test that exercises the service has to stand up the same two things: a
webhook-config row for the service to read, and an ``httpx.Client`` whose
``post`` returns a chosen status. That pair was copy-pasted across
``test_webhook_delivery_service.py`` and ``test_delivery.py``, so a change to
the config shape (a new column the service reads, say) meant editing five
blocks and silently passing in whichever one was missed -- ``MagicMock``
answers any attribute, so a stale copy does not fail, it just stops testing
the thing it names.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_webhook_config(
    *,
    url: str = "https://example.com/webhook",
    authentication_type: str | None = None,
    authentication_token: str | None = None,
    validation_token: str | None = None,
    webhook_secret: str | None = None,
    **overrides: Any,
) -> MagicMock:
    """Build a webhook-config row mock with every field the service reads set.

    Set explicitly rather than left to ``MagicMock``'s auto-attribute: an
    unset attribute returns a truthy ``MagicMock``, so a config that means
    "unsigned" would read as "signed with a Mock secret" and the test would
    exercise a branch it did not intend.
    """
    config = MagicMock()
    config.url = url
    config.authentication_type = authentication_type
    config.authentication_token = authentication_token
    config.validation_token = validation_token
    config.webhook_secret = webhook_secret
    for name, value in overrides.items():
        setattr(config, name, value)
    return config


def mock_httpx_post(mock_client: MagicMock, *, status_code: int = 200) -> MagicMock:
    """Wire *mock_client* (a patched ``httpx.Client``) to return *status_code*.

    Returns the ``post`` mock so callers can read ``call_args`` for the bytes
    and headers actually transmitted.
    """
    response = MagicMock()
    response.status_code = status_code
    post = mock_client.return_value.__enter__.return_value.post
    post.return_value = response
    return post


def serve_webhook_configs(mock_session: MagicMock, *configs: MagicMock) -> MagicMock:
    """Make *mock_session* return *configs* from the SQLAlchemy 2.0 read path.

    The service does ``session.scalars(stmt).all()``; spelling that chain by
    hand in each test is what drifted.
    """
    mock_session.scalars.return_value.all.return_value = list(configs)
    return mock_session
