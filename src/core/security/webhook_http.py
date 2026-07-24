"""Single SSRF-safe HTTP transport for outbound webhook delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy

from src.core.bounded_executor import AsyncThreadPoolBulkhead, SyncThreadPoolBulkhead
from src.core.security.url_validator import resolve_and_validate_target
from src.core.webhook_validator import (
    _allow_private_webhook_targets,
    webhook_url_has_embedded_credentials,
)

# AdCP legacy webhook authentication schemes, spelled exactly as the protocol
# does. ``core/push_notification_config.json`` (v3.1.1) enumerates
# ``['Bearer']`` and ``['HMAC-SHA256']``, and the A2A/REST intake stores
# ``authentication.scheme`` verbatim — so a conformant config is capitalized.
# Comparing against a lowercase literal silently produced an unauthenticated
# delivery, which is why every comparison now goes through ``is_auth_scheme``.
BEARER_AUTH_SCHEME = "Bearer"
HMAC_AUTH_SCHEME = "HMAC-SHA256"


def is_auth_scheme(configured: str | None, scheme: str) -> bool:
    """Match a stored ``authentication_type`` against an AdCP scheme, case-insensitively.

    The comparison is case-insensitive because rows predating the protocol
    spelling exist; the canonical spelling is the constant, not the stored value.
    """
    return configured is not None and configured.casefold() == scheme.casefold()


WEBHOOK_DNS_TIMEOUT_SECONDS = 2.0
WEBHOOK_DELIVERY_DEADLINE_SECONDS = 12.0
WEBHOOK_DELIVERY_MAX_WORKERS = 4
_DELIVERY_DNS_BULKHEAD = SyncThreadPoolBulkhead(
    max_workers=WEBHOOK_DELIVERY_MAX_WORKERS,
    thread_name_prefix="webhook-dns",
)
_ASYNC_WEBHOOK_DELIVERY_BULKHEAD = AsyncThreadPoolBulkhead(
    max_workers=WEBHOOK_DELIVERY_MAX_WORKERS,
    thread_name_prefix="webhook-delivery",
)


class UnsafeWebhookTargetError(requests.RequestException):
    """A webhook target cannot be connected to without violating SSRF policy."""


def _resolve_delivery_target(
    url: str,
    *,
    require_https: bool,
    allow_private: bool,
) -> tuple[str | None, str]:
    """Resolve one target in a bounded DNS bulkhead with a hard deadline."""
    try:
        return _DELIVERY_DNS_BULKHEAD.run(
            resolve_and_validate_target,
            url,
            require_https=require_https,
            allow_private=allow_private,
            timeout_seconds=WEBHOOK_DNS_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise requests.Timeout("Webhook target resolution timed out") from exc


class PinningHTTPAdapter(HTTPAdapter):
    """Resolve, validate, and connect to the same IP for every webhook attempt."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: bool | str | None,
        proxies: Mapping[str, str] | None = None,
        cert: Any = None,
    ) -> Any:
        url = request.url or ""
        if webhook_url_has_embedded_credentials(url):
            raise UnsafeWebhookTargetError(
                "Webhook delivery refused: callback URLs must not contain embedded credentials"
            )
        allow_private = _allow_private_webhook_targets()
        pinned_ip, ssrf_error = _resolve_delivery_target(
            url,
            require_https=not allow_private,
            allow_private=allow_private,
        )
        if pinned_ip is None:
            raise UnsafeWebhookTargetError(f"Webhook URL failed SSRF validation: {ssrf_error}")

        # A proxy would resolve the original hostname again and bypass pinning.
        if select_proxy(url, proxies):
            raise UnsafeWebhookTargetError(
                "Webhook delivery refused: a proxy is configured for this target, "
                "which would bypass SSRF connection-pinning. Webhook egress must be direct."
            )

        resolved_verify: bool | str = True if verify is None else verify
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, resolved_verify, cert)
        hostname = host_params["host"]
        host_params["host"] = pinned_ip
        pinned_kwargs: dict[str, Any] = dict(pool_kwargs)
        if host_params["scheme"] == "https":
            # The socket is IP-pinned, while TLS SNI and certificate checks remain
            # bound to the original hostname.
            pinned_kwargs["server_hostname"] = hostname
            pinned_kwargs["assert_hostname"] = hostname
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=cast(Any, pinned_kwargs))


def create_pinned_webhook_session() -> requests.Session:
    """Create a direct-only requests session with the pinning adapter mounted."""
    session = requests.Session()
    session.trust_env = False
    adapter = PinningHTTPAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def webhook_host_header(url: str) -> str:
    """Return requests' canonical HTTP Host without leaking URL userinfo.

    ``requests`` IDNA-encodes Unicode hostnames while preparing the request.
    Build the explicit Host header from that same prepared URL so the header,
    TLS SNI, and certificate hostname all use one ASCII representation.
    """
    prepared = requests.Request(method="POST", url=url).prepare()
    parsed = urlparse(prepared.url or url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{hostname}:{parsed.port}" if parsed.port is not None else hostname


def post_webhook_status(
    session: requests.Session,
    url: str,
    *,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> int:
    """POST exact bytes through the pinned transport and return only the status."""
    response = session.post(
        url,
        data=body,
        headers={**headers, "Host": webhook_host_header(url)},
        timeout=timeout,
        allow_redirects=False,
        stream=True,
    )
    try:
        return response.status_code
    finally:
        response.close()


async def post_webhook_status_async(
    session: requests.Session,
    url: str,
    *,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
    deadline_seconds: float = WEBHOOK_DELIVERY_DEADLINE_SECONDS,
) -> int:
    """POST off-loop within a capacity-limited end-to-end deadline.

    Caller cancellation never frees a worker permit while the underlying
    synchronous request is still running. This prevents slow DNS or socket I/O
    from turning repeated timeouts into an unbounded default-executor queue.
    """
    try:
        async with asyncio.timeout(deadline_seconds):
            return await _ASYNC_WEBHOOK_DELIVERY_BULKHEAD.run(
                post_webhook_status,
                session,
                url,
                body=body,
                headers=headers,
                timeout=timeout,
            )
    except TimeoutError as exc:
        raise requests.Timeout("Webhook delivery deadline exceeded") from exc
