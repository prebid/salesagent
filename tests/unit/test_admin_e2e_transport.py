"""AdminAccountEnv is TOLD its transport and address — it never infers them.

This env used to auto-detect its transport from the process-global
``ADCP_SALES_PORT``. That is the defect these tests now pin the absence of:

- A global carries no sender, so the env could not tell "my caller wants e2e"
  from "docker-compose.e2e.yml exports a port for every process in the
  container". Presence of the variable had to be read as intent, which is a
  guess.
- A global carries no multiplicity, so it cannot express a per-worker address at
  all: under ``E2E_PER_WORKER`` each xdist worker targets its own container
  (``http://<project>-server-gwN:8080``). One variable, N required values.
- A guess that coexists with an explicit argument produces a conflict, which
  needs a precedence rule, which needs a test to referee it. Removing the guess
  removes all three.

The transport now arrives from the BDD collection-time parametrization and the
address from ``e2e_stack``; both are ordinary arguments.
"""

from __future__ import annotations

import os

import pytest

from tests.harness.admin_accounts import AdminAccountEnv, AdminTransport


class TestTransportIsTold:
    """The mode comes from the caller, never from the environment."""

    def test_defaults_to_integration(self) -> None:
        assert AdminAccountEnv().mode == "integration"

    def test_explicit_integration_mode(self) -> None:
        assert AdminAccountEnv(mode="integration").mode == "integration"

    def test_explicit_e2e_mode(self) -> None:
        env = AdminAccountEnv(mode="e2e", base_url="http://server-gw0:8080")
        assert env.mode == "e2e"

    def test_ambient_sales_port_does_not_select_e2e(self) -> None:
        """The inversion of the old contract: the env var has NO effect on mode.

        This is the regression that matters. ``ADCP_SALES_PORT`` is set for
        essentially every containerized run — docker-compose.e2e.yml hardcodes
        it to "8000", tests/integration/conftest.py and tests/e2e/conftest.py
        both set it — so an env that keyed on it silently became e2e inside
        Docker regardless of what the caller wanted.
        """
        old = os.environ.get("ADCP_SALES_PORT")
        os.environ["ADCP_SALES_PORT"] = "8092"
        try:
            assert AdminAccountEnv().mode == "integration", (
                "ADCP_SALES_PORT must not influence transport selection — the caller decides"
            )
        finally:
            if old is None:
                os.environ.pop("ADCP_SALES_PORT", None)
            else:
                os.environ["ADCP_SALES_PORT"] = old

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="mode must be"):
            AdminAccountEnv(mode="e2e_rest")


class TestAddressIsTold:
    """Over e2e the address is supplied, not discovered."""

    def test_e2e_without_base_url_is_refused(self) -> None:
        """Fail loudly at construction rather than resolve an address itself.

        The alternative — falling back to a host/port derivation — is what made
        a per-worker address impossible to express and let a scenario silently
        drive the wrong server.
        """
        with pytest.raises(ValueError, match="requires base_url"):
            AdminAccountEnv(mode="e2e")

    def test_e2e_uses_the_supplied_address(self) -> None:
        env = AdminAccountEnv(mode="e2e", base_url="http://myproj-server-gw3:8080")
        assert env._base_url == "http://myproj-server-gw3:8080"


class TestAdminTransportEnum:
    """The two ids the feature declares, kept out of the AdCP Transport enum."""

    def test_values(self) -> None:
        assert AdminTransport.INTEGRATION.value == "admin_integration"
        assert AdminTransport.E2E.value == "e2e_admin"

    def test_e2e_member_carries_the_load_bearing_prefix(self) -> None:
        """``ctx`` and ``is_e2e()`` both key on an ``e2e_`` value prefix."""
        assert AdminTransport.E2E.value.startswith("e2e_")
        assert not AdminTransport.INTEGRATION.value.startswith("e2e_")

    def test_members_are_not_adcp_transports(self) -> None:
        """An admin member in ``Transport`` would need a fabricated AdCP protocol."""
        from tests.harness.transport import Transport

        for member in AdminTransport:
            assert not isinstance(member, Transport)
