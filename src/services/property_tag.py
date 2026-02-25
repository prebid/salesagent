"""Property tag service — business logic for property tag CRUD.

Extracted from src/admin/blueprints/authorized_properties.py Flask blueprint.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import PropertyTag
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)

_TAG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class PropertyTagService:
    """Stateless service for property tag operations."""

    def list_tags(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all property tags for a tenant, ensuring 'all_inventory' exists."""
        with get_db_session() as session:
            self._ensure_default_tag(session, tenant_id)
            stmt = select(PropertyTag).filter_by(tenant_id=tenant_id).order_by(PropertyTag.name)
            tags = session.scalars(stmt).all()
            return [self._to_dict(tag) for tag in tags]

    def create_tag(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new property tag."""
        tag_id = data.get("tag_id", "")
        name = data.get("name", "")
        description = data.get("description", "")

        if not tag_id or not name:
            raise AdCPValidationError("tag_id and name are required")

        # Normalize tag_id
        tag_id = tag_id.lower().replace("-", "_")
        self._validate_tag_id(tag_id)

        with get_db_session() as session:
            # Check for duplicate
            stmt = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id=tag_id)
            existing = session.scalars(stmt).first()
            if existing:
                raise AdCPValidationError(f"Property tag '{tag_id}' already exists")

            tag = PropertyTag(
                tag_id=tag_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
            )
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return self._to_dict(tag)

    def delete_tag(self, tenant_id: str, tag_id: str) -> dict[str, Any]:
        """Delete a property tag."""
        if tag_id == "all_inventory":
            raise AdCPValidationError("Cannot delete the 'all_inventory' default tag")

        with get_db_session() as session:
            stmt = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id=tag_id)
            tag = session.scalars(stmt).first()
            if not tag:
                raise AdCPNotFoundError(f"Property tag '{tag_id}' not found")

            session.delete(tag)
            session.commit()
            return {"message": f"Property tag '{tag_id}' deleted", "tag_id": tag_id}

    def _ensure_default_tag(self, session: Any, tenant_id: str) -> None:
        """Auto-create 'all_inventory' tag if it doesn't exist."""
        stmt = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
        if session.scalars(stmt).first():
            return

        tag = PropertyTag(
            tag_id="all_inventory",
            tenant_id=tenant_id,
            name="All Inventory",
            description="Default tag that applies to all properties. Used when no specific targeting is needed.",
        )
        session.add(tag)
        session.commit()

    def _validate_tag_id(self, tag_id: str) -> None:
        """Validate tag_id format: lowercase alphanumeric + underscores."""
        if not _TAG_ID_RE.match(tag_id):
            raise AdCPValidationError(f"Invalid tag_id '{tag_id}': must be lowercase alphanumeric with underscores")

    def _to_dict(self, tag: PropertyTag) -> dict[str, Any]:
        """Convert PropertyTag ORM object to dict."""
        return {
            "tag_id": tag.tag_id,
            "tenant_id": tag.tenant_id,
            "name": tag.name,
            "description": tag.description,
            "created_at": tag.created_at.isoformat() if tag.created_at else None,
        }
