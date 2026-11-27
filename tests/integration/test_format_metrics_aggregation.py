"""The format metrics job aggregates every GAM-connected tenant, one on a service account too (#2728 review).

The job's tenant query once required a stored OAuth refresh token, so a tenant on a
service account was never listed: the job skipped it and reported nothing. It now lists
tenants by the rule ``require_gam_config`` refuses by (``list_gam_connected_tenants``).

This runs the cron entry point, ``scripts/ops/aggregate_format_metrics.py``, for a tenant
whose only credential is a service account. The account's token endpoint is a local
origin that records the token request and refuses it (as ``T-ADMIN-REFUSAL-060`` does), so
the job reaches GAM as that account, fails there, and counts the tenant as failed.
"""

from __future__ import annotations

import logging
import runpy
import sys
from pathlib import Path

import pytest

from tests.helpers.gam_credentials import service_account_email, service_account_json, token_request_issuer
from tests.helpers.local_http_origin import run_local_origin

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "aggregate_format_metrics.py"
PROJECT_ID = "metrics-job-project"


@pytest.fixture
def token_endpoint():
    """A local token endpoint that refuses every token request, as Google refuses an assertion it does not accept."""
    with run_local_origin() as origin:
        origin.respond_with(400, body=b'{"error": "invalid_grant"}')
        yield origin


@pytest.fixture
def service_account_tenant(factory_session, token_endpoint):
    """An active GAM tenant whose only stored credential is a service account naming *token_endpoint*."""
    from tests.factories import TenantFactory
    from tests.utils.tenant_setup import onboard_gam

    tenant = TenantFactory(tenant_id="metrics_sa_tenant", is_active=True)
    config = onboard_gam(tenant, trafficker_id="metrics_trafficker")
    config.gam_refresh_token = None
    config.gam_auth_method = "service_account"
    config.gam_service_account_json = service_account_json(
        project_id=PROJECT_ID, token_uri=f"{token_endpoint.base_url}/token"
    )
    factory_session.commit()
    return tenant.tenant_id


def test_the_job_aggregates_a_tenant_on_a_service_account(service_account_tenant, token_endpoint, monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    # The tenant is counted, and fails because its token endpoint refuses the account.
    assert exited.value.code == 1, caplog.text
    assert "0 successful, 1 failed out of 1 tenants" in caplog.text, caplog.text
    # It failed asking for a token AS the service account.
    assert token_endpoint.requests, "No token request reached the service account's token endpoint"
    assert token_request_issuer(token_endpoint.requests[0].body) == service_account_email(PROJECT_ID)
