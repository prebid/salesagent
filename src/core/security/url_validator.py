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


# RFC 2606 / RFC 6761 reserved TLDs. These are guaranteed never to resolve to a
# real host, so a URL under one can be judged unreachable WITHOUT a DNS lookup --
# which is what makes "this endpoint cannot be proven" deterministic instead of
# dependent on whether the local resolver happens to hijack NXDOMAIN.
#
# The six names AdCP 3.1.1 enumerates for this refusal, exhaustively:
# v3.1.1:docs/creative/canonical-formats.mdx:222 -- "RFC 6761 special-use names
# (`.local`, `.localhost`, `.internal`, `.test`, `.example`, `.invalid`)".
#
# This module OWNS the decision; it is not a shared constant callers re-match for
# themselves. Match through ``reserved_tld_for_host`` / ``is_reserved_tld_host``,
# never by iterating this set at a call site -- a call-site ``endswith`` skips the
# normalization those functions apply and silently accepts a host the owner
# refuses (the sync_accounts provisioning bug, GH #1291).
#
# Deliberately NOT applied inside ``check_url_syntax`` / ``check_url_ssrf``: the
# normative webhook-SSRF section (building/by-layer/L1/security.mdx:104-119) is a
# reserved-IP-RANGE rule and does not carry this name list, and folding it into the
# general gate would refuse the e2e stack's own ``adcp.test`` origins. Callers that
# need "can this endpoint ever be proven?" ask for it explicitly.
RESERVED_TLDS: frozenset[str] = frozenset({".test", ".invalid", ".example", ".localhost", ".local", ".internal"})


def reserved_tld_for_host(hostname: str) -> str | None:
    """Which RFC 2606/6761 reserved TLD *hostname* sits under, or None.

    The single matcher for this policy. Normalizes case and a trailing root dot,
    and matches a bare reserved LABEL (``"test"``) as well as a suffix
    (``"acme.test"``) -- all three are spellings a caller's plain ``endswith``
    misses. Returns WHICH tld matched so callers can name it in a refusal
    message without re-deriving it.
    """
    lowered = hostname.lower().rstrip(".")
    for tld in RESERVED_TLDS:
        if lowered == tld.lstrip(".") or lowered.endswith(tld):
            return tld
    return None


def is_reserved_tld_host(hostname: str) -> bool:
    """Whether *hostname* sits under an RFC 2606/6761 reserved TLD."""
    return reserved_tld_for_host(hostname) is not None


def _scheme_error(parsed: ParseResult, *, require_https: bool) -> str | None:
    if require_https:
        if parsed.scheme != "https":
            return f"URL must use HTTPS scheme, got '{parsed.scheme}'"
        return None
    if parsed.scheme not in ("http", "https"):
        return "URL must use http or https protocol"
    return None


def _ip_range_error(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, verb: str) -> str | None:
    """Error message if *ip* falls in a blocked/private range, else None.

    ``verb`` distinguishes the literal-host case ("targets") from the
    resolved-host case ("resolves to") so both callers keep their exact
    pre-extraction messages.
    """
    for network in BLOCKED_NETWORKS:
        if ip in network:
            return f"URL {verb} blocked IP range {network} (private/internal network)"
    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return f"URL {verb} private/internal IP address: {ip}"
    return None


def check_url_syntax(url: str, *, require_https: bool = False) -> tuple[bool, str]:
    """Check a URL's SHAPE without resolving DNS.

    The DNS-free half of :func:`check_url_ssrf`: scheme, hostname presence, the
    blocked-hostname list, and — crucially — the blocked/private/loopback ranges
    for URLs whose host is already an IP LITERAL.

    That last part is why this is not simply "everything before the resolve":
    in ``check_url_ssrf`` the ``BLOCKED_NETWORKS`` test runs only AFTER
    ``gethostbyname``, so a naive split would happily accept
    ``https://10.0.0.1/hook``, ``https://127.0.0.1/hook`` and ``https://[::1]/hook``.

    Use this at WRITE time, when a URL is being stored and must be well-formed but
    the host is not expected to resolve yet (a buyer may register a webhook before
    standing it up). Use :func:`check_url_ssrf` at FIRE time, when we are about to
    send a request and must know where it actually lands.

    Args:
        url: The URL to validate.
        require_https: If True, reject non-HTTPS schemes. Defaults to False to
            match :func:`check_url_ssrf` -- sibling functions with opposite
            defaults are a footgun; callers that need HTTPS pass it explicitly.

    Returns:
        (is_safe, error_message).
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

        # IP-literal hosts need no DNS to be judged -- and must not escape the
        # range checks just because resolution is skipped.
        try:
            literal_ip = ipaddress.ip_address(hostname)
        except ValueError:
            return True, ""

        range_error = _ip_range_error(literal_ip, "targets")
        if range_error:
            return False, range_error

        return True, ""

    except Exception as e:
        return False, f"Invalid URL: {e}"


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
            Equivalent to :func:`check_url_syntax`.

    Returns:
        (is_safe, error_message) -- is_safe is True if the URL is safe,
        error_message describes the problem if not.
    """
    # Shape first (scheme / hostname / blocklist / IP-literal ranges), then the
    # resolve-dependent checks. Error ordering is preserved: every message the
    # syntax half can produce is the message this function produced before the
    # extraction.
    ok, err = check_url_syntax(url, require_https=require_https)
    if not ok:
        return ok, err

    if not resolve_dns:
        return True, ""

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, "URL must have a valid hostname"

        # An IP-literal host was already range-checked by check_url_syntax;
        # nothing further to resolve.
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return True, ""

        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)
        except socket.gaierror:
            return False, f"Cannot resolve hostname: {hostname}"
        except ValueError as e:
            return False, f"Invalid IP address from hostname resolution: {e}"

        range_error = _ip_range_error(ip, "resolves to")
        if range_error:
            return False, range_error

        return True, ""

    except Exception as e:
        return False, f"Invalid URL: {e}"
