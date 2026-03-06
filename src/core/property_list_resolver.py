"""Property list resolver with caching.

Fetches buyer property lists from external agent services and caches
the results using the cache_valid_until TTL from the response.
"""

import ipaddress
import logging
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from adcp.types import GetPropertyListResponse, PropertyListReference

from src.core.exceptions import AdCPAdapterError

logger = logging.getLogger(__name__)

# Default timeout for HTTP requests (seconds)
_DEFAULT_TIMEOUT = 10.0

# Default cache TTL when cache_valid_until is not provided (seconds)
_DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes

# Cache: (agent_url, list_id) -> (identifier_values, expires_at)
_cache: dict[tuple[str, str], tuple[list[str], datetime]] = {}

# Blocked hostnames (cloud metadata services, localhost aliases)
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
    "instance-data",
}

# Blocked IP ranges (RFC 1918 private networks, loopback, link-local)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_agent_url(agent_url: str) -> None:
    """Validate agent_url to prevent SSRF attacks.

    Buyer-supplied agent_url must be HTTPS and must not target private/internal
    networks or cloud metadata services.

    Raises:
        AdCPAdapterError: If the URL is not allowed.
    """
    parsed = urlparse(agent_url)

    # Enforce HTTPS
    if parsed.scheme != "https":
        raise AdCPAdapterError(f"Property list agent_url must use HTTPS scheme, got '{parsed.scheme}'")

    hostname = parsed.hostname
    if not hostname:
        raise AdCPAdapterError("Property list agent_url must have a valid hostname")

    # Check against blocked hostnames
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise AdCPAdapterError(f"Property list agent_url hostname '{hostname}' is blocked (internal/private)")

    # Resolve hostname to IP and validate
    try:
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
    except socket.gaierror:
        raise AdCPAdapterError(f"Cannot resolve property list agent_url hostname: {hostname}")

    for network in _BLOCKED_NETWORKS:
        if ip in network:
            raise AdCPAdapterError(f"Property list agent_url resolves to blocked private/internal IP range ({network})")

    if ip.is_loopback or ip.is_link_local or ip.is_private:
        raise AdCPAdapterError(f"Property list agent_url resolves to private/internal IP address: {ip}")


async def resolve_property_list(ref: PropertyListReference) -> list[str]:
    """Resolve a property list reference to a list of property identifier strings.

    Fetches the property list from the agent service identified by ref.agent_url,
    caches the result using cache_valid_until from the response, and returns
    the identifier value strings.

    Args:
        ref: PropertyListReference containing agent_url, list_id, and optional auth_token.

    Returns:
        List of property identifier value strings.

    Raises:
        AdCPAdapterError: On HTTP errors, timeouts, connection failures, or SSRF violations.
    """
    agent_url_str = str(ref.agent_url)

    # Validate URL before any network I/O
    _validate_agent_url(agent_url_str)

    cache_key = (agent_url_str, ref.list_id)

    # Check cache
    if cache_key in _cache:
        identifiers, expires_at = _cache[cache_key]
        if datetime.now(UTC) < expires_at:
            logger.debug("Cache hit for property list %s/%s", ref.agent_url, ref.list_id)
            return identifiers
        else:
            del _cache[cache_key]

    # Build request
    url = agent_url_str.rstrip("/") + "/lists/" + ref.list_id
    headers: dict[str, str] = {}
    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"

    # Fetch
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AdCPAdapterError(f"Failed to fetch property list from {url}: HTTP {exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        raise AdCPAdapterError(f"Request to property list service timed out: {url}") from exc
    except httpx.RequestError as exc:
        raise AdCPAdapterError(f"Failed to connect to property list service: {url} — {exc}") from exc

    # Parse response
    parsed = GetPropertyListResponse.model_validate(response.json())

    # Extract identifier values
    identifier_values = [ident.value for ident in parsed.identifiers] if parsed.identifiers else []

    # Cache with TTL
    if parsed.cache_valid_until is not None:
        expires_at = parsed.cache_valid_until
    else:
        expires_at = datetime.now(UTC) + timedelta(seconds=_DEFAULT_CACHE_TTL_SECONDS)

    _cache[cache_key] = (identifier_values, expires_at)

    logger.debug(
        "Resolved property list %s/%s: %d identifiers (cached until %s)",
        ref.agent_url,
        ref.list_id,
        len(identifier_values),
        expires_at.isoformat(),
    )

    return identifier_values


def clear_cache() -> None:
    """Clear the property list cache."""
    _cache.clear()
