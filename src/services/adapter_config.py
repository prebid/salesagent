"""Adapter configuration service — business logic for adapter CRUD and capabilities.

Extracted from src/admin/blueprints/adapters.py and settings.py Flask blueprints.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Tenant
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class AdapterConfigService:
    """Stateless service for adapter configuration operations."""

    def get_adapter_config(self, tenant_id: str) -> dict[str, Any]:
        """Get the adapter configuration for a tenant."""
        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter = session.scalars(stmt).first()
            if not adapter:
                raise AdCPNotFoundError(f"No adapter configured for tenant '{tenant_id}'")

            result: dict[str, Any] = {
                "adapter_type": adapter.adapter_type,
                "created_at": adapter.created_at.isoformat() if adapter.created_at else None,
                "updated_at": adapter.updated_at.isoformat() if adapter.updated_at else None,
                "config": {},
            }

            # Build config dict from adapter-specific columns
            if adapter.adapter_type == "google_ad_manager":
                result["config"] = {
                    "gam_network_code": adapter.gam_network_code,
                    "has_refresh_token": bool(adapter.gam_refresh_token),
                    "gam_trafficker_id": adapter.gam_trafficker_id,
                    "gam_manual_approval_required": bool(adapter.gam_manual_approval_required),
                }
            elif adapter.adapter_type == "kevel":
                result["config"] = {
                    "kevel_network_id": adapter.kevel_network_id,
                    "has_api_key": bool(adapter.kevel_api_key),
                    "kevel_manual_approval_required": bool(adapter.kevel_manual_approval_required),
                }
            elif adapter.adapter_type == "triton":
                result["config"] = {
                    "triton_station_id": adapter.triton_station_id,
                    "has_api_key": bool(adapter.triton_api_key),
                }
            elif adapter.adapter_type == "broadstreet":
                result["config"] = {
                    "broadstreet_network_id": getattr(adapter, "broadstreet_network_id", None),
                    "has_api_key": bool(getattr(adapter, "broadstreet_api_key", None)),
                }
            elif adapter.adapter_type == "mock":
                result["config"] = {
                    "mock_dry_run": bool(adapter.mock_dry_run),
                }

            # Include schema-driven config if present
            if adapter.config_json:
                result["config"]["schema_config"] = adapter.config_json

            return result

    def save_adapter_config(self, tenant_id: str, adapter_type: str, config: dict[str, Any]) -> dict[str, Any]:
        """Save adapter configuration for a tenant."""
        from src.adapters import get_adapter_schemas

        with get_db_session() as session:
            # Verify tenant exists
            tenant_stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(tenant_stmt).first()
            if not tenant:
                raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

            # Validate against adapter schema if available
            # Skip validation for adapters using base schema (they use legacy columns)
            from src.adapters.base import BaseConnectionConfig

            schemas = get_adapter_schemas(adapter_type)
            validated_config = None
            if schemas and schemas.connection_config and schemas.connection_config is not BaseConnectionConfig:
                try:
                    validated = schemas.connection_config(**config)
                    validated_config = validated.model_dump()
                except Exception as e:
                    raise AdCPValidationError(f"Invalid adapter config: {e}")

            now = datetime.now(UTC)
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter = session.scalars(stmt).first()

            if not adapter:
                adapter = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    created_at=now,
                )
                session.add(adapter)

            adapter.adapter_type = adapter_type
            adapter.updated_at = now

            # Write to schema-driven config
            if validated_config:
                adapter.config_json = validated_config

            # Write to legacy columns for backward compatibility
            self._apply_legacy_columns(adapter, adapter_type, config)

            # Update tenant's ad_server field
            tenant.ad_server = adapter_type
            tenant.updated_at = now

            session.commit()

            return {
                "adapter_type": adapter_type,
                "tenant_id": tenant_id,
                "updated_at": now.isoformat(),
            }

    def test_connection(self, tenant_id: str) -> dict[str, Any]:
        """Test the adapter connection for a tenant."""
        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter = session.scalars(stmt).first()
            if not adapter:
                raise AdCPNotFoundError(f"No adapter configured for tenant '{tenant_id}'")

            # For mock adapter, connection always succeeds
            if adapter.adapter_type == "mock":
                return {"success": True, "message": "Mock adapter connected"}

            # For GAM, verify we have required credentials
            if adapter.adapter_type == "google_ad_manager":
                if not adapter.gam_network_code:
                    return {"success": False, "message": "Missing GAM network code"}
                if not adapter.gam_refresh_token:
                    return {"success": False, "message": "Missing GAM refresh token"}
                return {"success": True, "message": "GAM credentials present"}

            # For other adapters, check for required API keys
            if adapter.adapter_type == "kevel" and not adapter.kevel_api_key:
                return {"success": False, "message": "Missing Kevel API key"}
            if adapter.adapter_type == "triton" and not adapter.triton_api_key:
                return {"success": False, "message": "Missing Triton API key"}

            return {"success": True, "message": f"{adapter.adapter_type} credentials present"}

    def get_capabilities(self, adapter_type: str) -> dict[str, Any]:
        """Get capabilities for an adapter type."""
        from src.adapters import get_adapter_schemas

        schemas = get_adapter_schemas(adapter_type)
        if not schemas or not schemas.capabilities:
            raise AdCPNotFoundError(f"No capabilities found for adapter type '{adapter_type}'")

        return asdict(schemas.capabilities)

    def _apply_legacy_columns(self, adapter: AdapterConfig, adapter_type: str, config: dict[str, Any]) -> None:
        """Write config values to legacy per-column fields for backward compatibility."""
        field_map: dict[str, list[str]] = {
            "google_ad_manager": [
                "gam_network_code",
                "gam_refresh_token",
                "gam_trafficker_id",
                "gam_manual_approval_required",
            ],
            "kevel": ["kevel_network_id", "kevel_api_key", "kevel_manual_approval_required"],
            "triton": ["triton_station_id", "triton_api_key"],
            "broadstreet": ["broadstreet_network_id", "broadstreet_api_key"],
            "mock": ["mock_dry_run"],
        }
        for field in field_map.get(adapter_type, []):
            if field in config:
                setattr(adapter, field, config[field])
