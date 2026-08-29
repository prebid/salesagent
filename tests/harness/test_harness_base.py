"""Meta-tests for BaseTestEnv / IntegrationEnv base contracts.

Guards the DRY-01 refactor: merging IntegrationEnv + ImplTestEnv into
a single BaseTestEnv. These tests verify that both integration and unit
modes share the same lifecycle contract.
"""

from __future__ import annotations

import os
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

        # Sabotage patcher "b" (last started, first stopped) so its stop() raises
        # -- but only AFTER it has really stopped. A MagicMock(side_effect=...)
        # here instead of a wrapper would mean the real stop() never runs, and
        # `os.getpid` would stay a MagicMock for the REST OF THE PROCESS. That is
        # not hypothetical: it is the leak that made `tests/unit/` unrunnable
        # under xdist. `logging.LogRecord.__init__` does
        # `self.process = os.getpid()`, so every later log record in this worker
        # carried a mock; pytest-json-report copies `dict(record.__dict__)` onto
        # the report, pytest's _report_to_json copies report.__dict__ raw onto the
        # execnet wire, and execnet cannot serialize a mock -- killing the worker
        # and truncating the session behind a summary that read "0 failed".
        # See tests/_xdist_report_safety.py for the measurement.
        #
        # The test's actual subject is unchanged: __exit__ still meets a patcher
        # whose stop() raises, and must still stop the others and clear its state.
        real_stop = env._patchers[-1].stop

        def _stop_then_raise() -> None:
            real_stop()
            raise RuntimeError("stop failed")

        env._patchers[-1].stop = _stop_then_raise

        # __exit__ should still clean up patcher "a" and clear state
        # even though patcher "b" raises
        try:
            env.__exit__(None, None, None)
        except RuntimeError:
            pass  # Expected from the sabotaged patcher

        # Key assertion: mock dict and patchers list must be cleared
        assert env._patchers == []
        assert env.mock == {}
        # And the patch must be genuinely unwound, not merely forgotten. Clearing
        # the bookkeeping while leaving `os.getpid` replaced is what broke the
        # unit suite under xdist -- see the comment above.
        assert isinstance(os.getpid(), int), (
            f"os.getpid is still patched ({type(os.getpid).__name__}) -- this leaks a mock into "
            f"every subsequent logging.LogRecord in this process"
        )

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
        """call_via(Transport.MCP) dispatches through McpDispatcher → deliver_mcp."""

        from pydantic import BaseModel

        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import DeliverResult, Transport

        class _Resp(BaseModel):
            ok: bool = True

        class _TestEnv(BaseTestEnv):
            # Test double: overrides the DELIVER point, which is what the
            # dispatchers call.
            def deliver_mcp(self, **kwargs):
                return DeliverResult(payload=_Resp(), wire_response=None)

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


class TestIsE2EProperty:
    """BaseTestEnv.is_e2e keys on e2e_config, not database_url."""

    def test_is_e2e_true_when_e2e_config_set(self):
        """e2e_config set -> is_e2e True."""
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        env = BaseTestEnv(e2e_config=E2EConfig(base_url="http://unused", postgres_url="postgresql://x/y"))
        assert env.is_e2e is True

    def test_is_e2e_false_with_database_url_only(self):
        """database_url alone rebinds the DB but is NOT e2e mode."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv(database_url="postgresql://x/y")
        assert env.is_e2e is False

    def test_is_e2e_false_when_neither_set(self):
        """No e2e_config, no database_url -> in-process mode."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv()
        assert env.is_e2e is False


class _RecordingRestClient:
    """Records the verb, endpoint and kwargs of the one REST call made.

    Stands in for starlette's TestClient so the verb-derivation contract can be
    graded without a database or a live app — the assertion is about which
    method ``_run_rest_request`` reaches for, not about what the route returns.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def _record(self, verb: str):
        def _call(endpoint: str, **kwargs):
            self.calls.append((verb, endpoint, kwargs))
            return f"{verb}-response"

        return _call

    def __getattr__(self, name: str):
        if name in {"get", "post", "put", "delete", "head", "options", "patch"}:
            return self._record(name)
        raise AttributeError(name)


class TestRestVerbDerivation:
    """The base REST dispatch honors REST_METHOD and reads it AFTER the body.

    Both halves are load-bearing and neither is graded elsewhere:

    * honoring ``REST_METHOD`` is what lets a non-POST route (``GET
      /api/v1/capabilities``) drop its own ``_run_rest_request`` override
      instead of re-deriving the bodyless-verb rule a second time;
    * reading it AFTER ``build_rest_body`` is what makes a request-derived verb
      possible at all — ``CapabilitiesEnv._rest_has_body`` is set BY
      ``build_rest_body``, so a verb read first would always see the previous
      request's value (``False`` on a fresh env, i.e. GET for every request).
    """

    def _dispatch(self, env, **kwargs):
        from unittest.mock import patch as _patch

        client = _RecordingRestClient()
        with _patch.object(type(env), "_prepare_rest_request", return_value=(client, None)):
            env._run_rest_request("/api/v1/capabilities", **kwargs)
        return client.calls

    def test_parameterless_capabilities_request_gets(self):
        """No request params -> empty body -> bodyless GET, with no json= kwarg."""
        from tests.harness.capabilities import CapabilitiesEnv

        calls = self._dispatch(CapabilitiesEnv(), req=None)

        assert calls == [("get", "/api/v1/capabilities", {})], (
            f"parameterless capabilities discovery must GET without a body, got {calls!r}"
        )

    def test_body_carrying_capabilities_request_posts(self):
        """Request params -> non-empty body -> POST carrying that exact body.

        Also pins the ordering: on a fresh env ``_rest_has_body`` is False, so a
        verb read BEFORE ``build_rest_body`` would produce a GET here.
        """
        from adcp.types import GetAdcpCapabilitiesRequest

        from tests.harness.capabilities import CapabilitiesEnv

        calls = self._dispatch(CapabilitiesEnv(), req=GetAdcpCapabilitiesRequest(adcp_version="3.1"))

        assert calls == [("post", "/api/v1/capabilities", {"json": {"adcp_version": "3.1"}})], (
            f"a body-carrying capabilities request must POST the built body, got {calls!r}"
        )

    def test_default_env_without_rest_method_still_posts(self):
        """An env that declares no REST_METHOD keeps the historical POST default."""
        from tests.harness._base import IntegrationEnv

        class _PostOnlyEnv(IntegrationEnv):
            EXTERNAL_PATCHES: dict[str, str] = {}

        env = _PostOnlyEnv()
        assert not hasattr(env, "REST_METHOD")
        calls = self._dispatch(env, req=None)

        assert calls == [("post", "/api/v1/capabilities", {"json": {}})], (
            f"an env with no REST_METHOD must default to POST, got {calls!r}"
        )
