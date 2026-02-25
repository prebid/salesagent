"""Tenant management service — business logic for tenant CRUD.

Extracted from src/admin/tenant_management_api.py Flask blueprint.
Used by both the FastAPI admin router and (later) the Flask admin UI.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AuditLog,
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    InventoryProfile,
    MediaBuy,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    SyncJob,
    Tenant,
    TenantManagementConfig,
    User,
)
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class TenantManagementService:
    """Stateless service for tenant lifecycle operations."""

    def list_tenants(self) -> dict[str, Any]:
        with get_db_session() as session:
            stmt = (
                select(
                    Tenant.tenant_id,
                    Tenant.name,
                    Tenant.subdomain,
                    Tenant.is_active,
                    Tenant.billing_plan,
                    Tenant.ad_server,
                    Tenant.created_at,
                    func.count(AdapterConfig.tenant_id).label("has_adapter"),
                )
                .outerjoin(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
                .group_by(Tenant.tenant_id)
                .order_by(Tenant.created_at.desc())
            )
            rows = session.execute(stmt)

            tenants = []
            for row in rows:
                tenants.append(
                    {
                        "tenant_id": row.tenant_id,
                        "name": row.name,
                        "subdomain": row.subdomain,
                        "is_active": bool(row.is_active),
                        "billing_plan": row.billing_plan,
                        "ad_server": row.ad_server,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "adapter_configured": bool(row.has_adapter),
                    }
                )

            return {"tenants": tenants, "count": len(tenants)}

    def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        with get_db_session() as session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()
            if not tenant:
                raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

            result: dict[str, Any] = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "is_active": bool(tenant.is_active),
                "billing_plan": tenant.billing_plan,
                "billing_contact": tenant.billing_contact,
                "ad_server": tenant.ad_server,
                "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
                "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
                "settings": {
                    "enable_axe_signals": bool(tenant.enable_axe_signals),
                    "authorized_emails": tenant.authorized_emails if tenant.authorized_emails else [],
                    "authorized_domains": tenant.authorized_domains if tenant.authorized_domains else [],
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    "hitl_webhook_url": tenant.hitl_webhook_url,
                    "auto_approve_formats": tenant.auto_approve_format_ids if tenant.auto_approve_format_ids else [],
                    "human_review_required": bool(tenant.human_review_required),
                    "policy_settings": tenant.policy_settings if tenant.policy_settings else {},
                },
            }

            # Adapter config
            adapter_stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter = session.scalars(adapter_stmt).first()
            if adapter:
                adapter_data: dict[str, Any] = {
                    "adapter_type": adapter.adapter_type,
                    "created_at": adapter.created_at.isoformat() if adapter.created_at else None,
                }
                if adapter.adapter_type == "google_ad_manager":
                    adapter_data.update(
                        {
                            "gam_network_code": adapter.gam_network_code,
                            "has_refresh_token": bool(adapter.gam_refresh_token),
                            "gam_trafficker_id": adapter.gam_trafficker_id,
                            "gam_manual_approval_required": bool(adapter.gam_manual_approval_required),
                        }
                    )
                elif adapter.adapter_type == "kevel":
                    adapter_data.update(
                        {
                            "kevel_network_id": adapter.kevel_network_id,
                            "has_api_key": bool(adapter.kevel_api_key),
                            "kevel_manual_approval_required": bool(adapter.kevel_manual_approval_required),
                        }
                    )
                elif adapter.adapter_type == "triton":
                    adapter_data.update(
                        {"triton_station_id": adapter.triton_station_id, "has_api_key": bool(adapter.triton_api_key)}
                    )
                elif adapter.adapter_type == "mock":
                    adapter_data.update({"mock_dry_run": bool(adapter.mock_dry_run)})
                result["adapter_config"] = adapter_data

            # Principals count
            count_stmt = select(func.count()).select_from(Principal).filter_by(tenant_id=tenant_id)
            result["principals_count"] = session.scalar(count_stmt)

            return result

    def create_tenant(self, data: dict[str, Any]) -> dict[str, Any]:
        from src.core.webhook_validator import WebhookURLValidator

        # Validate required fields
        for field in ("name", "subdomain", "ad_server"):
            if not data.get(field):
                raise AdCPValidationError(f"Missing required field: {field}")

        # Validate webhook URLs
        for field_name, label in (
            ("slack_webhook_url", "Slack webhook URL"),
            ("slack_audit_webhook_url", "Slack audit webhook URL"),
            ("hitl_webhook_url", "HITL webhook URL"),
        ):
            url = data.get(field_name)
            if url:
                is_valid, error_msg = WebhookURLValidator.validate_webhook_url(url)
                if not is_valid:
                    raise AdCPValidationError(f"Invalid {label}: {error_msg}")

        # Access control
        email_list = list(data.get("authorized_emails", []))
        creator_email = data.get("creator_email")
        if creator_email and creator_email not in email_list:
            email_list.append(creator_email)

        domain_list = list(data.get("authorized_domains", []))
        if not email_list and not domain_list:
            if creator_email:
                email_list.append(creator_email)
            else:
                raise AdCPValidationError(
                    "Must specify at least one authorized email or domain. "
                    "Provide 'authorized_emails', 'authorized_domains', or 'creator_email'."
                )

        tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
        admin_token = secrets.token_urlsafe(32)
        adapter_type = data["ad_server"]

        with get_db_session() as session:
            new_tenant = Tenant(
                tenant_id=tenant_id,
                name=data["name"],
                subdomain=data["subdomain"],
                ad_server=adapter_type,
                is_active=data.get("is_active", True),
                billing_plan=data.get("billing_plan", "standard"),
                billing_contact=data.get("billing_contact"),
                enable_axe_signals=data.get("enable_axe_signals", True),
                authorized_emails=json.dumps(email_list),
                authorized_domains=json.dumps(domain_list),
                slack_webhook_url=data.get("slack_webhook_url"),
                slack_audit_webhook_url=data.get("slack_audit_webhook_url"),
                hitl_webhook_url=data.get("hitl_webhook_url"),
                admin_token=admin_token,
                auto_approve_format_ids=json.dumps(data.get("auto_approve_format_ids", ["display_300x250"])),
                human_review_required=data.get("human_review_required", True),
                policy_settings=json.dumps(data.get("policy_settings", {})),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
            )
            session.add(new_tenant)

            # Create adapter config
            adapter = self._create_adapter_config(tenant_id, adapter_type, data)
            session.add(adapter)

            # Create default principal
            principal_token = None
            if data.get("create_default_principal", True):
                principal_token = self._create_default_principal(session, tenant_id, data["name"], adapter_type)

            session.commit()

            result: dict[str, Any] = {
                "tenant_id": tenant_id,
                "name": data["name"],
                "subdomain": data["subdomain"],
                "admin_token": admin_token,
            }
            if principal_token:
                result["default_principal_token"] = principal_token

            return result

    def update_tenant(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        from src.core.webhook_validator import WebhookURLValidator

        with get_db_session() as session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()
            if not tenant:
                raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

            # Validate webhook URLs
            for field_name, label in (
                ("slack_webhook_url", "Slack webhook URL"),
                ("slack_audit_webhook_url", "Slack audit webhook URL"),
                ("hitl_webhook_url", "HITL webhook URL"),
            ):
                if field_name in data and data[field_name]:
                    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(data[field_name])
                    if not is_valid:
                        raise AdCPValidationError(f"Invalid {label}: {error_msg}")

            # Apply simple field updates
            simple_fields = (
                "name",
                "is_active",
                "billing_plan",
                "billing_contact",
                "enable_axe_signals",
                "slack_webhook_url",
                "slack_audit_webhook_url",
                "hitl_webhook_url",
                "human_review_required",
            )
            for field in simple_fields:
                if field in data:
                    setattr(tenant, field, data[field])

            # JSON-serialized fields
            for field in ("authorized_emails", "authorized_domains", "auto_approve_format_ids", "policy_settings"):
                if field in data:
                    setattr(tenant, field, json.dumps(data[field]))

            tenant.updated_at = datetime.now(UTC)

            # Update adapter config if provided
            if "adapter_config" in data:
                self._update_adapter_config(session, tenant_id, data["adapter_config"])

            session.commit()

            return {
                "tenant_id": tenant_id,
                "name": tenant.name,
                "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
            }

    def delete_tenant(self, tenant_id: str, hard_delete: bool = False) -> dict[str, Any]:
        with get_db_session() as session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()
            if not tenant:
                raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

            if hard_delete:
                # Delete in dependency order (children before parents)
                for model in (
                    PricingOption,
                    MediaBuy,
                    Product,
                    CurrencyLimit,
                    PropertyTag,
                    AuthorizedProperty,
                    InventoryProfile,
                    GAMInventory,
                    SyncJob,
                    AdapterConfig,
                    Principal,
                    AuditLog,
                    User,
                ):
                    session.execute(delete(model).where(model.tenant_id == tenant_id))
                session.delete(tenant)
                message = "Tenant and all related data permanently deleted"
            else:
                tenant.is_active = False
                tenant.updated_at = datetime.now(UTC)
                message = "Tenant deactivated successfully"

            session.commit()
            return {"message": message, "tenant_id": tenant_id}

    def initialize_api_key(self) -> dict[str, Any]:
        with get_db_session() as session:
            stmt = select(TenantManagementConfig).filter_by(config_key="tenant_management_api_key")
            existing = session.scalars(stmt).first()
            if existing:
                raise AdCPValidationError("API key already initialized")

            api_key = f"sk-{secrets.token_urlsafe(32)}"
            config = TenantManagementConfig(
                config_key="tenant_management_api_key",
                config_value=api_key,
                description="Tenant management API key for tenant administration",
                updated_at=datetime.now(UTC),
                updated_by="system",
            )
            session.add(config)
            session.commit()

            return {
                "message": "Tenant management API key initialized",
                "api_key": api_key,
                "warning": "Save this key securely. It cannot be retrieved again.",
            }

    # --- Private helpers ---

    def _create_adapter_config(self, tenant_id: str, adapter_type: str, data: dict[str, Any]) -> AdapterConfig:
        now = datetime.now(UTC)
        if adapter_type == "google_ad_manager":
            return AdapterConfig(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                gam_network_code=data.get("gam_network_code"),
                gam_refresh_token=data.get("gam_refresh_token"),
                gam_trafficker_id=data.get("gam_trafficker_id"),
                gam_manual_approval_required=data.get("gam_manual_approval_required", False),
                created_at=now,
                updated_at=now,
            )
        elif adapter_type == "kevel":
            return AdapterConfig(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                kevel_network_id=data.get("kevel_network_id"),
                kevel_api_key=data.get("kevel_api_key"),
                kevel_manual_approval_required=data.get("kevel_manual_approval_required", False),
                created_at=now,
                updated_at=now,
            )
        elif adapter_type == "triton":
            return AdapterConfig(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                triton_station_id=data.get("triton_station_id"),
                triton_api_key=data.get("triton_api_key"),
                created_at=now,
                updated_at=now,
            )
        else:
            return AdapterConfig(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                mock_dry_run=data.get("mock_dry_run", False),
                created_at=now,
                updated_at=now,
            )

    def _create_default_principal(self, session: Any, tenant_id: str, tenant_name: str, adapter_type: str) -> str:
        principal_id = f"principal_{uuid.uuid4().hex[:8]}"
        principal_token = secrets.token_urlsafe(32)

        default_mappings: dict[str, Any] = {}
        if adapter_type == "google_ad_manager":
            default_mappings = {"google_ad_manager": {"advertiser_id": "placeholder"}}
        elif adapter_type == "kevel":
            default_mappings = {"kevel": {"advertiser_id": "placeholder"}}
        elif adapter_type == "triton":
            default_mappings = {"triton": {"advertiser_id": "placeholder"}}
        else:
            default_mappings = {"mock": {"advertiser_id": "default"}}

        principal = Principal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            name=f"{tenant_name} Default Principal",
            platform_mappings=json.dumps(default_mappings),
            access_token=principal_token,
            created_at=datetime.now(UTC),
        )
        session.add(principal)
        return principal_token

    def _update_adapter_config(self, session: Any, tenant_id: str, adapter_data: dict[str, Any]) -> None:
        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter = session.scalars(stmt).first()
        if not adapter:
            return

        field_map: dict[str, list[str]] = {
            "google_ad_manager": [
                "gam_network_code",
                "gam_refresh_token",
                "gam_trafficker_id",
                "gam_manual_approval_required",
            ],
            "kevel": ["kevel_network_id", "kevel_api_key", "kevel_manual_approval_required"],
            "triton": ["triton_station_id", "triton_api_key"],
            "mock": ["mock_dry_run"],
        }

        for field in field_map.get(adapter.adapter_type, []):
            if field in adapter_data:
                setattr(adapter, field, adapter_data[field])

        adapter.updated_at = datetime.now(UTC)
