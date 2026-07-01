"""URL validation to prevent SSRF attacks.

Single source of truth for blocked networks and hostnames used by both
property list resolution and webhook URL validation.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.sync import SyncBackend

# Blocked IP ranges (RFC 1918 private networks, loopback, link-local)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Blocked hostnames (cloud metadata services, localhost aliases, Docker-internal hostnames)
BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
    "instance-data",
    # Docker-internal hostnames that resolve to private/loopback IPs and
    # are not guaranteed to be caught by DNS resolution in all environments
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.host.internal",
}


def _ip_blocked_reason(ip_str: str) -> str | None:
    """Return the SSRF rejection reason for ``ip_str`` (full ``URL ...`` message), or None if safe."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as e:
        return f"Invalid IP address from hostname resolution: {e}"
    for network in BLOCKED_NETWORKS:
        if ip in network:
            return f"URL resolves to blocked IP range {network} (private/internal network)"
    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return f"URL resolves to private/internal IP address: {ip}"
    return None


def _parse_scheme_host(url: str, require_https: bool) -> tuple[str | None, str]:
    """Parse + scheme-check + hostname-blocklist. Returns (hostname, "") or (None, error)."""
    parsed = urlparse(url)
    if require_https:
        if parsed.scheme != "https":
            return None, f"URL must use HTTPS scheme, got '{parsed.scheme}'"
    elif parsed.scheme not in ("http", "https"):
        return None, "URL must use http or https protocol"
    hostname = parsed.hostname
    if not hostname:
        return None, "URL must have a valid hostname"
    if hostname.lower() in BLOCKED_HOSTNAMES:
        return None, f"URL hostname '{hostname}' is blocked (internal/private)"
    return hostname, ""


def check_url_ssrf(url: str, *, require_https: bool = False) -> tuple[bool, str]:
    """Check a URL for SSRF safety.

    Validates that the URL does not target private/internal networks
    or cloud metadata services.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes. If False,
            allow both HTTP and HTTPS.

    Returns:
        (is_safe, error_message) -- is_safe is True if the URL is safe,
        error_message describes the problem if not.

    NOTE: a bool-only verdict cannot pin the connection, so a caller that fetches
    after this returns is exposed to the DNS-rebinding TOCTOU (this validates one
    resolved IP; the HTTP client re-resolves at connect). Buyer-controlled fetches
    must use ``resolve_validated_ip`` + ``ssrf_pinned_transport`` instead.
    """
    try:
        hostname, error = _parse_scheme_host(url, require_https)
        if hostname is None:
            return False, error
        try:
            ip_str = socket.gethostbyname(hostname)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"
        reason = _ip_blocked_reason(ip_str)
        if reason is not None:
            return False, reason
        return True, ""
    except Exception as e:
        return False, f"Invalid URL: {e}"


def resolve_validated_ip(url: str, *, require_https: bool = False) -> tuple[str | None, str]:
    """Resolve ``url``'s host and validate EVERY resolved address; return (validated_ip, "") or (None, error).

    Unlike :func:`check_url_ssrf` (which validates a single ``gethostbyname`` result), this
    validates ALL ``getaddrinfo`` addresses — closing the multi-A-record bypass where the
    first record is public but another is private — and returns a safe IP so the caller can
    PIN the TCP connection to it (:func:`ssrf_pinned_transport`). Pinning closes the
    resolve-vs-connect DNS-rebinding TOCTOU a bool-only validator leaves open.
    """
    try:
        hostname, error = _parse_scheme_host(url, require_https)
        if hostname is None:
            return None, error
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return None, f"Cannot resolve hostname: {hostname}"
        ips = [str(info[4][0]) for info in infos]
        if not ips:
            return None, f"Cannot resolve hostname: {hostname}"
        for ip_str in ips:
            reason = _ip_blocked_reason(ip_str)
            if reason is not None:
                return None, reason
        return ips[0], ""
    except Exception as e:
        return None, f"Invalid URL: {e}"


class _PinnedSyncBackend(SyncBackend):
    """httpcore sync backend that connects to a pre-validated IP instead of re-resolving the host."""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    def connect_tcp(self, host: str, port: int, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        return super().connect_tcp(self._ip, port, *args, **kwargs)


class _PinnedAsyncBackend(AnyIOBackend):
    """Async dual of :class:`_PinnedSyncBackend`."""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def connect_tcp(self, host: str, port: int, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        return await super().connect_tcp(self._ip, port, *args, **kwargs)


def ssrf_pinned_transport(validated_ip: str) -> httpx.HTTPTransport:
    """A sync httpx transport whose TCP connect is pinned to ``validated_ip``.

    The request URL keeps its hostname, so httpx's TLS SNI and certificate-hostname
    verification run normally — a wrong/rebound IP whose certificate does not match the
    hostname FAILS (verified). Only the connect target is pinned to the already-validated
    IP from :func:`resolve_validated_ip`, closing the SSRF DNS-rebinding TOCTOU.

    Implementation note: overrides httpcore's private ``_network_backend`` (httpcore 1.0.9
    via httpx 0.28.1 — the only safe seam; the public ``sni_hostname`` request extension
    does NOT verify the cert hostname). Guarded by ``test_ssrf_url_validator`` so an
    httpcore-internals change reddens rather than silently disabling the pin.
    """
    transport = httpx.HTTPTransport()
    transport._pool._network_backend = _PinnedSyncBackend(validated_ip)
    return transport


def ssrf_pinned_async_transport(validated_ip: str) -> httpx.AsyncHTTPTransport:
    """Async dual of :func:`ssrf_pinned_transport`."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool._network_backend = _PinnedAsyncBackend(validated_ip)
    return transport
