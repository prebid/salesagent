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
        assert env._identity is None
        identity = env.identity
        assert identity.principal_id == "p1"
        assert identity.tenant_id == "t1"

    def test_unit_env_identity_is_lazy(self):
        """Identity is built on first access, not in __init__."""
        from tests.harness._base import BaseTestEnv

        env = BaseTestEnv(principal_id="p1", tenant_id="t1")
        assert env._identity is None
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
