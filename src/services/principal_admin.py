"""Principal (advertiser) admin service — business logic for principal CRUD.

Extracted from src/admin/blueprints/principals.py Flask blueprint.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, MediaBuy, Principal
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class PrincipalAdminService:
    """Stateless service for principal (advertiser) operations."""

    def list_principals(self, tenant_id: str) -> dict[str, Any]:
        """List all principals for a tenant with media buy counts."""
        with get_db_session() as session:
            stmt = (
                select(
                    Principal,
                    func.count(MediaBuy.media_buy_id).label("media_buy_count"),
                )
                .outerjoin(MediaBuy, Principal.principal_id == MediaBuy.principal_id)
                .filter(Principal.tenant_id == tenant_id)
                .group_by(Principal.principal_id, Principal.tenant_id)
                .order_by(Principal.name)
            )
            rows = session.execute(stmt).all()

            principals = []
            for principal, count in rows:
                d = self._to_dict(principal)
                d["media_buy_count"] = count
                principals.append(d)

            return {"principals": principals, "count": len(principals)}

    def get_principal(self, tenant_id: str, principal_id: str) -> dict[str, Any]:
        """Get a single principal with details."""
        with get_db_session() as session:
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            principal = session.scalars(stmt).first()
            if not principal:
                raise AdCPNotFoundError(f"Principal '{principal_id}' not found")

            result = self._to_dict(principal)

            # Count media buys
            count_stmt = select(func.count()).select_from(MediaBuy).filter_by(principal_id=principal_id)
            result["media_buy_count"] = session.scalar(count_stmt)

            return result

    def create_principal(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new principal (advertiser). Returns access_token."""
        name = data.get("name", "")
        if not name:
            raise AdCPValidationError("Principal name is required")

        principal_id = f"prin_{uuid.uuid4().hex[:8]}"
        access_token = f"tok_{secrets.token_urlsafe(32)}"

        platform_mappings = data.get("platform_mappings", {})

        with get_db_session() as session:
            principal = Principal(
                tenant_id=tenant_id,
                principal_id=principal_id,
                name=name,
                access_token=access_token,
                platform_mappings=(
                    json.dumps(platform_mappings) if isinstance(platform_mappings, dict) else platform_mappings
                ),
                created_at=datetime.now(UTC),
            )
            session.add(principal)
            session.commit()
            session.refresh(principal)

            result = self._to_dict(principal)
            result["access_token"] = access_token  # Only return on create
            return result

    def update_principal(self, tenant_id: str, principal_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update a principal."""
        with get_db_session() as session:
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            principal = session.scalars(stmt).first()
            if not principal:
                raise AdCPNotFoundError(f"Principal '{principal_id}' not found")

            if "name" in data:
                principal.name = data["name"]

            if "platform_mappings" in data:
                mappings = data["platform_mappings"]
                principal.platform_mappings = json.dumps(mappings) if isinstance(mappings, dict) else mappings

            principal.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(principal)
            return self._to_dict(principal)

    def delete_principal(self, tenant_id: str, principal_id: str) -> dict[str, Any]:
        """Delete a principal."""
        with get_db_session() as session:
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            principal = session.scalars(stmt).first()
            if not principal:
                raise AdCPNotFoundError(f"Principal '{principal_id}' not found")

            session.delete(principal)
            session.commit()
            return {"message": f"Principal '{principal_id}' deleted", "principal_id": principal_id}

    def regenerate_token(self, tenant_id: str, principal_id: str) -> dict[str, Any]:
        """Regenerate access token for a principal."""
        with get_db_session() as session:
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            principal = session.scalars(stmt).first()
            if not principal:
                raise AdCPNotFoundError(f"Principal '{principal_id}' not found")

            new_token = f"tok_{secrets.token_urlsafe(32)}"
            principal.access_token = new_token
            principal.updated_at = datetime.now(UTC)
            session.commit()

            return {
                "principal_id": principal_id,
                "access_token": new_token,
                "message": "Access token regenerated",
            }

    def search_gam_advertisers(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Search GAM advertisers for principal mapping."""
        with get_db_session() as session:
            # Verify tenant has GAM configured
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
            adapter = session.scalars(stmt).first()
            if not adapter:
                raise AdCPValidationError("GAM adapter not configured for this tenant")

            if not adapter.gam_network_code:
                raise AdCPValidationError("GAM network code not configured")

        # Attempt to query GAM
        try:
            from src.adapters.gam.gam_config_helpers import build_gam_config_from_adapter
            from src.adapters.google_ad_manager import GoogleAdManager
            from src.core.schemas import Principal as PrincipalSchema

            config = build_gam_config_from_adapter(adapter)
            admin_principal = PrincipalSchema(principal_id="admin", name="admin", platform_mappings={})
            gam = GoogleAdManager(
                config=config,
                principal=admin_principal,
                network_code=adapter.gam_network_code or "",
                advertiser_id=None,
                trafficker_id=adapter.gam_trafficker_id or None,
                dry_run=False,
                tenant_id=tenant_id,
            )

            advertisers = gam.orders_manager.get_advertisers(
                search_query=data.get("search"),
                limit=data.get("limit", 500),
                fetch_all=data.get("fetch_all", False),
            )
            return {"advertisers": advertisers, "count": len(advertisers)}

        except ImportError:
            raise AdCPValidationError("GAM adapter not available")
        except Exception as e:
            logger.error(f"Failed to search GAM advertisers: {e}")
            raise AdCPValidationError(f"Failed to search advertisers: {e}")

    def _to_dict(self, principal: Principal) -> dict[str, Any]:
        """Convert Principal ORM object to dict."""
        # Parse platform_mappings
        mappings = principal.platform_mappings
        if isinstance(mappings, str):
            try:
                mappings = json.loads(mappings)
            except (json.JSONDecodeError, TypeError):
                mappings = {}

        return {
            "tenant_id": principal.tenant_id,
            "principal_id": principal.principal_id,
            "name": principal.name,
            "platform_mappings": mappings or {},
            "created_at": principal.created_at.isoformat() if principal.created_at else None,
        }
