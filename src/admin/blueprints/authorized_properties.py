"""Admin views of what the tenant's publishers authorize, and the seller's property tags.

Properties are never typed by the operator: they come only from each publisher's
adagents.json, which ``src/services/publisher_ingest.py`` stores and derives them from
(Publishers, under Inventory). These pages read what is stored.
"""

import logging
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import PropertyTag, Tenant
from src.core.database.repositories.uow import PublisherUoW
from src.core.publisher_format_catalog import authorized_properties
from src.core.schemas import PROPERTY_ERROR_MESSAGES

logger = logging.getLogger(__name__)

authorized_properties_bp = Blueprint("authorized_properties", __name__)


@authorized_properties_bp.route("/<tenant_id>/authorized-properties")
@require_tenant_access()
def list_authorized_properties(tenant_id: str) -> str | Response:
    """The properties each publisher's stored adagents.json authorizes this agent for."""
    now = datetime.now(UTC)
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None and uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        if tenant is None:
            flash(PROPERTY_ERROR_MESSAGES["tenant_not_found"], "error")
            return redirect(url_for("core.admin_dashboard"))
        publishers = uow.publishers.list_all()
        rows = [
            {"property": prop, "publisher": publisher, "authorized": prop in authorized_properties(publisher, now)}
            for publisher in publishers
            for prop in publisher.properties
        ]
        return render_template(
            "authorized_properties_list.html",
            tenant=tenant,
            rows=rows,
            property_counts={
                "total": len(rows),
                "authorized": sum(1 for row in rows if row["authorized"]),
                "publishers": len(publishers),
            },
            session=session,
            user=session.get("user"),
        )


@authorized_properties_bp.route("/<tenant_id>/property-tags")
@require_tenant_access()
def list_property_tags(tenant_id: str) -> str | Response:
    """List and manage property tags for a tenant."""
    try:
        with get_db_session() as db_session:
            # Get tenant info
            stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.admin_dashboard"))

            # Ensure 'all_inventory' tag exists (default tag for all properties)
            tag_stmt = select(PropertyTag).where(
                PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == "all_inventory"
            )
            all_inventory_tag = PropertyTag(
                tag_id="all_inventory",
                tenant_id=tenant_id,
                name="All Inventory",
                description="Default tag that applies to all properties. Used when no specific targeting is needed.",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            # Auto-create the default tag; a concurrent viewer that created it first
            # leaves it present, which is all this branch wanted.
            if (
                resolve_or_write(
                    db_session,
                    conflict=lambda: db_session.scalars(tag_stmt).first(),
                    write=lambda: db_session.add(all_inventory_tag),
                    constraint="property_tags_pkey",
                )
                is None
            ):
                db_session.commit()
                logger.info(f"Auto-created 'all_inventory' tag for tenant {tenant_id}")

            # Get all tags for this tenant
            all_tags_stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.name)
            tags = db_session.scalars(all_tags_stmt).all()

            return render_template(
                "property_tags_list.html",
                tenant=tenant,
                tags=tags,
                session=session,
                user=session.get("user"),
            )

    except Exception as e:
        logger.error(f"Error listing property tags: {e}")
        flash(f"Error loading tags: {str(e)}", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/property-tags/create", methods=["POST"])
@log_admin_action("create_property_tag")
@require_tenant_access()
def create_property_tag(tenant_id: str) -> Response:
    """Create a new property tag."""
    try:
        tag_id = request.form.get("tag_id", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not tag_id or not name or not description:
            flash(PROPERTY_ERROR_MESSAGES["all_fields_required"], "error")
            return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

        # Validate tag_id format (lowercase, underscores only)
        if not tag_id.replace("_", "").replace("-", "").isalnum():
            flash(PROPERTY_ERROR_MESSAGES["invalid_tag_id"], "error")
            return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

        tag_id = tag_id.lower().replace("-", "_")

        with get_db_session() as db_session:
            # Check if tag already exists. The primary key is the authority, so this
            # same answer serves the loser of a concurrent create (see resolve_or_write)
            # rather than a flash carrying the driver's message.
            stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == tag_id)

            def tag_already_exists() -> Response | None:
                if db_session.scalars(stmt).first() is None:
                    return None
                flash(PROPERTY_ERROR_MESSAGES["tag_already_exists"].format(tag_id=tag_id), "error")
                return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

            # Create new tag
            new_tag = PropertyTag(
                tag_id=tag_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            taken = resolve_or_write(
                db_session,
                conflict=tag_already_exists,
                write=lambda: db_session.add(new_tag),
                constraint="property_tags_pkey",
            )
            if taken is not None:
                return taken

            db_session.commit()
            flash(f"Tag '{name}' created successfully", "success")

    except Exception as e:
        logger.error(f"Error creating property tag: {e}")
        flash(f"Error creating tag: {str(e)}", "error")

    return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/api/list")
@require_tenant_access(api_mode=True)
def list_authorized_properties_api(tenant_id: str):
    """The properties the publishers authorize this agent for now, as JSON for the inventory profile editor.

    Each entry: ``publisher_domain``, ``property_name``, ``property_type``, ``property_ids``
    (the identifier values) and ``property_tags``.
    """
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None
        now = datetime.now(UTC)
        properties_data = [
            {
                "publisher_domain": prop.publisher_domain,
                "property_name": prop.name,
                "property_type": prop.property_type,
                "property_ids": [i["value"] for i in prop.identifiers or [] if isinstance(i, dict) and "value" in i],
                "property_tags": list(prop.tags or []),
            }
            for publisher in uow.publishers.list_all()
            for prop in authorized_properties(publisher, now)
        ]
    return jsonify({"properties": properties_data, "total": len(properties_data)})
