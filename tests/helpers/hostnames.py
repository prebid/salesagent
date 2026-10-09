"""Host and domain names for test fixtures, and the validator's refusal of one.

Stdlib only (plus ``src.core.http_utils``, which is stdlib only), so a step module or an e2e
helper can import it without pulling in factory-boy.
"""

from __future__ import annotations

from src.core.http_utils import validate_virtual_host


def dns_label(identifier: str) -> str:
    """*identifier* (a tenant id, account id or slug) with its underscores as hyphens.

    The one place a fixture turns an id into part of a host or domain name. An id may hold
    underscores; ``validate_virtual_host`` refuses them, because the admin plane cannot build
    URLs for a ``Host`` that carries one, and a domain pattern refuses them too.
    """
    return identifier.replace("_", "-")


def account_domain(account_id: str) -> str:
    """The brand and operator domain a seeded account declares."""
    return f"{dns_label(account_id)}.com"


def virtual_host_refusal(host: str) -> str:
    """``validate_virtual_host``'s refusal message for *host*, the text an entry point must surface."""
    try:
        validate_virtual_host(host)
    except ValueError as exc:
        return str(exc)
    raise AssertionError(f"{host!r} is accepted, so there is no refusal to compare with")
