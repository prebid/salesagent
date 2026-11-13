"""Drive principal forms through the shared admin client and read their persisted state."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from src.core.database.models import AuditLog, Principal
from src.core.database.repositories.principal import PrincipalRepository
from tests.harness._base import BareIntegrationEnv
from tests.harness.admin_client import AdminClient, AdminResponse
from tests.harness.database_faults import DatabaseFaultMixin
from tests.harness.transport import E2EConfig


class AdminPrincipalEnv(DatabaseFaultMixin, BareIntegrationEnv):
    """Principal creation, editing and token rotation on either admin transport."""

    def __init__(
        self,
        *,
        mode: str = "integration",
        base_url: str | None = None,
        database_url: str | None = None,
        e2e_config: E2EConfig | None = None,
        **kwargs: Any,
    ) -> None:
        if mode not in {"integration", "e2e"}:
            raise ValueError(f"Unknown admin transport: {mode}")
        if e2e_config is None and mode == "e2e":
            if not base_url or not database_url:
                raise ValueError("Live principal tests require both base_url and database_url")
            e2e_config = E2EConfig(base_url=base_url, postgres_url=database_url)
        super().__init__(database_url=database_url, e2e_config=e2e_config, **kwargs)
        self._client: AdminClient | None = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def client(self) -> AdminClient:
        assert self._client is not None, "Enter the principals env before issuing requests"
        return self._client

    def _enter_post(self) -> None:
        self.ensure_tenant()
        self._client = AdminClient(self.e2e_config.base_url if self.e2e_config else None)
        self._guard("principal_admin_client", self._client.close)
        self.authenticate()

    def authenticate(self, tenant_id: str | None = None) -> None:
        self.client.authenticate(tenant_id or self.tenant_id)

    def clear_auth(self) -> None:
        self.client.clear_auth()

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
        self._commit_factory_data()
        return self.client.request(
            "post",
            f"/tenant/{self.tenant_id}/principals/create",
            data={"name": "Created Advertiser", "enable_mock": "1", **fields},
        )

    def post_edit(self, principal_id: str, **fields: str) -> AdminResponse:
        self._commit_factory_data()
        return self.client.request("post", f"/tenant/{self.tenant_id}/principals/{principal_id}/edit", data=fields)

    def get_create_form(self) -> AdminResponse:
        self._commit_factory_data()
        return self.client.request("get", f"/tenant/{self.tenant_id}/principals/create")

    def follow(self, response: AdminResponse) -> AdminResponse:
        location = urlsplit(response.headers["Location"])
        return self.client.request("get", location.path + (f"?{location.query}" if location.query else ""))

    def post_rotate_token(
        self, principal_id: str, *, authenticated: bool, form: dict[str, str] | None = None
    ) -> AdminResponse:
        self._commit_factory_data()
        if authenticated:
            self.authenticate()
        else:
            self.clear_auth()
        return self.client.request(
            "post", f"/tenant/{self.tenant_id}/principal/{principal_id}/rotate-token", data=form or {}
        )

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
        session = self.get_session()
        session.expire_all()
        rows = session.scalars(select(AuditLog).filter_by(tenant_id=self.tenant_id, operation=operation))
        return [row for row in rows if method is None or row.details.get("method") == method]
