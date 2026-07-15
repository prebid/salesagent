"""Unit tests for SSRF URL validation (F-04).

Covers:
- check_url_ssrf: core validator used across signals agents, webhooks, property lists
- validate_agent_url: media_buy_create wrapper
- BLOCKED_HOSTNAMES: Docker-internal and cloud metadata hostname coverage
- Flask endpoint-level wiring for signals agents add/edit handlers
"""

import os
from unittest.mock import MagicMock, patch

from src.core.security.url_validator import BLOCKED_HOSTNAMES, check_url_ssrf


class TestCheckUrlSsrf:
    """Core validator rejects private/internal targets."""

    def test_valid_public_https_url_accepted(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_safe, error = check_url_ssrf("https://example.com/agent")
        assert is_safe is True
        assert error == ""

    def test_valid_public_http_url_accepted(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_safe, error = check_url_ssrf("http://example.com/agent")
        assert is_safe is True
        assert error == ""

    def test_localhost_rejected(self):
        is_safe, error = check_url_ssrf("http://localhost:9999")
        assert is_safe is False
        assert "blocked" in error.lower() or "private" in error.lower() or "loopback" in error.lower()

    def test_loopback_ip_rejected(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["127.0.0.1"]):
            is_safe, error = check_url_ssrf("http://127.0.0.1:9999")
        assert is_safe is False

    def test_private_rfc1918_10_rejected(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["10.0.0.1"]):
            is_safe, error = check_url_ssrf("http://internal-host.example.com")
        assert is_safe is False
        assert "10.0.0.0/8" in error or "private" in error.lower()

    def test_private_rfc1918_192168_rejected(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["192.168.1.1"]):
            is_safe, error = check_url_ssrf("http://router.local")
        assert is_safe is False

    def test_private_rfc1918_172_rejected(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["172.16.0.1"]):
            is_safe, error = check_url_ssrf("http://internal.corp")
        assert is_safe is False

    def test_link_local_169_254_rejected(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["169.254.169.254"]):
            is_safe, error = check_url_ssrf("http://169.254.169.254/metadata")
        assert is_safe is False

    def test_aws_metadata_hostname_rejected(self):
        is_safe, error = check_url_ssrf("http://169.254.169.254/latest/meta-data/")
        assert is_safe is False

    def test_gcp_metadata_hostname_rejected(self):
        is_safe, error = check_url_ssrf("http://metadata.google.internal/computeMetadata/v1/")
        assert is_safe is False
        assert "blocked" in error.lower()

    def test_docker_internal_hostname_rejected(self):
        """F-04: host.docker.internal is the exact vector from the audit evidence."""
        is_safe, error = check_url_ssrf("http://host.docker.internal:9999")
        assert is_safe is False
        assert "blocked" in error.lower()

    def test_gateway_docker_internal_rejected(self):
        is_safe, error = check_url_ssrf("http://gateway.docker.internal")
        assert is_safe is False

    def test_non_http_scheme_rejected(self):
        is_safe, error = check_url_ssrf("ftp://example.com/agent")
        assert is_safe is False
        assert "http" in error.lower()

    def test_file_scheme_rejected(self):
        is_safe, error = check_url_ssrf("file:///etc/passwd")
        assert is_safe is False

    def test_require_https_rejects_http(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_safe, error = check_url_ssrf("http://example.com/agent", require_https=True)
        assert is_safe is False
        assert "https" in error.lower()

    def test_require_https_accepts_https(self):
        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_safe, error = check_url_ssrf("https://example.com/agent", require_https=True)
        assert is_safe is True

    def test_unresolvable_hostname_rejected(self):

        with patch(
            "src.core.security.url_validator._resolve_ips",
            side_effect=OSError("Name or service not known"),
        ):
            is_safe, error = check_url_ssrf("http://this-hostname-does-not-exist.invalid")
        assert is_safe is False
        assert "resolve" in error.lower() or "cannot" in error.lower()


class TestSsrfCompletenessAlwaysBlocked:
    """The metadata-tier additions: multicast, reserved, unspecified, CGNAT, and
    IPv4-mapped IPv6 — blocked in EVERY environment, even allow_private=True."""

    def _check(self, url, ip, *, allow_private=False):
        with patch("src.core.security.url_validator._resolve_ips", return_value=[ip]):
            return check_url_ssrf(url, allow_private=allow_private)

    def test_ipv4_multicast_rejected(self):
        is_safe, error = self._check("http://224.0.0.1/", "224.0.0.1")
        assert is_safe is False
        assert "blocked" in error.lower()

    def test_ipv6_multicast_rejected(self):
        is_safe, _ = self._check("http://[ff02::1]/", "ff02::1")
        assert is_safe is False

    def test_cgnat_rejected(self):
        is_safe, _ = self._check("http://100.64.0.1/", "100.64.0.1")
        assert is_safe is False

    def test_unspecified_rejected(self):
        is_safe, _ = self._check("http://0.0.0.0/", "0.0.0.0")
        assert is_safe is False

    def test_ipv4_mapped_metadata_rejected(self):
        # ::ffff:169.254.169.254 must unwrap to 169.254.169.254 and hit the IPv4 rule.
        is_safe, _ = self._check("http://[::ffff:169.254.169.254]/", "::ffff:169.254.169.254")
        assert is_safe is False

    def test_ipv4_mapped_loopback_rejected(self):
        # ::ffff:127.0.0.1 must unwrap to 127.0.0.1 (loopback, private tier).
        is_safe, _ = self._check("http://[::ffff:127.0.0.1]/", "::ffff:127.0.0.1")
        assert is_safe is False

    def test_always_blocked_even_with_allow_private(self):
        # multicast / CGNAT / mapped-metadata stay blocked when allow_private=True.
        assert self._check("http://224.0.0.1/", "224.0.0.1", allow_private=True)[0] is False
        assert self._check("http://100.64.0.1/", "100.64.0.1", allow_private=True)[0] is False
        assert self._check("http://[::ffff:169.254.169.254]/", "::ffff:169.254.169.254", allow_private=True)[0] is False


class TestSsrfAlwaysBlockRanges:
    """Reviewer probe (#1546): 0.0.0.0/8 (this-network) and fd00:ec2::/32 (AWS IPv6
    instance metadata) must be blocked in EVERY environment, even allow_private=True.

    fd00:ec2::/32 sits inside fc00::/7 (the unique-local private tier), so before the
    fix it was reachable whenever allow_private=True; 0.0.0.x fell through every range.
    These use real literal IPs (getaddrinfo resolves a literal to itself), so no mock.
    """

    def test_this_network_0_0_0_1_blocked_with_allow_private(self):
        is_safe, error = check_url_ssrf("http://0.0.0.1/", allow_private=True)
        assert is_safe is False
        assert "blocked" in error.lower()

    def test_this_network_0_1_2_3_blocked_with_allow_private(self):
        is_safe, error = check_url_ssrf("http://0.1.2.3/", allow_private=True)
        assert is_safe is False
        assert "blocked" in error.lower()

    def test_aws_ipv6_metadata_blocked_with_allow_private(self):
        is_safe, error = check_url_ssrf("http://[fd00:ec2::254]/", allow_private=True)
        assert is_safe is False
        assert "blocked" in error.lower()


class TestCallbackErrorNoInfoDisclosure:
    """Reviewer probe (#1546): the buyer-facing callback rejection must NOT leak the
    resolved IP or matched CIDR range — that error channel is an SSRF oracle.
    validate_callback_url returns a generic message; the detail goes to the log only.
    """

    def test_private_target_message_has_no_ip_or_range(self):
        import re

        from src.core.webhook_validator import WebhookURLValidator

        # Flag OFF + https scheme: passes the scheme gate, then resolves to a private
        # 10.x address — the branch whose detail string embeds the resolved IP.
        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=False),
            patch("src.core.security.url_validator._resolve_ips", return_value=["10.11.12.13"]),
        ):
            is_valid, error = WebhookURLValidator.validate_callback_url("https://internal.example.com/webhook")
        assert is_valid is False
        # No resolved IP octets and no CIDR range must appear in the buyer-facing string.
        assert "10.11.12.13" not in error
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", error), f"leaked an IP: {error!r}"
        assert "/" not in error, f"leaked a CIDR range: {error!r}"
        assert error == "URL failed SSRF validation"

    def test_metadata_target_message_has_no_ip_or_range(self):
        import re

        from src.core.webhook_validator import WebhookURLValidator

        with patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=True):
            is_valid, error = WebhookURLValidator.validate_callback_url("http://[fd00:ec2::254]/webhook")
        assert is_valid is False
        assert "fd00:ec2" not in error.lower()
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", error)
        assert error == "URL failed SSRF validation"


class TestValidateCallbackUrl:
    """The callback gate: strict by default (prod/staging/dev); private only via the
    dedicated ADCP_ALLOW_PRIVATE_WEBHOOKS opt-in (E2E harness) (#1512)."""

    def test_requires_https_by_default(self):
        """Any real deployment (flag off) requires HTTPS — not just production."""
        from src.core.webhook_validator import WebhookURLValidator

        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=False),
            patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]),
        ):
            is_valid, error = WebhookURLValidator.validate_callback_url("http://example.com/webhook")
        assert is_valid is False
        assert "https" in error.lower()

    def test_accepts_public_https_by_default(self):
        from src.core.webhook_validator import WebhookURLValidator

        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=False),
            patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]),
        ):
            is_valid, _ = WebhookURLValidator.validate_callback_url("https://example.com/webhook")
        assert is_valid is True

    def test_blocks_private_target_by_default(self):
        """A deployed staging/dev instance (flag off) must NOT accept private callbacks."""
        from src.core.webhook_validator import WebhookURLValidator

        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=False),
            patch("src.core.security.url_validator._resolve_ips", return_value=["10.0.0.5"]),
        ):
            is_valid, _ = WebhookURLValidator.validate_callback_url("https://internal.example.com/webhook")
        assert is_valid is False

    def test_flag_allows_localhost_http(self):
        """The E2E opt-in permits a loopback receiver over HTTP (round-1 regression)."""
        from src.core.webhook_validator import WebhookURLValidator

        with patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=True):
            is_valid, _ = WebhookURLValidator.validate_callback_url("http://localhost:8765/webhook")
        assert is_valid is True

    def test_flag_allows_private_compose_host(self):
        """The E2E opt-in reaches a compose receiver on a private (RFC-1918) address.

        The E2E runner exposes its receiver via the compose alias ``tests`` (a private
        172.x IP). This is permitted ONLY under the dedicated flag, never by default.
        """
        from src.core.webhook_validator import WebhookURLValidator

        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=True),
            patch("src.core.security.url_validator._resolve_ips", return_value=["172.20.0.5"]),
        ):
            is_valid, _ = WebhookURLValidator.validate_callback_url("http://tests:8765/webhook")
        assert is_valid is True

    def test_metadata_ip_blocked_even_with_flag(self):
        """The private opt-in must NOT extend to link-local/metadata targets."""
        from src.core.webhook_validator import WebhookURLValidator

        with patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=True):
            is_valid, _ = WebhookURLValidator.validate_callback_url("http://169.254.169.254/latest/meta-data")
        assert is_valid is False

    def test_metadata_hostname_blocked_even_with_flag(self):
        """The GCP metadata hostname is blocked regardless of the flag."""
        from src.core.webhook_validator import WebhookURLValidator

        with patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=True):
            is_valid, _ = WebhookURLValidator.validate_callback_url("http://metadata.google.internal/computeMetadata")
        assert is_valid is False

    def test_multi_record_hostname_blocked_if_any_record_is_private(self):
        """A hostname with one public and one private A record is rejected (#1512).

        Single-record gethostbyname would pass on the public record; validating ALL
        records catches the private/rebinding one.
        """
        from src.core.webhook_validator import WebhookURLValidator

        with (
            patch("src.core.webhook_validator._allow_private_webhook_targets", return_value=False),
            patch(
                "src.core.security.url_validator._resolve_ips",
                return_value=["93.184.216.34", "10.0.0.5"],
            ),
        ):
            is_valid, _ = WebhookURLValidator.validate_callback_url("https://rebind.example.com/webhook")
        assert is_valid is False


class TestBlockedHostnames:
    """BLOCKED_HOSTNAMES covers all known internal-alias patterns."""

    def test_localhost_in_blocked_hostnames(self):
        assert "localhost" in BLOCKED_HOSTNAMES

    def test_host_docker_internal_in_blocked_hostnames(self):
        assert "host.docker.internal" in BLOCKED_HOSTNAMES

    def test_gateway_docker_internal_in_blocked_hostnames(self):
        assert "gateway.docker.internal" in BLOCKED_HOSTNAMES

    def test_gcp_metadata_in_blocked_hostnames(self):
        assert "metadata.google.internal" in BLOCKED_HOSTNAMES

    def test_aws_metadata_ip_in_blocked_hostnames(self):
        assert "169.254.169.254" in BLOCKED_HOSTNAMES


class TestValidateAgentUrl:
    """validate_agent_url in media_buy_create validates format only (scheme + netloc).

    This function is called during approval processing against URLs already stored
    in the database, not against live user input. It validates structure, not
    network safety. SSRF protection for user-supplied URLs is enforced at the
    admin ingestion boundary in signals_agents.py via check_url_ssrf().
    """

    def test_none_rejected(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url(None) is False

    def test_empty_string_rejected(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("") is False

    def test_public_https_url_accepted(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("https://creatives.example.com/agent") is True

    def test_public_http_url_accepted(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("http://creatives.example.com/agent") is True

    def test_non_http_scheme_rejected(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("ftp://creatives.example.com") is False

    def test_missing_netloc_rejected(self):
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("https://") is False

    def test_unresolvable_hostname_accepted(self):
        """Format validation does not do DNS resolution — offline services are structurally valid."""
        from src.core.tools.media_buy_create import validate_agent_url

        assert validate_agent_url("https://not-deployed-yet.internal.example.com/agent") is True


def _make_signals_agent_client():
    """Create a Flask test client authenticated as super admin for signals agent endpoints."""
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["test_user"] = "test_super_admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["authenticated"] = True
    return client


def _mock_db_for_signals_add(mock_db, tenant_id="default"):
    """Wire mock_db so the add handler can query Tenant."""
    mock_tenant = MagicMock()
    mock_tenant.tenant_id = tenant_id
    mock_session = MagicMock()
    mock_session.scalars.return_value.first.return_value = mock_tenant
    mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_db.return_value.__exit__ = MagicMock(return_value=False)
    return mock_session


class TestSignalsAgentEndpointSSRFWiring:
    """Flask endpoint-level tests confirming check_url_ssrf() is wired into handlers.

    These tests exercise the actual POST /tenant/<id>/signals-agents/add and
    POST /tenant/<id>/signals-agents/<id>/edit endpoints so that removing or
    bypassing the check_url_ssrf() call in the handler would cause a real failure.
    """

    def test_add_endpoint_rejects_docker_internal_url(self):
        """POST /signals-agents/add with host.docker.internal URL must return a redirect with error flash."""
        client = _make_signals_agent_client()

        with patch("src.admin.blueprints.signals_agents.get_db_session") as mock_db:
            _mock_db_for_signals_add(mock_db)
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/signals-agents/add",
                    data={
                        "agent_url": "http://host.docker.internal:9999",
                        "name": "SSRF Test Agent",
                        "enabled": "on",
                        "timeout": "30",
                    },
                    follow_redirects=False,
                )

        # Must redirect back to add form (not to list — which would mean success)
        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")

    def test_add_endpoint_accepts_safe_public_url(self):
        """POST /signals-agents/add with a safe public URL must proceed past the SSRF check."""
        client = _make_signals_agent_client()

        with patch("src.admin.blueprints.signals_agents.get_db_session") as mock_db:
            mock_session = _mock_db_for_signals_add(mock_db)
            # Make session.add() and commit() no-ops
            mock_session.add = MagicMock()
            mock_session.commit = MagicMock()
            with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/signals-agents/add",
                        data={
                            "agent_url": "https://signals.example.com/agent",
                            "name": "Safe Agent",
                            "enabled": "on",
                            "timeout": "30",
                        },
                        follow_redirects=False,
                    )

        # Must redirect to list (success) — not back to add form
        assert response.status_code == 302
        assert "add" not in response.headers.get("Location", "")

    def test_edit_endpoint_rejects_unsafe_url_on_update(self):
        """POST /signals-agents/<id>/edit updating URL to host.docker.internal must be rejected.

        This is the exact scenario the reviewer asked about: editing from a safe URL
        to an unsafe one. The handler assigns agent.agent_url from the form value first,
        then validates it — so it is the new submitted value being checked.
        """
        client = _make_signals_agent_client()

        existing_agent = MagicMock()
        existing_agent.id = 1
        existing_agent.agent_url = "https://safe.example.com/agent"
        existing_agent.auth_credentials = None

        mock_session = MagicMock()
        mock_session.scalars.return_value.first.return_value = existing_agent

        with patch("src.admin.blueprints.signals_agents.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/signals-agents/1/edit",
                    data={
                        "agent_url": "http://host.docker.internal:9999",
                        "name": "Existing Agent",
                        "enabled": "on",
                        "timeout": "30",
                    },
                    follow_redirects=False,
                )

        # Must redirect back to edit form (not to list — which would mean success)
        assert response.status_code == 302
        assert "edit" in response.headers.get("Location", "")
        # Confirm the agent URL was NOT committed as the unsafe value
        mock_session.commit.assert_not_called()
