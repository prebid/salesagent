"""PerformanceEnv — integration test environment for _update_performance_index_impl.

Patches: get_adapter ONLY (the external ad server — performance feedback is
forwarded to the adapter as PackagePerformance entries).
Real: MediaBuyUoW ownership check, principal resolution, audit logging,
transport wrappers (MCP tool, A2A skill handler, REST route).

Requires: integration_db fixture.

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with PerformanceEnv() as env:
            tenant, principal = env.setup_default_data()
            buy = MediaBuyFactory(tenant=tenant, principal=principal)

            response = env.call_impl(
                media_buy_id=buy.media_buy_id,
                performance_data=[{"product_id": "pkg_1", "performance_index": 1.2}],
            )
            assert response.status == "success"

Available mocks via env.mock:
    "adapter" -- get_adapter mock; env.adapter_update_calls reads the
                 PackagePerformance forwarding.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import UpdatePerformanceIndexResponse
from tests.harness._base import IntegrationEnv


class PerformanceEnv(IntegrationEnv):
    """Integration test environment for update_performance_index.

    Only the adapter is mocked (external ad server). Everything else is real:
    ownership verification (MediaBuyUoW), principal resolution, audit logging,
    and all transport wrappers.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.performance.get_adapter",
        "audit": "src.core.tools.performance.get_audit_logger",
    }

    REST_ENDPOINT = "/api/v1/performance-index"

    def _configure_mocks(self) -> None:
        self.mock["adapter"].return_value.update_media_buy_performance_index.return_value = True

    @property
    def adapter_update_calls(self) -> list[Any]:
        """Call list of adapter.update_media_buy_performance_index (media_buy_id, [PackagePerformance])."""
        return self.mock["adapter"].return_value.update_media_buy_performance_index.call_args_list

    def call_impl(self, **kwargs: Any) -> UpdatePerformanceIndexResponse:
        """Call _update_performance_index_impl with a request built at the shared boundary."""
        from src.core.tools.performance import (
            _build_update_performance_index_request,
            _update_performance_index_impl,
        )

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = _build_update_performance_index_request(
            media_buy_id=kwargs["media_buy_id"],
            performance_data=kwargs["performance_data"],
            context=kwargs.get("context"),
        )
        return _update_performance_index_impl(req=req, identity=identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch through the REAL A2A pipeline (_handle_update_performance_index_skill)."""
        return self._run_a2a_handler("update_performance_index", UpdatePerformanceIndexResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call update_performance_index via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("update_performance_index", UpdatePerformanceIndexResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to UpdatePerformanceIndexBody shape for REST POST."""
        _BODY_FIELDS = ("media_buy_id", "performance_data", "context")
        return {k: kwargs[k] for k in _BODY_FIELDS if k in kwargs and kwargs[k] is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> UpdatePerformanceIndexResponse:
        """Parse REST JSON response into UpdatePerformanceIndexResponse."""
        return UpdatePerformanceIndexResponse(**data)
