"""Meta-tests for creative harness environments.

Verifies that CreativeSyncEnv, CreativeListEnv, and CreativeFormatsEnv
follow the IntegrationEnv lifecycle contract: patches start/stop correctly,
mock dict populated, identity lazy, _configure_mocks called.
"""

from __future__ import annotations

import re


class TestCreativeSyncEnvContract:
    """CreativeSyncEnv must mock only external services, not DB."""

    def test_import_succeeds(self):
        """CreativeSyncEnv is importable from harness."""
        from tests.harness.creative_sync import CreativeSyncEnv

        assert CreativeSyncEnv is not None

    def test_has_correct_external_patches(self):
        """CreativeSyncEnv patches the registry instance, notifications, audit."""
        from tests.harness.creative_sync import CreativeSyncEnv

        # ai_review_executor joined the set with #1721: the ai-powered branch hands a
        # job to a real ThreadPoolExecutor that opens its OWN AdminCreativeUoW and
        # commits a review verdict, an effect that escapes the sync transaction
        # entirely. Patching it is what makes "a preview submitted no AI review" an
        # observable rather than a race.
        # slack_notifier: the Slack sender the real _send_creative_notifications reaches.
        # Patching the sender (not the function) is what lets the function's own
        # "require-human only, webhook only" guard be graded.
        #
        # "run_async" LEFT the set with salesagent-6xtxb. It patched the sync bridge
        # _sync.py calls the registry through, with ``side_effect = lambda coro: []``, so
        # the in-process catalogue was EMPTY while the e2e server fetched 71 formats from
        # the committed capture. Its only call site is the list_all_formats fetch, so
        # dropping it restored exactly that fetch and nothing else.
        expected_keys = {
            "registry",
            "send_notifications",
            "slack_notifier",
            "audit_log",
            "ai_review_executor",
        }
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
            assert "ai_review_executor" in env.mock
            assert "send_notifications" in env.mock
            assert "slack_notifier" in env.mock
            assert "audit_log" in env.mock
            assert len(env.mock) == 5
            # The generative build reads the TENANT's AI configuration, and the env's
            # tenant carries none until a test sets one (set_gemini_api_key).
            assert env.identity.tenant.ai_config is None

    def test_identity_defaults(self):
        """Identity has sane defaults."""
        from tests.harness.creative_sync import CreativeSyncEnv

        env = CreativeSyncEnv()
        assert env.identity.principal_id == "test_principal"
        assert env.identity.tenant_id == "test_tenant"

    def test_configure_mocks_sets_registry_defaults(self):
        """_configure_mocks installs production's testing-mode registry, not a stand-in."""
        from src.core.creative_agent_registry import ReferenceFormatsRegistry
        from tests.harness.creative_sync import CreativeSyncEnv

        class _UnitMode(CreativeSyncEnv):
            use_real_db = False

        with _UnitMode() as env:
            registry = env.mock["registry"].return_value
            assert isinstance(registry, ReferenceFormatsRegistry), (
                "the env must install production's own ReferenceFormatsRegistry: a stand-in "
                "serves a catalogue the e2e server does not have, and answers any attribute "
                "truthily (canonical_kind_for read 'spec.canonical.kind' off a MagicMock and "
                "put the repr through production code)"
            )

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

        # idempotency_key AND account are present because AdCP 3.1.1 lists both in
        # sync-creatives-request /required: a REST body without them is not a valid request,
        # so the harness supplies them at every dispatch. ``account`` carries a literal id
        # here because this env has no session bound -- there is nothing to seed against
        # outside a ``with env:`` block, and this test asks for the body's SHAPE.
        # Asserted explicitly rather than loosened to a subset check -- the point of this
        # contract test is the EXACT body shape. The key's VALUE is checked by pattern
        # rather than equality: it is minted fresh per call now that sync_creatives honours
        # it, so a fixed expected value would be wrong by construction. Its shape is still
        # pinned, which is what a REST body has to get right.
        key = body.pop("idempotency_key")
        assert body == {
            "creatives": [],
            "dry_run": True,
            "account": {"account_id": "acct_unbound"},
        }
        assert re.fullmatch(r"[A-Za-z0-9_.:-]{16,255}", key), (
            f"the harness must supply a key matching the pinned pattern, got {key!r}"
        )
        assert key != env.build_rest_body(creatives=[], dry_run=True)["idempotency_key"], (
            "each dispatch must carry its OWN key -- a shared one makes two calls the same request"
        )

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
