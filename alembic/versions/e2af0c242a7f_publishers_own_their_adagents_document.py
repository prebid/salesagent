"""publishers own their adagents.json document; selections reference them by foreign key

Revision ID: e2af0c242a7f
Revises: 3c38b1ee241f
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
- A product's ``properties`` selectors and an inventory profile's ``publisher_properties``
  move into ``product_publisher_selections`` / ``inventory_profile_publisher_selections``,
  each row referencing its publisher by foreign key; every domain a selector names becomes
  a publisher. The two JSON columns are dropped.

STRUCTURE ONLY. No publisher has a document after the upgrade, so no product is offered
until its publishers' first ingest. ``downgrade()`` restores the dropped tables and columns
as structure; the selections and the documents are not carried back.
"""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2af0c242a7f"
down_revision: str | Sequence[str] | None = "3c38b1ee241f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PUBLISHER_FK = (["tenant_id", "publisher_domain"], ["publishers.tenant_id", "publishers.domain"])


def _publisher_domain(stored: str) -> str:
    """The host *stored* names, lower case, without scheme, path or trailing dot.

    Inlined rather than imported from ``src.core.http_utils``: a revision has to keep
    behaving as it did when it ran, and the application helper is free to change.
    """
    folded = (stored or "").strip().lower()
    host = urlsplit(folded if "//" in folded else f"//{folded}").netloc or folded
    return host.rstrip(".")


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
    for tenant_id, product_id, selectors in bind.execute(
        sa.text("SELECT tenant_id, product_id, properties FROM products ORDER BY tenant_id, product_id")
    ):
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
    op.add_column(
        "products", sa.Column("properties", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))
    )
    op.alter_column("products", "properties", server_default=None)
    op.drop_index("idx_inventory_profile_publisher_selections_profile", "inventory_profile_publisher_selections")
    op.drop_table("inventory_profile_publisher_selections")
    op.drop_index("idx_product_publisher_selections_product", "product_publisher_selections")
    op.drop_table("product_publisher_selections")
    op.drop_table("publisher_format_options")
    op.drop_index("idx_publisher_properties_publisher", "publisher_properties")
    op.drop_table("publisher_properties")
    op.drop_index("idx_publishers_tenant", "publishers")
    op.drop_table("publishers")
