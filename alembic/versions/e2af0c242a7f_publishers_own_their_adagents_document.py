"""publishers own their adagents.json document; selections reference them by foreign key

Revision ID: e2af0c242a7f
Revises: f9c29da38437
Create Date: 2026-11-09 12:00:00.000000

A tenant sells for publishers, and whether each authorizes this agent, for which
properties, and which format options it accepts, are stated in that publisher's
adagents.json. This revision gives that file a place to live and makes everything derived
from it a child of it:

- ``publisher_partners`` becomes ``publishers``: one row per (tenant, canonical domain),
  holding the last valid document. Every row starts ``never_fetched`` with no document.
- ``authorized_properties`` is dropped. Its properties were typed by an operator or copied
  in by a partner sync; they now come only from the publisher's file
  (``publisher_properties``). Each domain it named becomes a publisher, so the first
  refresh supplies that domain's properties.
- ``publisher_format_options`` holds each publisher's ``formats[]`` catalog.
- A product's publisher selection and an inventory profile's ``publisher_properties``
  move into ``product_publisher_selections`` / ``inventory_profile_publisher_selections``,
  each row referencing its publisher by foreign key; every domain a selector names becomes
  a publisher. The JSON columns are dropped.

A product selected its publishers one of three ways: explicit ``properties`` selectors, or
the legacy ``property_tags`` / ``property_ids`` columns, which name no publisher and were
resolved against the tenant's authorized properties on every read (#1845, review of #2330).
A legacy selection becomes explicit per-publisher selection rows (owner decision,
2026-11-07), read against ``authorized_properties`` before that table goes:

- ``property_ids`` becomes one ``by_id`` row per publisher domain of those properties;
- ``property_tags`` becomes one ``by_tag`` row per publisher domain whose authorized
  properties carry any of the tags (``all_inventory`` is the seller's own tag, carried by
  every publisher the tenant has a property of);
- an empty selection (``property_tags = []``, "every publisher") becomes one ``all`` row
  per publisher domain of the tenant.

Authorized properties of any verification status count. The meaning "a publisher verified
later that carries the tag" is not preserved, by decision. A row whose legacy selection
matches no authorized property has no publisher to name, and picking one is not this
migration's decision: the upgrade stops before changing anything and names each such row
(the refusal pattern of ``src/core/database/migration_guards.py``).

No publisher has a document after the upgrade, so no product is offered until its
publishers' first ingest. ``downgrade()`` restores the dropped tables, columns and check
constraints as structure; the selections and the documents are not carried back.
"""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2af0c242a7f"
down_revision: str | Sequence[str] | None = "f9c29da38437"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PUBLISHER_FK = (["tenant_id", "publisher_domain"], ["publishers.tenant_id", "publishers.domain"])


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


def _product_selectors(bind) -> dict[tuple[str, str], list[dict]]:
    """Every product's selectors by (tenant, product): its ``properties``, or its legacy selection converted.

    Raises before anything changes when a legacy selection matches no authorized property.
    """
    authorized: dict[str, list[tuple[str, str, Sequence[str]]]] = {}
    for tenant_id, property_id, domain, tags in bind.execute(
        sa.text(
            "SELECT tenant_id, property_id, publisher_domain, tags FROM authorized_properties "
            "ORDER BY tenant_id, publisher_domain, property_id"
        )
    ):
        authorized.setdefault(tenant_id, []).append((property_id, domain, tags or []))

    selectors: dict[tuple[str, str], list[dict]] = {}
    unmatched = []
    for tenant_id, product_id, properties, property_tags, property_ids in bind.execute(
        sa.text(
            "SELECT tenant_id, product_id, properties, property_tags, property_ids FROM products "
            "ORDER BY tenant_id, product_id"
        )
    ):
        if properties is not None:
            selectors[(tenant_id, product_id)] = properties
            continue
        converted = legacy_selectors(property_tags, property_ids, authorized.get(tenant_id, []))
        if converted:
            selectors[(tenant_id, product_id)] = converted
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
    return selectors


def _selection_type(selector: dict) -> str:
    """The selector's arm: as stored, or by the field it carries (a profile's JSON mode stored none)."""
    if selector.get("selection_type") in ("all", "by_id", "by_tag"):
        return selector["selection_type"]
    if selector.get("property_ids"):
        return "by_id"
    if selector.get("property_tags"):
        return "by_tag"
    return "all"


def _selection_rows(selectors: object) -> list[dict]:
    """The selection rows a stored selector list moves to; an entry naming no publisher is dropped."""
    rows = []
    for selector in selectors if isinstance(selectors, list) else []:
        if not isinstance(selector, dict) or not selector.get("publisher_domain"):
            continue
        kind = _selection_type(selector)
        rows.append(
            {
                "domain": _publisher_domain(str(selector["publisher_domain"])),
                "kind": kind,
                "ids": list(selector.get("property_ids") or []) if kind == "by_id" else None,
                "tags": list(selector.get("property_tags") or []) if kind == "by_tag" else None,
            }
        )
    return rows


def _selection_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("publisher_domain", sa.String(255), nullable=False),
        sa.Column("selection_type", sa.String(10), nullable=False),
        sa.Column("property_ids", postgresql.JSONB(), nullable=True),
        sa.Column("property_tags", postgresql.JSONB(), nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    # Read (and refuse) before any structure changes.
    product_selectors = _product_selectors(bind)
    op.create_table(
        "publishers",
        sa.Column("tenant_id", sa.String(50), sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("adagents", postgresql.JSONB(), nullable=True),
        sa.Column("catalog_etag", sa.String(255), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetch_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetch_status", sa.String(20), nullable=False, server_default="never_fetched"),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "domain"),
        sa.CheckConstraint(
            "fetch_status IN ('ok', 'not_found', 'invalid', 'unreachable', 'never_fetched')",
            name="ck_publishers_fetch_status",
        ),
    )
    op.create_index("idx_publishers_tenant", "publishers", ["tenant_id"])

    publishers: dict[tuple[str, str], str | None] = {}

    def hold(tenant_id: str, domain: str, display_name: str | None = None) -> None:
        if domain and (tenant_id, domain) not in publishers:
            publishers[(tenant_id, domain)] = display_name

    for tenant_id, domain, display_name in bind.execute(
        sa.text("SELECT tenant_id, publisher_domain, display_name FROM publisher_partners ORDER BY id")
    ):
        hold(tenant_id, _publisher_domain(domain), display_name)
    for tenant_id, domain in bind.execute(
        sa.text("SELECT DISTINCT tenant_id, publisher_domain FROM authorized_properties")
    ):
        hold(tenant_id, _publisher_domain(domain))

    product_rows = []
    for (tenant_id, product_id), selectors in product_selectors.items():
        for row in _selection_rows(selectors):
            hold(tenant_id, row["domain"])
            product_rows.append({"tid": tenant_id, "owner": product_id, **row})
    profile_rows = []
    for tenant_id, profile_pk, selectors in bind.execute(
        sa.text("SELECT tenant_id, id, publisher_properties FROM inventory_profiles ORDER BY id")
    ):
        for row in _selection_rows(selectors):
            hold(tenant_id, row["domain"])
            profile_rows.append({"tid": tenant_id, "owner": profile_pk, **row})

    for (tenant_id, domain), display_name in publishers.items():
        bind.execute(
            sa.text("INSERT INTO publishers (tenant_id, domain, display_name) VALUES (:tid, :domain, :name)"),
            {"tid": tenant_id, "domain": domain, "name": display_name or domain},
        )

    op.create_table(
        "publisher_properties",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("publisher_domain", sa.String(255), nullable=False),
        sa.Column("property_id", sa.String(100), nullable=True),
        sa.Column("property_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("identifiers", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("placement_ids", postgresql.JSONB(), nullable=True),
        sa.Column("countries", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(*_PUBLISHER_FK, ondelete="CASCADE"),
    )
    op.create_index("idx_publisher_properties_publisher", "publisher_properties", ["tenant_id", "publisher_domain"])
    op.create_table(
        "publisher_format_options",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("publisher_domain", sa.String(255), nullable=False),
        sa.Column("format_option_id", sa.String(255), nullable=False),
        sa.Column("declaration", postgresql.JSONB(), nullable=False),
        sa.Column("applies_to_property_ids", postgresql.JSONB(), nullable=True),
        sa.Column("applies_to_property_tags", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "publisher_domain", "format_option_id"),
        sa.ForeignKeyConstraint(*_PUBLISHER_FK, ondelete="CASCADE"),
    )
    op.create_table(
        "product_publisher_selections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.String(100), nullable=False),
        *_selection_columns(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"], ["products.tenant_id", "products.product_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(*_PUBLISHER_FK, ondelete="CASCADE"),
        sa.CheckConstraint(
            "selection_type IN ('all', 'by_id', 'by_tag')", name="ck_product_publisher_selections_selection_type"
        ),
    )
    op.create_index(
        "idx_product_publisher_selections_product", "product_publisher_selections", ["tenant_id", "product_id"]
    )
    op.create_table(
        "inventory_profile_publisher_selections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("inventory_profile_id", sa.Integer(), nullable=False),
        *_selection_columns(),
        sa.ForeignKeyConstraint(["inventory_profile_id"], ["inventory_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(*_PUBLISHER_FK, ondelete="CASCADE"),
        sa.CheckConstraint(
            "selection_type IN ('all', 'by_id', 'by_tag')",
            name="ck_inventory_profile_publisher_selections_selection_type",
        ),
    )
    op.create_index(
        "idx_inventory_profile_publisher_selections_profile",
        "inventory_profile_publisher_selections",
        ["inventory_profile_id"],
    )

    for table, owner_column, rows in (
        ("product_publisher_selections", "product_id", product_rows),
        ("inventory_profile_publisher_selections", "inventory_profile_id", profile_rows),
    ):
        for row in rows:
            bind.execute(
                sa.text(
                    f"INSERT INTO {table} ({owner_column}, tenant_id, publisher_domain, selection_type, property_ids, "
                    "property_tags) VALUES (:owner, :tid, :domain, :kind, CAST(:ids AS jsonb), CAST(:tags AS jsonb))"
                ),
                {**row, "ids": _json_or_null(row["ids"]), "tags": _json_or_null(row["tags"])},
            )

    # Both checks name the legacy columns this drops: ck_product_properties_xor (eef85c5fe627),
    # ck_product_authorization (3d2f7ff99896).
    op.drop_constraint("ck_product_properties_xor", "products", type_="check")
    op.drop_constraint("ck_product_authorization", "products", type_="check")
    op.drop_column("products", "property_tags")
    op.drop_column("products", "property_ids")
    op.drop_column("products", "properties")
    op.drop_column("inventory_profiles", "publisher_properties")
    op.drop_table("authorized_properties")
    op.drop_table("publisher_partners")


def _json_or_null(value: list | None) -> str | None:
    return None if value is None else json.dumps(value)


def downgrade() -> None:
    op.create_table(
        "publisher_partners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("publisher_domain", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "publisher_domain", name="uq_tenant_publisher"),
        sa.CheckConstraint("sync_status IN ('pending', 'success', 'error')", name="ck_sync_status"),
    )
    op.create_index("idx_publisher_partners_tenant", "publisher_partners", ["tenant_id"])
    op.create_index("idx_publisher_partners_domain", "publisher_partners", ["publisher_domain"])
    op.create_index("idx_publisher_partners_verified", "publisher_partners", ["is_verified"])
    op.create_table(
        "authorized_properties",
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("property_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("identifiers", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("publisher_domain", sa.String(255), nullable=False),
        sa.Column("verification_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("verification_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("property_id", "tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "property_type IN ('website', 'mobile_app', 'ctv_app', 'dooh', 'podcast', 'radio', 'streaming_audio')",
            name="ck_property_type",
        ),
        sa.CheckConstraint("verification_status IN ('pending', 'verified', 'failed')", name="ck_verification_status"),
    )
    op.create_index("idx_authorized_properties_tenant", "authorized_properties", ["tenant_id"])
    op.create_index("idx_authorized_properties_domain", "authorized_properties", ["publisher_domain"])
    op.create_index("idx_authorized_properties_type", "authorized_properties", ["property_type"])
    op.create_index("idx_authorized_properties_verification", "authorized_properties", ["verification_status"])
    op.add_column(
        "inventory_profiles",
        sa.Column("publisher_properties", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.alter_column("inventory_profiles", "publisher_properties", server_default=None)
    # An empty selection on each existing row, so the restored check constraints hold.
    op.add_column(
        "products", sa.Column("properties", postgresql.JSONB(), nullable=True, server_default=sa.text("'[]'::jsonb"))
    )
    op.alter_column("products", "properties", server_default=None)
    op.add_column("products", sa.Column("property_ids", postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("property_tags", postgresql.JSONB(), nullable=True))
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
    op.drop_index("idx_inventory_profile_publisher_selections_profile", "inventory_profile_publisher_selections")
    op.drop_table("inventory_profile_publisher_selections")
    op.drop_index("idx_product_publisher_selections_product", "product_publisher_selections")
    op.drop_table("product_publisher_selections")
    op.drop_table("publisher_format_options")
    op.drop_index("idx_publisher_properties_publisher", "publisher_properties")
    op.drop_table("publisher_properties")
    op.drop_index("idx_publishers_tenant", "publishers")
    op.drop_table("publishers")
