"""Inventory profile service — business logic for inventory profile CRUD.

Inventory profiles are reusable templates combining inventory (ad units/placements),
formats, publisher properties, and optional targeting.

Extracted from src/admin/blueprints/inventory_profiles.py Flask blueprint.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, Product
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class InventoryProfileService:
    """Stateless service for inventory profile operations."""

    def list_profiles(self, tenant_id: str) -> dict[str, Any]:
        """List all inventory profiles for a tenant with product counts."""
        with get_db_session() as session:
            stmt = (
                select(InventoryProfile, func.count(Product.product_id).label("product_count"))
                .outerjoin(Product, InventoryProfile.id == Product.inventory_profile_id)
                .filter(InventoryProfile.tenant_id == tenant_id)
                .group_by(InventoryProfile.id)
                .order_by(InventoryProfile.name)
            )
            rows = session.execute(stmt).all()

            profiles = []
            for profile, product_count in rows:
                d = self._to_dict(profile)
                d["product_count"] = product_count
                profiles.append(d)

            return {"profiles": profiles, "count": len(profiles)}

    def create_profile(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new inventory profile."""
        name = data.get("name", "")
        if not name:
            raise AdCPValidationError("Profile name is required")

        profile_id = data.get("profile_id") or self._generate_profile_id(name)

        with get_db_session() as session:
            # Check for duplicate
            stmt = select(InventoryProfile).filter_by(tenant_id=tenant_id, profile_id=profile_id)
            if session.scalars(stmt).first():
                raise AdCPValidationError(f"Inventory profile '{profile_id}' already exists")

            profile = InventoryProfile(
                tenant_id=tenant_id,
                profile_id=profile_id,
                name=name,
                description=data.get("description"),
                inventory_config=data.get("inventory_config", {}),
                format_ids=data.get("format_ids", []),
                publisher_properties=data.get("publisher_properties", []),
                targeting_template=data.get("targeting_template"),
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return self._to_dict(profile)

    def update_profile(self, tenant_id: str, profile_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update an inventory profile."""
        with get_db_session() as session:
            stmt = select(InventoryProfile).filter_by(tenant_id=tenant_id, profile_id=profile_id)
            profile = session.scalars(stmt).first()
            if not profile:
                raise AdCPNotFoundError(f"Inventory profile '{profile_id}' not found")

            for field in ("name", "description"):
                if field in data:
                    setattr(profile, field, data[field])

            for json_field in ("inventory_config", "format_ids", "publisher_properties", "targeting_template"):
                if json_field in data:
                    setattr(profile, json_field, data[json_field])

            profile.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(profile)
            return self._to_dict(profile)

    def delete_profile(self, tenant_id: str, profile_id: str) -> dict[str, Any]:
        """Delete an inventory profile if no products reference it."""
        with get_db_session() as session:
            stmt = select(InventoryProfile).filter_by(tenant_id=tenant_id, profile_id=profile_id)
            profile = session.scalars(stmt).first()
            if not profile:
                raise AdCPNotFoundError(f"Inventory profile '{profile_id}' not found")

            # Check for product references
            count_stmt = select(func.count()).select_from(Product).filter_by(inventory_profile_id=profile.id)
            product_count = session.scalar(count_stmt)
            if product_count and product_count > 0:
                raise AdCPValidationError(
                    f"Cannot delete profile '{profile_id}': referenced by {product_count} product(s)"
                )

            session.delete(profile)
            session.commit()
            return {"message": f"Inventory profile '{profile_id}' deleted", "profile_id": profile_id}

    def _generate_profile_id(self, name: str) -> str:
        """Convert name to URL-safe slug."""
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug or "profile"

    def _to_dict(self, profile: InventoryProfile) -> dict[str, Any]:
        """Convert InventoryProfile ORM object to dict."""
        return {
            "id": profile.id,
            "profile_id": profile.profile_id,
            "tenant_id": profile.tenant_id,
            "name": profile.name,
            "description": profile.description,
            "inventory_config": profile.inventory_config if profile.inventory_config else {},
            "format_ids": profile.format_ids if profile.format_ids else [],
            "publisher_properties": profile.publisher_properties if profile.publisher_properties else [],
            "targeting_template": profile.targeting_template,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }
