"""P1 (#1544): proprietary X-* test headers are gated fail-CLOSED.

The pinned AdCP sandbox guidance
(dist/docs/3.1.0/media-buy/advanced-topics/sandbox.mdx) says sellers MUST NOT
alter behavior based on X-Dry-Run / X-Mock-Time. Those headers are internal
tooling, so ``AdCPTestContext.from_headers`` honors them ONLY on explicit
opt-in: ``ENVIRONMENT`` set to a dev value (``development``/``test``) or
``ADCP_TEST_HOOKS_ENABLED=true``. An UNSET ``ENVIRONMENT`` disables them —
a production deployment is safe even if the operator forgot to set
``ENVIRONMENT=production``.
"""

from types import SimpleNamespace

import pytest

from src.core.testing_hooks import AdCPTestContext, test_hooks_enabled

_HEADERS = {"x-dry-run": "true", "x-force-error": "budget_exceeded", "x-simulated-spend": "true"}


def _clear_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ADCP_TEST_HOOKS_ENABLED", raising=False)


def test_test_headers_ignored_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: an UNSET ENVIRONMENT disables the test hooks entirely."""
    _clear_gate_env(monkeypatch)
    assert test_hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_test_headers_ignored_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENVIRONMENT=production disables the test hooks."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert test_hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_test_headers_ignored_for_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-dev ENVIRONMENT value (e.g. staging) keeps the gate closed."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert test_hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


@pytest.mark.parametrize("environment", ["development", "test", "Development", "TEST"])
def test_dry_run_honored_with_explicit_dev_environment(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    """An EXPLICIT dev ENVIRONMENT opts in to the internal dry-run tooling."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert test_hooks_enabled() is True
    ctx = AdCPTestContext.from_headers(_HEADERS)
    assert ctx is not None
    assert ctx.dry_run is True


def test_force_error_honored_with_dev_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: another test header rides the same explicit opt-in gate."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    ctx = AdCPTestContext.from_headers(_HEADERS)
    assert ctx is not None
    assert ctx.force_error == "budget_exceeded"


def test_adcp_test_hooks_enabled_overrides_unset_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADCP_TEST_HOOKS_ENABLED=true opts in without a dev ENVIRONMENT."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ADCP_TEST_HOOKS_ENABLED", "true")
    assert test_hooks_enabled() is True
    ctx = AdCPTestContext.from_headers(_HEADERS)
    assert ctx is not None
    assert ctx.dry_run is True


def test_adcp_test_hooks_enabled_requires_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the literal true opts in — other values keep the gate closed."""
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("ADCP_TEST_HOOKS_ENABLED", "1")
    assert test_hooks_enabled() is False
    assert AdCPTestContext.from_headers(_HEADERS) is None


def test_rest_dependency_extracts_dry_run_when_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The REST auth dependency wires x-dry-run into the resolved identity.

    Pins the REST boundary end-to-end at the unit level: with the gate open
    (ENVIRONMENT=development), ``_resolve_rest_identity`` extracts the testing
    context from the raw request headers and forwards it to
    ``resolve_identity`` with ``dry_run=True``.
    """
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
    testing_context = captured["testing_context"]
    assert isinstance(testing_context, AdCPTestContext)
    assert testing_context.dry_run is True
