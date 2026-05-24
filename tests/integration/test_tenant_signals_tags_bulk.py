"""Integration tests for tag projection + bulk-update / bulk-delete helpers.

Covers PR 2 additions (#477 + #478 + #481 #6):

- ``TenantSignal.tags`` round-trips through the column
- ``_tenant_signal_to_adcp`` includes tags when non-empty
- ``_normalize_tags`` validation behavior
- ``_gam_admin_url`` deep-link helper
- ``_apply_bulk_update``: add_tag / remove_tag / rename_prefix / rename_suffix
- ``_apply_bulk_delete``: reference-safety gate
"""

from __future__ import annotations

import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SignalTagsEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestNormalizeTags:
    """Unit-level behavior of ``_normalize_tags`` — pure function."""

    def test_accepts_comma_separated_string(self):
        from src.admin.blueprints.tenant_signals import _normalize_tags

        assert _normalize_tags("premium, sports, q4_holiday") == ["premium", "q4_holiday", "sports"]

    def test_lowercases_and_deduplicates(self):
        from src.admin.blueprints.tenant_signals import _normalize_tags

        assert _normalize_tags("Premium, PREMIUM, sports") == ["premium", "sports"]

    def test_accepts_list_input(self):
        from src.admin.blueprints.tenant_signals import _normalize_tags

        assert _normalize_tags(["premium", "sports"]) == ["premium", "sports"]

    def test_empty_string_returns_empty_list(self):
        from src.admin.blueprints.tenant_signals import _normalize_tags

        assert _normalize_tags("") == []
        assert _normalize_tags(None) == []

    def test_invalid_tag_raises(self):
        from src.admin.blueprints.tenant_signals import _normalize_tags

        with pytest.raises(ValueError, match="invalid tag"):
            _normalize_tags("symbols!")
        with pytest.raises(ValueError, match="invalid tag"):
            _normalize_tags("has.dots")


class TestGamAdminUrl:
    """Deep-link helper for the Maps-to panel (#481 #6)."""

    def test_audience_segment_url(self):
        from src.admin.blueprints.tenant_signals import _gam_admin_url

        url = _gam_admin_url("123456", "audience_segment", "98765")
        assert url == "https://admanager.google.com/123456#delivery/audience-segments/detail/audience_segment_id=98765"

    def test_custom_targeting_key_url(self):
        from src.admin.blueprints.tenant_signals import _gam_admin_url

        url = _gam_admin_url("123456", "custom_targeting_key", "11111")
        assert url == "https://admanager.google.com/123456#inventory/custom-targeting/detail/key_id=11111"

    def test_missing_network_code_returns_none(self):
        from src.admin.blueprints.tenant_signals import _gam_admin_url

        assert _gam_admin_url(None, "audience_segment", "98765") is None
        assert _gam_admin_url("", "audience_segment", "98765") is None

    def test_unknown_kind_returns_none(self):
        from src.admin.blueprints.tenant_signals import _gam_admin_url

        assert _gam_admin_url("123456", "freewheel_audience_item", "x") is None


class TestTagsColumn:
    """``TenantSignal.tags`` is a real JSONB column that persists list[str]."""

    def test_tags_round_trip_through_column(self, integration_db):
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="tag_t1")
            TenantSignalFactory(
                tenant=tenant,
                signal_id="sports",
                tags=["premium", "sports"],
            )
            session = env.get_session()
            signal = TenantSignalRepository(session, "tag_t1").get_by_id("sports")
        assert signal is not None
        assert signal.tags == ["premium", "sports"]

    def test_tags_default_to_empty_list(self, integration_db):
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="tag_t2")
            TenantSignalFactory(tenant=tenant, signal_id="no_tags")
            session = env.get_session()
            signal = TenantSignalRepository(session, "tag_t2").get_by_id("no_tags")
        assert signal is not None
        assert signal.tags == []

    def test_legacy_dotted_signal_id_resolves_by_wire_safe_id(self, integration_db):
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="tag_t3")
            TenantSignalFactory(tenant=tenant, signal_id="audience.sports_fans")
            session = env.get_session()
            repo = TenantSignalRepository(session, "tag_t3")

            signal = repo.get_by_id("audience_sports_fans")
            signals = repo.list_by_ids(["audience_sports_fans"])

        assert signal is not None
        assert signal.signal_id == "audience.sports_fans"
        assert [row.signal_id for row in signals] == ["audience.sports_fans"]


class TestAdcpProjectionIncludesTags:
    """``_tenant_signal_to_adcp`` projects tags to the wire ``Signal``."""

    def test_tags_appear_in_wire_dump_when_set(self, integration_db):
        from src.core.tools.signals import _tenant_signal_to_adcp
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv():
            tenant = TenantFactory(tenant_id="adcp_tag_t1", ad_server="google_ad_manager")
            signal = TenantSignalFactory(
                tenant=tenant,
                signal_id="sports",
                tags=["premium", "sports"],
            )
            wire = _tenant_signal_to_adcp(
                signal,
                ad_server=tenant.ad_server,
                agent_url=tenant.public_agent_url,
            ).model_dump(mode="json")
        assert wire["tags"] == ["premium", "sports"]

    def test_tags_omitted_when_empty(self, integration_db):
        from src.core.tools.signals import _tenant_signal_to_adcp
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv():
            tenant = TenantFactory(tenant_id="adcp_tag_t2", ad_server="google_ad_manager")
            signal = TenantSignalFactory(tenant=tenant, signal_id="no_tags", tags=[])
            wire = _tenant_signal_to_adcp(
                signal,
                ad_server=tenant.ad_server,
                agent_url=tenant.public_agent_url,
            ).model_dump(mode="json")
        # Either omitted entirely or rendered as None — both signal "no tags".
        assert wire.get("tags") in (None, [])

    def test_legacy_dotted_signal_id_projects_wire_safe_id(self, integration_db):
        from src.core.tools.signals import _tenant_signal_to_adcp
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv():
            tenant = TenantFactory(tenant_id="adcp_tag_t3", ad_server="google_ad_manager")
            signal = TenantSignalFactory(tenant=tenant, signal_id="audience.sports_fans", tags=[])
            wire = _tenant_signal_to_adcp(
                signal,
                ad_server=tenant.ad_server,
                agent_url=tenant.public_agent_url,
            ).model_dump(mode="json")

        assert wire["signal_id"]["id"] == "audience_sports_fans"
        assert wire["signal_agent_segment_id"] == "audience_sports_fans"


class TestApplyBulkUpdate:
    """``_apply_bulk_update`` mutates signals through the repository."""

    def test_add_tag_appends_to_existing(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_tag_t1")
            TenantSignalFactory(tenant=tenant, signal_id="a", tags=["existing"])
            TenantSignalFactory(tenant=tenant, signal_id="b", tags=[])
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, skipped = _apply_bulk_update(repo, ["a", "b"], "add_tag", "premium")
            session.flush()

            assert sorted(updated) == ["a", "b"]
            assert skipped == []
            assert sorted(repo.get_by_id("a").tags) == ["existing", "premium"]
            assert repo.get_by_id("b").tags == ["premium"]

    def test_add_tag_skips_already_tagged(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_tag_t2")
            TenantSignalFactory(tenant=tenant, signal_id="a", tags=["premium"])
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, skipped = _apply_bulk_update(repo, ["a"], "add_tag", "premium")

            assert updated == []
            assert skipped == ["a"]

    def test_remove_tag_drops_from_list(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_tag_t3")
            TenantSignalFactory(tenant=tenant, signal_id="a", tags=["premium", "sports"])
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, skipped = _apply_bulk_update(repo, ["a"], "remove_tag", "premium")
            session.flush()

            assert updated == ["a"]
            assert skipped == []
            assert repo.get_by_id("a").tags == ["sports"]

    def test_remove_tag_skips_when_absent(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_tag_t4")
            TenantSignalFactory(tenant=tenant, signal_id="a", tags=["sports"])
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, skipped = _apply_bulk_update(repo, ["a"], "remove_tag", "premium")

            assert updated == []
            assert skipped == ["a"]

    def test_rename_prefix_prepends(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_rename_t1")
            TenantSignalFactory(tenant=tenant, signal_id="s1", name="Sports Fans")
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, _ = _apply_bulk_update(repo, ["s1"], "rename_prefix", "Premium ")
            session.flush()

            assert updated == ["s1"]
            assert repo.get_by_id("s1").name == "Premium Sports Fans"

    def test_rename_suffix_appends(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_rename_t2")
            TenantSignalFactory(tenant=tenant, signal_id="s1", name="Sports Fans")
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, _ = _apply_bulk_update(repo, ["s1"], "rename_suffix", " (Q4)")
            session.flush()

            assert updated == ["s1"]
            assert repo.get_by_id("s1").name == "Sports Fans (Q4)"

    def test_rename_prefix_skips_when_already_present(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_update
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_rename_t3")
            TenantSignalFactory(tenant=tenant, signal_id="s1", name="Premium Sports Fans")
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            updated, skipped = _apply_bulk_update(repo, ["s1"], "rename_prefix", "Premium ")

            assert updated == []
            assert skipped == ["s1"]


class TestApplyBulkDelete:
    """``_apply_bulk_delete`` enforces the reference-safety gate."""

    def test_deletes_unreferenced_signals(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_delete
        from src.core.database.repositories.signal_usage import SignalUsageRepository
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_del_t1")
            TenantSignalFactory(tenant=tenant, signal_id="a")
            TenantSignalFactory(tenant=tenant, signal_id="b")
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            usage_repo = SignalUsageRepository(session, tenant.tenant_id)
            deleted, not_found, blocked = _apply_bulk_delete(repo, usage_repo, ["a", "b"], "")
            session.flush()

            assert sorted(deleted) == ["a", "b"]
            assert not_found == []
            assert blocked == []
            assert repo.get_by_id("a") is None
            assert repo.get_by_id("b") is None

    def test_blocks_referenced_signals_without_confirm(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_delete
        from src.core.database.repositories.signal_usage import SignalUsageRepository
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_del_t2")
            TenantSignalFactory(tenant=tenant, signal_id="referenced")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb1",
                status="active",
                raw_request={
                    "packages": [
                        {
                            "package_id": "p1",
                            "product_id": "prod_001",
                            "targeting_overlay": {"audience_include": ["referenced"]},
                        }
                    ]
                },
            )
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            usage_repo = SignalUsageRepository(session, tenant.tenant_id)
            deleted, _, blocked = _apply_bulk_delete(repo, usage_repo, ["referenced"], "")

            assert deleted == []
            assert blocked == ["referenced"]
            assert repo.get_by_id("referenced") is not None

    def test_allows_referenced_delete_with_typed_confirm(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_delete
        from src.core.database.repositories.signal_usage import SignalUsageRepository
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_del_t3")
            TenantSignalFactory(tenant=tenant, signal_id="referenced")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb2",
                status="active",
                raw_request={
                    "packages": [
                        {
                            "package_id": "p1",
                            "product_id": "prod_001",
                            "targeting_overlay": {"audience_include": ["referenced"]},
                        }
                    ]
                },
            )
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            usage_repo = SignalUsageRepository(session, tenant.tenant_id)
            deleted, _, blocked = _apply_bulk_delete(repo, usage_repo, ["referenced"], "DELETE")
            session.flush()

            assert deleted == ["referenced"]
            assert blocked == []
            assert repo.get_by_id("referenced") is None

    def test_unknown_signal_id_reported_as_not_found(self, integration_db):
        from src.admin.blueprints.tenant_signals import _apply_bulk_delete
        from src.core.database.repositories.signal_usage import SignalUsageRepository
        from src.core.database.repositories.tenant_signal import TenantSignalRepository
        from tests.factories import TenantFactory, TenantSignalFactory

        with _SignalTagsEnv() as env:
            tenant = TenantFactory(tenant_id="bulk_del_t4")
            TenantSignalFactory(tenant=tenant, signal_id="exists")
            session = env.get_session()
            repo = TenantSignalRepository(session, tenant.tenant_id)
            usage_repo = SignalUsageRepository(session, tenant.tenant_id)
            deleted, not_found, _ = _apply_bulk_delete(repo, usage_repo, ["exists", "ghost"], "")

            assert deleted == ["exists"]
            assert not_found == ["ghost"]
