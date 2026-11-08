"""legacy publisher selections to explicit selectors

Revision ID: 3c38b1ee241f
Revises: f9c29da38437
Create Date: 2026-11-07 21:00:00.000000

A product selected its publishers one of three ways: explicit ``properties`` selectors,
or the legacy ``property_tags`` / ``property_ids`` columns, which name no publisher and
were resolved against the tenant's authorized properties on every read (#1845, review of
#2330). This revision rewrites every legacy selection into explicit per-publisher
selectors and drops the two columns, so ``properties`` is the one place a product's
selection lives (owner decision, 2026-11-07):

- ``property_ids`` becomes one ``by_id`` selector per publisher domain of those
  properties;
- ``property_tags`` becomes one ``by_tag`` selector per publisher domain whose authorized
  properties carry any of the tags (``all_inventory`` is the seller's own tag, carried by
  every publisher the tenant has a property of);
- an empty selection (``property_tags = []``, "every publisher") becomes one ``all``
  selector per publisher domain of the tenant.

Authorized properties of any verification status count: what is SOLD is decided on read,
per property, by ``authorized_selectors``. The meaning "a publisher verified later that
carries the tag" is not preserved, by decision.

A row whose legacy selection matches no authorized property has no publisher to name, and
picking one is not this migration's decision: the upgrade stops and names each such row
(the refusal pattern of ``src/core/database/migration_guards.py``).

Downgrade restores the columns and both check constraints as structure only; the
selections stay in ``properties``.
"""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c38b1ee241f"
down_revision: str | Sequence[str] | None = "f9c29da38437"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The seller's default tag, carried by every publisher the tenant holds a property of.
_ALL_INVENTORY = "all_inventory"


def _publisher_domain(stored: str) -> str:
    """The host *stored* names, lower case, without scheme, path or trailing dot.

    Inlined rather than imported from ``src.core.http_utils``: a revision has to keep
    behaving as it did when it ran, and the application helper is free to change.
    """
    folded = (stored or "").strip().lower()
    host = urlsplit(folded if "//" in folded else f"//{folded}").netloc or folded
    return host.rstrip(".")


def legacy_selectors(
    property_tags: Sequence[str] | None,
    property_ids: Sequence[str] | None,
    authorized: Sequence[tuple[str, str, Sequence[str]]],
) -> list[dict]:
    """The explicit selectors a legacy selection converts to, or ``[]`` when it matches nothing.

    *authorized* are the tenant's authorized properties as ``(property_id, publisher_domain,
    tags)``, any verification status. Publishers come out sorted by domain.
    """
    by_domain: dict[str, list[tuple[str, Sequence[str]]]] = {}
    for property_id, domain, tags in authorized:
        by_domain.setdefault(_publisher_domain(domain), []).append((property_id, tags or ()))
    domains = sorted(by_domain)
    if property_ids:
        selectors = []
        for domain in domains:
            ids = [i for i in property_ids if i in {pid for pid, _ in by_domain[domain]}]
            if ids:
                selectors.append({"publisher_domain": domain, "property_ids": ids, "selection_type": "by_id"})
        return selectors
    if property_tags:
        selectors = []
        for domain in domains:
            carried = {tag for _, tags in by_domain[domain] for tag in tags} | {_ALL_INVENTORY}
            matched = [t for t in dict.fromkeys(property_tags) if t in carried]
            if matched:
                selectors.append({"publisher_domain": domain, "property_tags": matched, "selection_type": "by_tag"})
        return selectors
    return [{"publisher_domain": domain, "selection_type": "all"} for domain in domains]


def upgrade() -> None:
    bind = op.get_bind()
    authorized: dict[str, list[tuple[str, str, Sequence[str]]]] = {}
    for tenant_id, property_id, domain, tags in bind.execute(
        sa.text(
            "SELECT tenant_id, property_id, publisher_domain, tags FROM authorized_properties "
            "ORDER BY tenant_id, publisher_domain, property_id"
        )
    ):
        authorized.setdefault(tenant_id, []).append((property_id, domain, tags or []))

    legacy = bind.execute(
        sa.text(
            "SELECT tenant_id, product_id, property_tags, property_ids FROM products "
            "WHERE properties IS NULL ORDER BY tenant_id, product_id"
        )
    ).fetchall()
    converted, unmatched = [], []
    for tenant_id, product_id, property_tags, property_ids in legacy:
        selectors = legacy_selectors(property_tags, property_ids, authorized.get(tenant_id, []))
        if selectors:
            converted.append({"tid": tenant_id, "pid": product_id, "props": json.dumps(selectors)})
        else:
            unmatched.append(
                f"  tenant={tenant_id} product={product_id} property_tags={property_tags} property_ids={property_ids}"
            )
    if unmatched:
        raise RuntimeError(
            f"{len(unmatched)} product(s) select their publishers by legacy property tags or ids that match no "
            "authorized property of their tenant, so they cannot be converted to explicit selectors:\n"
            + "\n".join(unmatched)
            + "\nAdd the authorized properties these selections mean, or give each product explicit publisher "
            "selectors in the admin UI, then re-run the migration. This migration does not choose a publisher "
            "for a product."
        )
    # Both checks name the columns this drops, and forbid a row holding properties and tags
    # at once: ck_product_properties_xor (eef85c5fe627), ck_product_authorization (3d2f7ff99896).
    op.drop_constraint("ck_product_properties_xor", "products", type_="check")
    op.drop_constraint("ck_product_authorization", "products", type_="check")
    for row in converted:
        bind.execute(
            sa.text(
                "UPDATE products SET properties = CAST(:props AS jsonb) WHERE tenant_id = :tid AND product_id = :pid"
            ),
            row,
        )
    op.drop_column("products", "property_tags")
    op.drop_column("products", "property_ids")
    op.alter_column("products", "properties", existing_type=sa.dialects.postgresql.JSONB(), nullable=False)


def downgrade() -> None:
    op.alter_column("products", "properties", existing_type=sa.dialects.postgresql.JSONB(), nullable=True)
    op.add_column("products", sa.Column("property_ids", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("property_tags", sa.dialects.postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_product_properties_xor",
        "products",
        "(properties IS NOT NULL AND property_tags IS NULL) OR (properties IS NULL AND property_tags IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_product_authorization",
        "products",
        "(properties IS NOT NULL AND property_ids IS NULL AND property_tags IS NULL) OR "
        "(properties IS NULL AND property_ids IS NOT NULL AND property_tags IS NULL) OR "
        "(properties IS NULL AND property_ids IS NULL AND property_tags IS NOT NULL)",
    )
