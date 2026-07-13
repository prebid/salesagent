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

    Validates that the URL does not target private/internal networks
    or cloud metadata services.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes. If False,
            allow both HTTP and HTTPS.
        allow_private: If True, permit loopback / RFC-1918 private targets and
            localhost/Docker aliases — a trusted test/dev deployment whose webhook
            receiver is on the local host or a compose network. Cloud-metadata and
            link-local ranges/hostnames (169.254.x, fe80::, metadata.google.internal)
            remain blocked regardless — they are never a legitimate target.

    Returns:
        (is_safe, error_message) -- is_safe is True if the URL is safe,
        error_message describes the problem if not.
    """
    try:
        parsed = urlparse(url)

        if require_https:
            if parsed.scheme != "https":
                return False, f"URL must use HTTPS scheme, got '{parsed.scheme}'"
        elif parsed.scheme not in ("http", "https"):
            return False, "URL must use http or https protocol"

        hostname = parsed.hostname
        if not hostname:
            return False, "URL must have a valid hostname"

        lowered = hostname.lower()
        if lowered in METADATA_HOSTNAMES:
            return False, f"URL hostname '{hostname}' is a blocked cloud-metadata endpoint"
        if lowered in LOCAL_HOSTNAMES and not allow_private:
            return False, f"URL hostname '{hostname}' is blocked (internal/private)"

        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"
        except ValueError as e:
            return False, f"Invalid IP address from hostname resolution: {e}"

        # Metadata / link-local is ALWAYS blocked (checked before the private
        # ranges because Python classifies link-local as private too).
        for network in METADATA_NETWORKS:
            if ip in network:
                return False, f"URL resolves to blocked link-local/metadata range {network}"
        if ip.is_link_local:
            return False, f"URL resolves to a link-local/metadata IP address: {ip}"

        if not allow_private:
            for network in PRIVATE_NETWORKS:
                if ip in network:
                    return False, f"URL resolves to blocked IP range {network} (private/internal network)"
            if ip.is_loopback or ip.is_private:
                return False, f"URL resolves to private/internal IP address: {ip}"

        return True, ""

    except Exception as e:
        return False, f"Invalid URL: {e}"
