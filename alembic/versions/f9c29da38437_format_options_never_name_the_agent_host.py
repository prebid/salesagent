"""format options never name the agent host as their publisher

Revision ID: f9c29da38437
Revises: f7c2e8a13d64
Create Date: 2026-11-08 18:00:00.000000

``tenants.publisher_format_options`` was a seller-authored catalog, published as the
``formats[]`` of the adagents.json served at the tenant's host, and products referenced it
with ``publisher_domain`` set to that host. AdCP 3.2.7 gives that catalog to the publisher
alone: ``adagents.json#/properties/formats`` is the "Publisher-authoritative format catalog",
and ``core/product-format-declaration.json`` ``publisher_domain`` namespaces an option "from
that publisher's adagents.json top-level ``formats[]``". A tenant is a seller with publishers,
none of them primary (owner decision, 2026-11-08), so the catalog goes and this agent publishes
no ``formats[]``.

UPGRADE drops ``publisher_domain`` from every ``products.format_options`` and
``inventory_profiles.format_options`` declaration whose ``publisher_domain`` is its tenant's
agent host (``tenants.virtual_host_name``). Each stays a ``format_kind`` + ``params``
declaration and keeps its ``format_option_id``, now product-local. Then the column is dropped.

A DOCUMENT WHOSE REWRITE WOULD REPEAT A ``format_option_id`` IS LEFT AS STORED, and logged
with each row named: a product-local option already carries that id, so the two would no
longer be distinguishable. Boot is not blocked, as for 7a74ba340f11: the admin refuses the
product's next save until the seller renames or removes one.

DOWNGRADE re-adds the column, empty. The rewritten declarations are not restored.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "f9c29da38437"
down_revision: str | Sequence[str] | None = "f7c2e8a13d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_PRODUCTS_SQL = sa.text(
    """
    SELECT p.tenant_id, p.product_id, p.format_options, t.virtual_host_name
      FROM products p JOIN tenants t ON t.tenant_id = p.tenant_id
     WHERE p.format_options IS NOT NULL
     ORDER BY p.tenant_id, p.product_id
    """
)
_PROFILES_SQL = sa.text(
    """
    SELECT ip.tenant_id, ip.id, ip.format_options, t.virtual_host_name
      FROM inventory_profiles ip JOIN tenants t ON t.tenant_id = ip.tenant_id
     WHERE ip.format_options IS NOT NULL
     ORDER BY ip.tenant_id, ip.id
    """
)
_UPDATE_PRODUCT_SQL = sa.text(
    "UPDATE products SET format_options = CAST(:options AS jsonb) WHERE tenant_id = :tenant_id AND product_id = :key"
)
_UPDATE_PROFILE_SQL = sa.text(
    "UPDATE inventory_profiles SET format_options = CAST(:options AS jsonb) WHERE tenant_id = :tenant_id AND id = :key"
)


def product_local(declarations: Sequence[Any], agent_host: str) -> list[Any] | None:
    """*declarations* with ``publisher_domain`` dropped where it is *agent_host*.

    ``None`` when nothing names the host, or when the rewrite would give two declarations one
    ``format_option_id``.
    """
    rewritten, changed = [], False
    for declaration in declarations:
        if isinstance(declaration, dict) and declaration.get("publisher_domain") == agent_host:
            declaration = {key: value for key, value in declaration.items() if key != "publisher_domain"}
            changed = True
        rewritten.append(declaration)
    if not changed:
        return None
    ids = [d.get("format_option_id") for d in rewritten if isinstance(d, dict) and d.get("format_option_id")]
    if len(ids) != len(set(ids)):
        raise ValueError(f"repeats a format_option_id: {sorted({i for i in ids if ids.count(i) > 1})}")
    return rewritten


def upgrade() -> None:
    """Make every agent-host option product-local, then drop the tenant catalog."""
    bind = op.get_bind()
    rewritten = 0
    left: list[str] = []
    for rows, update, label in (
        (bind.execute(_PRODUCTS_SQL).fetchall(), _UPDATE_PRODUCT_SQL, "product"),
        (bind.execute(_PROFILES_SQL).fetchall(), _UPDATE_PROFILE_SQL, "inventory profile"),
    ):
        for tenant_id, key, options, agent_host in rows:
            try:
                document = product_local(options, agent_host)
            except ValueError as e:
                left.append(f"tenant={tenant_id!r} {label}={key!r} {e}")
                continue
            if document is not None:
                bind.execute(update, {"options": json.dumps(document), "tenant_id": tenant_id, "key": key})
                rewritten += 1
    logger.info("Made the agent-host format options of %d document(s) product-local.", rewritten)
    if left:
        logger.warning(
            "%d format_options document(s) name the agent host as publisher_domain and were left as stored, "
            "because dropping it would repeat a format_option_id: %s. Rename or remove one of each pair in the "
            "admin, which refuses the product's next save until then.",
            len(left),
            "; ".join(left),
        )
    op.drop_column("tenants", "publisher_format_options")


def downgrade() -> None:
    """Re-add the column, empty; the rewritten declarations are not restored."""
    op.add_column("tenants", sa.Column("publisher_format_options", JSONType, nullable=True))
