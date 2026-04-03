"""Meta-tests for BaseTestEnv / IntegrationEnv base contracts.

Guards the DRY-01 refactor: merging IntegrationEnv + ImplTestEnv into
a single BaseTestEnv. These tests verify that both integration and unit
modes share the same lifecycle contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestBaseClassContract:
    """BaseTestEnv must work in both integration (use_real_db=True) and unit modes."""

    def test_integration_env_has_mock_dict(self):
        """IntegrationEnv.__enter__ populates self.mock from EXTERNAL_PATCHES."""
        from tests.harness._base import IntegrationEnv

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {
                "some_dep": "os.getcwd",
            }

        env = _TestEnv()
        # Before enter, mock dict is empty
        assert env.mock == {}

        with patch("src.core.database.database_session.get_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            with patch("tests.factories.ALL_FACTORIES", []):
                with env:
                    assert "some_dep" in env.mock
                    assert isinstance(env.mock["some_dep"], MagicMock)

        # After exit, mock dict is cleared
        assert env.mock == {}

    def test_unit_env_has_mock_dict(self):
        """BaseTestEnv.__enter__ populates self.mock from EXTERNAL_PATCHES."""
        from tests.harness._base import BaseTestEnv

        class _TestEnv(BaseTestEnv):
            EXTERNAL_PATCHES = {"some_dep": "os.getcwd"}

        env = _TestEnv()
        assert env.mock == {}

        with env:
            assert "some_dep" in env.mock
            assert isinstance(env.mock["some_dep"], MagicMock)

        assert env.mock == {}

    def test_integration_env_identity_is_lazy(self):
        """Identity is built on first access, not in __init__."""
        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(principal_id="p1", tenant_id="t1")
        assert env._identity_cache == {}
        identity = env.identity
        assert identity.principal_id == "p1"
        assert identity.tenant_id == "t1"

    def test_unit_env_identity_is_lazy(self):
        """Identity is built on first access, not in __init__."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv(principal_id="p1", tenant_id="t1")
        assert env._identity_cache == {}
        identity = env.identity
        assert identity.principal_id == "p1"
        assert identity.tenant_id == "t1"

    def test_integration_env_patches_are_reversed_on_exit(self):
        """Patches are stopped in reverse order on exit."""
        from tests.harness._base import IntegrationEnv

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {
                "a": "os.getcwd",
                "b": "os.getpid",
            }

        env = _TestEnv()
        with patch("src.core.database.database_session.get_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            with patch("tests.factories.ALL_FACTORIES", []):
                with env:
                    assert len(env._patchers) == 2
                # After exit, patchers are cleared
                assert len(env._patchers) == 0

    def test_unit_env_patches_are_reversed_on_exit(self):
        """Patches are stopped in reverse order on exit."""
        from tests.harness._base import BaseTestEnv

        class _TestEnv(BaseTestEnv):
            EXTERNAL_PATCHES = {"a": "os.getcwd", "b": "os.getpid"}

        env = _TestEnv()
        with env:
            assert len(env._patchers) == 2
        assert len(env._patchers) == 0

    def test_identity_respects_dry_run(self):
        """Both base classes pass dry_run to testing_context."""
        from tests.harness._base import BaseTestEnv, IntegrationEnv

        for cls in [IntegrationEnv, BaseTestEnv]:
            env = cls(dry_run=True)
            assert env.identity.testing_context.dry_run is True

    def test_configure_mocks_called_during_enter(self):
        """_configure_mocks is called after patches start."""
        from tests.harness._base import BaseTestEnv

        configure_called = []

        class _TestEnv(BaseTestEnv):
            EXTERNAL_PATCHES = {"dep": "os.getcwd"}

            def _configure_mocks(self):
                # Verify mocks are already available when configure is called
                configure_called.append(list(self.mock.keys()))

        with _TestEnv():
            pass

        assert configure_called == [["dep"]]

    def test_integration_env_has_use_real_db(self):
        """IntegrationEnv has use_real_db=True, BaseTestEnv has False."""
        from tests.harness._base import BaseTestEnv, IntegrationEnv

        assert BaseTestEnv.use_real_db is False
        assert IntegrationEnv.use_real_db is True

    def test_exit_cleans_up_even_when_patcher_raises(self):
        """__exit__ must stop all patchers even if one raises during stop."""
        from tests.harness._base import BaseTestEnv

        class _TestEnv(BaseTestEnv):
            EXTERNAL_PATCHES = {
                "a": "os.getcwd",
                "b": "os.getpid",
            }

        env = _TestEnv()
        env.__enter__()

        # Sabotage patcher "b" (last started, first stopped) to raise on stop
        env._patchers[-1].stop = MagicMock(side_effect=RuntimeError("stop failed"))

        # __exit__ should still clean up patcher "a" and clear state
        # even though patcher "b" raises
        try:
            env.__exit__(None, None, None)
        except RuntimeError:
            pass  # Expected from the sabotaged patcher

        # Key assertion: mock dict and patchers list must be cleared
        assert env._patchers == []
        assert env.mock == {}

    def test_exception_in_test_body_still_cleans_up(self):
        """If test body raises, __exit__ still cleans up patches and mock dict."""
        from tests.harness._base import BaseTestEnv

        class _TestEnv(BaseTestEnv):
            EXTERNAL_PATCHES = {"a": "os.getcwd", "b": "os.getpid"}

        env = _TestEnv()
        try:
            with env:
                assert len(env.mock) == 2
                raise ValueError("simulated test failure")
        except ValueError:
            pass

        # Cleanup must have happened despite the exception
        assert env.mock == {}
        assert env._patchers == []

    def test_identity_for_returns_correct_protocol(self):
        """identity_for(transport) sets the correct protocol on identity."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        env = BaseTestEnv(principal_id="p1", tenant_id="t1")

        impl_id = env.identity_for(Transport.IMPL)
        assert impl_id.protocol == "mcp"

        a2a_id = env.identity_for(Transport.A2A)
        assert a2a_id.protocol == "a2a"

        rest_id = env.identity_for(Transport.REST)
        assert rest_id.protocol == "rest"

        mcp_id = env.identity_for(Transport.MCP)
        assert mcp_id.protocol == "mcp"

        # All share same principal/tenant
        for ident in [impl_id, a2a_id, rest_id, mcp_id]:
            assert ident.principal_id == "p1"
            assert ident.tenant_id == "t1"

    def test_identity_for_is_cached_per_protocol(self):
        """Repeated calls with same transport return same identity object."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        env = BaseTestEnv()
        id1 = env.identity_for(Transport.REST)
        id2 = env.identity_for(Transport.REST)
        assert id1 is id2

    def test_identity_backward_compat(self):
        """env.identity still works and returns IMPL protocol."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv(principal_id="p1")
        assert env.identity.principal_id == "p1"
        assert env.identity.protocol == "mcp"

    def test_call_via_raises_for_unimplemented_transport(self):
        """call_via with Transport.A2A raises NotImplementedError if call_a2a not overridden."""

        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        env = BaseTestEnv()
        result = env.call_via(Transport.A2A)
        assert result.is_error
        assert isinstance(result.error, NotImplementedError)

    def test_call_via_mcp_raises_for_unimplemented(self):
        """call_via with Transport.MCP raises NotImplementedError if call_mcp not overridden."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        env = BaseTestEnv()
        result = env.call_via(Transport.MCP)
        assert result.is_error
        assert isinstance(result.error, NotImplementedError)

    def test_call_via_mcp_routes_through_call_mcp(self):
        """call_via(Transport.MCP) dispatches through McpDispatcher → call_mcp."""

        from pydantic import BaseModel

        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        class _Resp(BaseModel):
            ok: bool = True

        class _TestEnv(BaseTestEnv):
            def call_mcp(self, **kwargs):
                return _Resp()

        env = _TestEnv()
        result = env.call_via(Transport.MCP)
        assert result.is_success
        assert result.payload.ok is True
        assert result.envelope.get("transport") == "mcp"

    def test_call_via_impl_uses_call_impl(self):
        """call_via(Transport.IMPL) routes through call_impl."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        class _TestEnv(BaseTestEnv):
            def call_impl(self, **kwargs):
                from pydantic import BaseModel

                class _Resp(BaseModel):
                    ok: bool = True

                return _Resp()

        env = _TestEnv()
        result = env.call_via(Transport.IMPL)
        assert result.is_success
        assert result.payload.ok is True

    def test_nested_integration_env_raises(self):
        """Nesting two IntegrationEnvs must raise to prevent session corruption."""
        import pytest

        from tests.harness._base import IntegrationEnv

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {"dep": "os.getcwd"}

        with patch("src.core.database.database_session.get_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            # First env binds factories
            with patch("tests.factories.ALL_FACTORIES", [MagicMock(_meta=MagicMock(sqlalchemy_session=None))]):
                with _TestEnv():
                    # Second env should fail because factories are already bound
                    with pytest.raises(AssertionError, match="already bound"):
                        _TestEnv().__enter__()


class TestEnvMethodNamingConsistency:
    """Env methods with the same name across subclasses must have consistent semantics."""

    def test_integration_env_has_setup_default_data(self):
        """IntegrationEnv.setup_default_data creates tenant + principal via factories."""
        from tests.harness._base import IntegrationEnv

        assert hasattr(IntegrationEnv, "setup_default_data"), (
            "IntegrationEnv should have setup_default_data() to reduce boilerplate"
        )

    def test_base_env_has_run_mcp_wrapper(self):
        """BaseTestEnv exposes _run_mcp_wrapper for DRY MCP dispatch."""
        from tests.harness._base import BaseTestEnv

        assert hasattr(BaseTestEnv, "_run_mcp_wrapper"), (
            "BaseTestEnv should have _run_mcp_wrapper to reduce call_mcp duplication"
        )

    def test_creative_sync_env_has_set_run_async_result(self):
        """CreativeSyncEnv uses set_run_async_result, not set_registry_formats.

        set_registry_formats patches registry.list_all_formats (CreativeFormatsEnv).
        CreativeSyncEnv patches run_async.side_effect, which is a different mechanic.
        Using the same name is a trap for new Env authors.
        """
        from tests.harness.creative_sync import CreativeSyncEnv

        assert hasattr(CreativeSyncEnv, "set_run_async_result"), (
            "CreativeSyncEnv should have set_run_async_result (not set_registry_formats)"
        )
        assert not hasattr(CreativeSyncEnv, "set_registry_formats"), (
            "CreativeSyncEnv should NOT have set_registry_formats — "
            "that name belongs to CreativeFormatsEnv (different mechanic)"
        )


class TestDatabaseUrlParam:
    """BaseTestEnv accepts database_url for E2E mode (direct engine creation)."""

    def test_database_url_stored_on_init(self):
        """database_url kwarg is stored and available before __enter__."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv(database_url="postgresql://user:pass@host:5432/db")
        assert env._database_url == "postgresql://user:pass@host:5432/db"

    def test_database_url_default_is_none(self):
        """Without database_url, the field defaults to None."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv()
        assert env._database_url is None

    def test_database_url_passes_through_kwargs(self):
        """database_url flows through **kwargs in subclass constructors."""
        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(database_url="postgresql://x:y@z:1/db")
        assert env._database_url == "postgresql://x:y@z:1/db"

    def test_e2e_config_derives_database_url(self):
        """e2e_config auto-derives database_url from postgres_url."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        config = E2EConfig(base_url="http://localhost:8092", postgres_url="postgresql://u:p@h:5/db")
        env = BaseTestEnv(e2e_config=config)
        assert env._database_url == "postgresql://u:p@h:5/db"
        assert env.e2e_config is config

    def test_e2e_config_default_is_none(self):
        """Without e2e_config, the field defaults to None."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv()
        assert env.e2e_config is None

    def test_e2e_engine_created_from_database_url(self):
        """When database_url is set, __enter__ creates a separate engine."""
        from tests.harness._base import IntegrationEnv

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {}

        env = _TestEnv(database_url="postgresql://user:pass@localhost:5432/testdb")

        with patch("tests.factories.ALL_FACTORIES", []):
            with patch("sqlalchemy.create_engine") as mock_create:
                mock_engine = MagicMock()
                mock_create.return_value = mock_engine
                with env:
                    assert env._e2e_engine is mock_engine
                    assert env._session is not None

            # After exit, engine is disposed
            assert env._e2e_engine is None

    def test_without_database_url_uses_get_engine(self):
        """Without database_url, __enter__ uses the cached get_engine()."""
        from tests.harness._base import IntegrationEnv

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {}

        env = _TestEnv()  # No database_url

        with patch("src.core.database.database_session.get_engine") as mock_get:
            mock_get.return_value = MagicMock()
            with patch("tests.factories.ALL_FACTORIES", []):
                with env:
                    mock_get.assert_called_once()
                    assert env._e2e_engine is None


class TestIntegrationModeIdentityContract:
    """Guard: integration mode identity must use real DB data, never synthetic.

    Prevents regression where identity_for() silently returns auth_token=None
    or a synthetic tenant dict instead of a real TenantContext from the DB.
    Runs during ``make quality`` (unit tests, no real DB) by mocking the session.
    """

    def test_integration_identity_uses_tenant_context_not_dict(self):
        """identity_for() in integration mode must return TenantContext, not dict."""
        from tests.harness._base import IntegrationEnv
        from tests.harness.transport import Transport

        class _TestEnv(IntegrationEnv):
            EXTERNAL_PATCHES = {}

        env = _TestEnv(tenant_id="t1", principal_id="p1")

        # Mock the session and DB queries
        mock_session = MagicMock()
        env._session = mock_session

        # Mock Principal query (for _ensure_default_data_for_auth + _resolve_auth_token)
        from src.core.database.models import Tenant as TenantModel

        mock_principal = MagicMock()
        mock_principal.access_token = "real-token-from-db"

        mock_tenant = MagicMock(spec=TenantModel)
        mock_tenant.tenant_id = "t1"
        mock_tenant.name = "Test Tenant"
        mock_tenant.subdomain = "pub-t1"
        mock_tenant.virtual_host = None
        mock_tenant.ad_server = "mock"
        mock_tenant.enable_axe_signals = True
        mock_tenant.authorized_emails = "[]"
        mock_tenant.authorized_domains = "[]"
        mock_tenant.slack_webhook_url = None
        mock_tenant.slack_audit_webhook_url = None
        mock_tenant.hitl_webhook_url = None
        mock_tenant.admin_token = None
        mock_tenant.auto_approve_format_ids = None
        mock_tenant.human_review_required = True
        mock_tenant.policy_settings = None
        mock_tenant.signals_agent_config = None
        mock_tenant.approval_mode = "require-human"
        mock_tenant.gemini_api_key = None
        mock_tenant.creative_review_criteria = None
        mock_tenant.brand_manifest_policy = "require_auth"
        mock_tenant.advertising_policy = None
        mock_tenant.product_ranking_prompt = None

        # First scalars call: _ensure_default_data_for_auth checks Principal exists
        # Second scalars call: _resolve_auth_token gets the token
        # Third scalars call: _resolve_identity_from_db loads Tenant
        mock_session.scalars.return_value.first.side_effect = [
            mock_principal,  # _ensure_default_data_for_auth: Principal exists
            "real-token-from-db",  # _resolve_auth_token
            mock_tenant,  # _resolve_identity_from_db: Tenant
        ]

        identity = env.identity_for(Transport.IMPL)

        from src.core.tenant_context import TenantContext

        assert isinstance(identity.tenant, TenantContext), (
            f"Integration mode identity.tenant must be TenantContext, got {type(identity.tenant).__name__}. "
            f"This means identity_for() is using synthetic make_tenant() instead of loading from DB."
        )
        assert identity.auth_token == "real-token-from-db"
        assert identity.tenant["tenant_id"] == "t1"
        assert identity.tenant["ad_server"] == "mock"

    def test_unit_mode_identity_uses_synthetic_dict(self):
        """Unit mode (use_real_db=False) should use make_identity with synthetic tenant."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import Transport

        env = BaseTestEnv(tenant_id="t1", principal_id="p1")
        identity = env.identity_for(Transport.IMPL)

        # Unit mode: no DB, so tenant is a plain dict from make_tenant()
        assert isinstance(identity.tenant, dict)
        assert identity.auth_token is None  # no DB to resolve from
        assert identity.tenant["tenant_id"] == "t1"

    def test_resolve_identity_from_db_asserts_on_none_token(self):
        """_resolve_identity_from_db must fail hard if auth_token is None."""
        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(tenant_id="t1", principal_id="p1")
        mock_session = MagicMock()
        env._session = mock_session

        # Principal exists but token resolves to None
        mock_session.scalars.return_value.first.side_effect = [
            MagicMock(),  # _ensure_default_data_for_auth: Principal exists
            None,  # _resolve_auth_token: no token!
        ]

        import pytest

        with pytest.raises(AssertionError, match="auth_token is None"):
            env._resolve_identity_from_db("mcp")

    def test_ensure_default_data_creates_missing_principal(self):
        """_ensure_default_data_for_auth must create tenant+principal when missing."""
        from tests.harness._base import IntegrationEnv

        env = IntegrationEnv(tenant_id="t1", principal_id="p1")
        mock_session = MagicMock()
        env._session = mock_session

        # Principal doesn't exist, Tenant doesn't exist
        mock_session.scalars.return_value.first.side_effect = [
            None,  # Principal query: not found
            None,  # Tenant query: not found
        ]

        with patch("tests.factories.TenantFactory") as mock_tf, patch("tests.factories.PrincipalFactory") as mock_pf:
            mock_tf.return_value = MagicMock(tenant_id="t1")
            env._ensure_default_data_for_auth()

            # Both factories should have been called
            mock_tf.assert_called_once_with(tenant_id="t1")
            mock_pf.assert_called_once()
