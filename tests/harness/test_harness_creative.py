"""Meta-tests for creative harness environments.

Verifies that CreativeSyncEnv, CreativeListEnv, and CreativeFormatsEnv
follow the IntegrationEnv lifecycle contract: patches start/stop correctly,
mock dict populated, identity lazy, _configure_mocks called.
"""

from __future__ import annotations


def _unit_mode(env_cls: type) -> type:
    """Return a ``use_real_db = False`` subclass of *env_cls* for unit-mode smoke tests.

    Single home for the unit-mode stamp — avoids ``class _UnitMode(<Env>):
    use_real_db = False`` copy-pasted at every call site that wants to exercise
    an IntegrationEnv's patches without needing the ``integration_db`` fixture.
    """
    return type("_UnitMode", (env_cls,), {"use_real_db": False})


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
        with _unit_mode(CreativeSyncEnv)() as env:
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

        with _unit_mode(CreativeSyncEnv)() as env:
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

    def test_clear_gemini_api_key_clears_in_process_mock(self):
        """In-process clear_gemini_api_key nulls the account-scoped tenant key."""
        from tests.harness.creative_sync import CreativeSyncEnv

        with _unit_mode(CreativeSyncEnv)() as env:
            env.set_gemini_keys(tenant="present", global_key="present")
            env.clear_gemini_api_key()
            assert env.identity.tenant["gemini_api_key"] is None
            assert env.mock["config"].return_value.gemini_api_key is None

    def test_clear_gemini_api_key_e2e_nulls_tenant_row(self):
        """e2e realization nulls the shared-DB tenant column (account-scoped)."""
        from unittest.mock import MagicMock

        from tests.harness.creative_sync import _clear_gemini_api_key_e2e

        tenant_row = MagicMock()
        tenant_row.gemini_api_key = "present"
        session = MagicMock()
        session.scalars.return_value.first.return_value = tenant_row
        env = MagicMock()
        env.get_session.return_value = session
        env._tenant_id = "t1"
        env.identity.tenant = {"gemini_api_key": "present"}

        _clear_gemini_api_key_e2e(env)

        assert tenant_row.gemini_api_key is None
        env._commit_factory_data.assert_called_once_with()
        assert env.identity.tenant["gemini_api_key"] is None

    def test_setup_generative_build_e2e_unsupported(self):
        """e2e cannot stub creative-agent catalog — declare unsupported (#1964)."""
        import pytest

        from tests.harness._realize import E2EUnsupportedSetup
        from tests.harness.creative_sync import CreativeSyncEnv
        from tests.harness.transport import E2EConfig

        e2e = E2EConfig(
            base_url="http://proxy:8000",
            postgres_url="postgresql://unused",
        )
        with _unit_mode(CreativeSyncEnv)(e2e_config=e2e) as env:
            with pytest.raises(E2EUnsupportedSetup) as exc_info:
                env.setup_generative_build()
        assert exc_info.value.method_name == "setup_generative_build"
        assert "#1964" in str(exc_info.value)

    def test_set_gemini_keys_independent_surfaces(self):
        """Tenant and global GEMINI keys must be independently controllable."""
        from tests.harness.creative_sync import CreativeSyncEnv

        with _unit_mode(CreativeSyncEnv)() as env:
            env.set_gemini_keys(tenant="tenant-only-key", global_key=None)
            assert env.identity.tenant["gemini_api_key"] == "tenant-only-key"
            assert env.mock["config"].return_value.gemini_api_key is None
            env.set_gemini_keys(tenant=None, global_key="global-only-key")
            assert env.identity.tenant["gemini_api_key"] is None
            assert env.mock["config"].return_value.gemini_api_key == "global-only-key"

    def test_set_gemini_keys_applies_to_later_transport_identities(self):
        """call_via builds per-protocol identities after setup — key must survive."""
        from tests.harness.creative_sync import CreativeSyncEnv
        from tests.harness.transport import Transport

        with _unit_mode(CreativeSyncEnv)() as env:
            env.set_gemini_keys(tenant="shared-key", global_key=None)
            a2a = env.identity_for(Transport.A2A)
            rest = env.identity_for(Transport.REST)
            assert a2a.tenant["gemini_api_key"] == "shared-key"
            assert rest.tenant["gemini_api_key"] == "shared-key"

    def test_deliver_a2a_carries_wire_on_result(self, monkeypatch):
        """A2A wire rides DeliverResult — not a deleted per-env ``_last_wire_response`` stash.

        After the single-dispatch migration, ``call_a2a`` is ``deliver_a2a(...).payload`` and
        dispatchers read ``DeliverResult.wire_response``. The old unit contract mocked
        ``sync_creatives_raw`` and asserted ``env._last_wire_response``; that bypass and
        attribute are gone. Pin the return-value contract instead.
        """
        from tests.harness.creative_sync import CreativeSyncEnv
        from tests.harness.transport import DeliverResult

        wire = {
            "creatives": [
                {
                    "creative_id": "c1",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING"}],
                }
            ],
        }
        payload = object()

        def _fake_handler(self, skill_name, response_cls, **kwargs):
            return DeliverResult(payload=payload, wire_response=wire)

        monkeypatch.setattr(CreativeSyncEnv, "_run_a2a_handler", _fake_handler)
        with _unit_mode(CreativeSyncEnv)() as env:
            env._commit_factory_data = lambda: None  # noqa: E731 — unit stub
            delivered = env.deliver_a2a(creatives=[])
            out = env.call_a2a(creatives=[])
        assert delivered.wire_response == wire
        assert delivered.payload is payload
        assert out is payload
        assert not hasattr(env, "_last_wire_response")


class TestNestedCreativeAdvisoryAccessor:
    """first_failed_creative_advisory / assert_wire_advisory contracts."""

    def test_loud_when_wire_missing_on_wire_transport(self):
        import pytest

        from tests.harness.transport import Transport, first_failed_creative_advisory

        with pytest.raises(AssertionError, match="wire_response missing"):
            first_failed_creative_advisory(None, transport=Transport.A2A)

    def test_soft_none_on_impl(self):
        from tests.harness.transport import Transport, first_failed_creative_advisory

        assert first_failed_creative_advisory(None, transport=Transport.IMPL) is None

    def test_returns_first_failed_advisory(self):
        from tests.harness.transport import Transport, first_failed_creative_advisory

        wire = {
            "creatives": [
                {"creative_id": "ok", "action": "created", "errors": []},
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                },
            ]
        }
        advisory = first_failed_creative_advisory(wire, transport=Transport.MCP)
        assert advisory == {"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}

    def test_loud_when_failed_creative_drops_errors_with_response(self):
        """Present wire + failed creative + dropped errors[] must raise (KM Aug-05)."""
        from types import SimpleNamespace

        import pytest

        from tests.harness.transport import Transport, first_failed_creative_advisory

        wire = {"creatives": [{"creative_id": "bad", "action": "failed"}]}  # errors[] dropped
        resp = SimpleNamespace(
            creatives=[SimpleNamespace(action="failed", errors=[{"code": "X"}])],
        )
        with pytest.raises(AssertionError, match="dropped errors"):
            first_failed_creative_advisory(wire, transport=Transport.REST, response=resp)

    def test_soft_none_when_wire_has_no_failed_creative(self):
        """Present wire + no failed creative → soft None (envelope-only paths)."""
        from tests.harness.transport import Transport, first_failed_creative_advisory

        wire = {"creatives": [{"creative_id": "ok", "action": "created", "errors": []}]}
        assert first_failed_creative_advisory(wire, transport=Transport.REST) is None

    def test_assert_wire_advisory_grades_code_and_recovery(self):
        from tests.harness.transport import Transport, assert_wire_advisory

        wire = {
            "creatives": [
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                }
            ]
        }
        advisory = assert_wire_advisory(
            wire,
            "X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
            recovery="terminal",
            transport=Transport.REST,
        )
        assert advisory is not None

    def test_assert_wire_advisory_reddens_on_wrong_code(self):
        """Mutation oracle: gutting the code assert must fail this meta-test."""
        import pytest

        from tests.harness.transport import Transport, assert_wire_advisory

        wire = {
            "creatives": [
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                }
            ]
        }
        with pytest.raises(AssertionError, match="unexpected wire advisory code"):
            assert_wire_advisory(wire, "WRONG_CODE", recovery="terminal", transport=Transport.REST)

    def test_assert_wire_advisory_reddens_on_wrong_recovery(self):
        """Mutation oracle: gutting the recovery assert must fail this meta-test."""
        import pytest

        from tests.harness.transport import Transport, assert_wire_advisory

        wire = {
            "creatives": [
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                }
            ]
        }
        with pytest.raises(AssertionError, match="unexpected wire advisory recovery"):
            assert_wire_advisory(
                wire,
                "X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
                recovery="transient",
                transport=Transport.REST,
            )

    def test_assert_wire_advisory_reddens_on_wrong_suggestion(self):
        """Mutation oracle: gutting the suggestion assert must fail this meta-test."""
        import pytest

        from tests.harness.transport import Transport, assert_wire_advisory

        wire = {
            "creatives": [
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [
                        {
                            "code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
                            "recovery": "terminal",
                            "suggestion": "wrong suggestion",
                        }
                    ],
                }
            ]
        }
        with pytest.raises(AssertionError, match="unexpected wire advisory suggestion"):
            assert_wire_advisory(
                wire,
                "X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
                recovery="terminal",
                suggestion="Ask the seller to configure a Gemini API key for this account",
                transport=Transport.REST,
            )

    def test_assert_wire_advisory_refuses_proxy_when_require_real_wire(self):
        import pytest

        from tests.harness.transport import Transport, assert_wire_advisory

        wire = {
            "creatives": [
                {
                    "creative_id": "bad",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                }
            ]
        }
        with pytest.raises(AssertionError, match="model_dump proxy"):
            assert_wire_advisory(
                wire,
                "X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
                recovery="terminal",
                transport=Transport.A2A,
                wire_is_proxy=True,
                require_real_wire=True,
            )

    def test_nested_bdd_helper_soft_on_envelope_less_error_path(self):
        """UC003/UC019-style: wire transport, no wire_response, no failed creative → soft None."""
        from tests.bdd.steps.generic.then_error import _nested_creative_advisory_error
        from tests.harness.transport import Transport

        assert (
            _nested_creative_advisory_error({"transport": Transport.MCP, "wire_response": None, "response": None})
            is None
        )

    def test_nested_bdd_helper_loud_when_failed_creative_without_wire(self):
        """Success+advisory without stashed wire must not soft-fallback."""
        from types import SimpleNamespace

        import pytest

        from tests.bdd.steps.generic.then_error import _nested_creative_advisory_error
        from tests.harness.transport import Transport

        resp = SimpleNamespace(
            creatives=[SimpleNamespace(action="failed", errors=[{"code": "X"}])],
        )
        with pytest.raises(AssertionError, match="wire_response missing"):
            _nested_creative_advisory_error({"transport": Transport.A2A, "wire_response": None, "response": resp})

    def test_nested_bdd_helper_grades_a2a_model_dump_proxy_payload(self):
        """BDD grades proxy *payload* until real A2A framing lands (#1919).

        Framing refusal is ``require_real_wire=True`` (integration +
        ``test_assert_wire_advisory_refuses_proxy_when_require_real_wire``).
        """
        from types import SimpleNamespace

        from tests.bdd.steps.generic.then_error import _nested_creative_advisory_error
        from tests.harness.transport import Transport

        result = SimpleNamespace(envelope={"wire_response_is_proxy": True})
        wire = {
            "creatives": [
                {
                    "creative_id": "c1",
                    "action": "failed",
                    "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING", "recovery": "terminal"}],
                }
            ]
        }
        advisory = _nested_creative_advisory_error(
            {
                "transport": Transport.A2A,
                "wire_response": wire,
                "response": None,
                "result": result,
            }
        )
        assert advisory is not None
        assert advisory["code"] == "X_PREBID_CREATIVE_GEMINI_KEY_MISSING"

    def test_nested_bdd_helper_loud_when_wire_without_transport(self):
        """Present wire + unset transport must not default to IMPL."""
        import pytest

        from tests.bdd.steps.generic.then_error import _nested_creative_advisory_error

        with pytest.raises(AssertionError, match="transport"):
            _nested_creative_advisory_error(
                {
                    "wire_response": {
                        "creatives": [
                            {
                                "creative_id": "bad",
                                "action": "failed",
                                "errors": [{"code": "X_PREBID_CREATIVE_GEMINI_KEY_MISSING"}],
                            }
                        ]
                    }
                }
            )


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

        with _unit_mode(CreativeListEnv)() as env:
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

        with _unit_mode(CreativeFormatsEnv)() as env:
            assert "registry" in env.mock
            assert "audit_logger" in env.mock
            assert len(env.mock) == 2
