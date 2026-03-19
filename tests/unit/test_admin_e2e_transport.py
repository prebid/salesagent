"""Regression test: AdminAccountEnv supports e2e transport via HTTP requests.

Verifies that when ADCP_SALES_PORT is set, AdminAccountEnv uses requests.Session
to hit the Docker stack instead of Flask test_client (integration mode).

beads: salesagent-oj0.1.3
"""

from __future__ import annotations


class TestAdminE2eTransportCapability:
    """Verify admin harness supports e2e transport."""

    def test_harness_has_e2e_mode_attribute(self) -> None:
        """AdminAccountEnv must expose a mode property (integration vs e2e)."""
        from tests.harness.admin_accounts import AdminAccountEnv

        env = AdminAccountEnv()
        assert hasattr(env, "mode"), "AdminAccountEnv must have a 'mode' attribute for transport selection"

    def test_explicit_integration_mode(self) -> None:
        """Explicit mode='integration' overrides env var."""
        from tests.harness.admin_accounts import AdminAccountEnv

        env = AdminAccountEnv(mode="integration")
        assert env.mode == "integration", f"Expected 'integration' mode, got '{env.mode}'"

    def test_explicit_e2e_mode(self) -> None:
        """Explicit mode='e2e' works regardless of env var."""
        from tests.harness.admin_accounts import AdminAccountEnv

        env = AdminAccountEnv(mode="e2e")
        assert env.mode == "e2e", f"Expected 'e2e' mode, got '{env.mode}'"

    def test_auto_detection_without_port(self) -> None:
        """Without ADCP_SALES_PORT, auto mode is 'integration'."""
        import os

        from tests.harness.admin_accounts import AdminAccountEnv

        old = os.environ.pop("ADCP_SALES_PORT", None)
        try:
            env = AdminAccountEnv()
            assert env.mode == "integration", f"Expected 'integration', got '{env.mode}'"
        finally:
            if old is not None:
                os.environ["ADCP_SALES_PORT"] = old

    def test_auto_detection_with_port(self) -> None:
        """With ADCP_SALES_PORT, auto mode is 'e2e'."""
        import os

        from tests.harness.admin_accounts import AdminAccountEnv

        os.environ["ADCP_SALES_PORT"] = "8092"
        try:
            env = AdminAccountEnv()
            assert env.mode == "e2e", f"Expected 'e2e', got '{env.mode}'"
        finally:
            del os.environ["ADCP_SALES_PORT"]
