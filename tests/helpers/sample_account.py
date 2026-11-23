"""The one definition of the sample account the spec-field graders exercise.

Two grader modules send `account` on a real `get_products` call
(`test_spec_request_fields_accepted.py` and
`test_raw_wrapper_spec_fields_accepted.py`). Because `get_products` now
HONORS that field instead of accepting-and-dropping it, both must seed a
matching account or resolution correctly raises ACCOUNT_NOT_FOUND — so both need
the same natural key AND the same seeding steps.

Kept here rather than duplicated per module: the request payload and the fixture
that satisfies it have to agree on operator + brand.domain, and a second
hand-written copy is how they drift apart (CLAUDE.md's DRY invariant — the
duplicate would be a defect, not a style preference).

`spec_field_product_env` is here for the same reason one layer out. Both graders
need the SAME seeded world (a product that can serialize, an account this
principal can reach, policy and ranking neutralized) and differ only in tenant
id; the duplication ratchet flagged the second copy of that fixture body the
moment it appeared.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: The natural key resolution matches on: operator + brand.domain (+ sandbox).
SAMPLE_ACCOUNT: dict[str, Any] = {
    "brand": {"domain": "acmeoutdoor.example"},
    "operator": "pinnacle-agency.example",
}

#: The advertiser a request names when the test does not care which one.
#:
#: RESERVED, and that is the whole requirement. ``.example`` is an RFC 2606 §2 reserved
#: top-level domain, so no one can register this and no request built from it can name a
#: host somebody else controls. The value this replaced was ``testbrand.com`` — a real
#: registrable domain, reached from here into roughly every generated payload in the suite
#: and, until it was corrected, into a corpus proposed to the AdCP spec.
#:
#: It sits beside :data:`SAMPLE_ACCOUNT` because it is the same kind of value for the same
#: reason: a name the fixtures agree on, defined once so the copies cannot drift.
SAMPLE_BRAND: dict[str, Any] = {"domain": "testbrand.example"}


def seed_sample_account(tenant: Any, principal: Any) -> Any:
    """Create the sample account and grant *principal* access to it.

    Both halves are required: `list_by_natural_key` scopes to accounts the agent
    can reach through the AgentAccountAccess join, so an account without the
    access row resolves to "not found" exactly as if it were absent.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    account = AccountFactory(
        tenant=tenant,
        operator=SAMPLE_ACCOUNT["operator"],
        brand={"domain": SAMPLE_ACCOUNT["brand"]["domain"]},
    )
    AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
    return account


#: Both graders authenticate as this principal; the seeded account is reachable by it.
_PRINCIPAL_ID = "test_principal"


@contextmanager
def spec_field_product_env(tenant_id: str) -> Iterator[Any]:
    """A `ProductEnv` seeded so a REAL `get_products` call grades field handling.

    Every seeding step here is load-bearing, which is why it is shared rather
    than re-typed:

    * the pricing option — a product with none fails to serialize at all, and
      `get_products` answers SERVICE_UNAVAILABLE, so every case would fail for a
      reason unrelated to request fields;
    * `seed_sample_account` — because `get_products` HONORS `account`,
      an unseeded account makes ACCOUNT_NOT_FOUND the CORRECT answer, and the
      graders would be measuring a missing fixture instead of field honoring
      (while the field was silently dropped, they passed with no seed at all —
      exactly the hole being closed);
    * policy/ranking — neutralized so a policy verdict cannot mask the result.

    Imported lazily: `tests.helpers` is a leaf that `tests.harness` may itself
    import, and a module-level harness import here would risk a cycle.
    """
    from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
    from tests.harness.product import ProductEnv

    with ProductEnv(tenant_id=tenant_id, principal_id=_PRINCIPAL_ID) as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
        product = ProductFactory(tenant=tenant, delivery_type="guaranteed")
        PricingOptionFactory(product=product, pricing_model="cpm", rate="15.00", is_fixed=True, currency="USD")
        seed_sample_account(tenant, principal)
        env.set_policy_approved()
        env.set_ranking_disabled()
        yield env
