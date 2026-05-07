"""Format-only SSRF gate on ``_validate_publisher_domain``.

Closes #80 (boundary half). The DNS-time half lives in
``check_publisher`` inside ``sync_publisher_partners`` — covered by
the existing ``check_url_ssrf`` test suite in
``tests/unit/test_ssrf_url_validator.py``.

These tests assert the create-time format gate at the boundary: IP
literals, localhost-aliases, Docker-internal hostnames, and
structurally-malformed strings get rejected before they're ever
persisted, so downstream sync/discovery code never sees them.
"""

from __future__ import annotations

import pytest

from src.admin.blueprints.publisher_partners import _validate_publisher_domain

# Entity scoping: this is an admin-blueprint auth/SSRF boundary check.
pytestmark = [pytest.mark.admin, pytest.mark.auth]


# (input, expected_ok, label) covering all four classes:
#
#   - well-formed domains (accepted, including unresolvable ones — DNS check
#     is sync-time, not boundary-time)
#   - IP literals (rejected as not-a-hostname)
#   - blocked hostnames (rejected by name regardless of resolution)
#   - structurally-malformed strings (rejected before any network use)
#
# Single parametrized test rather than four near-identical class methods
# keeps the duplication-detector from flagging the test bodies.
CASES: list[tuple[str, bool, str]] = [
    # --- accepted ------------------------------------------------------
    ("example.com", True, "well-formed apex"),
    ("publisher.example.com", True, "well-formed subdomain"),
    ("very-long-subdomain.publisher.example.co.uk", True, "deep subdomain"),
    ("a.b", True, "minimal valid"),
    ("not-deployed-yet.staging.example.com", True, "unresolvable but well-formed"),
    # --- IP literals (must reject) -------------------------------------
    ("127.0.0.1", False, "IPv4 loopback"),
    ("169.254.169.254", False, "AWS/GCP metadata IP"),
    ("10.0.0.1", False, "RFC1918 10/8"),
    ("192.168.1.1", False, "RFC1918 192.168/16"),
    ("172.16.0.5", False, "RFC1918 172.16/12"),
    ("8.8.8.8", False, "public IP literal still rejected"),
    ("::1", False, "IPv6 loopback"),
    ("fe80::1", False, "IPv6 link-local"),
    # --- blocked hostnames (private even before DNS) -------------------
    ("localhost", False, "localhost"),
    ("host.docker.internal", False, "docker host"),
    ("gateway.docker.internal", False, "docker gateway"),
    ("metadata.google.internal", False, "GCP metadata"),
    ("metadata", False, "metadata short alias"),
    # --- malformed strings (rejected at format level) ------------------
    ("", False, "empty string"),
    ("no-tld", False, "missing TLD dot"),
    (".starts-with-dot.com", False, "leading dot"),
    ("ends-with-dot.com.", False, "trailing dot"),
    ("has spaces.com", False, "embedded space"),
    ("has_underscores.com", False, "underscore"),
    ("-leading-hyphen.com", False, "leading hyphen on label"),
    ("trailing-hyphen-.com", False, "trailing hyphen on label"),
    ("a." * 130 + "x", False, ">253 chars"),
]


@pytest.mark.parametrize("domain,expected_ok,label", CASES, ids=[c[2] for c in CASES])
def test_validate_publisher_domain(domain, expected_ok, label):
    ok, err = _validate_publisher_domain(domain)
    assert (
        ok is expected_ok
    ), f"{label}: _validate_publisher_domain({domain!r}) → (ok={ok}, err={err!r}); expected ok={expected_ok}"
    if expected_ok:
        assert err == "", f"{label}: accepted but error message non-empty: {err!r}"
    else:
        assert err != "", f"{label}: rejected but no error message"
