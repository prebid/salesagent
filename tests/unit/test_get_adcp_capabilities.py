"""Unit tests for get_adcp_capabilities tool.

Tests the capabilities endpoint that returns what this sales agent supports
per the AdCP spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import GetAdcpCapabilitiesResponse
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    SupportedProtocol,
)

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity


class TestGetAdcpCapabilitiesSchema:
    """Test GetAdcpCapabilitiesResponse schema validation."""

    def test_response_requires_adcp_field(self):
        """Test that response requires adcp field."""
        # Must have adcp and supported_protocols per spec
        with pytest.raises(ValueError):
            GetAdcpCapabilitiesResponse(supported_protocols=[SupportedProtocol.media_buy])

    def test_response_requires_supported_protocols(self):
        """Test that response requires supported_protocols field."""
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            Idempotency3,
            MajorVersion,
        )

        # Must have supported_protocols (non-empty list)
        with pytest.raises(ValueError):
            GetAdcpCapabilitiesResponse(
                adcp=Adcp(
                    major_versions=[MajorVersion(root=3)],
                    idempotency=Idempotency3(supported=False),
                ),
                supported_protocols=[],  # Empty not allowed
            )

    def test_valid_minimal_response(self):
        """Test creating a valid minimal response."""
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            Idempotency3,
            MajorVersion,
        )

        response = GetAdcpCapabilitiesResponse(
            adcp=Adcp(
                major_versions=[MajorVersion(root=3)],
                idempotency=Idempotency3(supported=False),
            ),
            supported_protocols=[SupportedProtocol.media_buy],
        )

        assert response.adcp is not None
        assert len(response.adcp.major_versions) == 1
        assert response.adcp.major_versions[0].root == 3
        assert SupportedProtocol.media_buy in response.supported_protocols

    def test_response_with_media_buy_capabilities(self):
        """Test creating response with media_buy capabilities."""
        from adcp.types.generated_poc.core.media_buy_features import MediaBuyFeatures
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
            Adcp,
            Execution,
            Idempotency3,
            MajorVersion,
            MediaBuy,
            Portfolio,
            PublisherDomain,
            Targeting,
        )

        response = GetAdcpCapabilitiesResponse(
            adcp=Adcp(
                major_versions=[MajorVersion(root=3)],
                idempotency=Idempotency3(supported=False),
            ),
            supported_protocols=[SupportedProtocol.media_buy],
            media_buy=MediaBuy(
                portfolio=Portfolio(
                    description="Test portfolio",
                    publisher_domains=[PublisherDomain(root="example.com")],
                ),
                features=MediaBuyFeatures(
                    inline_creative_management=True,
                    property_list_filtering=True,
                    # catalog_management example must match production (False until
                    # sync_catalogs ships). Schema-construction tests are
                    # documentation by example; declaring True here while
                    # production declares False would mislead future readers.
                    catalog_management=False,
                ),
                execution=Execution(
                    targeting=Targeting(
                        geo_countries=True,
                        geo_regions=True,
                    ),
                ),
            ),
        )

        assert response.media_buy is not None
        assert response.media_buy.portfolio is not None
        assert len(response.media_buy.portfolio.publisher_domains) == 1
        assert response.media_buy.features is not None
        assert response.media_buy.features.inline_creative_management is True


class TestGetAdcpCapabilitiesImports:
    """Test that get_adcp_capabilities can be imported correctly."""

    def test_capabilities_module_imports(self):
        """Test that the capabilities module can be imported."""
        from src.core.tools import capabilities

        assert capabilities is not None

    def test_impl_function_exists(self):
        """Test that the impl function exists."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        assert callable(_get_adcp_capabilities_impl)

    def test_mcp_wrapper_exists(self):
        """Test that the MCP wrapper function exists."""
        from src.core.tools.capabilities import get_adcp_capabilities

        assert callable(get_adcp_capabilities)

    def test_raw_function_exists(self):
        """Test that the raw function exists."""
        from src.core.tools.capabilities import get_adcp_capabilities_raw

        assert callable(get_adcp_capabilities_raw)

    def test_raw_function_exported_from_tools(self):
        """Test that the raw function is exported from tools module."""
        from src.core.tools import get_adcp_capabilities_raw

        assert callable(get_adcp_capabilities_raw)


class TestGetAdcpCapabilitiesImpl:
    """Test the _get_adcp_capabilities_impl function."""

    def test_impl_returns_response_without_context(self):
        """Test that impl returns minimal response when no context is available."""
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Reset tenant context to ensure clean state (tests may have set it)
        current_tenant.set(None)

        # Call without context - should return minimal response
        response = _get_adcp_capabilities_impl(None, None)

        assert isinstance(response, GetAdcpCapabilitiesResponse)
        assert response.adcp is not None
        assert response.adcp.major_versions[0].root == 3
        assert SupportedProtocol.media_buy in response.supported_protocols
        # The honesty VALUES below (idempotency.supported, specialisms, account.sandbox /
        # supported_billing) are an _impl self-consistency check; the AUTHORITATIVE wire grade is
        # tests/helpers/capabilities.py::assert_declared_capabilities, exercised across a2a/mcp/rest
        # by the UC-010 BDD honesty graders + the integration wire test (#1329). Kept here
        # (not deleted) as the fast _impl-layer smoke, not as a second source of truth.
        # Idempotency declares supported=False (agent-wide). create_media_buy dedups, but
        # 3 of 4 mutating tools (update_media_buy, sync_accounts, sync_governance) do not,
        # and the schema's supported is Literal[True] with no per-tool field — so the honest
        # agent-wide claim is the Idempotency3 (supported=False) variant (#1329).
        assert response.adcp.idempotency.supported is False
        assert not hasattr(response.adcp.idempotency, "replay_ttl_seconds")
        # No specialism is declared (#1329): sales-non-guaranteed was withdrawn — its
        # requires_scenarios gate is not backed end-to-end (see _SPECIALISM_AUDIT). The
        # exact declared set is graded on the wire by assert_declared_capabilities.
        assert response.specialisms == []
        # #1329 gap 13: sandbox is declared FALSE (no behavioral isolation ships).
        # Declaring the account capability requires the schema-required supported_billing.
        assert response.account is not None
        assert response.account.sandbox is False
        assert response.account.supported_billing  # non-empty (schema-required)

    def test_impl_response_is_json_serializable(self):
        """The _impl response round-trips through model_dump(mode="json").

        A structural smoke check of the _impl layer only — it asserts the envelope keys
        are present and JSON-serializable, NOT the honesty VALUES (specialisms set,
        account.sandbox). Those are the buyer contract and are graded on the real wire by
        ``tests/helpers/capabilities.py::assert_declared_capabilities`` across a2a/mcp/rest
        (#1329) — a model_dump() equality here is a self-consistency check, not a
        wire grade, so it must not masquerade as one.
        """
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Reset tenant context to ensure clean state
        current_tenant.set(None)

        response = _get_adcp_capabilities_impl(None, None)
        data = response.model_dump(mode="json")

        assert {"adcp", "supported_protocols", "specialisms", "account"} <= data.keys()


def test_declared_specialisms_are_valid_non_deprecated_pinned_enum_ids():
    """Every declared specialism is a valid, non-deprecated id in the PINNED enum schema.

    The audit's declaration must be backed by machine-readable spec data, not prose that
    can drift from the artifact it cites (#1329). This reads the pinned
    `enums/specialism.json` (adcp 6.6.0 / AdCP 3.1.1) — the repo's grounding authority —
    and asserts each `_DECLARED_SPECIALISMS` entry is a real enum member AND absent from
    `x-deprecated-enum-values`. It reddens if a declared specialism is dropped/deprecated
    upstream on a spec bump, or if a deprecated slot (e.g. sales-proposal-mode) is ever
    declared — turning the deprecation rationale in capabilities.py into an enforced check
    instead of a comment.
    """
    from src.core.tools.capabilities import _DECLARED_SPECIALISMS
    from tests.helpers.pinned_schema import load

    schema = load("enums/specialism.json")
    valid_ids = set(schema["enum"])
    deprecated_ids = set(schema.get("x-deprecated-enum-values", []))
    # Guard the guard: the artifact must actually carry a deprecated set, else this test
    # would pass vacuously if the schema shape changed.
    assert deprecated_ids, "pinned specialism.json must declare x-deprecated-enum-values"

    for specialism in _DECLARED_SPECIALISMS:
        assert specialism.value in valid_ids, f"{specialism.value} is not a pinned AdcpSpecialism enum id"
        assert specialism.value not in deprecated_ids, (
            f"{specialism.value} is deprecated in the pinned specialism.json — do not declare a deprecated slot"
        )


# The test layer states what SATISFIES each specialism's storyboard requirement (src states
# only the requirement — #1329). Maps a storyboard scenario id ->
# the executing in-repo mirror (``module::symbol``) that grades it end-to-end. A row can be
# DECLARED only if its (transcribed) requires_scenarios are all backed here. Empty today: no
# specialism's full storyboard gate is mirrored, so nothing is declared.
_BACKED_SPECIALISM_SCENARIOS: dict[str, str] = {}


def _specialism_qualifies(sid, row, *, registered_tools, deprecated_ids, backed) -> bool:
    """True iff this seller genuinely qualifies to DECLARE the specialism.

    A row QUALIFIES when its parent protocol is hosted AND every required tool is registered
    AND the id is not deprecated in the pinned enum AND its requires_scenarios are transcribed
    (not the NOT_TRANSCRIBED sentinel) AND every scenario resolves to a backed, importable
    ``module::symbol``. The gate then asserts decision == DECLARED iff qualifies, both ways.
    """
    import importlib

    from src.core.tools.capabilities import _SUPPORTED_PROTOCOLS

    if sid.value in deprecated_ids:
        return False
    if row.parent_protocol not in _SUPPORTED_PROTOCOLS:
        return False
    if set(row.required_tools) - registered_tools:
        return False
    if row.requires_scenarios is None:  # NOT_TRANSCRIBED sentinel — cannot declare unverified
        return False
    for scenario in row.requires_scenarios:
        symbol = backed.get(scenario)
        if not symbol:
            return False
        module_name, _, attr = symbol.partition("::")
        module = importlib.import_module(module_name)
        if not hasattr(module, attr):
            return False
    return True


def test_specialism_audit_gate():
    """Walk EVERY specialism audit row and assert its decision is consistent with the facts (#1329).

    The audit is DATA (`_SPECIALISM_AUDIT`), not prose, so an inconsistent decision reddens HERE
    instead of only when a human re-reads a comment. For EVERY row (not only the DECLARED ones —
    the round-13 finding: the old gate ``continue``d past every non-declared row and so walked
    nothing), the gate computes whether the row genuinely QUALIFIES and asserts
    ``decision == DECLARED`` iff it does. That two-way check catches a DECLARED row that does not
    qualify AND a DECLINED row that secretly does (e.g. an empty required_tools reading as "no
    requirement", or a hosted-parent row whose decline is unjustified).

    Also asserts the table covers every pinned enum member (a new upstream specialism forces an
    audit decision) and that `_DECLARED_SPECIALISMS` is exactly the derived declared set. Nothing
    is declared today, so every row must be DECLINED and must genuinely fail ≥1 gate; flipping any
    media_buy row (e.g. sales-non-guaranteed) to DECLARED reddens the gate because its
    requires_scenarios are NOT_TRANSCRIBED / not in the backed map.
    """
    from adcp.types.generated_poc.enums.specialism import AdcpSpecialism

    from src.core.main import mcp
    from src.core.tools.capabilities import _DECLARED_SPECIALISMS, _SPECIALISM_AUDIT, _SpecialismDecision
    from src.core.validation_helpers import run_async_in_sync_context
    from tests.helpers.pinned_schema import load

    # Completeness: every pinned enum member has an audit decision.
    assert set(_SPECIALISM_AUDIT) == set(AdcpSpecialism), (
        "every pinned AdcpSpecialism must have an _SPECIALISM_AUDIT row — a new upstream "
        "specialism forces an explicit declared/declined decision"
    )

    # Derived list matches the table (they cannot drift).
    derived = [sid for sid, row in _SPECIALISM_AUDIT.items() if row.decision is _SpecialismDecision.DECLARED]
    assert _DECLARED_SPECIALISMS == derived

    registered_tools = {t.name for t in run_async_in_sync_context(mcp.list_tools())}
    deprecated_ids = set(load("enums/specialism.json").get("x-deprecated-enum-values", []))
    assert deprecated_ids, "pinned specialism.json must declare x-deprecated-enum-values"

    for sid, row in _SPECIALISM_AUDIT.items():
        qualifies = _specialism_qualifies(
            sid,
            row,
            registered_tools=registered_tools,
            deprecated_ids=deprecated_ids,
            backed=_BACKED_SPECIALISM_SCENARIOS,
        )
        declared = row.decision is _SpecialismDecision.DECLARED
        assert declared == qualifies, (
            f"{sid.value}: decision={row.decision.value} but qualifies={qualifies} — the audit is "
            f"self-inconsistent (a DECLARED row must pass every gate; a DECLINED row must fail ≥1). "
            f"rationale={row.rationale!r}"
        )


def test_specialism_audit_gate_backed_resolution_blocks_underbacked_declare():
    """The requires_scenarios backing check actually resolves symbols and blocks an under-backed declare.

    Exercises ``_specialism_qualifies``'s scenario-resolution path with a SYNTHETIC row (no real
    row is DECLARED, so this keeps the tuple/backing machinery live rather than dead) and pins the
    two failure modes that keep an unbacked declaration out: a transcribed scenario with no backed
    mirror, and a NOT_TRANSCRIBED column.
    """
    from adcp.types.generated_poc.enums.specialism import AdcpSpecialism

    from src.core.tools.capabilities import NOT_TRANSCRIBED, SupportedProtocol, _SpecialismAudit, _SpecialismDecision

    sid = AdcpSpecialism.sales_non_guaranteed
    tools = {"sync_governance", "get_products", "create_media_buy"}
    row = _SpecialismAudit(
        decision=_SpecialismDecision.DECLARED,
        parent_protocol=SupportedProtocol.media_buy,
        required_tools=("sync_governance", "get_products", "create_media_buy"),
        requires_scenarios=("media_buy_seller/delivery_reporting",),
        rationale="synthetic",
    )
    common = {"registered_tools": tools, "deprecated_ids": set()}

    # Scenario not backed -> does not qualify.
    assert not _specialism_qualifies(sid, row, backed={}, **common)
    # Scenario backed by a resolvable module::symbol -> qualifies (resolution path runs).
    assert _specialism_qualifies(
        sid,
        row,
        backed={"media_buy_seller/delivery_reporting": "src.core.tools.capabilities::_adcp_metadata"},
        **common,
    )
    # NOT_TRANSCRIBED column -> never qualifies even with tools met.
    assert not _specialism_qualifies(sid, row._replace(requires_scenarios=NOT_TRANSCRIBED), backed={}, **common)


class TestGetAdcpCapabilitiesWithTenant:
    """Test get_adcp_capabilities with mocked tenant context."""

    def test_impl_returns_full_response_with_tenant(self):
        """Test that impl returns full capabilities when tenant context is available."""
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Set up mock tenant
        mock_tenant = {
            "tenant_id": "test-tenant-123",
            "name": "Test Publisher",
            "subdomain": "testpub",
            "advertising_policy": {"description": "Family-friendly content only"},
        }
        current_tenant.set(mock_tenant)

        try:
            # Mock TenantConfigUoW to avoid actual DB calls
            mock_repo = MagicMock()
            mock_repo.list_publisher_partners.return_value = []
            mock_uow = MagicMock()
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow.tenant_config = mock_repo

            with patch("src.core.tools.capabilities.TenantConfigUoW", return_value=mock_uow):
                from tests.factories import PrincipalFactory

                identity = PrincipalFactory.make_identity(
                    principal_id=None,
                    tenant_id="test-tenant-123",
                    tenant=mock_tenant,
                    protocol="mcp",
                )
                response = _get_adcp_capabilities_impl(None, identity)

                # Verify full response structure
                assert response.adcp is not None
                assert response.adcp.major_versions[0].root == 3
                assert SupportedProtocol.media_buy in response.supported_protocols
                # As in the minimal-path test, the honesty VALUES below are an _impl
                # self-consistency check; the AUTHORITATIVE wire grade is
                # assert_declared_capabilities across a2a/mcp/rest (#1329).
                # Full response declares idempotency consistently with the minimal path:
                # supported=False (agent-wide) — see the honesty rationale above (#1329).
                assert response.adcp.idempotency.supported is False
                # No specialism declared (#1329) — consistent across minimal and full paths.
                assert response.specialisms == []
                # #1329 gap 13: sandbox declared False at the wire (honesty), consistent
                # across minimal and full paths.
                assert response.account is not None
                assert response.account.sandbox is False
                assert response.account.supported_billing

                # Should have media_buy capabilities with portfolio
                assert response.media_buy is not None
                assert response.media_buy.portfolio is not None
                assert response.media_buy.portfolio.description == "Advertising inventory from Test Publisher"

                # Should have features
                assert response.media_buy.features is not None
                assert response.media_buy.features.inline_creative_management is True

                # Honesty assertions: capabilities the seller can't actually fulfill
                # MUST declare False so buyers see the gap at discovery time, not at
                # task-dispatch time. property_list_filtering: no adapter compiles it
                # yet — flips True via supports_property_list_filtering().
                # catalog_management: no sync_catalogs tool ships in this codebase;
                # admin product CRUD is NOT the spec's buyer-driven catalog sync.
                assert response.media_buy.features.property_list_filtering is False
                assert response.media_buy.features.catalog_management is False

                # Should have execution with targeting
                assert response.media_buy.execution is not None
                assert response.media_buy.execution.targeting is not None
        finally:
            # Reset tenant context
            current_tenant.set(None)

    def test_impl_includes_targeting_from_adapter(self):
        """Test that targeting capabilities come from adapter."""
        from src.adapters.base import TargetingCapabilities
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_tenant = {
            "tenant_id": "test-tenant-456",
            "name": "GAM Publisher",
            "subdomain": "gampub",
        }
        current_tenant.set(mock_tenant)

        try:
            # Create mock adapter with targeting capabilities
            mock_adapter = MagicMock()
            mock_adapter.default_channels = ["display", "video"]
            mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
                geo_countries=True,
                geo_regions=True,
                nielsen_dma=True,
                us_zip=True,
            )

            mock_repo = MagicMock()
            mock_repo.list_publisher_partners.return_value = []
            mock_uow = MagicMock()
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            mock_uow.tenant_config = mock_repo

            with patch("src.core.tools.capabilities.TenantConfigUoW", return_value=mock_uow):
                from tests.factories import PrincipalFactory

                identity = PrincipalFactory.make_identity(
                    principal_id="principal-123",
                    tenant_id="test-tenant-456",
                    tenant=mock_tenant,
                    protocol="mcp",
                )

                with patch("src.core.tools.capabilities.get_principal_object") as mock_principal:
                    mock_principal.return_value = MagicMock()

                    with patch("src.core.tools.capabilities.get_adapter") as mock_get_adapter:
                        mock_get_adapter.return_value = mock_adapter

                        response = _get_adcp_capabilities_impl(None, identity)

                        # Verify targeting from adapter
                        assert response.media_buy is not None
                        assert response.media_buy.execution is not None
                        targeting = response.media_buy.execution.targeting
                        assert targeting is not None
                        assert targeting.geo_countries is True
                        assert targeting.geo_regions is True

                        # Should have geo_metros with nielsen_dma
                        assert targeting.geo_metros is not None
                        assert targeting.geo_metros.nielsen_dma is True

                        # Should have geo_postal_areas with us_zip
                        assert targeting.geo_postal_areas is not None
                        assert targeting.geo_postal_areas.us_zip is True
        finally:
            current_tenant.set(None)


class TestGetAdcpCapabilitiesA2AIntegration:
    """Test A2A integration for get_adcp_capabilities."""

    def test_skill_in_discovery_skills(self):
        """Test that get_adcp_capabilities is in DISCOVERY_SKILLS."""
        from src.a2a_server.adcp_a2a_server import DISCOVERY_SKILLS

        assert "get_adcp_capabilities" in DISCOVERY_SKILLS

    def test_skill_handler_exists(self):
        """Test that the skill handler method exists."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        assert hasattr(handler, "_handle_get_adcp_capabilities_skill")
        assert callable(handler._handle_get_adcp_capabilities_skill)


# ===========================================================================
# Channel mapping and adapter integration tests
# Reference: beads
# ===========================================================================


def _make_capabilities_identity(
    principal_id: str | None = "principal-123",
    tenant_id: str = "test-tenant",
    tenant: dict | None = None,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity for capabilities tests."""
    from tests.factories import PrincipalFactory

    if tenant is None:
        tenant = {"tenant_id": tenant_id, "name": "Test Publisher", "subdomain": "testpub"}
    return PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
    )


def _patch_capabilities_deps(
    adapter=None,
    db_partners=None,
):
    """Return a context manager stack patching common capabilities dependencies.

    Args:
        adapter: Mock adapter to return from get_adapter (None = no adapter).
        db_partners: List of mock PublisherPartner objects from DB query.
    """
    from contextlib import ExitStack

    stack = ExitStack()

    # Mock TenantConfigUoW — the repository pattern replacement for get_db_session
    mock_repo = MagicMock()
    mock_repo.list_publisher_partners.return_value = db_partners or []
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.tenant_config = mock_repo
    stack.enter_context(patch("src.core.tools.capabilities.TenantConfigUoW", return_value=mock_uow))

    # Mock log_tool_activity (no-op)
    stack.enter_context(patch("src.core.tools.capabilities.log_tool_activity"))

    # Mock get_principal_object
    if adapter is not None:
        stack.enter_context(patch("src.core.tools.capabilities.get_principal_object", return_value=MagicMock()))
        stack.enter_context(patch("src.core.tools.capabilities.get_adapter", return_value=adapter))
    else:
        stack.enter_context(patch("src.core.tools.capabilities.get_principal_object", return_value=None))

    return stack


class TestChannelMapping:
    """Test CHANNEL_MAPPING integration in _get_adcp_capabilities_impl."""

    def test_channel_aliases_video_maps_to_olv(self):
        """Video channel alias maps to MediaChannel.olv in response."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["video"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio is not None
        assert MediaChannel.olv in response.media_buy.portfolio.primary_channels

    def test_channel_aliases_audio_maps_to_streaming_audio(self):
        """Audio channel alias maps to MediaChannel.streaming_audio in response."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["audio"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert MediaChannel.streaming_audio in response.media_buy.portfolio.primary_channels

    def test_unknown_channel_names_gracefully_ignored(self):
        """Unknown channel names are silently ignored (not in CHANNEL_MAPPING)."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["unknown_channel", "display"]
        mock_adapter.get_targeting_capabilities.return_value = None

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        channels = response.media_buy.portfolio.primary_channels
        assert MediaChannel.display in channels
        # Unknown channel is silently skipped
        assert len(channels) == 1

    def test_no_adapter_channels_defaults_to_display(self):
        """When adapter has no default_channels, defaults to display."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Adapter without default_channels attribute
        mock_adapter = MagicMock(spec=[])
        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert MediaChannel.display in response.media_buy.portfolio.primary_channels


class TestGracefulDegradation:
    """Test graceful degradation when adapter or DB raises exceptions."""

    def test_adapter_exception_falls_back_to_display(self):
        """Adapter exception during channel detection falls back to display channel."""
        from adcp.types.generated_poc.enums.channels import MediaChannel

        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity()

        mock_repo = MagicMock()
        mock_repo.list_publisher_partners.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.tenant_config = mock_repo

        with (
            patch("src.core.tools.capabilities.TenantConfigUoW", return_value=mock_uow),
            patch("src.core.tools.capabilities.log_tool_activity"),
            patch("src.core.tools.capabilities.get_principal_object", return_value=MagicMock()),
            patch("src.core.tools.capabilities.get_adapter", side_effect=Exception("Adapter init failed")),
        ):
            response = _get_adcp_capabilities_impl(None, identity)

        # Should still succeed with display as default
        assert response.media_buy is not None
        assert MediaChannel.display in response.media_buy.portfolio.primary_channels

    def test_db_exception_uses_placeholder_domain(self):
        """Database exception during publisher domain query uses placeholder domain."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(
            tenant={"tenant_id": "t1", "name": "Test", "subdomain": "testpub"},
        )

        with (
            patch("src.core.tools.capabilities.TenantConfigUoW", side_effect=Exception("DB down")),
            patch("src.core.tools.capabilities.log_tool_activity"),
            patch("src.core.tools.capabilities.get_principal_object", return_value=None),
        ):
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        domains = response.media_buy.portfolio.publisher_domains
        assert len(domains) == 1
        assert "testpub.example.com" in domains[0].root


class TestAdvertisingPolicies:
    """Test advertising policy extraction from tenant config."""

    def test_advertising_policy_description_extracted(self):
        """Advertising policy description is extracted from tenant config."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        tenant = {
            "tenant_id": "t1",
            "name": "Policy Pub",
            "subdomain": "policypub",
            "advertising_policy": {"description": "No adult content allowed"},
        }
        identity = _make_capabilities_identity(principal_id=None, tenant=tenant)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio.advertising_policies == "No adult content allowed"

    def test_no_advertising_policy_returns_none(self):
        """When tenant has no advertising_policy, portfolio.advertising_policies is None."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        tenant = {"tenant_id": "t1", "name": "No Policy Pub", "subdomain": "nopolicy"}
        identity = _make_capabilities_identity(principal_id=None, tenant=tenant)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        assert response.media_buy.portfolio.advertising_policies is None


class TestPublisherDomains:
    """Test publisher domain extraction from database."""

    def test_publisher_domains_from_database(self):
        """Publisher domains are read from PublisherPartner records in DB."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        # Create mock partner records
        partner1 = MagicMock()
        partner1.publisher_domain = "example.com"
        partner2 = MagicMock()
        partner2.publisher_domain = "news.org"

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps(db_partners=[partner1, partner2])

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy is not None
        domains = [d.root for d in response.media_buy.portfolio.publisher_domains]
        assert "example.com" in domains
        assert "news.org" in domains

    def test_partner_without_domain_skipped(self):
        """Partners with publisher_domain=None are skipped."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        partner_with = MagicMock()
        partner_with.publisher_domain = "real.com"
        partner_without = MagicMock()
        partner_without.publisher_domain = None

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps(db_partners=[partner_with, partner_without])

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        domains = [d.root for d in response.media_buy.portfolio.publisher_domains]
        assert "real.com" in domains
        assert len(domains) == 1


class TestResponseShapeCapabilities:
    """Test response structure and serialization for get_adcp_capabilities."""

    def test_last_updated_present_with_tenant(self):
        """Response includes last_updated when tenant context is available."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.last_updated is not None

    def test_last_updated_absent_without_tenant(self):
        """Response has no last_updated when no tenant context (minimal response)."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        response = _get_adcp_capabilities_impl(None, None)
        assert response.last_updated is None

    def test_features_defaults_with_tenant(self):
        """Features defaults: inline_creative_management=True, property_list_filtering=False.

        property_list_filtering is False until an adapter actually compiles
        `targeting_overlay.property_list` into native ad-server targeting.
        """
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        features = response.media_buy.features
        assert features.inline_creative_management is True
        assert features.property_list_filtering is False

    def test_full_response_serialization_shape(self):
        """Full response model_dump(mode='json') has expected keys."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = _make_capabilities_identity(principal_id=None)
        stack = _patch_capabilities_deps()

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        data = response.model_dump(mode="json")
        assert "adcp" in data
        assert "supported_protocols" in data
        assert "media_buy" in data
        assert data["supported_protocols"] == ["media_buy"]
        assert "portfolio" in data["media_buy"]
        assert "features" in data["media_buy"]
        assert "execution" in data["media_buy"]

    def test_minimal_response_no_media_buy(self):
        """Minimal response (no tenant) omits media_buy from serialized output."""
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        response = _get_adcp_capabilities_impl(None, None)
        assert response.media_buy is None
        data = response.model_dump(mode="json")
        # media_buy is excluded from serialization when None
        assert "media_buy" not in data


class TestGeoPostalAreas:
    """Test geo_postal_areas building from targeting capabilities."""

    def test_geo_postal_areas_built_from_adapter(self):
        """geo_postal_areas are populated from adapter targeting capabilities."""
        from src.adapters.base import TargetingCapabilities
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["display"]
        mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            us_zip=True,
            ca_fsa=True,
            gb_outward=True,
        )

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        postal = response.media_buy.execution.targeting.geo_postal_areas
        assert postal is not None
        assert postal.us_zip is True
        assert postal.ca_fsa is True
        assert postal.gb_outward is True
        # Fields not set should be None
        assert postal.de_plz is None
        assert postal.fr_code_postal is None

    def test_no_postal_targeting_means_none(self):
        """When no postal targeting capabilities, geo_postal_areas is None."""
        from src.adapters.base import TargetingCapabilities
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        mock_adapter = MagicMock()
        mock_adapter.default_channels = ["display"]
        mock_adapter.get_targeting_capabilities.return_value = TargetingCapabilities(
            geo_countries=True,
            geo_regions=True,
            # No postal targeting set
        )

        identity = _make_capabilities_identity()
        stack = _patch_capabilities_deps(adapter=mock_adapter)

        with stack:
            response = _get_adcp_capabilities_impl(None, identity)

        assert response.media_buy.execution.targeting.geo_postal_areas is None


class TestSupportedBillingParity:
    """`SELLER_ACCOUNT_BILLING` must stay in lockstep with the DB constraint.

    The advertised default is a hand-maintained subset of BillingParty that mirrors
    the `ck_accounts_billing` CHECK constraint (accounts.billing IN {operator,
    agent}). If the two drift, the seller advertises a billing party its own column
    rejects. Pin them together (#1329).
    """

    def test_default_billing_equals_db_constraint_allowed_set(self):
        import re

        from adcp.types.generated_poc.enums.billing_party import BillingParty

        from src.core.database.models import Account
        from src.core.helpers.account_helpers import SELLER_ACCOUNT_BILLING

        # Every advertised value is a real BillingParty enum member.
        assert all(isinstance(p, BillingParty) for p in SELLER_ACCOUNT_BILLING)

        constraint = next(c for c in Account.__table_args__ if getattr(c, "name", None) == "ck_accounts_billing")
        permitted = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
        advertised = {p.value for p in SELLER_ACCOUNT_BILLING}

        assert advertised == permitted == {"operator", "agent"}
        # `advertiser` is deliberately excluded — this seller has no direct
        # advertiser-billing relationship (see ck_accounts_billing rationale).
        assert "advertiser" not in advertised

    def test_configured_billing_narrows_within_permitted_set(self):
        """A tenant may narrow within {operator, agent}; a non-account party is dropped.

        Same resolver as sync_accounts, so what is advertised equals what is accepted
        (#1329).
        """
        from src.core.tools.capabilities import _build_account_capability

        # A permitted subset is honored (narrowing within {operator, agent}).
        narrowed = _build_account_capability({"supported_billing": ["operator"]})
        assert [p.value for p in narrowed.supported_billing] == ["operator"]

        # A mixed list keeps only the account-billable parties (advertiser dropped — it
        # is a media-buy party, not account-billable — never advertised on the account).
        mixed = _build_account_capability({"supported_billing": ["operator", "advertiser"]})
        assert [p.value for p in mixed.supported_billing] == ["operator"]

    def test_configured_billing_with_no_account_party_raises(self):
        """A config declaring no account-billable party fails LOUD and TERMINAL.

        `["advertiser"]` (media-buy-only), `["bogus"]` (typo), AND `[]` (not spec-
        expressible at the pin, account.supported_billing minItems:1) all resolve to an
        empty account-billable set. This is a SELLER misconfiguration the buyer cannot fix,
        so it raises a TERMINAL CONFIGURATION_ERROR on the capabilities wire — the SAME
        resolver sync_accounts uses — with a buyer-safe message that discloses neither the
        tenant config nor the internal constraint name (#1329/C2).
        """
        import pytest

        from src.core.exceptions import AdCPConfigurationError
        from src.core.tools.capabilities import _build_account_capability

        for configured in (["advertiser"], ["bogus"], []):
            with pytest.raises(AdCPConfigurationError) as exc_info:
                _build_account_capability({"supported_billing": configured})
            assert exc_info.value.recovery == "terminal"
            assert "ck_accounts_billing" not in str(exc_info.value)
