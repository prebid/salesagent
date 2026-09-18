"""Unit tests for SSRF-adjacent URL handling (F-04).

``TestCheckUrlSsrf`` and ``TestBlockedHostnames`` — the direct tests of
``check_url_ssrf``/``BLOCKED_HOSTNAMES`` — are DELETED. The module
``src.core.security.url_validator`` is GONE entirely: it first lost the SSRF
half (salesagent-tbrk.1 / GH #1802) — ``check_url_ssrf``, ``check_url_syntax``,
``BLOCKED_NETWORKS``, ``BLOCKED_HOSTNAMES`` — and its surviving reserved-TLD
family then moved to its one remaining owner,
``src.core.security.egress.policy`` (``RESERVED_TLDS`` /
``reserved_tld_for_host`` / ``is_reserved_tld_host``), which is where
``TestReservedTldPolicy`` below grades it. The policy VERDICT graded here is
unchanged by that move — only the import path is.
Every deleted row's behavioral content migrated into
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
  the gap recorded in #1792).
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

One more class is RETIRED rather than migrated, and for a different reason —
its subject is gone too, but it had no address-policy content to move:

- ``TestValidateAgentUrl`` (7 rows) tested
  ``src.core.tools.media_buy_create.validate_agent_url``, a format-only
  (scheme + netloc) pre-check. That function no longer exists anywhere in
  ``src/``: ``media_buy_create`` now hands the creative-agent URL straight to
  the seam with ``CounterpartyUrl`` provenance
  (``media_buy_create.py`` lines 418 / 702 / 718), so the real verdict — not a
  structural guess — is what the buyer gets. A format-only accept is no longer
  a behavior this codebase has, so there is nothing to retarget; the refusal
  that replaced it is graded in
  ``tests/integration/test_creative_agent_url_ingest_refusal.py``.

Covers:
- reserved-TLD policy: the surviving half of the retired ``url_validator``, now
  owned by ``src.core.security.egress.policy``
  (RFC 9421 notification-proof policy — "can an endpoint under this host ever be
  PROVEN?" — which the egress seam has no notion of and deliberately does not
  want)
- Flask endpoint-level wiring for signals agents add/edit handlers (routes
  through ``src.admin.utils.url_policy`` -> ``outbound_http.validate_url``,
  never called ``check_url_ssrf`` directly)
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestReservedTldPolicy:
    """The half of the retired ``url_validator`` that SURVIVED #1802 still refuses.

    This is notification-proof policy, not address policy: it answers "can an
    endpoint under this hostname ever be PROVEN?", which the egress seam has no
    notion of — a ``.invalid`` host passes ``check_registration`` on its own
    terms. So this obligation has no home in the seam's parity table and keeps
    its owner here, where the module's own unit tests live.

    Deliberately narrow: ``tests/unit/test_architecture_reserved_tld_single_matcher.py``
    grades the *structural* rule (one matcher, boolean delegates to it, callers
    never re-match the frozenset). What is graded here is the policy VERDICT the
    F-04 file has always been about — which hosts get refused, which get through.
    """

    def test_dot_test_host_refused(self):
        from src.core.security.egress.policy import is_reserved_tld_host, reserved_tld_for_host

        assert reserved_tld_for_host("acme.test") == ".test"
        assert is_reserved_tld_host("acme.test") is True

    def test_dot_invalid_host_refused(self):
        from src.core.security.egress.policy import is_reserved_tld_host, reserved_tld_for_host

        assert reserved_tld_for_host("no-such-host.invalid") == ".invalid"
        assert is_reserved_tld_host("no-such-host.invalid") is True

    def test_normal_public_host_passes(self):
        """The non-vacuity half: a refusal that refuses everything grades nothing.

        ``signals.example.com`` is the trap spelling — ``.example`` IS reserved,
        but only as a TLD, and a call-site ``endswith(".example")`` that this
        owner exists to replace would get this one right while getting
        ``Acme.TEST`` and ``acme.test.`` wrong.
        """
        from src.core.security.egress.policy import is_reserved_tld_host, reserved_tld_for_host

        for host in ("signals.example.com", "creatives.example.com", "93.184.216.34"):
            assert reserved_tld_for_host(host) is None, host
            assert is_reserved_tld_host(host) is False, host


# A public, non-reserved address as a literal: the ingest gate's verdict on it is
# decided entirely by address policy, with no DNS lookup to go missing offline.
# (The mirror image of the reject cases' 169.254.169.254 / host.docker.internal.)
SAFE_PUBLIC_URL = "https://93.184.216.34/agent"

# The URL both reject-path wiring tests submit. Named once so the seam-linkage
# test below and the endpoint tests cannot drift onto different vectors — a
# redirect proves the handler refused, not WHY, and the two only compose into
# "the seam is what refused it" while they are the same URL.
BLOCKED_INGEST_URL = "http://host.docker.internal:9999"


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
    handlers no longer call ``check_url_ssrf`` directly: they go through
    ``src.admin.utils.url_policy`` -> ``src.core.security.outbound_http.validate_url``,
    whose address policy is the live ``adcp.signing`` validator. The accepted URL is
    therefore a public IP literal rather than a fixture hostname: nothing here patches
    a resolver any more, so a hostname would make the accept case depend on this
    machine's DNS, and the verdict must be a pure address-policy fact.
    """

    def test_the_seam_raises_on_the_url_the_endpoints_reject(self):
        """The redirect assertions below are caused by the seam REFUSING, not by luck.

        A 302-back-to-the-form is what this handler does for any failure at all, so
        on its own it grades "something went wrong", not "egress policy said no".
        This pins the missing half: for the exact URL those tests submit, the seam
        raises ``OutboundRequestBlocked`` — a raise, not a ``(bool, str)`` verdict,
        which is the contract change ``check_url_ssrf`` -> ``validate_url`` made.

        The refusal is also asserted to be OPAQUE (AdCP 3.1.1,
        ``building/by-layer/L1/security.mdx`` point 6): the sentence handed back
        names neither the resolved address nor which rule fired, because the party
        that supplied the URL would otherwise have a host and port scanner. The
        general form of that rule is owned by
        ``tests/integration/test_outbound_http.py``; what is graded here is that THIS
        file's vector obeys it on the way to the operator.
        """
        from src.core.security.outbound_http import OutboundRequestBlocked, validate_url

        with pytest.raises(OutboundRequestBlocked) as blocked:
            validate_url(BLOCKED_INGEST_URL)

        message = str(blocked.value)
        for leak in ("host.docker.internal", "9999", "scheme", "https", "resolve failed"):
            assert leak not in message, f"refusal echoed {leak!r} back to the URL's supplier: {message!r}"

        # Non-vacuity: the same call admits the URL the accept-path test submits, so
        # the raise above is a verdict on the vector and not a blanket refusal.
        assert validate_url(SAFE_PUBLIC_URL) is None

    def test_add_endpoint_rejects_docker_internal_url(self):
        """POST /signals-agents/add with host.docker.internal URL must return a redirect with error flash."""
        client = _make_signals_agent_client()

        with patch("src.admin.blueprints.signals_agents.get_db_session") as mock_db:
            _mock_db_for_signals_add(mock_db)
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/signals-agents/add",
                    data={
                        "agent_url": BLOCKED_INGEST_URL,
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
                        "agent_url": BLOCKED_INGEST_URL,
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
