"""narrow multi-publisher format options to their common intersection

``core/product.json`` ``format_options`` at the pin: "If a product spans multiple publishers
but omits placements[], there is no public routing key for per-placement creatives: its
format_options MUST therefore be the common intersection accepted across every selected
publisher/property scope." A declaration carrying ``publisher_domain`` is accepted only in
that publisher's scope (``docs/creative/formats.mdx`` § Matching and satisfaction:
"publisher-scoped options only to their publisher"), so on a product of two or more
publishers it is outside the intersection, and the intersection is exactly the product-local
declarations. Every write path now refuses such a product
(``src.core.format_satisfaction.refuse_outside_common_intersection``); this revision brings
the rows already stored into line.

WHICH ROWS. A product with no placements (NULL or ``[]``) whose served contract carries a
publisher-scoped declaration and whose publisher properties name two or more
``publisher_domain`` values. The contract and the properties are read where
``Product.format_contract`` and ``Product.effective_properties`` read them: a linked
inventory profile supplies both, except that a profile with no canonical declarations leaves
the product's own; an unlinked product's properties are its ``properties`` column (its
``property_ids`` / ``property_tags`` variants name one publisher, the tenant's).

UPGRADE rewrites the declarations where they are stored -- the product row, or the linked
profile -- to the product-local ones, in their stored order. A profile is shared, so when it
supplies the contract to such a product it is rewritten once and every product linked to it
inherits the intersection, as it inherits any profile edit.

AN EMPTY INTERSECTION IS LEFT AS STORED, and logged with each row named. That is a contract
whose every declaration is scoped to one publisher, and no rewrite yields a row the pin
admits: ``format_options`` has ``minItems: 1``, and NULL leaves a new product with no
canonical arm ("New 3.2 products MUST carry format_options"; "Products MUST carry
format_ids, format_options, or both"). The pin's remedy is the seller's to choose -- publish
placements with publisher-scoped identities, or narrow the product to fewer publishers -- and
``formats.mdx`` says such a slice "fails closed until the seller supplies placements or a
refined product", which the buy path already does. Boot is not blocked: the rows load and
serve, the admin refuses their next save until the seller picks a remedy, and a migration
that fails on startup would take every tenant down for one tenant's catalogue (the reasoning
of c7a2f10b93de).

DOWNGRADE changes nothing. This revision changes no structure, and the migration rules
revert structure only; the declarations it removed are not restored.

Revision ID: 7a74ba340f11
Revises: c4e7a2b9d1f0
Create Date: 2026-10-30 22:35:18.579772

"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a74ba340f11"
down_revision: str | Sequence[str] | None = "c4e7a2b9d1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: Every product without placements, with what its served contract and publisher
#: properties are read from. The rule is decided in Python below, row by row.
_CANDIDATES_SQL = sa.text(
    """
    SELECT p.tenant_id, p.product_id, p.format_options, p.properties,
           p.inventory_profile_id, ip.format_options AS profile_format_options,
           ip.publisher_properties AS profile_publisher_properties
      FROM products p
      LEFT JOIN inventory_profiles ip ON ip.id = p.inventory_profile_id
     WHERE p.placements IS NULL OR p.placements = '[]'::jsonb
     ORDER BY p.tenant_id, p.product_id
    """
)

_UPDATE_PRODUCT_SQL = sa.text(
    "UPDATE products SET format_options = CAST(:options AS jsonb) WHERE tenant_id = :tenant_id AND product_id = :key"
)
_UPDATE_PROFILE_SQL = sa.text("UPDATE inventory_profiles SET format_options = CAST(:options AS jsonb) WHERE id = :key")


def _scoped(declaration: Any) -> bool:
    """Whether a stored declaration is scoped to one publisher."""
    return isinstance(declaration, dict) and bool(declaration.get("publisher_domain"))


def _domains(publisher_properties: Any) -> set[str]:
    """The distinct publisher domains a stored ``publisher_properties`` document names."""
    return {
        str(selector["publisher_domain"])
        for selector in publisher_properties or []
        if isinstance(selector, dict) and selector.get("publisher_domain")
    }


def upgrade() -> None:
    """Rewrite each violating contract to its product-local declarations; log the ones with none."""
    bind = op.get_bind()
    rewrites: dict[tuple[str, Any], list[Any]] = {}
    empty: list[str] = []
    for row in bind.execute(_CANDIDATES_SQL):
        linked = row.inventory_profile_id is not None
        if linked and row.profile_format_options:
            target, key, options = "profile", row.inventory_profile_id, row.profile_format_options
        else:
            target, key, options = "product", (row.tenant_id, row.product_id), row.format_options
        publishers = row.profile_publisher_properties if linked else row.properties
        if len(_domains(publishers)) < 2 or not any(_scoped(d) for d in options or []):
            continue
        intersection = [d for d in options if not _scoped(d)]
        if intersection:
            rewrites[(target, key)] = intersection
        else:
            empty.append(f"tenant={row.tenant_id!r} product={row.product_id!r} ({target} {key!r})")

    for (target, key), intersection in rewrites.items():
        document = json.dumps(intersection)
        if target == "profile":
            bind.execute(_UPDATE_PROFILE_SQL, {"options": document, "key": key})
        else:
            tenant_id, product_id = key
            bind.execute(_UPDATE_PRODUCT_SQL, {"options": document, "tenant_id": tenant_id, "key": product_id})
    logger.info("Narrowed %d format_options document(s) to their common intersection.", len(rewrites))

    if empty:
        logger.warning(
            "%d multi-publisher product(s) without placements declare only publisher-scoped format options, "
            "so their common intersection is empty and they were left as stored: %s. Publish placements with "
            "publisher-scoped identities, or narrow each product to fewer publishers; the admin refuses their "
            "next save until then.",
            len(empty),
            "; ".join(empty),
        )


def downgrade() -> None:
    """No structure to revert: the removed declarations are not restored."""
    logger.info(
        "Downgrade of %s is a no-op by design: it changed no structure, and the format_options it narrowed "
        "stay narrowed.",
        revision,
    )
