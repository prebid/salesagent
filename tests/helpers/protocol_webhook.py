"""Shared assertions for the protocol webhook transport contract."""

from unittest.mock import Mock


def assert_protocol_webhook_post(
    mock_post: Mock,
    *,
    url: str,
    body: bytes,
    host: str,
) -> None:
    """Assert the exact pinned, non-redirecting request made by the transport."""
    mock_post.assert_called_once_with(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/1.0",
            "Host": host,
        },
        timeout=10.0,
        allow_redirects=False,
        stream=True,
    )
