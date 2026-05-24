"""End-to-end signal flow: operator authoring → get_signals discovery → GAM resolution.

Walks the full vertical added in the composition branch:

  1. Operator declares a ``TenantSignal`` (audience-segment kind).
  2. Storefront calls AdCP ``get_signals`` against the agent and sees the
     signal projected onto the AdCP ``Signal`` wire shape with
     ``adapter_config`` elided.
  3. Storefront passes the ``signal_id`` in
     ``TargetingOverlay.audience_include`` on ``create_media_buy``.
  4. GAM targeting manager resolves it into a line-item
     ``audienceTargeting.includedAudienceSegmentIds`` block.

Plus the parallel ``custom_key_value`` kind that lands in the shared
``custom_targeting`` accumulator, and the failure mode (unknown signal_id
raises a typed error).
"""

from __future__ import annotations

import asyncio

import pytest
from adcp.types.generated_poc.core.targeting import TargetingOverlay

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SignalFlowEnv(IntegrationEnv):
    """Bare integration env — signals flow doesn't need external mocks."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestTenantSignalsDiscovery:
    """get_signals merges operator-declared TenantSignal rows alongside the
    hardcoded sample signals, projected onto the AdCP ``Signal`` shape with
    ``adapter_config`` elided.
    """

    def test_audience_signal_appears_in_get_signals(self, integration_db):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import GetSignalsRequest
        from src.core.tools.signals import _get_signals_impl
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(
                tenant_id="sig_disc_t1",
                ad_server="google_ad_manager",
                public_agent_url="https://disc.example.com/agent",
            )
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                name="Sports Fans",
                value_type="binary",
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
                targeting_dimension="audience",
                data_provider="publisher_1p",
            )

            identity = ResolvedIdentity(
                tenant_id="sig_disc_t1",
                principal_id="buyer_1",
                tenant={
                    "ad_server": "google_ad_manager",
                    "public_agent_url": "https://disc.example.com/agent",
                },
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=identity))

        target = [s for s in response.signals if s.signal_agent_segment_id == "audience_sports_fans"]
        assert len(target) == 1, "operator-declared signal should appear in get_signals response"
        wire = target[0].model_dump(mode="json")
        assert wire["value_type"] == "binary"
        assert wire["data_provider"] == "publisher_1p"
        # adapter_config is operator-only — must never appear on the wire.
        assert "adapter_config" not in wire

    def test_numeric_range_signal_carries_range(self, integration_db):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import GetSignalsRequest
        from src.core.tools.signals import _get_signals_impl
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="sig_disc_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="weather_temp_f",
                name="Temperature",
                value_type="numeric",
                range_min=-40,
                range_max=120,
                adapter_config={"kind": "custom_key_value", "key_id": "11111"},
                targeting_dimension="weather",
            )

            identity = ResolvedIdentity(
                tenant_id="sig_disc_t2",
                principal_id="buyer_1",
                tenant={"ad_server": "google_ad_manager"},
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(_get_signals_impl(GetSignalsRequest(), identity=identity))

        target = next(s for s in response.signals if s.signal_agent_segment_id == "weather_temp_f")
        wire = target.model_dump(mode="json")
        assert wire["value_type"] == "numeric"
        assert wire["range"] == {"min": -40.0, "max": 120.0}


def _gam_tm(tenant_id: str):
    """Shared GAMTargetingManager builder — no external GAM, no AXE keys."""
    from src.adapters.gam.managers.targeting import GAMTargetingManager

    return GAMTargetingManager(
        tenant_id=tenant_id,
        gam_client=None,
        targeting_config={
            "custom_targeting_keys": {},
            "axe_include_key": None,
            "axe_exclude_key": None,
            "axe_macro_key": None,
        },
    )


class TestGamPassthroughSignals:
    """Pass-through signals (one signal = one adapter primitive).

    Includes legacy ``{kind, ...}`` rows without explicit ``type`` for
    backward compatibility.
    """

    def test_audience_segment_passthrough_legacy_shape(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_pt_t1", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                # Legacy shape: no ``type`` field, just kind + segment_id.
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            )
            env.get_session()
            audience_block = _gam_tm("gam_pt_t1")._resolve_audience_signals(
                TargetingOverlay(audience_include=["audience_sports_fans"]),
                {},
            )
        assert audience_block == {"includedAudienceSegmentIds": ["98765"]}

    def test_audience_segment_passthrough_explicit_type(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_pt_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                adapter_config={
                    "type": "passthrough",
                    "kind": "audience_segment",
                    "segment_id": "98765",
                },
            )
            env.get_session()
            audience_block = _gam_tm("gam_pt_t2")._resolve_audience_signals(
                TargetingOverlay(audience_include=["audience_sports_fans"]),
                {},
            )
        assert audience_block == {"includedAudienceSegmentIds": ["98765"]}

    def test_audience_segment_excluded_at_buyer_level(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_pt_t3", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_competitors",
                adapter_config={"kind": "audience_segment", "segment_id": "55555"},
            )
            env.get_session()
            audience_block = _gam_tm("gam_pt_t3")._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["audience_competitors"]),
                {},
            )
        assert audience_block == {"excludedAudienceSegmentIds": ["55555"]}

    def test_custom_key_value_passthrough_layers_into_custom_targeting(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_pt_t4", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="kv_vertical_news",
                adapter_config={
                    "kind": "custom_key_value",
                    "key_id": "11111",
                    "value_id": "22222",
                },
            )
            env.get_session()
            tm = _gam_tm("gam_pt_t4")

            ct_include: dict[str, str] = {}
            audience_block = tm._resolve_audience_signals(
                TargetingOverlay(audience_include=["kv_vertical_news"]),
                ct_include,
            )
            assert audience_block is None
            assert ct_include == {"11111": "22222"}

            ct_exclude: dict[str, str] = {}
            tm._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["kv_vertical_news"]),
                ct_exclude,
            )
            assert ct_exclude == {"NOT_11111": "22222"}


class TestGamTargetingGroupsSignals:
    """gam_targeting_groups signals carry the TargetingWidget groups
    payload verbatim. They flow through the per-signal accumulator as a
    single ``{"groups": [...]}`` value that the downstream GAM
    customTargeting builder already understands."""

    _GROUPS = [
        {
            "criteria": [
                {"keyId": "11111", "values": ["22222", "33333"]},
                {"keyId": "44444", "values": ["55555"], "exclude": True},
            ]
        },
        {"criteria": [{"keyId": "66666", "values": ["77777"]}]},
    ]

    def test_groups_signal_alone_populates_accumulator(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_tg_t1", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="complex_premium",
                adapter_config={
                    "type": "passthrough",
                    "kind": "gam_targeting_groups",
                    "groups": self._GROUPS,
                },
            )
            env.get_session()
            custom_targeting: dict = {}
            _gam_tm("gam_tg_t1")._resolve_audience_signals(
                TargetingOverlay(audience_include=["complex_premium"]),
                custom_targeting,
            )
        assert "groups" in custom_targeting
        assert custom_targeting["groups"] == self._GROUPS

    def test_groups_signal_in_exclude_flips_exclude_flags(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_tg_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="complex_premium",
                adapter_config={
                    "type": "passthrough",
                    "kind": "gam_targeting_groups",
                    "groups": self._GROUPS,
                },
            )
            env.get_session()
            custom_targeting: dict = {}
            _gam_tm("gam_tg_t2")._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["complex_premium"]),
                custom_targeting,
            )
        flipped = custom_targeting["groups"]
        # First criterion of group 0 had no exclude → now exclude=True.
        assert flipped[0]["criteria"][0]["exclude"] is True
        # Second criterion of group 0 had exclude=True → now exclude=False.
        assert flipped[0]["criteria"][1]["exclude"] is False
        # Group 1 criterion 0 had no exclude → now exclude=True.
        assert flipped[1]["criteria"][0]["exclude"] is True

    def test_groups_signal_rejects_mixing_with_custom_kv_signal(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_tg_t3", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="complex_premium",
                adapter_config={
                    "type": "passthrough",
                    "kind": "gam_targeting_groups",
                    "groups": self._GROUPS,
                },
            )
            TenantSignalFactory(
                tenant=tenant,
                signal_id="simple_section",
                adapter_config={
                    "type": "passthrough",
                    "kind": "custom_key_value",
                    "key_id": "99999",
                    "value_id": "88888",
                },
            )
            env.get_session()
            with pytest.raises(ValueError, match="exclusive|combine"):
                _gam_tm("gam_tg_t3")._resolve_audience_signals(
                    TargetingOverlay(audience_include=["complex_premium", "simple_section"]),
                    {},
                )

    def test_groups_signal_rejects_mixing_with_audience_segment_signal(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_tg_t4", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="complex_premium",
                adapter_config={
                    "type": "passthrough",
                    "kind": "gam_targeting_groups",
                    "groups": self._GROUPS,
                },
            )
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports",
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            )
            env.get_session()
            with pytest.raises(ValueError, match="exclusive"):
                _gam_tm("gam_tg_t4")._resolve_audience_signals(
                    # Order matters: audience first populates segment_include,
                    # then groups signal sees it and rejects.
                    TargetingOverlay(audience_include=["audience_sports", "complex_premium"]),
                    {},
                )


class TestGamComposedSignals:
    """Composed signals (AND of criteria, mixed kinds, per-criterion mode).

    Operator pre-bundles common combinations into a single signal id so the
    storefront/buyer references one thing.
    """

    def test_composed_mixes_kv_and_segment_criteria(self, integration_db):
        """``premium_sports = vertical=sports AND audience_segment=12345``"""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_co_t1", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="premium_sports",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {
                            "kind": "custom_key_value",
                            "key_id": "11111",
                            "value_id": "22222",
                            "mode": "include",
                        },
                        {"kind": "audience_segment", "segment_id": "12345", "mode": "include"},
                    ],
                },
            )
            env.get_session()
            ct: dict[str, str] = {}
            audience_block = _gam_tm("gam_co_t1")._resolve_audience_signals(
                TargetingOverlay(audience_include=["premium_sports"]),
                ct,
            )
        assert audience_block == {"includedAudienceSegmentIds": ["12345"]}
        assert ct == {"11111": "22222"}

    def test_composed_per_criterion_exclude_mode(self, integration_db):
        """A criterion can be exclude even when the signal is included."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_co_t2", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="premium_sports_minus_competitors",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {
                            "kind": "audience_segment",
                            "segment_id": "12345",
                            "mode": "include",
                        },
                        {
                            "kind": "audience_segment",
                            "segment_id": "99999",
                            "mode": "exclude",
                        },
                    ],
                },
            )
            env.get_session()
            audience_block = _gam_tm("gam_co_t2")._resolve_audience_signals(
                TargetingOverlay(audience_include=["premium_sports_minus_competitors"]),
                {},
            )
        assert audience_block == {
            "includedAudienceSegmentIds": ["12345"],
            "excludedAudienceSegmentIds": ["99999"],
        }

    def test_composed_signal_in_audience_exclude_inverts_criteria(self, integration_db):
        """Outer ``exclude`` mode XORs with each criterion's mode."""
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_co_t3", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="comp",
                adapter_config={
                    "type": "composed",
                    "criteria": [
                        {
                            "kind": "audience_segment",
                            "segment_id": "include_seg",
                            "mode": "include",
                        },
                        {
                            "kind": "audience_segment",
                            "segment_id": "exclude_seg",
                            "mode": "exclude",
                        },
                    ],
                },
            )
            env.get_session()
            # Signal in audience_exclude → flip each criterion's mode
            audience_block = _gam_tm("gam_co_t3")._resolve_audience_signals(
                TargetingOverlay(audience_exclude=["comp"]),
                {},
            )
        # include_seg → excluded; exclude_seg → included (double negative)
        assert audience_block == {
            "includedAudienceSegmentIds": ["exclude_seg"],
            "excludedAudienceSegmentIds": ["include_seg"],
        }

    def test_composed_validates_missing_segment_id(self, integration_db):
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="gam_co_t4", ad_server="google_ad_manager")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="malformed",
                adapter_config={
                    "type": "composed",
                    "criteria": [{"kind": "audience_segment"}],  # missing segment_id
                },
            )
            env.get_session()
            with pytest.raises(ValueError, match="requires segment_id"):
                _gam_tm("gam_co_t4")._resolve_audience_signals(
                    TargetingOverlay(audience_include=["malformed"]),
                    {},
                )


class TestActivateSignalForTenantSignals:
    """``activate_signal`` returns a stable decisioning_platform_segment_id
    (== signal_id) for operator-declared TenantSignal rows. No synthetic
    UUID drift across calls — repeat activations are idempotent.
    """

    def test_activate_known_tenant_signal_returns_stable_handle(self, integration_db):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.signals import _activate_signal_impl
        from tests.factories import PrincipalFactory, TenantFactory, TenantSignalFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="act_t1", ad_server="google_ad_manager")
            principal = PrincipalFactory(tenant=tenant)
            TenantSignalFactory(
                tenant=tenant,
                signal_id="audience_sports_fans",
                adapter_config={"kind": "audience_segment", "segment_id": "98765"},
            )
            env.get_session()

            identity = ResolvedIdentity(
                tenant_id="act_t1",
                principal_id=principal.principal_id,
                tenant={"ad_server": "google_ad_manager"},
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(
                _activate_signal_impl(
                    signal_agent_segment_id="audience_sports_fans",
                    identity=identity,
                )
            )

        assert response.errors is None
        assert response.activation_details is not None
        # Stable handle: decisioning_platform_segment_id IS the signal_id.
        assert response.activation_details["decisioning_platform_segment_id"] == "audience_sports_fans"
        assert response.activation_details["status"] == "deployed"
        # Publisher first-party signal = zero activation latency.
        assert response.activation_details["estimated_activation_duration_minutes"] == 0.0

    def test_activate_unknown_signal_falls_through_to_mock(self, integration_db):
        """Sample signals from the hardcoded demo set still get the
        mock-activation flow — backward compat with existing buyer demos."""
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.signals import _activate_signal_impl
        from tests.factories import PrincipalFactory, TenantFactory

        with _SignalFlowEnv() as env:
            tenant = TenantFactory(tenant_id="act_t2", ad_server="google_ad_manager")
            principal = PrincipalFactory(tenant=tenant)
            env.get_session()

            identity = ResolvedIdentity(
                tenant_id="act_t2",
                principal_id=principal.principal_id,
                tenant={"ad_server": "google_ad_manager"},
                principal=None,
                testing_context=None,
                auth_method="api_key",
                raw_credential=None,
            )
            response = asyncio.run(
                _activate_signal_impl(
                    signal_agent_segment_id="auto_intenders_q1_2025",
                    identity=identity,
                )
            )

        # Mock path: synthetic decisioning_platform_segment_id, processing status.
        assert response.errors is None
        assert response.activation_details is not None
        assert response.activation_details["status"] == "processing"
        assert response.activation_details["decisioning_platform_segment_id"].startswith("seg_auto_intenders_q1_2025_")


class TestGamSignalErrors:
    """Failure modes — fail loud, never silently drop targeting."""

    def test_unknown_signal_id_raises(self, integration_db):
        from tests.factories import TenantFactory

        with _SignalFlowEnv() as env:
            TenantFactory(tenant_id="gam_err_t1", ad_server="google_ad_manager")
            env.get_session()
            with pytest.raises(ValueError) as exc_info:
                _gam_tm("gam_err_t1")._resolve_audience_signals(
                    TargetingOverlay(audience_include=["nope_unknown"]),
                    {},
                )
        message = str(exc_info.value)
        assert "nope_unknown" in message
        assert "gam_err_t1" in message

    def test_empty_overlay_returns_none(self, integration_db):
        from tests.factories import TenantFactory

        with _SignalFlowEnv() as env:
            TenantFactory(tenant_id="gam_err_t2", ad_server="google_ad_manager")
            env.get_session()
            ct: dict[str, str] = {}
            audience_block = _gam_tm("gam_err_t2")._resolve_audience_signals(TargetingOverlay(), ct)
        assert audience_block is None
        assert ct == {}
