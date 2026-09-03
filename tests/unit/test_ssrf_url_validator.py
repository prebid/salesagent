"""Unit tests for SSRF-adjacent URL handling (F-04).

``TestCheckUrlSsrf`` and ``TestBlockedHostnames`` — the direct tests of
``check_url_ssrf``/``BLOCKED_HOSTNAMES`` — are DELETED: that module
(``src.core.security.url_validator``) no longer exists (GH #1802).
Every row's behavioral content migrated into
``tests/integration/test_outbound_http.py``'s verdict-parity table (see that
file's ``EgressPolicy.check_registration`` cases), which grades the SAME
address predicate now shared by both the registration and dial verdicts —
triaged row-by-row so nothing was silently dropped:

- localhost / metadata / docker / gateway-docker hostname rows -> the
  blocked-hostname parity rows.
- loopback, RFC1918 x3, link-local, 224.0.0.1, ``[ff02::1]``,
  ``[64:ff9b::...]`` -> the reserved-range parity rows.
- CGNAT literal -> the supplement-range parity rows (this is the row that
  used to pass ONLY because ``url_validator.BLOCKED_NETWORKS`` covered it —
  now covered by the shared predicate on both verdicts, closing
  GH #1802).
- non-http / file scheme / require_https -> the non-https parity rows.
- ``valid_public_https_url_accepted`` -> the accepted half of the
  unresolvable-hostname divergence case.
- ``test_unresolvable_hostname_rejected`` (the ``resolve_dns=True`` DNS
  branch — dropped as dead code, one production caller, always
  ``resolve_dns=False``) -> its live semantics are preserved by the DIAL half
  of that same divergence case: an unresolvable host IS refused, via the
  SDK's single pinned resolution.
- ``valid_public_http_url_accepted`` (``require_https=False``) -> genuinely
  dead; the one production caller always passed ``require_https=True``.
  Nothing to migrate.

Covers:
- validate_agent_url: media_buy_create wrapper (format-only, unrelated to the
  address-policy migration above)
- Flask endpoint-level wiring for signals agents add/edit handlers (routes
  through ``src.admin.utils.url_policy`` -> ``outbound_http.validate_url``,
  never called ``check_url_ssrf`` directly)
"""

import os
from unittest.mock import MagicMock, patch


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


# A public, non-reserved address as a literal: the ingest gate's verdict on it is
# decided entirely by address policy, with no DNS lookup to go missing offline.
# (The mirror image of the reject cases' 169.254.169.254 / host.docker.internal.)
SAFE_PUBLIC_URL = "https://93.184.216.34/agent"


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
    """Flask endpoint-level tests confirming the ingest egress gate is wired into handlers.

    These tests exercise the actual POST /tenant/<id>/signals-agents/add and
    POST /tenant/<id>/signals-agents/<id>/edit endpoints so that removing or
    bypassing the ingest check in the handler would cause a real failure. The
    handlers no longer call check_url_ssrf() directly: they go through
    ``src.admin.utils.url_policy`` -> ``src.core.security.outbound_http.validate_url``,
    whose address policy is the live ``adcp.signing`` validator. The accepted URL is
    therefore a public IP literal rather than a fixture hostname: nothing here patches
    a resolver any more, so a hostname would make the accept case depend on this
    machine's DNS, and the verdict must be a pure address-policy fact.
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
        """POST /signals-agents/add with a safe public URL must proceed past the SSRF check.

        ``SAFE_PUBLIC_URL`` is an https IP literal in public space, so the gate's verdict
        needs no DNS and the agent row really is created — the redirect goes to the list.
        """
        client = _make_signals_agent_client()

        with patch("src.admin.blueprints.signals_agents.get_db_session") as mock_db:
            mock_session = _mock_db_for_signals_add(mock_db)
            # Make session.add() and commit() no-ops
            mock_session.add = MagicMock()
            mock_session.commit = MagicMock()
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/signals-agents/add",
                    data={
                        "agent_url": SAFE_PUBLIC_URL,
                        "name": "Safe Agent",
                        "enabled": "on",
                        "timeout": "30",
                    },
                    follow_redirects=False,
                )

        # Must redirect to list (success) — not back to add form
        assert response.status_code == 302
        assert "add" not in response.headers.get("Location", "")
        # And the row was actually written — proving it reached the endpoint-add path,
        # not merely that it failed somewhere past the gate. Assert on WHAT was added,
        # not just that add() fired: a bare assert_called_once() would stay green if a
        # regression wrote the wrong row.
        #
        # ONE assertion, not a count-then-dissect pair. The earlier form —
        # assert_called_once_with(ANY) followed by a call_args read — pinned the count
        # and nothing about the argument, which is the split shape the weak-mock guard
        # bans; routing it through ANY only disguised it.
        assert len(mock_session.add.call_args_list) == 1, (
            f"expected exactly one row to be added, got {mock_session.add.call_args_list!r}"
        )
        added = mock_session.add.call_args.args[0]
        assert str(getattr(added, "agent_url", None)) == SAFE_PUBLIC_URL, (
            f"the persisted row must carry the URL that passed the gate; got {added!r}"
        )
        mock_session.commit.assert_called_once_with()

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
