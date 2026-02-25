"""Authorized property service — business logic for property CRUD.

Extracted from src/admin/blueprints/authorized_properties.py Flask blueprint.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)

PROPERTY_TYPES = {"website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"}


class AuthorizedPropertyService:
    """Stateless service for authorized property operations."""

    def list_properties(self, tenant_id: str) -> dict[str, Any]:
        """List all authorized properties for a tenant with status counts."""
        with get_db_session() as session:
            stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id).order_by(AuthorizedProperty.name)
            properties = session.scalars(stmt).all()

            result = [self._to_dict(p) for p in properties]

            # Count by verification status
            counts: dict[str, int] = {"verified": 0, "pending": 0, "failed": 0}
            for p in properties:
                status = p.verification_status or "pending"
                counts[status] = counts.get(status, 0) + 1

            return {"properties": result, "count": len(result), "status_counts": counts}

    def create_property(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new authorized property."""
        self._validate_property_data(data)

        property_id = data.get("property_id") or f"prop_{uuid.uuid4().hex[:8]}"

        with get_db_session() as session:
            # Check for duplicate
            stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id=property_id)
            if session.scalars(stmt).first():
                raise AdCPValidationError(f"Property '{property_id}' already exists")

            prop = AuthorizedProperty(
                property_id=property_id,
                tenant_id=tenant_id,
                property_type=data.get("property_type", "website"),
                name=data["name"],
                publisher_domain=data["publisher_domain"],
                identifiers=data.get("identifiers", []),
                tags=data.get("tags", []),
                verification_status="pending",
            )
            session.add(prop)
            session.commit()
            session.refresh(prop)
            return self._to_dict(prop)

    def update_property(self, tenant_id: str, property_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update an authorized property. Resets verification_status to 'pending'."""
        with get_db_session() as session:
            stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id=property_id)
            prop = session.scalars(stmt).first()
            if not prop:
                raise AdCPNotFoundError(f"Property '{property_id}' not found")

            for field in ("name", "publisher_domain", "property_type"):
                if field in data:
                    setattr(prop, field, data[field])

            if "identifiers" in data:
                prop.identifiers = data["identifiers"]
            if "tags" in data:
                prop.tags = data["tags"]

            # Reset verification on edit
            prop.verification_status = "pending"
            prop.updated_at = datetime.now(UTC)

            session.commit()
            session.refresh(prop)
            return self._to_dict(prop)

    def delete_property(self, tenant_id: str, property_id: str) -> dict[str, Any]:
        """Delete an authorized property."""
        with get_db_session() as session:
            stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id=property_id)
            prop = session.scalars(stmt).first()
            if not prop:
                raise AdCPNotFoundError(f"Property '{property_id}' not found")

            session.delete(prop)
            session.commit()
            return {"message": f"Property '{property_id}' deleted", "property_id": property_id}

    def bulk_upload(self, tenant_id: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
        """Bulk create/update properties."""
        success_count = 0
        error_count = 0
        errors: list[str] = []

        with get_db_session() as session:
            for i, prop_data in enumerate(properties):
                try:
                    self._validate_property_data(prop_data)
                    property_id = prop_data.get("property_id") or f"prop_{uuid.uuid4().hex[:8]}"

                    stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id=property_id)
                    existing = session.scalars(stmt).first()

                    if existing:
                        for field in ("name", "publisher_domain", "property_type"):
                            if field in prop_data:
                                setattr(existing, field, prop_data[field])
                        if "identifiers" in prop_data:
                            existing.identifiers = prop_data["identifiers"]
                        if "tags" in prop_data:
                            existing.tags = prop_data["tags"]
                        existing.verification_status = "pending"
                        existing.updated_at = datetime.now(UTC)
                    else:
                        prop = AuthorizedProperty(
                            property_id=property_id,
                            tenant_id=tenant_id,
                            property_type=prop_data.get("property_type", "website"),
                            name=prop_data["name"],
                            publisher_domain=prop_data["publisher_domain"],
                            identifiers=prop_data.get("identifiers", []),
                            tags=prop_data.get("tags", []),
                            verification_status="pending",
                        )
                        session.add(prop)

                    success_count += 1
                except (AdCPValidationError, ValueError) as e:
                    error_count += 1
                    errors.append(f"Row {i}: {e}")

            session.commit()

        return {
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
        }

    def verify_properties(self, tenant_id: str, agent_url: str = "") -> dict[str, Any]:
        """Trigger verification of all pending properties."""
        # Delegate to PropertyVerificationService if available
        try:
            from src.services.property_verification_service import PropertyVerificationService

            svc = PropertyVerificationService()
            return svc.verify_all_properties(tenant_id, agent_url)
        except ImportError:
            return {"message": "Property verification service not available", "total_checked": 0}

    def _validate_property_data(self, data: dict[str, Any]) -> None:
        """Validate property data fields."""
        if not data.get("name"):
            raise AdCPValidationError("Property name is required")
        if not data.get("publisher_domain"):
            raise AdCPValidationError("Publisher domain is required")

        prop_type = data.get("property_type", "website")
        if prop_type not in PROPERTY_TYPES:
            raise AdCPValidationError(f"Invalid property_type '{prop_type}'. Valid: {sorted(PROPERTY_TYPES)}")

    def _to_dict(self, prop: AuthorizedProperty) -> dict[str, Any]:
        """Convert AuthorizedProperty ORM object to dict."""
        return {
            "property_id": prop.property_id,
            "tenant_id": prop.tenant_id,
            "property_type": prop.property_type,
            "name": prop.name,
            "publisher_domain": prop.publisher_domain,
            "identifiers": prop.identifiers if prop.identifiers else [],
            "tags": prop.tags if prop.tags else [],
            "verification_status": prop.verification_status or "pending",
            "created_at": prop.created_at.isoformat() if prop.created_at else None,
        }
