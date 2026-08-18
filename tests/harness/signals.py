"""GetSignalsEnv / ActivateSignalEnv — integration test environments for signals.py.

IMPL only. get_signals and activate_signal are NOT registered as MCP tools
(src/core/main.py's _register_tool() list has no entry for either -- confirmed
by grep), A2A intentionally excludes signals
(src/a2a_server/adcp_a2a_server.py:89, "signals should come from dedicated
signals agents", also documented at tests/integration/test_tool_registration.py:8),
and no REST route exists. These two functions are therefore unreachable on
EVERY transport in production today -- there is no live wire to grade. The
tests using these envs assert on the typed payload's model_dump() directly
(same rationale as test_get_products_wire_schema.py's IMPL-only test), as
defense-in-depth at the model layer, not as wire-transport coverage.

Requires: integration_db fixture.
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import ActivateSignalResponse, GetSignalsResponse
from tests.harness._base import IntegrationEnv


class GetSignalsEnv(IntegrationEnv):
    """Integration test environment for get_signals (IMPL only, see module docstring)."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def _configure_mocks(self) -> None:
        """No mocks needed."""

    def call_impl(self, **kwargs: Any) -> GetSignalsResponse:
        """Call _get_signals_impl with real identity/tenant resolution."""
        import asyncio

        from src.core.schemas import GetSignalsRequest
        from src.core.tools.signals import _get_signals_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            req = GetSignalsRequest(**kwargs)
        return asyncio.run(_get_signals_impl(req, identity))


class ActivateSignalEnv(IntegrationEnv):
    """Integration test environment for activate_signal (IMPL only, see module docstring)."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def _configure_mocks(self) -> None:
        """No mocks needed."""

    def call_impl(self, **kwargs: Any) -> ActivateSignalResponse:
        """Call _activate_signal_impl with real identity/tenant resolution."""
        import asyncio

        from src.core.tools.signals import _activate_signal_impl, _build_activate_signal_request

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            req = _build_activate_signal_request(
                signal_agent_segment_id=kwargs["signal_agent_segment_id"],
                campaign_id=kwargs.get("campaign_id"),
                media_buy_id=kwargs.get("media_buy_id"),
                context=kwargs.get("context"),
            )
        return asyncio.run(_activate_signal_impl(req=req, identity=identity))
