"""Tests for production-mode blocking of /test/auth (F-02)."""

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-18
#
# DECISION_BACKED (8/8 tests):
#   test_environment_production_blocks_test_auth  — security finding F-06: ENVIRONMENT=production
#       must hard-block /test/auth; documented in auth.py "[SECURITY] production mode detected"
#   test_production_flag_also_blocks              — security finding F-06: PRODUCTION=true must
#       hard-block /test/auth (alternative env var path in is_admin_production())
#   test_non_production_allows_when_both_enabled  — regression guard: production check must not
#       fire in non-production; ensures the two blocking tests above are not over-broad
#   test_external_next_url_not_stored_at_login    — security finding F-06: _safe_redirect()
#       must reject external URLs at the ingestion sink (GET /login?next=...)
#   test_external_next_url_does_not_redirect_to_attacker_domain — security finding F-06:
#       full attack flow must not reach attacker domain even after session priming
#   test_session_injected_next_url_rejected_at_auth_sink — security finding F-06: defense in
#       depth — sink-level _safe_redirect() must reject injected external URL independently
#   test_custom_env_var_credentials_accepted      — security fix: credentials come from env
#       vars, never hardcoded; documented in auth.py "# credentials are always read from env
#       vars, never hardcoded"
#   test_default_password_rejected_when_env_var_overrides_it — security fix: old hardcoded
#       password must be rejected when env var overrides the credential
# ---

import os
from unittest.mock import patch


class TestProductionHardBlock:
    """POST /test/auth must return 404 in production mode regardless of other flags."""

    def test_environment_production_blocks_test_auth(self, make_auth_test_client):
        """ENVIRONMENT=production must block /test/auth even when ADCP_AUTH_TEST_MODE=true."""
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, {"ENVIRONMENT": "production", "ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 404

    def test_production_flag_also_blocks(self, make_auth_test_client):
        """PRODUCTION=true must block /test/auth even when ADCP_AUTH_TEST_MODE=true."""
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, {"PRODUCTION": "true", "ENVIRONMENT": "", "ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 404

    def test_non_production_allows_when_both_enabled(self, make_auth_test_client):
        """Production check must not block valid non-production access (regression guard).

        Verifies that ENVIRONMENT/PRODUCTION checks only fire in production:
        a non-production deployment with env var + auth_setup_mode=True must succeed.
        """
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": "", "ENVIRONMENT": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 302


class TestOpenRedirectRejection:
    """Regression tests for F-06: open redirect via login next parameter.

    Exercises the full attack flow through Flask endpoints so that removing
    _safe_redirect() or skipping it at any sink would cause a real test failure.
    """

    def test_external_next_url_not_stored_at_login(self, make_auth_test_client):
        """GET /login?next=https://evil.example.com must not store the value in session.

        _safe_redirect() at ingestion rejects external URLs, so login_next_url
        is never set — the post-auth redirect cannot be influenced.
        """
        with make_auth_test_client(True) as (client, _):
            client.get("/login?next=https://evil.example.com")

        with client.session_transaction() as sess:
            assert "login_next_url" not in sess

    def test_external_next_url_does_not_redirect_to_attacker_domain(self, make_auth_test_client):
        """Full attack flow: prime session -> authenticate -> verify redirect stays internal.

        Even if login_next_url were somehow set to an external URL in the session,
        the sink in test_auth uses _safe_redirect() which returns the fallback.
        This test verifies the end-to-end flow stays within the application.
        """
        with make_auth_test_client(True) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": "", "ENVIRONMENT": ""}):
                # Step 1: prime the session with an external next URL
                client.get("/login?next=https://evil.example.com")

                # Step 2: authenticate via test auth
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                    follow_redirects=False,
                )

        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "evil.example.com" not in location

    def test_session_injected_next_url_rejected_at_auth_sink(self, make_auth_test_client):
        """Defense in depth: even if session login_next_url is set to an external URL,
        the /test/auth sink must not redirect to it.

        Directly injects the malicious value into the session to verify the sink-level
        _safe_redirect() call would catch it independently of the ingestion check.
        """
        with make_auth_test_client(True) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": "", "ENVIRONMENT": ""}):
                # Inject malicious next URL directly into session (bypasses ingestion check)
                with client.session_transaction() as sess:
                    sess["login_next_url"] = "https://evil.example.com/phishing"

                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                    follow_redirects=False,
                )

        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "evil.example.com" not in location


class TestSuperAdminCredentialPath:
    """Super-admin auth must go through test_users, not a hardcoded bypass."""

    _CUSTOM_CREDS_ENV = {
        "TEST_SUPER_ADMIN_EMAIL": "super@example.com",
        "TEST_SUPER_ADMIN_PASSWORD": "secure-random-pw",
        "ADCP_AUTH_TEST_MODE": "true",
        "PRODUCTION": "",
        "ENVIRONMENT": "",
    }

    def test_custom_env_var_credentials_accepted(self, make_auth_test_client):
        """Custom env var credentials are accepted at the /test/auth endpoint.

        Confirms the super-admin email and password come from env vars, not hardcoded values.
        Asserts the full session state set by the endpoint on success.
        """
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, self._CUSTOM_CREDS_ENV):
                response = client.post(
                    "/test/auth",
                    data={"email": "super@example.com", "password": "secure-random-pw", "tenant_id": "default"},
                    follow_redirects=False,
                )

            assert response.status_code == 302
            with client.session_transaction() as sess:
                assert sess.get("authenticated") is True
                assert sess.get("role") == "super_admin"
                assert sess.get("is_super_admin") is True
                assert sess.get("tenant_id") == "default"

    def test_default_password_rejected_when_env_var_overrides_it(self, make_auth_test_client):
        """The old hardcoded password is rejected when env vars override the credentials.

        When TEST_SUPER_ADMIN_PASSWORD is set, posting the old default password (test123)
        for the custom email must not authenticate — confirming the hardcoded bypass is gone.
        """
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, self._CUSTOM_CREDS_ENV):
                response = client.post(
                    "/test/auth",
                    data={"email": "super@example.com", "password": "test123", "tenant_id": "default"},
                    follow_redirects=False,
                )

            assert response.status_code == 302
            with client.session_transaction() as sess:
                assert "authenticated" not in sess
