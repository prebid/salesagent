"""No submitted secret reaches an admin audit row or a log line, and no page renders a stored one.

Every user of the tenant, viewers included, reads ``audit_logs.details`` on the Workflows page
and the dashboard. ``log_admin_action`` used to copy every POST body into
``details.request_data`` minus the top-level keys its name list recognised, so the Broadstreet
form's ``config.api_key`` reached the row inside a dict repr. Now a row carries only the fields
its route declares (``audit_fields``), and the assembled details are redacted once before the
write. The agent credentials and the Slack webhook URLs are write-only secret fields.
"""

from __future__ import annotations

import json
import logging

import pytest
from flask import Flask

from src.core.database.models import AuditLog, UserRole
from tests.helpers.admin_session import tenant_user_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "audit_secret_tenant"
ADMIN = "admin@audit-secret.example"
KEY = "bs_live_0123456789abcdef0123456789abcdef"
CREDENTIAL = "agent-credential-0123456789abcdef"


@pytest.fixture
def tenant_env(integration_db):
    from tests.factories import TenantFactory, UserFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv(tenant_id=TENANT_ID) as env:
        tenant = TenantFactory(tenant_id=TENANT_ID, subdomain="audit-secret")
        UserFactory(tenant=tenant, user_id="audit_secret_admin", email=ADMIN, role=UserRole.ADMIN)
        yield env, tenant


def _admin(app):
    client = app.test_client()
    tenant_user_session(client, ADMIN)
    return client


def _audit_details(env, operation):
    env.get_session().expire_all()
    (row,) = env.query(AuditLog, tenant_id=TENANT_ID, operation=operation)
    return row.details


def _all_audit_text(env):
    env.get_session().expire_all()
    return json.dumps([row.details for row in env.query(AuditLog, tenant_id=TENANT_ID)])


def test_only_declared_fields_reach_the_row_whatever_the_rest_are_called(tenant_env, test_admin_app):
    env, _ = tenant_env
    body = {
        "adapter_type": "broadstreet",
        "config": {"network_id": "12345", "api_key": KEY, "manual_approval_required": False},
        "innocuous_note": "note-" + KEY,  # an undeclared field no name rule would flag
    }
    response = _admin(test_admin_app).post(f"/api/tenant/{TENANT_ID}/adapter-config", json=body)
    assert response.status_code == 200, response.get_data(as_text=True)[:500]

    details = _audit_details(env, "AdminUI.update_adapter_config")
    assert KEY[-16:] not in json.dumps(details)
    assert details["request_data"] == {"adapter_type": "broadstreet"}  # the one field the route declares


def test_extract_details_output_is_redacted_too(tenant_env):
    """The one redaction pass covers everything a route adds, not just request fields."""
    from src.admin.utils.audit_decorator import log_admin_action

    env, _ = tenant_env
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/t/<tenant_id>", methods=["POST"])
    @log_admin_action("extracted", extract_details=lambda r, **kw: {"target": {"name": "x", "apiKey": KEY}})
    def view(tenant_id):
        return "ok"

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": ADMIN}
    assert client.post(f"/t/{TENANT_ID}", data={"password": KEY}).status_code == 200
    details = _audit_details(env, "AdminUI.extracted")
    assert KEY[-16:] not in json.dumps(details)
    assert details["target"] == {"name": "x"}


def test_update_adapter_logs_no_refresh_token(tenant_env, test_admin_app, caplog):
    env, _ = tenant_env
    with caplog.at_level(logging.INFO):
        _admin(test_admin_app).post(
            f"/tenant/{TENANT_ID}/settings/adapter", json={"adapter": "google_ad_manager", "gam_refresh_token": KEY}
        )
    assert KEY[-16:] not in caplog.text
    assert KEY[-16:] not in _all_audit_text(env)


def test_configure_gam_stores_no_service_account_key(tenant_env, test_admin_app):
    env, _ = tenant_env
    service_account = json.dumps({"type": "service_account", "project_id": "p", "private_key_id": KEY})
    _admin(test_admin_app).post(
        f"/tenant/{TENANT_ID}/gam/configure",
        json={"auth_method": "service_account", "network_code": "123", "service_account_json": service_account},
    )
    assert env.query(AuditLog, tenant_id=TENANT_ID, operation="AdminUI.configure_gam"), "no audit row: graded nothing"
    assert KEY[-16:] not in _all_audit_text(env)


AGENTS = [
    ("creative-agents", "CreativeAgentFactory", "CreativeAgent"),
    ("signals-agents", "SignalsAgentFactory", "SignalsAgent"),
]


@pytest.mark.parametrize(("prefix", "factory_name", "model_name"), AGENTS)
def test_an_agent_credential_is_write_only_and_never_audited(
    tenant_env, test_admin_app, monkeypatch, prefix, factory_name, model_name
):
    from src.core.database import models
    from tests import factories
    from tests.helpers.egress_backoff import set_flags
    from tests.integration._egress_ingest_helpers import ADMITTED_URL

    set_flags(monkeypatch, private=True)  # the edit re-grades agent_url; this one is admitted without DNS
    env, tenant = tenant_env
    agent = getattr(factories, factory_name)(
        tenant=tenant, agent_url=ADMITTED_URL, auth_type="bearer", auth_credentials=CREDENTIAL
    )
    client = _admin(test_admin_app)
    edit = f"/tenant/{TENANT_ID}/{prefix}/{agent.id}/edit"

    def stored():
        env.get_session().expire_all()
        return env.get_one(getattr(models, model_name), id=agent.id).auth_credentials

    page = client.get(edit)
    assert page.status_code == 200
    assert CREDENTIAL not in page.get_data(as_text=True)

    form = {"agent_url": ADMITTED_URL, "name": agent.name, "auth_type": "bearer"}
    assert client.post(edit, data={**form, "auth_credentials": ""}).status_code == 302
    assert stored() == CREDENTIAL  # blank keeps the saved credential

    replacement = "replacement-" + CREDENTIAL
    assert client.post(edit, data={**form, "auth_credentials": replacement}).status_code == 302
    assert stored() == replacement
    assert env.query(AuditLog, tenant_id=TENANT_ID), "the edits wrote no audit row"
    assert replacement not in _all_audit_text(env)

    assert client.post(edit, data={**form, "auth_credentials": "", "clear_auth_credentials": "on"}).status_code == 302
    assert stored() is None  # the clear checkbox removes it


SLACK = "https://hooks.slack.com/services/T000/B000/SAVEDSECRET"
AUDIT_SLACK = "https://hooks.slack.com/services/T000/B111/AUDITSECRET"


def _slack(env):
    from src.core.database.models import Tenant

    env.get_session().expire_all()
    tenant = env.get_one(Tenant, tenant_id=TENANT_ID)
    return tenant.slack_webhook_url, tenant.slack_audit_webhook_url


@pytest.fixture
def slack_env(tenant_env):
    env, tenant = tenant_env
    tenant.slack_webhook_url, tenant.slack_audit_webhook_url = SLACK, AUDIT_SLACK
    env.get_session().commit()
    return env


def test_a_blank_slack_field_keeps_the_saved_webhook(slack_env, test_admin_app):
    form = {"slack_webhook_url": "", "slack_audit_webhook_url": ""}
    assert _admin(test_admin_app).post(f"/tenant/{TENANT_ID}/settings/slack", data=form).status_code == 302
    assert _slack(slack_env) == (SLACK, AUDIT_SLACK)


def test_a_new_slack_url_is_stored_but_its_secret_path_is_not_audited(slack_env, test_admin_app, monkeypatch):
    from tests.helpers.egress_backoff import set_flags
    from tests.integration._egress_ingest_helpers import ADMITTED_URL

    set_flags(monkeypatch, private=True)  # admitted by egress policy without DNS
    new_url = f"{ADMITTED_URL}/NEWSECRETPATH?token=QUERYSECRET"
    form = {"slack_webhook_url": new_url, "slack_audit_webhook_url": ""}
    assert _admin(test_admin_app).post(f"/tenant/{TENANT_ID}/settings/slack", data=form).status_code == 302
    assert _slack(slack_env) == (new_url, AUDIT_SLACK)
    text = _all_audit_text(slack_env)
    assert "NEWSECRETPATH" not in text and "QUERYSECRET" not in text


def test_the_settings_page_renders_neither_stored_webhook(slack_env, test_admin_app):
    page = _admin(test_admin_app).get(f"/tenant/{TENANT_ID}/settings/integrations")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "SAVEDSECRET" not in html and "AUDITSECRET" not in html


def test_the_clear_control_removes_only_its_webhook(slack_env, test_admin_app):
    form = {"slack_webhook_url": "", "clear_slack_webhook_url": "on", "slack_audit_webhook_url": ""}
    assert _admin(test_admin_app).post(f"/tenant/{TENANT_ID}/settings/slack", data=form).status_code == 302
    assert _slack(slack_env) == (None, AUDIT_SLACK)
