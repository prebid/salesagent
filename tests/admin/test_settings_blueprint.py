"""Integration tests for the settings admin blueprint.

Covers the security-sensitive slice of src/admin/blueprints/settings.py:
  - /domains/add, /domains/remove  — authorized_domains CRUD
  - /emails/add, /emails/remove    — authorized_emails CRUD
  - /approximated-token            — DNS widget token generation (external API)

Does NOT yet cover: /general, /adapter, /slack, /ai, /ai/test, /ai/models,
/business-rules. Those routes mix tenant config saves + external API calls
and warrant their own test file with richer mocking. Of
/approximated-domain-status|register|unregister only the tenant-ownership
refusal is covered here (see TestApproximatedDomainTenantOwnership); their
success paths still belong to that future file.

Uses factory-boy factories per tests/CLAUDE.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.admin.app import create_app
from src.core.database.models import Tenant
from tests.factories import TenantFactory

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def client():
    """Flask test client with CSRF disabled for POST testing."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


def _auth_session(client, tenant_id: str) -> None:
    """Populate a super-admin test-mode session."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


@pytest.fixture(autouse=True)
def _enable_test_mode(monkeypatch):
    """Enable global test auth so require_tenant_access accepts the test session."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")


class TestAuthorizedDomainsAdd:
    """POST /tenant/<id>/settings/domains/add — appends to authorized_domains."""

    def test_add_domain_appends_to_list(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "new-domain.com"},
        )
        # Flash-based endpoint — always redirects back to tenant settings.
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "new-domain.com" in refreshed.authorized_domains

    def test_add_domain_rejects_missing_field(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={},  # no 'domain' field
        )
        assert response.status_code == 302  # redirects with flash error

        # Guardrail: the authorized_domains list must NOT have grown.
        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert refreshed.authorized_domains == ["example.com"]

    def test_add_domain_rejects_invalid_format(self, client, factory_session):
        """Missing '.' or containing '@' must be rejected."""
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "not-a-domain"},  # no '.'
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "not-a-domain" not in refreshed.authorized_domains

    def test_add_domain_is_idempotent(self, client, factory_session):
        """Adding an already-present domain must not duplicate it."""
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "example.com"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        # Domain appears exactly once — no duplicate appended.
        assert refreshed.authorized_domains.count("example.com") == 1


class TestAuthorizedDomainsSuperAdminHijackGuard:
    """Security guard: refuse to add the super-admin domain to any tenant."""

    def test_refuses_to_add_super_admin_domain(self, client, factory_session, monkeypatch):
        monkeypatch.setenv("SUPER_ADMIN_DOMAIN", "admin-controlled.example")
        tenant = TenantFactory(authorized_domains=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "admin-controlled.example"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "admin-controlled.example" not in (refreshed.authorized_domains or [])


class TestAuthorizedDomainsRemove:
    """POST /tenant/<id>/settings/domains/remove — drops from authorized_domains."""

    def test_remove_existing_domain(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com", "other.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/remove",
            data={"domain": "other.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "other.com" not in refreshed.authorized_domains
        assert "example.com" in refreshed.authorized_domains

    def test_remove_missing_field(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/remove",
            data={},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        # No change — list intact.
        assert refreshed.authorized_domains == ["example.com"]


class TestAuthorizedEmailsAdd:
    """POST /tenant/<id>/settings/emails/add — appends to authorized_emails."""

    def test_add_email_appends_to_list(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/add",
            data={"email": "new-user@example.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "new-user@example.com" in refreshed.authorized_emails

    def test_add_email_rejects_malformed(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/add",
            data={"email": "not-an-email"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "not-an-email" not in (refreshed.authorized_emails or [])


class TestAuthorizedEmailsRemove:
    """POST /tenant/<id>/settings/emails/remove — drops from authorized_emails."""

    def test_remove_existing_email(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=["test@example.com", "other@example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/remove",
            data={"email": "other@example.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "other@example.com" not in refreshed.authorized_emails
        assert "test@example.com" in refreshed.authorized_emails


class TestApproximatedToken:
    """POST /tenant/<id>/settings/approximated-token — DNS widget token.

    This endpoint handles API-key-backed external requests (Approximated
    DNS service). Tests exercise the gate behaviors without making real
    network calls.
    """

    def test_returns_500_when_api_key_missing(self, client, factory_session, monkeypatch):
        """Without APPROXIMATED_API_KEY in env, route returns 500 + error JSON."""
        monkeypatch.delenv("APPROXIMATED_API_KEY", raising=False)
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")
        assert response.status_code == 500
        body = response.get_json()
        assert body["success"] is False
        assert "not configured" in body["error"].lower()

    def test_returns_404_when_tenant_not_found(self, client, factory_session, monkeypatch):
        """Requires a real tenant to back the token request."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        # Auth against a real tenant (super-admin bypasses tenant scoping).
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post("/tenant/nonexistent_tenant/settings/approximated-token")
        assert response.status_code == 404
        body = response.get_json()
        assert body["success"] is False

    def test_returns_token_on_success(self, client, factory_session, monkeypatch):
        """Happy path: Approximated API returns 200 → endpoint forwards the token."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        monkeypatch.setenv("APPROXIMATED_PROXY_IP", "10.0.0.99")

        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        # Patch the SEAM, not get_dns_token: the api-key header is built inside
        # approximated_client._api now, and the security assertion below is
        # precisely that the key travels as a header. Doubling get_dns_token
        # would patch out the code under test and the assertion would grade
        # nothing. Lives in src.services.approximated_client since
        # GH #1802 moved the client out of this blueprint.
        mock_result = MagicMock()
        mock_result.json.return_value = {"token": "opaque-widget-token-123"}

        with patch("src.services.approximated_client.send", return_value=mock_result) as mock_get:
            # The client's own return type, graded directly: the route-level
            # assertions below are byte-identical whether get_dns_token hands
            # back a raw dict or a typed outcome, so they cannot grade the type
            # at all. Driven BEFORE the route so mock_get.call_args below still
            # belongs to the route's call, not this one.
            from src.services.approximated_client import get_dns_token

            dns = get_dns_token("fake-api-key")
            assert not isinstance(dns, dict), (
                "get_dns_token must hand back a typed outcome, not an open dict a caller reads "
                f"with .get() -- got {dns!r}"
            )

            from src.services.approximated_client import DnsToken

            assert dns == DnsToken(token="opaque-widget-token-123")

            response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")

        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        assert body["token"] == "opaque-widget-token-123"
        assert body["proxy_ip"] == "10.0.0.99"

        # Security: API key must be sent in the request header, not leaked in body.
        called_kwargs = mock_get.call_args.kwargs
        assert called_kwargs["headers"]["api-key"] == "fake-api-key"

    def test_propagates_upstream_error(self, client, factory_session, monkeypatch):
        """Non-200 from Approximated → endpoint surfaces the upstream status."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        # The seam raises on a non-2xx and discards the response, so the upstream
        # status now arrives on the typed failure rather than on a returned object.
        # get_dns_token (src.services.approximated_client, imported into this
        # blueprint since GH #1802) never catches OutboundError -- every
        # status it can receive is a genuine failure -- so the exception reaches
        # this route's own except OutboundError arm unchanged.
        from src.core.security.outbound_http import OutboundDeliveryFailed

        with patch(
            "src.admin.blueprints.settings.get_dns_token",
            side_effect=OutboundDeliveryFailed(attempts=1, http_status=401),
        ):
            response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")

        assert response.status_code == 401
        body = response.get_json()
        assert body["success"] is False


class TestApproximatedDomainTenantOwnership:
    """The three Approximated domain routes must refuse a domain the tenant does not own.

    Root B / SF4 (CodeQL 909, 910, 911): the ownership predicate lives inside ONE
    handler (``register_approximated_domain``), so the sibling handlers omit it and
    dial the vendor with an attacker-named domain. Parametrized over all three
    routes because the gap is per-route: a grader that covered only ``unregister``
    would let the identical hole in ``/approximated-domain-status`` stay open, and
    would not grade that the two ungated routes answer with the SAME 400 shape the
    register route already returns.

    Both halves are asserted, per the lane: the 400 + the existing message
    contract, AND that the egress seam was never touched. Status-code-only would
    pass against a route that refuses AFTER dialling the vendor — which is the
    exact defect (the domain reaches Approximated in the URL path / request body
    before anything checks who owns it).
    """

    ROUTES = [
        "approximated-domain-status",
        "approximated-register-domain",
        "approximated-unregister-domain",
    ]

    @pytest.mark.parametrize("route", ROUTES)
    def test_refuses_domain_owned_by_another_tenant_without_dialling_vendor(
        self, client, factory_session, monkeypatch, route
    ):
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")

        victim = TenantFactory(virtual_host="victim.example.com")
        attacker = TenantFactory(virtual_host="attacker.example.com")
        factory_session.commit()
        _auth_session(client, attacker.tenant_id)

        # Patch the SEAM (same idiom as TestApproximatedToken above): every
        # Approximated call goes through src.services.approximated_client.send,
        # so "was never called" is the whole-vendor-silence assertion. A vendor
        # response is stocked deliberately — if a route DOES dial, it succeeds
        # and answers 200, so this test fails on the contract, not on a crash.
        vendor_response = MagicMock()
        vendor_response.json.return_value = {
            "data": {"status": "ACTIVE_SSL", "has_ssl": True, "target_address": "adcp-sales-agent.fly.dev"}
        }

        with patch("src.services.approximated_client.send", return_value=vendor_response) as mock_send:
            response = client.post(
                f"/tenant/{attacker.tenant_id}/settings/{route}",
                json={"domain": victim.virtual_host},
            )

        assert response.status_code == 400, (
            f"/{route} accepted {victim.virtual_host}, which belongs to tenant "
            f"{victim.tenant_id}, from tenant {attacker.tenant_id} "
            f"(virtual_host={attacker.virtual_host})"
        )
        assert response.get_json() == {
            "success": False,
            "error": "Domain must match tenant's virtual_host",
        }
        mock_send.assert_not_called()

    @pytest.mark.parametrize("route", ROUTES)
    def test_permits_the_tenants_own_domain_and_dials_the_vendor(self, client, factory_session, monkeypatch, route):
        """The permit leg, which the refusal test alone cannot grade.

        Without this, a predicate mutated to refuse EVERYTHING leaves the whole
        repo green: the refusal test passes harder, and nothing anywhere asserts
        that a tenant can still operate on its own domain. A gate that refuses
        every request is as broken as one that refuses none, and it is the
        failure mode a security fix actually tends to ship.

        Asserts the seam WAS reached, which is the same observation the refusal
        test makes in the negative, off the same patch point.
        """
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")

        owner = TenantFactory(virtual_host="owner.example.com")
        factory_session.commit()
        _auth_session(client, owner.tenant_id)

        vendor_response = MagicMock()
        vendor_response.json.return_value = {
            "data": {"status": "ACTIVE_SSL", "has_ssl": True, "target_address": "adcp-sales-agent.fly.dev"}
        }

        with patch("src.services.approximated_client.send", return_value=vendor_response) as mock_send:
            response = client.post(
                f"/tenant/{owner.tenant_id}/settings/{route}",
                json={"domain": owner.virtual_host},
            )

        assert response.status_code == 200, (
            f"/{route} refused {owner.virtual_host}, which IS tenant {owner.tenant_id}'s "
            f"virtual_host — the gate is refusing its own tenant: {response.get_json()}"
        )
        assert response.get_json()["success"] is True

        # Not a bare assert_called_once: assert WHICH domain was dialled with.
        # "the seam was reached" alone would also pass if the route carried some
        # other tenant's domain to the vendor, which is the very thing this pair
        # grades. status/unregister put the domain in the URL path; register puts
        # it in the JSON body -- so the assertion looks at the whole call.
        assert mock_send.call_count == 1, f"expected exactly one vendor dial, got {mock_send.call_count}"
        call = mock_send.call_args
        dialled = f"{call.args[0]} {call.kwargs.get('json')}"
        assert owner.virtual_host in dialled, (
            f"/{route} reached the vendor without naming {owner.virtual_host}: {dialled!r}"
        )
