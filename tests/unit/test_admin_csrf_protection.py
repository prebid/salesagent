"""Cross-site request forgery protection for the admin UI (src/admin/csrf.py).

A cross-site page could post a form to any admin route and the browser attached the
session cookie (SameSite=None in production, no token, no origin check), so a signed-in
member who merely viewed the page could, for example, add an attacker's address as a
tenant admin. These tests pin the refusal and the production cookie flags.
"""

from __future__ import annotations

import dataclasses

import pytest
from flask import Flask

from src.admin.auth_helpers import require_api_key_auth
from src.admin.csrf import csrf_exempt, init_csrf_protection

ORIGIN = "https://admin.example.com"


@pytest.fixture
def client():
    app = Flask(__name__)
    init_csrf_protection(app)

    @app.route("/change", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def change():
        return "changed"

    @app.route("/exempt", methods=["POST"])
    @csrf_exempt
    def exempt():
        return "exempt"

    @app.route("/api-key", methods=["POST"])
    @require_api_key_auth(setting="sync_api_key", config_key="sync_api_key", header="X-API-Key")
    def api_key_view():
        return "api"

    return app.test_client()


def _post(client, path="/change", method="POST", **headers):
    return client.open(path, method=method, base_url=ORIGIN, headers=headers)


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site", "CROSS-SITE"])
def test_refuses_a_post_from_another_site(client, fetch_site):
    response = _post(client, **{"Sec-Fetch-Site": fetch_site, "Origin": ORIGIN})
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_refuses_every_unsafe_method_cross_site(client, method):
    assert _post(client, method=method, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403


@pytest.mark.parametrize("fetch_site", ["same-origin", "none"])
def test_accepts_a_post_from_its_own_origin_or_the_user(client, fetch_site):
    response = _post(client, **{"Sec-Fetch-Site": fetch_site})
    assert response.status_code == 200
    assert response.data == b"changed"


def test_falls_back_to_origin_when_sec_fetch_site_is_absent(client):
    assert _post(client, Origin=ORIGIN).status_code == 200
    assert _post(client, Origin="https://ADMIN.example.com").status_code == 200
    assert _post(client, Origin="https://evil.example").status_code == 403
    assert _post(client, Origin="http://admin.example.com").status_code == 403
    assert _post(client, Origin="https://admin.example.com.evil.example").status_code == 403
    assert _post(client, Origin="null").status_code == 403
    assert _post(client, Origin="http://[admin.example.com").status_code == 403


def test_accepts_a_request_without_browser_headers(client):
    """Scripts and the test client send neither header; they are not a forgery vector."""
    assert _post(client).status_code == 200


def test_never_checks_safe_methods(client):
    response = client.get("/change", base_url=ORIGIN, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


def test_exempt_views_are_not_checked(client):
    assert _post(client, "/exempt", **{"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_api_key_views_are_exempt_and_still_need_their_key(client):
    """Reaching the key check (401), not the CSRF refusal (403), is the exemption."""
    response = _post(client, "/api-key", **{"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 401


def test_can_be_turned_off_by_config():
    app = Flask(__name__)
    app.config["CSRF_PROTECTION"] = False
    init_csrf_protection(app)
    app.add_url_rule("/change", "change", lambda: "changed", methods=["POST"])
    response = app.test_client().post("/change", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


def test_the_admin_app_refuses_the_advisory_proof_of_concept():
    """The cross-site form that added an attacker as tenant admin is refused before any view runs."""
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    response = app.test_client().post(
        "/tenant/default/users/add",
        data={"email": "attacker@evil.example", "role": "admin"},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_production_session_cookie_is_lax_and_http_only():
    from src.admin.app import create_app
    from src.core.config import load_settings

    settings = load_settings()
    production = dataclasses.replace(settings, runtime=settings.runtime.model_copy(update={"production": True}))
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"}, settings=production)

    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SECURE"] is True
