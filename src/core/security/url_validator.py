"""URL validation to prevent SSRF attacks.

Single source of truth for blocked networks and hostnames used by both
property list resolution and webhook URL validation.
"""

import ipaddress
import socket
from urllib.parse import ParseResult, urlparse

# Blocked IP ranges (RFC 1918 private networks, loopback, link-local,
# CGNAT shared space, and multicast).
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast (AdCP L1 SSRF step 2)
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known prefix (RFC 6052)
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


def _scheme_error(parsed: ParseResult, *, require_https: bool) -> str | None:
    if require_https:
        if parsed.scheme != "https":
            return f"URL must use HTTPS scheme, got '{parsed.scheme}'"
        return None
    if parsed.scheme not in ("http", "https"):
        return "URL must use http or https protocol"
    return None


def _blocked_ip_error(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    for network in BLOCKED_NETWORKS:
        if ip in network:
            return f"URL resolves to blocked IP range {network} (private/internal network)"
    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return f"URL resolves to private/internal IP address: {ip}"
    return None


def _check_hostname_resolution(hostname: str, *, resolve_dns: bool) -> tuple[bool, str]:
    """Literal-IP and optional DNS checks for a hostname already known to be non-blocked."""
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        error = _blocked_ip_error(literal_ip)
        return (False, error) if error else (True, "")

    if not resolve_dns:
        return True, ""

    try:
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"
    except ValueError as e:
        return False, f"Invalid IP address from hostname resolution: {e}"

    error = _blocked_ip_error(ip)
    return (False, error) if error else (True, "")


def check_url_ssrf(
    url: str,
    *,
    require_https: bool = False,
    resolve_dns: bool = True,
) -> tuple[bool, str]:
    """Check a URL for SSRF safety.

    Validates that the URL does not target private/internal networks
    or cloud metadata services.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes. If False,
            allow both HTTP and HTTPS.
        resolve_dns: If True (default), resolve the hostname and reject
            private/link-local results. If False, only apply scheme,
            blocked-hostname, and literal-IP checks — used at webhook
            *registration* so fixture hostnames (e.g. ``buyer.example.com``)
            are not rejected for NXDOMAIN; send-time still uses DNS.

    Returns:
        (is_safe, error_message) -- is_safe is True if the URL is safe,
        error_message describes the problem if not.
    """
    try:
        parsed = urlparse(url)
        scheme_err = _scheme_error(parsed, require_https=require_https)
        if scheme_err:
            return False, scheme_err

        hostname = parsed.hostname
        if not hostname:
            return False, "URL must have a valid hostname"

        if hostname.lower() in BLOCKED_HOSTNAMES:
            return False, f"URL hostname '{hostname}' is blocked (internal/private)"

        return _check_hostname_resolution(hostname, resolve_dns=resolve_dns)

    except Exception as e:
        return False, f"Invalid URL: {e}"
