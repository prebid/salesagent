"""CapabilitiesEnv — integration test environment for get_adcp_capabilities.

Nothing external is mocked: capabilities is a read-only discovery call whose
whole answer is derived from the tenant row, its publisher partnerships and the
bound ad-server adapter. Those all live in the real database, so the env seeds a
tenant/principal via factories (``ad_server="mock"`` → ``MockAdServer``) and lets
production resolve the adapter for real. The one scenario-scoped override is
``set_adapter_pricing_models`` (degrade partitions) — it seeds the adapter's
declared pricing surface as a DB row the real adapter reads, so the degrade
partitions dispatch live on every transport, e2e included.

Transport coverage: A2A (``get_adcp_capabilities`` skill), MCP
(``get_adcp_capabilities`` tool), and REST. The REST route is
``GET /api/v1/capabilities`` — the only harness endpoint that is not a POST —
so this env derives the verb from the request: the parameterless discovery call
GETs, a request carrying a body POSTs it. ``build_rest_body`` records whether a
body was built and the ``REST_METHOD`` property reads that flag; the base
``_run_rest_request`` and ``RestE2EDispatcher`` both honor it, and both route the
"does this verb carry ``json=``" decision through ``client.py``'s single
``_rest_request_kwargs``/``_BODILESS_REST_VERBS`` home, so the two dispatch paths
cannot disagree on the verb (precedent: the ``REST_METHOD``/``REST_ENDPOINT``
properties on ``media_buy_dual.py``).

Usage::

    with CapabilitiesEnv() as env:
        env.setup_default_data()
        result = env.call_via(Transport.MCP)
        assert result.payload.media_buy.supported_pricing_models
"""

from __future__ import annotations

from typing import Any

from adcp.types import GetAdcpCapabilitiesResponse

from tests.harness._base import IntegrationEnv


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for ``_get_adcp_capabilities_impl``."""

    # Dispatch declaration: the base owns call_mcp/call_a2a.
    MCP_TOOL = "get_adcp_capabilities"
    A2A_SKILL = "get_adcp_capabilities"
    RESPONSE_MODEL = GetAdcpCapabilitiesResponse

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/capabilities"
    # Whether the last-built REST body carried request params. Set by
    # ``build_rest_body`` (which both dispatch paths call first) and read by the
    # ``REST_METHOD`` property to derive the verb — a single source of truth for
    # the in-process and e2e dispatchers, instead of a hand-synced constant.
    _rest_has_body: bool = False

    def set_adapter_pricing_models(self, models: set[str]) -> None:
        """Pin what the bound (mock) adapter reports as its pricing surface.

        The degrade partitions of POST-S10 need an adapter that reports nothing
        or off-enum strings. Production still resolves the REAL ``MockAdServer``
        (``EXTERNAL_PATCHES`` stays empty); the surface is seeded as
        ``test_behavior["supported_pricing_models"]`` on the tenant's
        ``AdapterConfig`` row, which ``MockAdServer.get_supported_pricing_models``
        reads — the same DB-resolvable channel the adapter's fault injection
        already uses.

        Deliberately NOT a ``realize_e2e`` two-branch method: the DB row is the
        one mechanism both dispatch paths read, so there is no in-process/e2e
        split to keep in sync and the degrade partitions dispatch live on every
        transport. In e2e mode ``self._session`` is bound to the live server's
        Postgres, so the row the server reads is the row this writes.

        ``sorted()`` because the value round-trips through JSON, which has no
        set type — an ordered list keeps the persisted row deterministic.
        """
        from tests.factories.core import set_adapter_test_behavior

        set_adapter_test_behavior(self, self._tenant_id, supported_pricing_models=sorted(models))

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call ``_get_adcp_capabilities_impl`` directly (no wire)."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _get_adcp_capabilities_impl(kwargs["req"], kwargs["identity"])

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build the REST body and record whether the request carried params.

        Capabilities discovery is parameterless today (``req=None`` -> ``{}``), so
        the recorded flag drives ``REST_METHOD`` to a bodiless GET. A future
        parameterized request (protocols filter, context echo, version) yields a
        non-empty body and POSTs it — the verb follows the request, not a
        hand-synced constant.
        """
        body = super().build_rest_body(**kwargs)
        self._rest_has_body = bool(body)
        return body

    @property
    def REST_METHOD(self) -> str:  # noqa: N802 — dispatcher reads getattr(env, "REST_METHOD", "post")
        """Verb derived from the request: POST when it carries a body, else GET.

        ``RestE2EDispatcher`` (which never calls ``_run_rest_request``) reads this
        AFTER it calls ``build_rest_body``, so the flag is current; the in-process
        ``_run_rest_request`` reads the same property, so the two dispatch paths
        can never disagree on the verb.
        """
        return "post" if self._rest_has_body else "get"

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
