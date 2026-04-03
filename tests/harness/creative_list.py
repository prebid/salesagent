"""CreativeListEnv — integration test environment for _list_creatives_impl.

Patches: audit logger ONLY.
Real: get_db_session, CreativeRepository, all query building (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal)

            response = env.call_impl()
            assert len(response.creatives) == 1

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import in listing.py)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import ListCreativesResponse
from tests.harness._base import IntegrationEnv


class CreativeListEnv(IntegrationEnv):
    """Integration test environment for _list_creatives_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real CreativeRepository -> real DB reads
    - Real query building, filtering, pagination
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.creatives.listing.get_audit_logger",
    }
    REST_ENDPOINT = "/api/v1/creatives"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for audit logger."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    def call_impl(self, **kwargs: Any) -> ListCreativesResponse:
        """Call _list_creatives_impl with real DB.

        Accepts all _list_creatives_impl kwargs. The 'identity' kwarg
        defaults to self.identity if not provided.
        """
        from src.core.tools.creatives.listing import _list_creatives_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return _list_creatives_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> ListCreativesResponse:
        """Call list_creatives via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("list_creatives", ListCreativesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> ListCreativesResponse:
        """Call list_creatives via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("list_creatives", ListCreativesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to ListCreativesBody shape for REST POST."""
        body: dict[str, Any] = {}
        for key in ("media_buy_id", "media_buy_ids", "buyer_ref", "status", "format"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> ListCreativesResponse:
        """Parse REST JSON into ListCreativesResponse."""
        return ListCreativesResponse(**data)
