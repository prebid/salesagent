"""virtual_host is mandatory

Revision ID: 7f31c0ab94d2
Revises: 390461e816ea
Create Date: 2026-09-28 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f31c0ab94d2"
down_revision: str | Sequence[str] | None = "e7a2c40b91d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _parsed_host(host: str | None) -> tuple[str, str]:
    """``(folded, name)`` for *host*, or a ValueError naming why it is not a host.

    Inlined rather than imported from ``src.core.http_utils``: a revision has to keep
    behaving the way it did when it ran, and an application helper is free to change --
    if the application later accepts a shape this revision refused, this revision must
    still refuse it, because the rows it already admitted were admitted under these rules.
    That is why this is not the DRY violation it resembles: the two answer different
    questions, one fixed at this revision and one current.

    ``urlsplit`` is the parser, so nothing here takes a URL apart by hand. The checks are
    the same axes the application validates -- scheme, path, query, fragment, userinfo,
    port, whitespace -- because the derived name below makes a malformed row REACHABLE: a
    stored ``user@host.com`` derives the name ``host.com``, and a request naming that host
    would then be served a card advertising ``https://user@host.com/a2a``.
    """
    from urllib.parse import urlsplit

    folded = (host or "").strip().lower()
    if not folded:
        raise ValueError("is blank")
    if any(ch.isspace() for ch in folded):
        raise ValueError("contains whitespace, so no Host header can name it")
    try:
        parts = urlsplit(f"//{folded}")
    except ValueError as exc:
        # urllib's own text ("Invalid IPv6 URL") names no tenant, and it would abort the
        # upgrade instead of reporting the row.
        raise ValueError("is not parseable as a host") from exc
    if parts.path or parts.query or parts.fragment:
        raise ValueError("carries a scheme or a path, not a bare host")
    if parts.netloc != folded or "@" in parts.netloc:
        raise ValueError("is not a bare host[:port]")
    if folded.endswith(":"):
        raise ValueError("ends with a colon but names no port")
    try:
        parts.port  # noqa: B018 — raises for a non-numeric port
    except ValueError as exc:
        raise ValueError("has a non-numeric port") from exc
    if not parts.hostname:
        raise ValueError("names no host")
    return folded, parts.hostname


def upgrade() -> None:
    """Make ``tenants.virtual_host`` NOT NULL.

    A tenant declares the host it is served at, always: a request names a tenant by ``Host``
    against this column or by the ``x-adcp-tenant`` literal id (#2191), so a row holding NULL
    is unreachable by ``Host`` at all.

    This REFUSES rather than backfills. A migration cannot know where a deployment answers,
    and any host it could invent is a name nothing serves, published on that tenant's agent
    card as its own (#1845). The operator can know, so the refusal names each offending row
    and carries the statement that fixes it. Rows are not deleted either: losing a
    publisher's tenant is worse than running one UPDATE.

    It also adds ``virtual_host_name`` -- the host without its port -- and keys uniqueness on
    it. Populating a new NOT NULL column is initialising the structure being added, not
    preserving data across the change: ``downgrade`` drops the column outright rather than
    putting anything back, and nothing here rewrites ``virtual_host`` itself.

    Case needs no separate refusal: ``_hostname_of`` folds, so a legacy row holding
    ``Host.com`` derives ``host.com`` and routes correctly without its raw value being
    touched. DNS is case-insensitive (RFC 7230 §5.4), so the card publishing the stored
    spelling is still dialled.
    """
    offenders = [
        row[0]
        for row in op.get_bind().execute(sa.text("SELECT tenant_id FROM tenants WHERE virtual_host IS NULL")).fetchall()
    ]
    if offenders:
        named = ", ".join(repr(tenant_id) for tenant_id in offenders)
        statements = "\n".join(
            f"  UPDATE tenants SET virtual_host = '<the host this tenant is served at>' "
            f"WHERE tenant_id = '{tenant_id}';"
            for tenant_id in offenders
        )
        raise RuntimeError(
            f"{len(offenders)} tenant(s) declare no virtual_host: {named}.\n"
            "virtual_host is the address a tenant is served at and the only way a request "
            "can name it, so this revision makes it mandatory. It will not guess a value: a "
            "host derived from a subdomain is a name nothing serves, published on the agent "
            "card as the tenant's own (#1845). Set each one to the host that deployment "
            "really answers at, then re-run the migration:\n"
            f"{statements}"
        )

    # The key routing resolves by is the host's NAME, port aside: the same tenant answers at
    # `host` and at `host:8443` (@T-TENANTID-host-with-port). It is stored in its own column
    # so that only stdlib ever parses the URL -- an index expression would have to take the
    # host apart in SQL, which is incorrect for a bracketed IPv6 literal.
    op.add_column("tenants", sa.Column("virtual_host_name", sa.Text(), nullable=True))

    rows = op.get_bind().execute(sa.text("SELECT tenant_id, virtual_host FROM tenants")).fetchall()

    # Refuses SHAPE too, for the same reason it refuses NULL: this revision derives a name
    # from the stored origin, and a name makes the row reachable. The application validator
    # refuses every one of these shapes, so such a row predates it or came from direct SQL.
    malformed = []
    derived = {}
    for row in rows:
        try:
            _, derived[row.tenant_id] = _parsed_host(row.virtual_host)
        except ValueError as exc:
            malformed.append((row.tenant_id, row.virtual_host, str(exc)))
    if malformed:
        named = "\n".join(f"  {tenant_id!r} holds {host!r}, which {why}" for tenant_id, host, why in malformed)
        raise RuntimeError(
            f"{len(malformed)} tenant(s) store a virtual_host that is not a host a request can name:\n"
            f"{named}\n"
            "This revision derives the routing key from this column, which would make each of "
            "these rows REACHABLE -- a stored 'user@host.com' derives the name 'host.com', and "
            "a request naming that host would be served a card advertising "
            "'https://user@host.com/a2a'. Set each one to the host that deployment really "
            "answers at, then re-run the migration."
        )

    # Refused rather than resolved, like the NULLs above: which of two tenants claiming one
    # name keeps it is not a decision a migration can make.
    claimed: dict[str, list[str]] = {}
    for tenant_id, name in derived.items():
        claimed.setdefault(name, []).append(tenant_id)
    collisions = {name: ids for name, ids in claimed.items() if len(ids) > 1}
    if collisions:
        named = "\n".join(f"  {name!r} is claimed by: {', '.join(ids)}" for name, ids in sorted(collisions.items()))
        raise RuntimeError(
            f"{len(collisions)} host name(s) are claimed by more than one tenant:\n{named}\n"
            "A port says how a deployment is reached, not which seller it is, so "
            "'host' and 'host:8443' are ONE address and only one tenant can hold it. This "
            "revision makes that a unique index. Decide which tenant keeps each name and "
            "change or deactivate the others, then re-run the migration. Case is part of it: "
            "'Host.com' and 'host.com' are the same name."
        )

    for tenant_id, name in derived.items():
        op.get_bind().execute(
            sa.text("UPDATE tenants SET virtual_host_name = :name WHERE tenant_id = :tenant_id"),
            {"name": name, "tenant_id": tenant_id},
        )

    op.alter_column("tenants", "virtual_host", existing_type=sa.Text(), nullable=False)
    op.alter_column("tenants", "virtual_host_name", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_tenants_virtual_host", table_name="tenants")
    op.create_index("ux_tenants_virtual_host_name", "tenants", ["virtual_host_name"], unique=True)


def downgrade() -> None:
    """Let ``tenants.virtual_host`` be NULL again, keyed on the raw column."""
    op.drop_index("ux_tenants_virtual_host_name", table_name="tenants")
    op.create_index("ix_tenants_virtual_host", "tenants", ["virtual_host"], unique=True)
    op.drop_column("tenants", "virtual_host_name")
    op.alter_column("tenants", "virtual_host", existing_type=sa.Text(), nullable=True)
