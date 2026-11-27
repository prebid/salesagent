"""record the booked format contract of each package that selected none

Revision ID: a3a9e980762e
Revises: e36bcd73e4b0
Create Date: 2026-11-27 12:05:04.703792

A package is held to the format contract it was booked on, never to its product as it is
now. create_media_buy.mdx (v3.2.7) line 339: "validation and serving use the package-time
contract, never mutable Product discovery". A package that selected declarations records them
in ``package_config["formats_to_provide"]``. From this change on, create records the product's
contract under ``package_config["booked_format_contract"]`` for a package that selected none:
its request named no selector ("omitting both selects all formats supported by the product",
``media-buy/package-request.json``), or its product has no canonical arm. The approval and the
retroactive push read one of the two (``format_resolver.booked_format_contract``) and refuse a
package with neither.

This revision writes ``booked_format_contract`` for every package stored before then that
selected nothing, from its product's contract at migration time. That contract is read by
``format_resolver.linked_format_contract``, the rule ``Product.format_contract`` applies, over
the product's own arms, its linked inventory profile's, and its tenant's publisher catalog.
A package whose product row no longer exists is left as it is: the gate reads no contract for
a package without a product.

Keeping the rows is an owner-approved exception to CLAUDE.md's "Do not preserve data across a
migration". Owner ruling 2026-11-27 (#2582 F3): backfill each existing no-selector package's
booking-time contract from its product, so the gate never meets a package with no contract.

``downgrade`` removes the key from every package.
"""

import json
import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3a9e980762e"
down_revision: str | Sequence[str] | None = "e36bcd73e4b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: The key this revision writes, inlined: a revision keeps behaving the way it did when it ran.
_KEY = "booked_format_contract"

#: Each package that selected no declaration and records no booked contract, with its product.
#: ``package_config`` is a ``json`` column, so it is read as ``jsonb`` once, in ``pc``.
_UNRECORDED_SQL = f"""
    SELECT mp.media_buy_id, mp.package_id, mb.tenant_id, pc.config ->> 'product_id' AS product_id
      FROM media_packages AS mp
      JOIN media_buys AS mb ON mb.media_buy_id = mp.media_buy_id
     CROSS JOIN LATERAL (SELECT mp.package_config::jsonb AS config) AS pc
     WHERE pc.config ->> 'product_id' IS NOT NULL
       AND COALESCE(jsonb_typeof(pc.config -> '{_KEY}'), 'null') = 'null'
       AND CASE jsonb_typeof(pc.config -> 'formats_to_provide')
             WHEN 'array' THEN jsonb_array_length(pc.config -> 'formats_to_provide') = 0
             ELSE true
           END
     ORDER BY mp.media_buy_id, mp.package_id
"""

_PRODUCT_SQL = """
    SELECT p.format_options, p.format_ids, ip.format_options AS profile_format_options,
           ip.format_ids AS profile_format_ids, ip.id AS profile_id
      FROM products AS p
      LEFT JOIN inventory_profiles AS ip ON ip.id = p.inventory_profile_id
     WHERE p.tenant_id = :tenant_id AND p.product_id = :product_id
"""

_PUBLISHER_OPTIONS_SQL = """
    SELECT publisher_domain, format_option_id, declaration, applies_to_property_ids, applies_to_property_tags
      FROM publisher_format_options
     WHERE tenant_id = :tenant_id
"""

_RECORD_SQL = f"""
    UPDATE media_packages
       SET package_config = (package_config::jsonb || jsonb_build_object('{_KEY}', CAST(:record AS jsonb)))::json
     WHERE media_buy_id = :media_buy_id AND package_id = :package_id
"""

_REMOVE_SQL = f"""
    UPDATE media_packages
       SET package_config = (package_config::jsonb - '{_KEY}')::json
     WHERE package_config::jsonb -> '{_KEY}' IS NOT NULL
"""


def _contract_record(bind: Any, tenant_id: str, product_id: str) -> dict[str, list[Any]] | None:
    """The product's contract as a package row keeps it, or None when the product row is gone.

    Imported inside, not at module scope: alembic imports every revision in the directory to
    build its graph, and a module-scope import of ``src/`` would make that graph depend on the
    application package being importable.
    """
    from src.core.format_resolver import booked_contract_record, linked_format_contract
    from src.core.publisher_format_catalog import options_by_key

    product = bind.execute(sa.text(_PRODUCT_SQL), {"tenant_id": tenant_id, "product_id": product_id}).first()
    if product is None:
        return None
    profile = (
        SimpleNamespace(format_options=product.profile_format_options, format_ids=product.profile_format_ids)
        if product.profile_id is not None
        else None
    )
    options = bind.execute(sa.text(_PUBLISHER_OPTIONS_SQL), {"tenant_id": tenant_id}).all()
    contract = linked_format_contract(
        profile=profile,
        format_options=product.format_options,
        format_ids=product.format_ids,
        publisher_options=options_by_key([SimpleNamespace(format_options=options)]),
    )
    return booked_contract_record(contract)


def upgrade() -> None:
    """Record each unrecorded package's product contract; leave a package whose product is gone."""
    bind = op.get_bind()
    recorded = skipped = 0
    records: dict[tuple[str, str], dict[str, list[Any]] | None] = {}
    for row in bind.execute(sa.text(_UNRECORDED_SQL)).all():
        key = (row.tenant_id, row.product_id)
        if key not in records:
            records[key] = _contract_record(bind, row.tenant_id, row.product_id)
        record = records[key]
        if record is None:
            skipped += 1
            continue
        bind.execute(
            sa.text(_RECORD_SQL),
            {"record": json.dumps(record), "media_buy_id": row.media_buy_id, "package_id": row.package_id},
        )
        recorded += 1
    logger.info(
        "Recorded the booked format contract of %d package(s); %d package(s) name a product that no longer exists.",
        recorded,
        skipped,
    )


def downgrade() -> None:
    """Remove the booked contract from every package."""
    result = op.get_bind().execute(sa.text(_REMOVE_SQL))
    logger.info("Removed the booked format contract from %d package(s).", result.rowcount)
