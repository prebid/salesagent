"""Meta-tests for creative harness environments.

Verifies that CreativeSyncEnv, CreativeListEnv, and CreativeFormatsEnv
follow the IntegrationEnv lifecycle contract: patches start/stop correctly,
mock dict populated, identity lazy, _configure_mocks called.
"""

from __future__ import annotations


class TestCreativeSyncEnvContract:
    """CreativeSyncEnv must mock only external services, not DB."""

    def test_import_succeeds(self):
        """CreativeSyncEnv is importable from harness."""
        from tests.harness.creative_sync import CreativeSyncEnv

        assert CreativeSyncEnv is not None

    def test_has_correct_external_patches(self):
        """CreativeSyncEnv patches registry, run_async, notifications, audit."""
        from tests.harness.creative_sync import CreativeSyncEnv

        expected_keys = {"registry", "run_async", "send_notifications", "audit_log", "config"}
        assert set(CreativeSyncEnv.EXTERNAL_PATCHES.keys()) == expected_keys

    def test_is_integration_env(self):
        """CreativeSyncEnv uses real DB (use_real_db=True)."""
        from tests.harness.creative_sync import CreativeSyncEnv

        assert CreativeSyncEnv.use_real_db is True

    def test_mock_dict_populated_in_unit_mode(self):
        """Verify patches activate correctly (unit-mode smoke test without DB)."""
        from tests.harness.creative_sync import CreativeSyncEnv

        # Override use_real_db to avoid needing integration_db fixture
        class _UnitMode(CreativeSyncEnv):
            use_real_db = False

        with _UnitMode() as env:
            assert "registry" in env.mock
            assert "run_async" in env.mock
            assert "send_notifications" in env.mock
            assert "audit_log" in env.mock
            assert "config" in env.mock
            assert len(env.mock) == 5

    def test_identity_defaults(self):
        """Identity has sane defaults."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        assert env.identity.principal_id == "test_principal"
        assert env.identity.tenant_id == "test_tenant"

    def test_configure_mocks_sets_registry_defaults(self):
        """_configure_mocks sets up happy-path registry return values."""
        from tests.harness.creative_sync import CreativeSyncEnv

        class _UnitMode(CreativeSyncEnv):
            use_real_db = False

        with _UnitMode() as env:
            # Registry mock should have a return value configured
            assert env.mock["registry"].return_value is not None

    def test_has_rest_endpoint(self):
        """CreativeSyncEnv defines REST_ENDPOINT for REST dispatch."""
        from tests.harness.creative_sync import CreativeSyncEnv

        assert CreativeSyncEnv.REST_ENDPOINT == "/api/v1/creatives/sync"

    def test_has_call_a2a(self):
        """CreativeSyncEnv implements call_a2a for A2A dispatch."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        assert hasattr(env, "call_a2a")
        # Should not raise NotImplementedError (unlike base class)
        assert env.call_a2a.__func__ is not env.call_impl.__func__

    def test_has_build_rest_body(self):
        """CreativeSyncEnv implements build_rest_body for REST dispatch."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        body = env.build_rest_body(creatives=[], dry_run=True)
        assert body == {"creatives": [], "dry_run": True}

    def test_has_parse_rest_response(self):
        """CreativeSyncEnv implements parse_rest_response."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        # Smoke test: should accept a dict with expected shape
        response = env.parse_rest_response({"creatives": [], "dry_run": False})
        assert response is not None

    def test_has_call_mcp(self):
        """CreativeSyncEnv implements call_mcp for MCP dispatch."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        assert hasattr(env, "call_mcp")
        # Should be a distinct method (not inherited NotImplementedError stub)
        assert callable(env.call_mcp)


class TestCreativeListEnvContract:
    """CreativeListEnv must mock only audit logger."""

    def test_import_succeeds(self):
        """CreativeListEnv is importable from harness."""
        from tests.harness.creative_list import CreativeListEnv

        assert CreativeListEnv is not None

    def test_has_correct_external_patches(self):
        """CreativeListEnv patches audit_logger only."""
        from tests.harness.creative_list import CreativeListEnv

        expected_keys = {"audit_logger"}
        assert set(CreativeListEnv.EXTERNAL_PATCHES.keys()) == expected_keys

    def test_is_integration_env(self):
        """CreativeListEnv uses real DB."""
        from tests.harness.creative_list import CreativeListEnv

        assert CreativeListEnv.use_real_db is True

    def test_mock_dict_populated_in_unit_mode(self):
        """Verify patches activate correctly."""
        from tests.harness.creative_list import CreativeListEnv

        class _UnitMode(CreativeListEnv):
            use_real_db = False

        with _UnitMode() as env:
            assert "audit_logger" in env.mock
            assert len(env.mock) == 1


class TestCreativeFormatsEnvContract:
    """CreativeFormatsEnv must mock registry and audit logger."""

    def test_import_succeeds(self):
        """CreativeFormatsEnv is importable from harness."""
        from tests.harness.creative_formats import CreativeFormatsEnv

        assert CreativeFormatsEnv is not None

    def test_configure_mocks_provides_default_format(self):
        """_configure_mocks() seeds a default-display format via FormatFactory.

        Core invariant: harness owns defaults, not BDD Background steps.
        After refactoring, _configure_mocks() should use FormatFactory.build()
        (not _get_mock_formats() from production code) to create a single
        default-display format.
        """
        from tests.harness.creative_formats import CreativeFormatsEnv

        class _UnitMode(CreativeFormatsEnv):
            use_real_db = False

        with _UnitMode() as env:
            mock_registry = env.mock["registry"].return_value
            default_formats = mock_registry.list_all_formats.return_value
            # Harness must provide at least one default format
            assert len(default_formats) >= 1
            # The default should include a display format
            has_display = any(
                getattr(fmt, "name", "").lower().find("display") >= 0
                or str(getattr(fmt, "type", "")).lower().find("display") >= 0
                for fmt in default_formats
            )
            assert has_display, "Default formats must include at least one display format"

    def test_set_registry_formats_overrides_defaults(self):
        """set_registry_formats() fully replaces _configure_mocks() defaults."""
        from tests.factories.format import FormatFactory as FF
        from tests.harness.creative_formats import CreativeFormatsEnv

        class _UnitMode(CreativeFormatsEnv):
            use_real_db = False

        with _UnitMode() as env:
            custom = [FF.build(name="custom-video")]
            env.set_registry_formats(custom)
            mock_registry = env.mock["registry"].return_value
            assert mock_registry.list_all_formats.return_value == custom

    def test_default_format_built_by_factory_not_production(self):
        """After refactoring, _configure_mocks() uses FormatFactory, not _get_mock_formats().

        This is the TDD "red" test — it will fail until _configure_mocks()
        is changed to use FormatFactory.build() instead of _get_mock_formats().
        """
        from tests.harness.creative_formats import CreativeFormatsEnv

        class _UnitMode(CreativeFormatsEnv):
            use_real_db = False

        with _UnitMode() as env:
            mock_registry = env.mock["registry"].return_value
            default_formats = mock_registry.list_all_formats.return_value
            # After refactoring: exactly 1 default-display format (not 11 production mocks)
            assert len(default_formats) == 1, (
                f"Expected 1 default format from FormatFactory, got {len(default_formats)} "
                f"(still using _get_mock_formats()?)"
            )

    def test_has_correct_external_patches(self):
        """CreativeFormatsEnv patches registry and audit_logger."""
        from tests.harness.creative_formats import CreativeFormatsEnv

        expected_keys = {"registry", "audit_logger"}
        assert set(CreativeFormatsEnv.EXTERNAL_PATCHES.keys()) == expected_keys

    def test_is_integration_env(self):
        """CreativeFormatsEnv uses real DB."""
        from tests.harness.creative_formats import CreativeFormatsEnv

        assert CreativeFormatsEnv.use_real_db is True

    def test_mock_dict_populated_in_unit_mode(self):
        """Verify patches activate correctly."""
        from tests.harness.creative_formats import CreativeFormatsEnv

        class _UnitMode(CreativeFormatsEnv):
            use_real_db = False

        with _UnitMode() as env:
            assert "registry" in env.mock
            assert "audit_logger" in env.mock
            assert len(env.mock) == 2
