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

        _list_creatives_impl now takes a typed ``req: ListCreativesRequest`` plus
        the out-of-band ``format`` / ``include_performance`` / ``include_sub_assets``
        / ``page`` kwargs. This method accepts either a pre-built ``req=`` or the
        flat request fields and builds the request (matching MediaBuyCreateEnv).
        """
        from src.core.tools.creatives.listing import _build_list_creatives_request, _list_creatives_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)

        # Out-of-band params not representable on ListCreativesRequest
        out_of_band = {
            key: kwargs.pop(key)
            for key in ("format", "include_performance", "include_sub_assets", "page")
            if key in kwargs
        }

        req = kwargs.pop("req", None)
        if req is None:
            req = _build_list_creatives_request(**kwargs)

        return _list_creatives_impl(req=req, identity=identity, **out_of_band)

    def call_a2a(self, **kwargs: Any) -> ListCreativesResponse:
        """Call list_creatives via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("list_creatives", ListCreativesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> ListCreativesResponse:
        """Call list_creatives via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("list_creatives", ListCreativesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to ListCreativesBody shape for REST POST."""
        body: dict[str, Any] = {}
        for key in ("media_buy_id", "media_buy_ids", "status", "format"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        # The structured filters travel over REST as a JSON dict — the body field is
        # typed dict and coerced to CreativeFilters server-side. Callers pass an
        # already-serialized dict (see the UC-018 concept_ids When step).
        filters = kwargs.get("filters")
        if filters is not None:
            body["filters"] = filters
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> ListCreativesResponse:
        """Parse REST JSON into ListCreativesResponse."""
        return ListCreativesResponse(**data)
