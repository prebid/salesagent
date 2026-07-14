"""URL validation to prevent SSRF attacks.

Single source of truth for blocked networks and hostnames used by both
property list resolution and webhook URL validation.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Link-local / cloud-metadata ranges. ALWAYS blocked — never a legitimate webhook
# target, in any environment (this is the cloud-credential-exfiltration surface).
METADATA_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
]

# RFC-1918 private + loopback + unique-local ranges. Blocked by default, but a
# LEGITIMATE target for a trusted test/dev deployment (local receiver, Docker
# compose network), so allowed when the caller passes ``allow_private=True``.
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

# Backward-compatible union (importers depend on this name).
BLOCKED_NETWORKS = METADATA_NETWORKS + PRIVATE_NETWORKS

# Cloud-metadata hostnames — ALWAYS blocked (see METADATA_NETWORKS).
METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
    "instance-data",
}

# Localhost / Docker-internal aliases. Blocked by default, allowed with
# ``allow_private=True`` (a trusted test/dev receiver reachable by these names).
LOCAL_HOSTNAMES = {
    "localhost",
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.host.internal",
}

# Backward-compatible union (importers depend on this name).
BLOCKED_HOSTNAMES = METADATA_HOSTNAMES | LOCAL_HOSTNAMES


def check_url_ssrf(url: str, *, require_https: bool = False, allow_private: bool = False) -> tuple[bool, str]:
    """Check a URL for SSRF safety.

    Thin wrapper over :func:`resolve_and_validate_target` for callers that only
    need a yes/no (e.g. registration-time validation). Delivery callers should use
    ``resolve_and_validate_target`` and CONNECT to the returned IP (connection
    pinning) so the address checked is the address used.
    """
    _ip, error = resolve_and_validate_target(url, require_https=require_https, allow_private=allow_private)
    return (error == ""), error


def resolve_and_validate_target(
    url: str, *, require_https: bool = False, allow_private: bool = False
) -> tuple[str | None, str]:
    """Validate a URL for SSRF safety and return a single validated IP to pin.

    Resolves the hostname ONCE, validates EVERY resolved A/AAAA record, and returns
    ``(pinned_ip, "")`` where ``pinned_ip`` is a validated address the caller should
    connect to directly — eliminating the re-resolution / DNS-rebinding gap between
    validation and connection. On any failure returns ``(None, error_message)``.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes.
        allow_private: If True, permit loopback / RFC-1918 private targets and
            localhost/Docker aliases (a trusted test/dev receiver). Cloud-metadata
            and link-local ranges/hostnames (169.254.x, fe80::,
            metadata.google.internal) remain blocked regardless.
    """
    try:
        parsed = urlparse(url)

        if require_https:
            if parsed.scheme != "https":
                return None, f"URL must use HTTPS scheme, got '{parsed.scheme}'"
        elif parsed.scheme not in ("http", "https"):
            return None, "URL must use http or https protocol"

        hostname = parsed.hostname
        if not hostname:
            return None, "URL must have a valid hostname"

        lowered = hostname.lower()
        if lowered in METADATA_HOSTNAMES:
            return None, f"URL hostname '{hostname}' is a blocked cloud-metadata endpoint"
        if lowered in LOCAL_HOSTNAMES and not allow_private:
            return None, f"URL hostname '{hostname}' is blocked (internal/private)"

        try:
            resolved = _resolve_ips(hostname)
        except OSError:
            return None, f"Cannot resolve hostname: {hostname}"
        if not resolved:
            return None, f"Cannot resolve hostname: {hostname}"

        # Validate EVERY resolved A/AAAA record — a hostname with one public and one
        # private/IPv6 record must not pass on the strength of its public record
        # (multi-record / DNS-rebinding surface). The caller connects to the returned
        # address (connection pinning) so the checked IP is the one actually used.
        for ip_str in resolved:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return None, f"Invalid IP address from hostname resolution: {ip_str}"

            # Metadata / link-local is ALWAYS blocked (checked before the private
            # ranges because Python classifies link-local as private too).
            if any(ip in network for network in METADATA_NETWORKS) or ip.is_link_local:
                return None, f"URL resolves to a link-local/metadata IP address: {ip}"

            if not allow_private:
                if any(ip in network for network in PRIVATE_NETWORKS) or ip.is_loopback or ip.is_private:
                    return None, f"URL resolves to private/internal IP address: {ip}"

        # Every record validated — pin to the first (a literal-IP host pins to itself).
        return resolved[0], ""

    except Exception as e:
        return None, f"Invalid URL: {e}"


def _resolve_ips(hostname: str) -> list[str]:
    """Resolve a hostname to ALL of its A/AAAA records (not just the first IPv4).

    SSRF validation must consider every address the connection could use: a single
    ``gethostbyname`` call returns one IPv4 and would miss a second (private) record
    or an IPv6 record. Raises ``OSError``/``socket.gaierror`` when the name does not
    resolve. Deduplicates while preserving order.
    """
    infos = socket.getaddrinfo(hostname, None)
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(str(info[4][0]), None)
    return list(seen)
