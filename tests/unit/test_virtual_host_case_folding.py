"""``virtual_host`` is all lowercase, and the two halves of that rule with no DB in them.

A ``Host`` names a DNS name and DNS is case-insensitive (RFC 7230 §5.4), so
``Probe-Case.AdCP.test`` and ``probe-case.adcp.test`` are ONE host. The column is
``Text`` and SQL comparison is not case-folding, so a row stored with a capital is
unroutable on every reader that answers "which tenant serves this request", leaving the
tenant reachable only through the ``x-adcp-tenant`` literal-id path (#2191).

The rule holds end to end: folded on write, folded again wherever a host is compared
or published. Graded here are the two halves that need no database — the column's own
validator, which fires on assignment, and the domain-ownership gate, which is a pure
comparison. The three READERS (the resolver, the landing page, the agent card) each need
a committed row and are graded together in
``tests/integration/test_virtual_host_integration.py``.
"""

import pytest

from src.core.database.models import Tenant
from src.services.approximated_client import DomainNotOwned, tenant_owns_domain

pytestmark = pytest.mark.unit

MIXED = "Probe-Case.AdCP.test"


class TestTheColumnFoldsOnAssignment:
    """``Tenant.virtual_host`` holds no uppercase, whatever it is handed."""

    def test_an_uppercase_host_is_stored_folded(self):
        assert Tenant(tenant_id="t", virtual_host=MIXED).virtual_host == MIXED.lower()

    def test_a_later_assignment_folds_too(self):
        """The admin settings form assigns after construction — the same hook must fire."""
        tenant = Tenant(tenant_id="t", virtual_host="already.lower.test")
        tenant.virtual_host = MIXED.upper()

        assert tenant.virtual_host == MIXED.lower()

    def test_a_tenant_declaring_no_host_is_refused(self):
        """``None`` is not an answer: a tenant declares the host it is served at.

        With Host-against-virtual_host one of only two ways to name a tenant (#2191), a
        host-less tenant is unreachable rather than domain-less, and a reader given one can
        only invent a host — a name nothing on the network serves, published on the card
        (#1845). The same hook that folds case refuses the absence, so no creation path can
        produce one.
        """
        with pytest.raises(ValueError):
            Tenant(tenant_id="t", virtual_host=None)

    def test_a_blank_host_is_refused_too(self):
        """SQL has no opinion about ``"   "``, so the hook is what makes NOT NULL mean it."""
        with pytest.raises(ValueError):
            Tenant(tenant_id="t", virtual_host="   ")


class TestTheDomainOwnershipGateFoldsBothSides:
    """The Approximated gate proves a host, so it compares hosts case-insensitively."""

    def test_a_differently_cased_domain_is_still_owned(self):
        tenant = Tenant(tenant_id="t", virtual_host="acme.example.com")

        assert tenant_owns_domain(tenant, "Acme.Example.COM").domain == "Acme.Example.COM"

    def test_a_different_domain_is_still_refused(self):
        """Folding widens the comparison to case and to nothing else."""
        tenant = Tenant(tenant_id="t", virtual_host="acme.example.com")

        with pytest.raises(DomainNotOwned):
            tenant_owns_domain(tenant, "other.example.com")

    def test_a_host_less_tenant_cannot_reach_the_gate_at_all(self):
        """The gate never sees a host-less tenant: construction refuses one first.

        A stronger guarantee than the gate answering ``DomainNotOwned`` for such a tenant —
        the state that answer defends against cannot be built.
        """
        with pytest.raises(ValueError):
            tenant_owns_domain(Tenant(tenant_id="t", virtual_host=None), "acme.example.com")
