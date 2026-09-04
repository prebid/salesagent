"""Authorization regression tests for publisher-partner admin JSON routes.

All five tenant-scoped routes must return JSON 401/403 from
``require_tenant_access(api_mode=True)``. Rejected mutations must not
alter stored partners. Authorized same-tenant members (active ``User``
row, not the super-admin/test-mode bypass) continue to succeed.

Uses factory-boy factories per ``tests/CLAUDE.md`` — no inline
``session.add()`` / ``get_db_session()`` in test bodies.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from adcp.exceptions import AdagentsNotFoundError
from flask.testing import FlaskClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.admin.app import create_app
from src.core.database.models import PublisherPartner
from tests.factories import PublisherPartnerFactory, TenantFactory, UserFactory

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

CREATE_PAYLOAD = {"publisher_domain": "unauthorized-add.example", "display_name": "Should Not Persist"}

# (id, method, path suffix relative to /publisher-partners, JSON body or None)
_ROUTES: list[tuple[str, str, str, dict[str, str] | None]] = [
    ("list", "GET", "", None),
    ("create", "POST", "", CREATE_PAYLOAD),
    ("delete", "DELETE", "/{partner_id}", None),
    ("sync", "POST", "/sync", None),
    ("properties", "GET", "/{partner_id}/properties", None),
]


@pytest.fixture
def client() -> Iterator[FlaskClient]:
    """Flask test client with CSRF disabled."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as test_client:
        yield test_client


def _member_session(client: FlaskClient, tenant_id: str, email: str) -> None:
    """Authenticated session for a normal tenant member.

    Omits ``test_user`` / ``test_user_role`` so ``require_tenant_access``
    uses the ``User`` membership lookup instead of the test-mode bypass.
    """
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": email, "is_super_admin": False}
        sess["email"] = email
        sess["tenant_id"] = tenant_id


def _partners_url(tenant_id: str, suffix: str = "") -> str:
    return f"/tenant/{tenant_id}/publisher-partners{suffix}"


def _dispatch(client: FlaskClient, method: str, url: str, payload: dict[str, str] | None):
    if method == "GET":
        return client.get(url)
    if method == "POST":
        return client.post(url, json=payload or {})
    if method == "DELETE":
        return client.delete(url)
    raise AssertionError(f"unexpected method {method}")


def _partner_count(session: Session, tenant_id: str) -> int:
    return session.scalar(select(func.count()).select_from(PublisherPartner).filter_by(tenant_id=tenant_id)) or 0


def _partner_snapshot(session: Session, partner_id: int) -> tuple[Any, ...] | None:
    partner = session.get(PublisherPartner, partner_id)
    if partner is None:
        return None
    return (
        partner.id,
        partner.tenant_id,
        partner.publisher_domain,
        partner.display_name,
        partner.is_verified,
        partner.sync_status,
        partner.sync_error,
        partner.last_synced_at,
    )


def _seed_target_tenant(factory_session: Session) -> tuple[str, int, int, tuple[Any, ...] | None]:
    """Create a tenant with one partner. Returns ids plus a stored-data snapshot."""
    tenant = TenantFactory()
    partner = PublisherPartnerFactory(tenant=tenant, publisher_domain="existing.example")
    tenant_id = tenant.tenant_id
    partner_id = partner.id
    count = _partner_count(factory_session, tenant_id)
    snapshot = _partner_snapshot(factory_session, partner_id)
    return tenant_id, partner_id, count, snapshot


def _assert_partners_unchanged(
    factory_session: Session,
    tenant_id: str,
    partner_id: int,
    count: int,
    snapshot: tuple[Any, ...] | None,
) -> None:
    factory_session.expire_all()
    assert _partner_count(factory_session, tenant_id) == count
    assert _partner_snapshot(factory_session, partner_id) == snapshot
    added = factory_session.scalars(
        select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain=CREATE_PAYLOAD["publisher_domain"])
    ).first()
    assert added is None


def _route_url(tenant_id: str, suffix_template: str, partner_id: int) -> str:
    return _partners_url(tenant_id, suffix_template.format(partner_id=partner_id))


class TestPublisherPartnersAnonymousDenied:
    """Unauthenticated callers receive JSON 401 and cannot mutate partners."""

    @pytest.mark.parametrize("route_id,method,suffix,payload", _ROUTES, ids=[r[0] for r in _ROUTES])
    def test_anonymous_receives_json_401(
        self,
        client: FlaskClient,
        factory_session: Session,
        route_id: str,
        method: str,
        suffix: str,
        payload: dict[str, str] | None,
    ) -> None:
        tenant_id, partner_id, count, snapshot = _seed_target_tenant(factory_session)

        response = _dispatch(client, method, _route_url(tenant_id, suffix, partner_id), payload)

        assert response.status_code == 401
        assert response.get_json() == {"error": "Authentication required"}
        _assert_partners_unchanged(factory_session, tenant_id, partner_id, count, snapshot)


class TestPublisherPartnersOtherTenantDenied:
    """Authenticated members of another tenant receive JSON 403 and cannot mutate."""

    @pytest.mark.parametrize("route_id,method,suffix,payload", _ROUTES, ids=[r[0] for r in _ROUTES])
    def test_other_tenant_receives_json_403(
        self,
        client: FlaskClient,
        factory_session: Session,
        route_id: str,
        method: str,
        suffix: str,
        payload: dict[str, str] | None,
    ) -> None:
        tenant_id, partner_id, count, snapshot = _seed_target_tenant(factory_session)
        other_tenant = TenantFactory()
        other_user = UserFactory(tenant=other_tenant)
        _member_session(client, other_tenant.tenant_id, other_user.email)

        response = _dispatch(client, method, _route_url(tenant_id, suffix, partner_id), payload)

        assert response.status_code == 403
        assert response.get_json() == {"error": "Access denied"}
        _assert_partners_unchanged(factory_session, tenant_id, partner_id, count, snapshot)


class TestPublisherPartnersAuthorizedMember:
    """Active same-tenant User membership is enough; super-admin bypass is not used."""

    def test_get_list_returns_partners(self, client: FlaskClient, factory_session: Session) -> None:
        tenant = TenantFactory()
        partner = PublisherPartnerFactory(tenant=tenant, publisher_domain="listed.example")
        user = UserFactory(tenant=tenant)
        _member_session(client, tenant.tenant_id, user.email)

        response = client.get(_partners_url(tenant.tenant_id))

        assert response.status_code == 200
        body = response.get_json()
        assert body["total"] == 1
        assert body["partners"][0]["id"] == partner.id
        assert body["partners"][0]["publisher_domain"] == "listed.example"

    def test_post_creates_partner(self, client: FlaskClient, factory_session: Session) -> None:
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant)
        _member_session(client, tenant.tenant_id, user.email)

        response = client.post(
            _partners_url(tenant.tenant_id),
            json={"publisher_domain": "created.example", "display_name": "Created"},
        )

        assert response.status_code == 201
        body = response.get_json()
        assert body["publisher_domain"] == "created.example"
        factory_session.expire_all()
        stored = factory_session.scalars(
            select(PublisherPartner).filter_by(tenant_id=tenant.tenant_id, publisher_domain="created.example")
        ).first()
        assert stored is not None
        assert stored.display_name == "Created"

    def test_delete_removes_partner(self, client: FlaskClient, factory_session: Session) -> None:
        tenant = TenantFactory()
        partner = PublisherPartnerFactory(tenant=tenant, publisher_domain="doomed.example")
        partner_id = partner.id
        user = UserFactory(tenant=tenant)
        _member_session(client, tenant.tenant_id, user.email)

        response = client.delete(_partners_url(tenant.tenant_id, f"/{partner_id}"))

        assert response.status_code == 200
        assert response.get_json() == {"message": "Publisher deleted successfully"}
        factory_session.expire_all()
        assert factory_session.get(PublisherPartner, partner_id) is None

    def test_sync_is_not_auth_rejected(self, client: FlaskClient, factory_session: Session) -> None:
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant)
        _member_session(client, tenant.tenant_id, user.email)

        response = client.post(_partners_url(tenant.tenant_id, "/sync"))

        assert response.status_code not in (401, 403)
        assert response.status_code == 200
        assert response.get_json() == {"message": "No publishers to sync"}

    def test_get_properties_is_not_auth_rejected(self, client: FlaskClient, factory_session: Session) -> None:
        tenant = TenantFactory()
        partner = PublisherPartnerFactory(tenant=tenant, publisher_domain="props.example")
        user = UserFactory(tenant=tenant)
        _member_session(client, tenant.tenant_id, user.email)

        with (
            patch(
                "src.admin.blueprints.publisher_partners.get_tenant_url",
                return_value="https://agent.example",
            ),
            patch(
                "src.admin.blueprints.publisher_partners.fetch_adagents",
                side_effect=AdagentsNotFoundError("missing"),
            ),
        ):
            response = client.get(_partners_url(tenant.tenant_id, f"/{partner.id}/properties"))

        assert response.status_code == 200
        body = response.get_json()
        assert body["is_authorized"] is False
        assert body.get("error") != "Authentication required"
        assert body.get("error") != "Access denied"
