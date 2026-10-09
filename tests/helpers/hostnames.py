"""Host and domain names for test fixtures, the validator's refusal of one, and a stored legacy host.

Imports no factory-boy (only stdlib, SQLAlchemy and ``src.core.http_utils``), so a step module
or an e2e helper can import it without pulling factory-boy in.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.http_utils import hostname_of, validate_virtual_host


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


def store_virtual_host_past_the_validator(session: Session, tenant_id: str, host: str) -> None:
    """Store *host* as the tenant's ``virtual_host`` with a raw UPDATE, and commit.

    ``Tenant.virtual_host``'s validator folds and checks every assignment, so no production
    path stores a host it refuses or a spelling it folds; such a row was stored before the rule
    changed, or by direct SQL, and this is the only way to seed one. ``virtual_host_name`` is
    written in the same statement, derived the way the migration derives it.
    """
    session.execute(
        text("UPDATE tenants SET virtual_host = :host, virtual_host_name = :name WHERE tenant_id = :tid"),
        {"host": host, "name": hostname_of(host), "tid": tenant_id},
    )
    session.commit()
