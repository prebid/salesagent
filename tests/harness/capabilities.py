"""CapabilitiesEnv — integration test environment for _get_adcp_capabilities_impl.

Patches: adapter CLASS resolver + audit logger ONLY.
Real: get_db_session, TenantConfigUoW (publisher partners), the full response
builder (all hit real DB).

Production reads adapter default_channels/get_targeting_capabilities off a
tenant-resolved adapter CLASS (get_adapter_class_for_tenant,
src/core/helpers/adapter_helpers.py) — principal-free, identical for
anonymous and authenticated callers per INV-4 (salesagent-dn2s). The mock
below stands in for that class: production code only reads class-level
attributes/staticmethods off it, so a MagicMock works interchangeably.

The adapter/audit patches are the ONLY ones: everything the wire-shape and
pinned-schema integration tests grade (the response builder, strip_none_deep,
the real transport serializers) runs unpatched, so this env is also the
environment behind the get_adcp_capabilities rows in
tests/integration/test_wire_omission_matrix.py and
tests/integration/test_a2a_wire_integer_serialization.py.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CapabilitiesEnv() as env:
            tenant, principal = env.setup_default_data()
            response = env.call_impl()
            assert response.supported_protocols

Available mocks via env.mock:
    "adapter"      -- get_adapter (module-level import in capabilities.py)
    "audit_logger" -- log_tool_activity (module-level import in capabilities.py)

tickets: #1825 (#1592 / #1210)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from adcp.types import GetAdcpCapabilitiesRequest, GetAdcpCapabilitiesResponse

from src.adapters.base import TargetingCapabilities
from tests.harness._base import IntegrationEnv
from tests.harness._realize import e2e_unsupported, realize_e2e

#: Default channels seeded on the adapter mock — matches the feature fixture
#: comment ("fixture seeds channels 'display, social, ctv' on the adapter").
DEFAULT_ADAPTER_CHANNELS = ["display", "social", "ctv"]

#: Default pricing models seeded on the adapter mock -- mirrors the REAL
#: MockAdServerAdapter.get_supported_pricing_models() set (mock_ad_server.py),
#: so the harness's MagicMock stand-in doesn't silently degrade to an empty
#: iterator (MagicMock() auto-implements __iter__ -> iter([]) unless a
#: return_value is set) for the one BDD scenario family (T-UC-010-pricing)
#: that actually exercises this method.
DEFAULT_ADAPTER_PRICING_MODELS = {"cpm", "vcpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"}


def _full_targeting_capabilities() -> TargetingCapabilities:
    """A TargetingCapabilities with every dimension enabled."""
    from dataclasses import fields

    return TargetingCapabilities(**{f.name: True for f in fields(TargetingCapabilities)})


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for get_adcp_capabilities.

    Only mocks the adapter factory and the audit logger. Everything else is
    real: real DB, real TenantConfigUoW (publisher partners), real transport
    wrappers. Capabilities is a pure read — no adapter I/O beyond attribute
    access on the mock.

    Transport routing:
    - call_impl(): direct _get_adcp_capabilities_impl (sync)
    - MCP / A2A: declared, not hand-written. MCP_TOOL / A2A_SKILL /
      RESPONSE_MODEL route both through the base's deliver_mcp / deliver_a2a,
      which dispatch via the one AdCPTestClient core and return a DeliverResult
      carrying the real wire alongside the parsed payload — so the invalid-token
      rows (@T-UC-010-ext-c-a2a / @T-UC-010-ext-c-mcp) grade a real
      wire_error_envelope rather than a reconstructed exception.
    - REST: POST /api/v1/capabilities, the tool's one route — the base's
      _run_rest_request POSTs build_rest_body(**kwargs), which is `{}` for the
      parameterless discovery call and the filter/context payload otherwise.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a.
    MCP_TOOL = "get_adcp_capabilities"
    A2A_SKILL = "get_adcp_capabilities"
    RESPONSE_MODEL = GetAdcpCapabilitiesResponse

    EXTERNAL_PATCHES = {
        "adapter": "src.services.seller_capabilities.get_adapter_class_for_tenant",
        "audit_logger": "src.core.tools.capabilities.log_tool_activity",
    }

    REST_ENDPOINT = "/api/v1/capabilities"

    def _configure_mocks(self) -> None:
        """Happy-path adapter: default channels + full targeting capabilities."""
        adapter = MagicMock()
        adapter.default_channels = list(DEFAULT_ADAPTER_CHANNELS)
        adapter.get_targeting_capabilities.return_value = _full_targeting_capabilities()
        adapter.get_supported_pricing_models.return_value = set(DEFAULT_ADAPTER_PRICING_MODELS)
        self.mock["adapter"].return_value = adapter
        self._adapter_mock = adapter
        self._capability_declarations: dict[str, Any] = {}

    # -- Given-step helpers ---------------------------------------------------

    def declare_capabilities(self, **blocks: Any) -> None:
        """Persist the tenant's capability declaration blocks (salesagent-3xmz).

        Each keyword is one declaration block keyed exactly as it appears in the
        capability-declaration store (``trusted_match``, ``creative_specs``,
        ``measurement``, ...); repeated calls MERGE, so a scenario may build the
        declaration across several Given steps.

        No ``@realize_e2e``: this is a real tenant-config DB write, not a
        monkeypatch. ``configure_tenant_field`` (tests/harness/_base.py) updates
        both the in-memory tenant overrides (mock identity path) and the DB
        ``tenants`` row, which the real MCP/A2A/e2e auth chain reads back via
        ``config_loader.get_tenant_by_id`` — the same shape as the undecorated
        ``set_billing_policy`` / ``set_approval_mode`` precedents
        (tests/harness/account_sync.py). Because it needs no test-only injection
        seam it declares no ``E2EUnsupportedSetup``, so the e2e escape-hatch pin
        (``EXPECTED_UNSUPPORTED_DECLARATIONS``) does not grow.
        """
        self._capability_declarations.update(blocks)
        self.configure_tenant_field("capability_declarations", dict(self._capability_declarations))

    def set_portfolio_channels(self, channels: list[str]) -> None:
        """Seed the tenant's catalog so its portfolio offers *channels*.

        NO ``@realize_e2e``, and that is the point: one product row per channel is
        ORDINARY TENANT DATA, written through ``ProductFactory`` into the same
        database the live server reads, so in-process and e2e run the identical
        setup against the identical production read
        (``media_buy.portfolio.primary_channels`` unions each product's effective
        channels -- src/core/helpers/channel_helpers.py). Its sibling
        ``given_publisher_partnerships`` already seeds ``PublisherPartner`` this way,
        which is why publisher_domains has always graded over a real transport
        while channels did not.

        This replaces ``set_adapter_channels``, which configured a channel set on
        the ADAPTER. That could only ever be a per-adapter-TYPE constant
        (``AdServerAdapter.default_channels`` is a class attribute), so honouring it
        per tenant needed an override read in core -- a test-only control surface
        this repo deleted on purpose (a1b79d22d, prebid/salesagent#1891). Seeding
        products needs no such surface.

        One product per channel rather than one product declaring all of them: the
        production rule is a UNION ACROSS products, and a single multi-channel
        product would not exercise it.
        """
        from src.core.database.models import Tenant
        from tests.factories import ProductFactory

        tenant = self.get_session().get(Tenant, self._tenant_id)
        for channel in channels:
            ProductFactory(tenant=tenant, channels=[channel])
        self._commit_factory_data()

    def _realize_targeting_capabilities(self, **dims: bool) -> None:
        """E2E realization: persist targeting_capabilities into test_behavior."""
        from tests.factories.core import set_adapter_test_behavior

        set_adapter_test_behavior(self, self._tenant_id, targeting_capabilities=dims)

    @realize_e2e(_realize_targeting_capabilities)
    def set_targeting_capabilities(self, **dims: bool) -> None:
        """Configure adapter targeting capabilities from keyword flags.

        Unnamed dimensions default to False (TargetingCapabilities defaults).
        In-process: overrides the adapter mock directly. E2E: persists the
        override into AdapterConfig.config_json['test_behavior'], read by
        get_targeting_capabilities_override (src/core/helpers/adapter_helpers.py).
        """
        self._adapter_mock.get_targeting_capabilities.return_value = TargetingCapabilities(**dims)

    def _realize_adapter_unavailable(self) -> None:
        """E2E realization: point the tenant at an ad server that does not exist.

        A REAL operator misconfiguration, exercising a refusal production already
        has: ``get_adapter_class`` (src/adapters/__init__.py) raises
        ``AdCPConfigurationError`` for an ``adapter_type`` outside
        ``ADAPTER_REGISTRY``, and ``get_adapter_class_for_tenant`` is inside the
        capabilities degradation boundary, so the response degrades and records the
        advisory exactly as it would for a tenant whose operator typed the adapter
        name wrong.

        This is why the "unavailable" simulation flag is not needed: no new
        production code, no fault-injection branch, and the state the scenario
        describes -- "this seller's adapter cannot be resolved" -- is one a real
        deployment reaches. Scoped to the scenario's own tenant, so the
        misconfiguration cannot follow any other tenant onto the media-buy path.
        """
        from tests.factories.core import set_adapter_type

        set_adapter_type(self, self._tenant_id, "__no_such_ad_server__")

    @realize_e2e(_realize_adapter_unavailable)
    def make_adapter_unavailable(self) -> None:
        """Adapter factory raises — production degrades to default channels.

        In-process: the adapter-class mock raises directly. E2E: persists
        test_behavior['unavailable']=True, read by get_adapter_class_for_tenant
        (src/core/helpers/adapter_helpers.py), which raises for mock-adapter
        tenants only.
        """
        self.mock["adapter"].side_effect = Exception("adapter unavailable (harness)")

    @realize_e2e(
        e2e_unsupported(
            "the fault is 'iterating the adapter's default_channels raises', which is a property of "
            "the in-process adapter object. Unlike 'unavailable' -- which get_adapter_class_for_tenant "
            "honours from AdapterConfig.test_behavior -- production has no read that could make channel "
            "ENUMERATION fail on a real adapter, and adding one would put a fault-injection branch in "
            "production for a test's benefit. The non-cascade it grades is transport-independent "
            "(one function's control flow in capabilities.py), so the in-process transports grade it fully"
        )
    )
    def make_adapter_channel_enumeration_fail(self) -> None:
        """The adapter RESOLVES, but reading its channels raises.

        Distinct from make_adapter_unavailable, and the distinction is the whole
        point: the adapter class is used for THREE things (channels, supported
        pricing models, targeting capabilities). If a channel-enumeration failure
        were allowed to discard the resolved adapter, one degradation would
        cascade into two more absent sections -- and the pricing-models one would
        vanish with no advisory at all, because its guard just skips. This seam
        is what makes that cascade observable; nothing else in the corpus
        distinguishes "adapter is gone" from "one thing about it failed".
        """

        class _RaisingChannels:
            def __iter__(self):
                raise RuntimeError("adapter channel enumeration failed (harness)")

        self._adapter_mock.default_channels = _RaisingChannels()

    @realize_e2e(
        e2e_unsupported(
            "no production tenant-config surface for the seller's advertised adcp version set "
            "(SUPPORTED_ADCP_VERSIONS/MAJORS are process-wide constants) — a "
            "module-constant monkeypatch cannot cross a real HTTP process boundary"
        )
    )
    def set_supported_versions(self, versions: list[str]) -> None:
        """Override the seller's advertised adcp_version/adcp_major_version release set.

        In-process only: monkeypatches src.core.version_negotiation's derived
        module constants, reached by every in-process transport (a2a/mcp/rest
        within the BDD harness) via their per-call lazy re-import.
        """
        majors = sorted({int(v.split(".")[0]) for v in versions})
        version_patcher = patch("src.core.version_negotiation.SUPPORTED_ADCP_VERSIONS", list(versions))
        major_patcher = patch("src.core.version_negotiation.SUPPORTED_ADCP_MAJORS", majors)
        self.mock["supported_versions"] = version_patcher.start()
        self.mock["supported_majors"] = major_patcher.start()
        self._guard("patch:supported_versions", version_patcher.stop)
        self._guard("patch:supported_majors", major_patcher.stop)

    @realize_e2e(
        e2e_unsupported(
            "no production tenant-config surface for the seller's advertised build_version "
            "(src.core.version.get_version() is a process-wide package-metadata read) "
            "— cannot be injected over real HTTP"
        )
    )
    def set_build_version(self, build_version: str) -> None:
        """Override the advisory build_version surfaced on a VERSION_UNSUPPORTED error."""
        patcher = patch("src.core.version.get_version", return_value=build_version)
        self.mock["build_version"] = patcher.start()
        self._guard("patch:build_version", patcher.stop)

    @realize_e2e(
        e2e_unsupported(
            "no production tenant-config surface for the adcp.idempotency posture "
            "(get_idempotency_posture() is a process-wide provider) — a "
            "module-function monkeypatch cannot cross a real HTTP process boundary"
        )
    )
    def set_idempotency_posture(
        self,
        *,
        supported: bool,
        replay_ttl_seconds: int | None = None,
        in_flight_max_seconds: int | None = None,
        account_id_is_opaque: bool = False,
    ) -> None:
        """Override the seller's declared adcp.idempotency posture.

        In-process only: monkeypatches get_idempotency_posture() at its module
        seam (src.core.idempotency_policy -- src.core.tools.capabilities
        ._build_adcp_block re-imports it per call). The overridden posture
        still flows through the REAL IdempotencyPosture.check_bounds()/
        to_sdk_union() production code -- only the input posture is
        test-controlled, not the validation/shaping.
        """
        from src.core.idempotency_policy import IdempotencyPosture

        posture = IdempotencyPosture(
            supported=supported,
            replay_ttl_seconds=replay_ttl_seconds,
            in_flight_max_seconds=in_flight_max_seconds,
            account_id_is_opaque=account_id_is_opaque,
        )
        patcher = patch(
            "src.core.idempotency_policy.get_idempotency_posture",
            return_value=posture,
        )
        self.mock["idempotency_posture"] = patcher.start()
        self._guard("patch:idempotency_posture", patcher.stop)

    @realize_e2e(
        e2e_unsupported("no production DB fault hook; TenantConfigUoW read failure cannot be injected over real HTTP")
    )
    def break_tenant_config_db(self) -> None:
        """Make the publisher-partner DB read fail — production degrades to placeholder.

        Patches TenantConfigUoW at the capabilities module seam. Registered
        with ``_guard``, so it is stopped on ctx-independent env teardown along
        with everything else — including when a later ``__enter__`` step raises.
        In-process only — no server-side DB-fault-injection surface exists (e2e branch
        declares E2EUnsupportedSetup).
        """
        patcher = patch(
            "src.services.seller_capabilities.TenantConfigUoW",
            side_effect=Exception("tenant config DB failure (harness)"),
        )
        self.mock["tenant_config_uow"] = patcher.start()
        self._guard("patch:tenant_config_uow", patcher.stop)

    # credential() lives on BaseTestEnv (tests/harness/_base.py) — every Env
    # subclass inherits it; token=None and token=INVALID_TOKEN are its no-auth forms.

    # -- Transport verbs ------------------------------------------------------

    @staticmethod
    def _build_request(**kwargs: Any) -> GetAdcpCapabilitiesRequest:
        """Build the typed request from flat When-step kwargs."""
        return GetAdcpCapabilitiesRequest(**kwargs)

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call _get_adcp_capabilities_impl directly (sync — no wrapper needed).

        Accepts either a pre-built ``req=GetAdcpCapabilitiesRequest(...)`` or the
        flat When-step kwargs (protocols/context/adcp_version/...) that
        ``_build_request`` assembles. No kwargs at all means ``req=None`` — the
        parameterless discovery call the wire-shape tests exercise.
        """
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None and kwargs:
            req = self._build_request(**kwargs)
        return _get_adcp_capabilities_impl(req, identity)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Flat kwargs (protocols/context/adcp_version/adcp_major_version) map
        1:1 onto GetCapabilitiesBody's top-level fields — no req object, no
        per-field extraction needed.
        """
        return kwargs

    # parse_rest_response: the base's, which revives RESPONSE_MODEL.

    # -- Async variants for @pytest.mark.asyncio tests ------------------------

    async def call_a2a_async(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Async wrapper for tests already inside an event loop."""
        return await asyncio.to_thread(self.call_a2a, **kwargs)
