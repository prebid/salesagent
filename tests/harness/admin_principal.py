"""Drive principal forms through the shared admin client and read their persisted state."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from src.core.database.models import AuditLog, Principal
from src.core.database.repositories.principal import PrincipalRepository
from tests.harness._base import BareIntegrationEnv
from tests.harness.admin_client import AdminClient, AdminOperatorMixin, AdminResponse
from tests.harness.database_faults import DatabaseFaultMixin


class AdminPrincipalEnv(AdminOperatorMixin, DatabaseFaultMixin, BareIntegrationEnv):
    """Principal creation, editing and token rotation on either admin transport."""

    @property
    def client(self) -> AdminClient:
        return self.admin_client(self.tenant_id)

    def _enter_post(self) -> None:
        self.ensure_tenant()

    def seed_principal(self, *, principal_id: str, tenant_id: str | None = None, **fields: Any) -> str:
        from src.core.platform_mappings import mock_platform_mapping
        from tests.factories import TenantFactory

        tenant_id = tenant_id or self.tenant_id
        if tenant_id != self.tenant_id:
            TenantFactory(tenant_id=tenant_id)
        PrincipalRepository(self.get_session(), tenant_id).issue(
            principal_id=principal_id,
            name=fields.pop("name", "Existing advertiser"),
            platform_mappings={"mock": mock_platform_mapping(principal_id)},
            **fields,
        )
        self._commit_factory_data()
        return principal_id

    def post_create(self, **fields: str) -> AdminResponse:
        return self.admin_request(
            self.tenant_id,
            "post",
            "principals/create",
            data={"name": "Created Advertiser", "enable_mock": "1", **fields},
        )

    def post_edit(self, principal_id: str, **fields: str) -> AdminResponse:
        return self.admin_request(self.tenant_id, "post", f"principals/{principal_id}/edit", data=fields)

    def get_create_form(self) -> AdminResponse:
        return self.admin_request(self.tenant_id, "get", "principals/create")

    def follow(self, response: AdminResponse) -> AdminResponse:
        location = urlsplit(response.headers["Location"])
        return self.client.request("get", location.path + (f"?{location.query}" if location.query else ""))

    def post_rotate_token(
        self, principal_id: str, *, authenticated: bool, form: dict[str, str] | None = None
    ) -> AdminResponse:
        if authenticated:
            self.authenticate(self.tenant_id)
        else:
            self.admin_client(self.tenant_id)
            self.clear_auth()
        return self.admin_request(self.tenant_id, "post", f"principal/{principal_id}/rotate-token", data=form or {})

    def principals(self, *, name: str | None = None, tenant_id: str | None = None) -> list[Principal]:
        session = self.get_session()
        session.expire_all()
        query = select(Principal).filter_by(tenant_id=tenant_id or self.tenant_id)
        if name is not None:
            query = query.filter_by(name=name)
        return list(session.scalars(query))

    def token_hash(self, principal_id: str) -> str:
        return next(row.token_hash for row in self.principals() if row.principal_id == principal_id)

    def audit_rows(self, operation: str, *, method: str | None = None) -> list[AuditLog]:
        return [
            row
            for row in self.get_audit_logs(operation)
            if row.operation == operation and (method is None or row.details.get("method") == method)
        ]

    def unauthenticated_product_audit_principal(self) -> str:
        """Read the identity written by a real anonymous product-discovery call."""
        from tests.harness.client import AdCPTestClient
        from tests.harness.transport import Transport

        self.ensure_tenant(brand_manifest_policy="public")
        self._commit_factory_data()
        transport = Transport.E2E_REST if self.e2e_config else Transport.REST
        result = AdCPTestClient(self).call(
            "get_products", {"brief": "Available display inventory"}, transport, credential=self.credential(token=None)
        )
        assert result.is_success is True, result
        (row,) = self.get_audit_logs("get_products")
        return row.principal_id
