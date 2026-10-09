"""``scripts/ops/check_tenants.py`` names each tenant stored at a host the validator refuses.

``Tenant.virtual_host``'s validator runs on assignment only, so a host stored before the rule
refused it still loads, and the operator's tenant report is where such a row shows up: the
refusal under that tenant's host, and the tenant in the closing summary.
"""

import pytest

from scripts.ops.check_tenants import check_all_tenants
from tests.factories import TenantFactory
from tests.helpers.hostnames import store_virtual_host_past_the_validator, virtual_host_refusal

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

SEPARATOR = "=" * 80


def _tenant_blocks(report: str) -> dict[str, str]:
    """Each tenant's lines in the report, keyed by tenant id."""
    tenant_section = report.split(SEPARATOR)[1]
    blocks = tenant_section.split("\nTenant: ")[1:]
    return {block.split("  ID: ", 1)[1].split("\n", 1)[0]: block for block in blocks}


def test_the_report_names_each_tenant_stored_at_a_refused_host(factory_session, capsys):
    TenantFactory(tenant_id="report_refused")
    TenantFactory(tenant_id="report_accepted", virtual_host="report-accepted.example.com")
    store_virtual_host_past_the_validator(factory_session, "report_refused", "report_refused.example.com")

    check_all_tenants()

    report = capsys.readouterr().out
    blocks = _tenant_blocks(report)
    assert f"  ⚠️  {virtual_host_refusal('report_refused.example.com')}\n" in blocks["report_refused"]
    assert "⚠️" not in blocks["report_accepted"]
    assert "⚠️  1 tenant(s) store a virtual_host the admin UI cannot serve: report_refused\n" in report
