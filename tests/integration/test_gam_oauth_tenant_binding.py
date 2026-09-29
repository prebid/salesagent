"""GAM OAuth binds a refresh token only to the tenant the caller's own session authorized.

https://github.com/prebid/salesagent/issues/2205: ``GET /auth/gam/callback`` took the
tenant from the ``state`` query parameter when the session held none, so anyone who
completed a Google consent could name a victim tenant and replace its ad-server
credential. ``GET /auth/gam/authorize/<tenant_id>`` checked that the tenant existed,
not that the caller belonged to it.

Every denial is graded on what was persisted and on whether Google was asked to
exchange the code — a response-only assertion passes against the vulnerable code.
No ``ADCP_AUTH_TEST_MODE`` and no ``test_user`` in the session: a pass here proves
membership was checked, not bypassed.
"""

from urllib.parse import urlsplit

import pytest

from src.admin.blueprints import auth as auth_view
from src.core.database.models import AdapterConfig, Tenant
from src.services.google_oauth_client import GoogleTokenResponse
from tests.factories import TenantFactory, UserFactory
from tests.integration.gam_oauth_helpers import AUTH_CODE, authorize, callback, log_in, start_flow

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REFRESH_TOKEN = "1//refresh-token-from-google"
# TenantFactory's ``ad_server`` default, no ``adapter_config`` row, so no token.
_UNTOUCHED = ("mock", False, None)


@pytest.fixture
def token_exchange(monkeypatch) -> list[str]:
    """Stand in for Google's token endpoint; returns the list of codes it was handed."""
    codes: list[str] = []

    def exchange(code: str, **_: str) -> GoogleTokenResponse:
        codes.append(code)
        return GoogleTokenResponse(refresh_token=_REFRESH_TOKEN)

    monkeypatch.setattr(auth_view, "exchange_authorization_code", exchange)
    return codes


@pytest.fixture
def own_tenant(bound_factory_session) -> Tenant:
    return TenantFactory()


@pytest.fixture
def other_tenant(bound_factory_session) -> Tenant:
    return TenantFactory()


@pytest.fixture
def member(own_tenant):
    """An active admin of ``own_tenant`` and of nothing else."""
    return UserFactory(tenant=own_tenant, role="admin")


def _persisted(session, tenant_id: str) -> tuple[str, bool, str | None]:
    """``(tenant.ad_server, an adapter_config row exists, its gam_refresh_token)`` as stored right now.

    Row existence is graded separately from the token: a callback that inserts the
    row and only then denies has still written to the tenant.
    """
    session.expire_all()
    adapter_config = session.get(AdapterConfig, tenant_id)
    return (
        session.get(Tenant, tenant_id).ad_server,
        adapter_config is not None,
        adapter_config.gam_refresh_token if adapter_config else None,
    )


# --- /auth/gam/authorize/<tenant_id> -----------------------------------------------------


def test_authorize_without_a_session_is_sent_to_login(admin_client, own_tenant, gam_oauth_configured):
    """AC4: an anonymous caller is not started on the flow."""
    response = authorize(admin_client, own_tenant.tenant_id)

    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == f"/tenant/{own_tenant.tenant_id}/login"


def test_authorize_by_a_non_member_is_forbidden(admin_client, member, other_tenant, gam_oauth_configured):
    """AC4: being logged in somewhere is not membership in the tenant named by the URL."""
    log_in(admin_client, member)

    assert authorize(admin_client, other_tenant.tenant_id).status_code == 403


# --- /auth/gam/callback ------------------------------------------------------------------


def test_member_connects_gam_with_the_state_it_was_issued(
    admin_client, member, own_tenant, gam_oauth_configured, token_exchange, bound_factory_session
):
    """AC5: authorize, come back with the state Google echoes, and the token is stored."""
    log_in(admin_client, member)
    state = start_flow(admin_client, own_tenant.tenant_id)

    response = callback(admin_client, state)

    assert urlsplit(response.headers["Location"]).path == f"/tenant/{own_tenant.tenant_id}/settings"
    assert token_exchange == [AUTH_CODE]
    assert _persisted(bound_factory_session, own_tenant.tenant_id) == ("google_ad_manager", True, _REFRESH_TOKEN)


@pytest.mark.parametrize("named", ["own", "other"], ids=["own tenant id", "other tenant id"])
def test_callback_without_a_started_flow_writes_nothing(
    named, admin_client, member, own_tenant, other_tenant, gam_oauth_configured, token_exchange, bound_factory_session
):
    """AC1: a logged-in member who never started the flow names a tenant in ``state``.

    A victim's tenant is the attack in the ticket. The member's own tenant is the
    login-CSRF variant: with no issued state to compare against, membership alone
    must not let ``state`` supply the tenant.
    """
    log_in(admin_client, member)

    callback(admin_client, {"own": own_tenant, "other": other_tenant}[named].tenant_id)

    assert _persisted(bound_factory_session, own_tenant.tenant_id) == _UNTOUCHED
    assert _persisted(bound_factory_session, other_tenant.tenant_id) == _UNTOUCHED
    assert token_exchange == []


@pytest.mark.parametrize("forged", ["own", "other"], ids=["own tenant id", "other tenant id"])
def test_callback_with_a_state_the_session_was_not_issued_writes_nothing(
    forged, admin_client, member, own_tenant, other_tenant, gam_oauth_configured, token_exchange, bound_factory_session
):
    """AC3: ``state`` is compared with the issued value, never read as a tenant id.

    The session did start the flow for ``own_tenant``; what comes back is a tenant
    id in place of the issued state — the member's own (guessable) or a victim's.
    Neither may bind a token to either tenant.
    """
    log_in(admin_client, member)
    start_flow(admin_client, own_tenant.tenant_id)

    callback(admin_client, {"own": own_tenant, "other": other_tenant}[forged].tenant_id)

    assert _persisted(bound_factory_session, own_tenant.tenant_id) == _UNTOUCHED
    assert _persisted(bound_factory_session, other_tenant.tenant_id) == _UNTOUCHED
    assert token_exchange == []


def test_callback_without_a_session_writes_nothing(
    admin_client, member, own_tenant, gam_oauth_configured, token_exchange, bound_factory_session
):
    """AC2: a state genuinely issued to a member is worthless without that member's session."""
    log_in(admin_client, member)
    state = start_flow(admin_client, own_tenant.tenant_id)

    callback(admin_client.application.test_client(), state)

    assert _persisted(bound_factory_session, own_tenant.tenant_id) == _UNTOUCHED
    assert token_exchange == []
