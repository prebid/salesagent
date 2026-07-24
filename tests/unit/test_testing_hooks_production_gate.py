"""Deprecated X-* protocol headers never alter seller behavior."""

from types import SimpleNamespace

import pytest

from src.core.testing_hooks import AdCPTestContext
from src.core.testing_hooks import test_hooks_enabled as hooks_enabled

_HEADERS = {"x-dry-run": "true", "x-force-error": "budget_exceeded", "x-simulated-spend": "true"}


def _clear_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ADCP_TEST_HOOKS_ENABLED", raising=False)
    # PRODUCTION is the SECOND production convention (is_production() is the
    # union of the two). Clearing it keeps every case below hermetic against an
    # ambient PRODUCTION=true, and makes the two cases that set it deliberate.
    monkeypatch.delenv("PRODUCTION", raising=False)


def test_test_headers_ignored_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: an UNSET ENVIRONMENT disables the test hooks entirely."""
    _clear_gate_env(monkeypatch)
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_test_headers_ignored_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENVIRONMENT=production disables the test hooks."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_test_headers_ignored_for_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-dev ENVIRONMENT value (e.g. staging) keeps the gate closed."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_test_headers_ignored_when_production_flag_set_with_dev_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRODUCTION=true wins over a dev ENVIRONMENT.

    ``is_production()`` is the union of the two deployment conventions, so a
    deploy that marks production with PRODUCTION=true while ENVIRONMENT still
    reads ``development`` IS production. Keying this gate on ENVIRONMENT alone
    honored buyer-supplied X-Dry-Run / X-Force-Error against a live seller.
    """
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("PRODUCTION", "true")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_explicit_opt_in_cannot_reopen_the_gate_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production SUBTRACTS: even the explicit opt-in cannot re-enable hooks.

    The headers alter spend-committing behavior, so there is no escape hatch
    that survives production detection by either convention.
    """
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ADCP_TEST_HOOKS_ENABLED", "true")
    monkeypatch.setenv("PRODUCTION", "true")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None

    monkeypatch.setenv("PRODUCTION", "false")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


@pytest.mark.parametrize("environment", ["development", "test", "Development", "TEST"])
def test_dry_run_header_ignored_with_explicit_dev_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Development mode does not restore deprecated protocol behavior."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert hooks_enabled() is True
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_force_error_header_ignored_with_dev_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_adcp_test_hooks_enabled_does_not_restore_protocol_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ADCP_TEST_HOOKS_ENABLED", "true")
    assert hooks_enabled() is True
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_adcp_test_hooks_enabled_requires_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the literal true opts in — other values keep the gate closed."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ADCP_TEST_HOOKS_ENABLED", "1")
    assert hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_rest_dependency_ignores_dry_run_header_when_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The REST boundary never derives testing behavior from buyer headers."""
    from src.core.auth_context import AuthContext, _resolve_rest_identity

    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")

    captured: dict[str, object] = {}

    def fake_resolve_identity(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(principal_id="principal_1", tenant_id="tenant_1", tenant=None)

    monkeypatch.setattr("src.core.resolved_identity.resolve_identity", fake_resolve_identity)

    auth_ctx = AuthContext(auth_token="tok_123", headers={"x-dry-run": "true"})
    identity = _resolve_rest_identity(auth_ctx, require_valid_token=True)

    assert identity is not None
    assert captured["testing_context"] is None
