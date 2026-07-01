"""Integration tests for the list_creatives filter-length cap (#1505).

Defense-in-depth: most CreativeFilters list fields are unbounded on the pinned
adcp schema (only creative_ids has MaxLen). An over-long list filter must be
rejected with a clean VALIDATION_ERROR rather than expanding into a very large
SQL IN (...) query. Uses the CreativeListEnv harness, mirroring
test_list_creatives_auth.py.
"""

import pytest
from adcp import CreativeFilters

from src.core.exceptions import AdCPValidationError
from src.core.tools.creatives.listing import _MAX_FILTER_LIST_LEN
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeListEnv, make_identity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT = "cap_test_tenant"
_PRINCIPAL = "advertiser_a"


def _seed():
    tenant = TenantFactory(tenant_id=_TENANT)
    PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL)


def _identity():
    return make_identity(
        principal_id=_PRINCIPAL,
        tenant_id=_TENANT,
        tenant={"tenant_id": _TENANT, "name": "Cap Test Tenant"},
    )


class TestListCreativesFilterCap:
    def test_over_long_filter_rejected(self, integration_db):
        """A list filter longer than the cap -> VALIDATION_ERROR (correctable).

        Oracle: if the cap in _list_creatives_impl is removed, the impl runs the
        query and returns a response instead of raising, so this test fails.
        """
        with CreativeListEnv() as env:
            _seed()
            over = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN + 1)])
            with pytest.raises(AdCPValidationError) as exc:
                env.call_impl(identity=_identity(), filters=over)

        assert exc.value.recovery == "correctable"
        assert "concept_ids" in str(exc.value)
        assert str(_MAX_FILTER_LIST_LEN) in str(exc.value)
        assert exc.value.suggestion  # a remediation suggestion is surfaced

    def test_filter_at_cap_is_allowed(self, integration_db):
        """Exactly at the cap is accepted (boundary / negative control)."""
        with CreativeListEnv() as env:
            _seed()
            at_cap = CreativeFilters(concept_ids=[f"concept-{i}" for i in range(_MAX_FILTER_LIST_LEN)])
            response = env.call_impl(identity=_identity(), filters=at_cap)

        assert response is not None  # did not raise; empty result set is fine
