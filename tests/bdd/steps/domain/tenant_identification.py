"""Steps for the locally-added tenant-identification feature.

Each When names the seller by exactly ONE of this deployment's routes and grades that
the same tenant answered. The suite's default is now the Host (tests/harness/_base.py
``credential``), so these exist to keep the other routes graded rather than assumed.

WHAT MAKES THE ASSERTION NON-VACUOUS. ``get_adcp_capabilities`` is public, so no
credential confuses the question, and its response is derived per tenant — the account
block comes from that tenant's row. So "which tenant answered" is read off the wire
rather than inferred from the absence of an error. A scenario that only checked for a
2xx would pass against the wrong tenant, which is the failure being guarded.
"""

from __future__ import annotations

from pytest_bdd import given, then, when

from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers.credentials import credential_headers

#: A host this deployment serves no tenant at. Under ``.invalid``, which RFC 2606 reserves
#: precisely so it can never resolve anywhere.
UNSERVED_HOST = "nobody-serves-this.invalid"


def _tenant_row(ctx: dict):
    """The env's tenant row — the source of every value these steps send."""
    from sqlalchemy import select

    from src.core.database.models import Tenant

    env = ctx["env"]
    env._commit_factory_data()
    return env._session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).one()


@given("the tenant is reachable at its own virtual host")
def given_tenant_has_virtual_host(ctx: dict) -> None:
    """Assert the precondition rather than configure it.

    ``TenantFactory`` sets ``virtual_host`` by default, so this sentence states what the
    scenario depends on and fails loudly if that default is ever dropped — which would
    otherwise turn every scenario below into a test of the fallback route.
    """
    tenant = _tenant_row(ctx)
    assert tenant.virtual_host, (
        f"tenant {tenant.tenant_id} has no virtual_host, so it cannot be identified by Host "
        f"and these scenarios would grade the fallback route instead"
    )


def _ask_capabilities(ctx: dict, headers: dict) -> None:
    """Dispatch get_adcp_capabilities presenting exactly *headers*."""
    dispatch_request(ctx, credential=headers)


@when("the buyer requests capabilities naming the seller by Host")
def when_by_host(ctx: dict) -> None:
    _ask_capabilities(ctx, credential_headers(host=_tenant_row(ctx).virtual_host))


@when("the buyer requests capabilities naming the seller by Host with a port")
def when_by_host_with_port(ctx: dict) -> None:
    """The port must not change the answer: a Host carries one whenever the origin is not
    on the scheme's default, and the stored column holds a hostname."""
    _ask_capabilities(ctx, credential_headers(host=f"{_tenant_row(ctx).virtual_host}:8443"))


@when("the buyer requests capabilities naming the seller by tenant header")
def when_by_tenant_header(ctx: dict) -> None:
    """The header carries a tenant_id and nothing else."""
    _ask_capabilities(ctx, credential_headers(tenant=_tenant_row(ctx).tenant_id))


@when("the buyer requests capabilities naming a seller nobody serves")
def when_by_unserved_host(ctx: dict) -> None:
    _ask_capabilities(ctx, credential_headers(host=UNSERVED_HOST))


def _portfolio_description(body: dict) -> str | None:
    """The one field of the capabilities wire that NAMES the tenant that answered.

    ``describe_seller`` builds it as ``f"Advertising inventory from {tenant.name}"``, so it
    distinguishes "the right tenant answered" from "some tenant answered" — which a status
    check cannot, and which ``account.operator`` cannot either: that is None unless the
    tenant configures one, so asserting on it would have passed for the wrong tenant.
    """
    return ((body.get("media_buy") or {}).get("portfolio") or {}).get("description")


@then("the response describes that tenant")
def then_describes_that_tenant(ctx: dict) -> None:
    """Read the tenant OFF THE WIRE, not off the env."""
    body = wire_dict(ctx)
    tenant = _tenant_row(ctx)
    described = _portfolio_description(body)
    assert described, (
        "the capabilities response names no tenant, so which one answered cannot be read "
        f"from it — a tenant-less (minimal) description means the request resolved NOTHING: {sorted(body)}"
    )
    assert tenant.name in described, (
        f"the response describes {described!r}, but the tenant this request named is "
        f"{tenant.name!r} — a different tenant answered"
    )


@then("the refusal names the host the request used")
def then_refusal_names_the_host(ctx: dict) -> None:
    """The operator reading it can see which host reached a deployment serving neither."""
    details = ctx["result"].wire_error_details("CONFIGURATION_ERROR")
    assert details.get("rejected_value") == UNSERVED_HOST, (
        f"the refusal carries {details.get('rejected_value')!r}, not the host the request "
        f"named ({UNSERVED_HOST!r}), so it does not say what was unserved"
    )
