"""The "Custom Domain (CNAME)" checklist task, now that every tenant declares a host.

``tenants.virtual_host`` is mandatory, so the presence of a host says nothing about whether
an operator configured one. The only host nobody chose is the one the DEPLOYMENT derived for
the tenant it bootstraps for itself (``deployment_virtual_host``), which the bootstraps store
verbatim — so "custom domain" is a host that DIFFERS from that one, and a tenant sitting on
the deployment's own host has the task still to do.

BDD cannot reach this: the checklist is an operator-facing onboarding report, not a buyer
response, so there is no wire and no transport to grade it on. Both production paths are
graded here — ``get_setup_status`` (one tenant, live queries) and ``get_bulk_setup_status``
(the dashboard's pre-fetched path), because they build the task list separately.
"""

from unittest.mock import patch

import pytest

from src.core.config import get_settings
from src.services.setup_checklist_service import SetupChecklistService
from tests.factories import TenantFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: What the deployment declares about itself, and therefore what its bootstrapped tenant holds.
DEPLOYMENT_HOST = "deployment-default.example.com"


def _as_the_deployment_host(host: str | None):
    """Make ``deployment_virtual_host()`` answer *host*, patched on the settings object.

    An environment write would land too late: production reads a field off settings built
    once, so the first read wins.
    """
    runtime = get_settings().runtime
    return (
        patch.object(runtime, "adcp_agent_url", None),
        patch.object(runtime, "sales_agent_domain", host),
    )


def _cname_task(status: dict) -> dict:
    """The tenant_cname task out of a setup-status report."""
    return next(task for task in status["recommended"] if task["key"] == "tenant_cname")


@pytest.mark.parametrize(
    ("virtual_host", "is_complete"),
    [
        (DEPLOYMENT_HOST, False),
        ("acme-sales.example.com", True),
    ],
    ids=["the deployment's own host is not a custom domain", "a host the operator stated is"],
)
def test_the_checklist_grades_a_custom_domain_against_the_deployments_own_host(
    integration_db, virtual_host, is_complete
):
    with BareIntegrationEnv() as env:
        TenantFactory(tenant_id="cname-t", virtual_host=virtual_host)
        env._commit_factory_data()
        SetupChecklistService.clear_cache()

        patch_url, patch_domain = _as_the_deployment_host(DEPLOYMENT_HOST)
        with patch_url, patch_domain:
            single = _cname_task(SetupChecklistService("cname-t").get_setup_status())
            bulk = _cname_task(SetupChecklistService.get_bulk_setup_status(["cname-t"])["cname-t"])

    assert single["is_complete"] is is_complete, single["details"]
    assert bulk == single, "the dashboard's bulk path grades the task differently"


def test_a_deployment_declaring_no_host_of_its_own_has_no_default_to_compare_against(integration_db):
    """``deployment_virtual_host()`` is None on a production install declaring neither
    variable, and then no tenant is on a deployment default — every host was stated."""
    with BareIntegrationEnv() as env:
        TenantFactory(tenant_id="cname-none", virtual_host="stated.example.com")
        env._commit_factory_data()
        SetupChecklistService.clear_cache()

        patch_url, patch_domain = _as_the_deployment_host(None)
        with (
            patch_url,
            patch_domain,
            patch.object(type(get_settings().runtime), "is_production", property(lambda _s: True)),
        ):
            task = _cname_task(SetupChecklistService("cname-none").get_setup_status())

    assert task["is_complete"] is True, task["details"]
