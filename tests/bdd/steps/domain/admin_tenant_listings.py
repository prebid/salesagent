"""BDD step definitions for BR-ADMIN-TENANT-LISTINGS: the two listings of every tenant a deployment holds.

The harness (``AdminTenantListingEnv``) is provided by the ``_harness_env`` fixture in
tests/bdd/conftest.py. The deployment and sign-in steps (``the deployment serves many
tenants``, ``the operator is signed in as ..., found a super admin just now``) come from
tests/bdd/steps/domain/admin_super_admin.py, and ``the page returns status`` from
tests/bdd/steps/domain/admin_accounts.py; all of them read the same ``ctx["admin_page"]`` key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _assert_json_body, _require_admin_page
from tests.bdd.steps.generic._table import as_bool, comma_list, rows

if TYPE_CHECKING:
    from tests.harness.admin_tenant_listings import AdminTenantListingEnv

#: The "ad server" cells that are not an ad server's name: a tenant that names none, and one
#: whose name is empty. Both are what the index's "not-configured" filter keeps.
_NO_AD_SERVER: dict[str, str | None] = {"(none)": None, "(empty)": ""}


def _env(ctx: dict) -> AdminTenantListingEnv:
    """Get the tenant-listing harness from context."""
    return ctx["env"]


def _index_page(ctx: dict) -> BeautifulSoup:
    """The all-tenants index the last When loaded, parsed."""
    return BeautifulSoup(_require_admin_page(ctx).data, "html.parser")


# ═══════════════════════════════════════════════════════════════════════════
# GIVEN steps
# ═══════════════════════════════════════════════════════════════════════════


@given("the deployment holds the tenants:")
def given_deployment_holds_tenants(ctx: dict, datatable: list) -> None:
    """One tenant per row, holding a media buy and an adapter configuration where the row says so."""
    env = _env(ctx)
    for row in rows(datatable):
        ad_server = row["ad server"]
        env.seed_listed_tenant(
            row["name"],
            ad_server=_NO_AD_SERVER.get(ad_server, ad_server),
            holds_media_buy=as_bool(row["media buy"], field="media buy"),
            is_active=as_bool(row["active"], field="active"),
            has_adapter_config=as_bool(row["adapter config"], field="adapter config"),
            created_at=row["created"],
        )


# ═══════════════════════════════════════════════════════════════════════════
# WHEN steps
# ═══════════════════════════════════════════════════════════════════════════


@when(parsers.parse('the operator opens the admin home page with "{name}" set to "{value}"'))
def when_open_admin_home_filtered(ctx: dict, name: str, value: str) -> None:
    """The index as its filter form submits it: one query parameter of the form, by its name."""
    ctx["admin_page"] = _env(ctx).open_admin_home({name: value})


@when("the tenant management API is asked for its list of tenants")
def when_management_api_lists_tenants(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).list_management_api_tenants()


# ═══════════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════════


@then(parsers.parse('the index lists the tenants "{names}"'))
def then_index_lists_tenants(ctx: dict, names: str) -> None:
    """The index's rows are the tenants *names*, in that order, by the name in each row's first cell."""
    listed = [row.td.get_text(strip=True) for row in _index_page(ctx).select("table tbody tr")]
    assert listed == comma_list(names), f"Expected the index to list {comma_list(names)!r}, got {listed!r}"


@then(parsers.parse("the index says it shows {shown:d} of {total:d} accounts"))
def then_index_counts_tenants(ctx: dict, shown: int, total: int) -> None:
    """The index's count line, as the operator reads it: how many tenants it shows, of how many the filter keeps."""
    marker = _index_page(ctx).find(string=lambda text: "Showing:" in text)
    said = " ".join(marker.parent.get_text().split()) if marker is not None else None
    expected = f"Showing: {shown} of {total} accounts"
    assert said == expected, f"Expected the index to say {expected!r}, got {said!r}"


@then(parsers.parse('the tenant list is "{names}", each as stored, with whether it has an adapter configuration'))
def then_tenant_list_is(ctx: dict, names: str) -> None:
    """Exactly today's body: the seeded tenants *names*, in that order, each with its stored columns."""
    _assert_json_body(ctx, _env(ctx).expected_tenant_list(comma_list(names)))
