"""The rows the create_media_buy setup-checklist gate grades.

``_create_media_buy_impl`` calls ``validate_setup_complete(tenant_id)`` unconditionally,
and a tenant is "set up" only when every CRITICAL task in
``SetupChecklistService._check_critical_tasks`` is complete. The gate used to be skipped
for a caller that set the testing context's ``dry_run`` -- every create_media_buy test set
it -- and that channel is gone (a1b79d22d), so a tenant such a test drives now needs the
real rows.

A fixture normally seeds the tasks a media buy visibly needs: the ad server, the currency,
a product, a principal. The two here are the ones nothing else in a test wants, so they
are the two every create_media_buy fixture was missing:

* an ``AuthorizedProperty`` -- the "Authorized Properties" task counts rows for the
  tenant; and
* a ``TenantAuthConfig`` with SSO on -- single-tenant deployments make
  ``sso_configuration`` critical (``_is_multi_tenant_mode()``), and the integration suite
  runs single-tenant.

The third half of "SSO configured" is ``auth_setup_mode=False``, which is a column on the
TENANT and so stays with whoever creates it: pass it to ``TenantFactory`` /
``create_tenant_with_timestamps``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.utils.database_helpers import bind_factories_to_session, create_tenant_with_timestamps

if TYPE_CHECKING:
    from src.core.config import GamSuiteNetwork


def seed_gam_tenant(
    session: Any,
    *,
    tenant_id: str,
    name: str,
    subdomain: str,
    trafficker_id: str,
) -> Any:
    """Create a set-up GAM tenant and return it, committed.

    Seeds the rows a GAM create_media_buy test cannot do without and never varies: the
    ``Tenant`` itself with ``human_review_required=False`` (so validation runs immediately
    instead of parking the buy in an approval workflow), the setup-checklist rows the gate
    grades, and the GAM onboarding rows (``onboard_gam``, which says why the adapter needs
    its refresh token).

    The caller still owns what it varies: the currency limit's ceiling, the property tags,
    the principal, and the products.
    """
    tenant = create_tenant_with_timestamps(
        tenant_id=tenant_id,
        name=name,
        subdomain=subdomain,
        human_review_required=False,
        # Half of the setup checklist's "SSO configured" task; the other half is the
        # TenantAuthConfig row seed_setup_checklist_rows writes.
        auth_setup_mode=False,
    )
    session.add(tenant)
    session.flush()
    seed_setup_checklist_rows(session, tenant)
    with bind_factories_to_session(session):
        onboard_gam(tenant, trafficker_id=trafficker_id)
    return tenant


def onboard_gam(
    tenant: Any,
    *,
    trafficker_id: str | None,
    network: GamSuiteNetwork | None = None,
    name_prefix: str | None = None,
) -> Any:
    """Leave *tenant* with the rows GAM onboarding in the Admin UI writes, and return the config.

    The one writer of that fact: ``tenant.ad_server``, which the setup checklist reads, and
    the tenant's ``AdapterConfig`` row, which adapter selection reads. A mock ``AdapterConfig``
    the tenant already has is replaced (the relationship's delete-orphan turns the two writes
    into one update).

    Without *network* the tenant is onboarded to the stand-in network every in-process caller
    builds: ``gam_refresh_token`` is what makes the adapter CONSTRUCTIBLE (it builds its
    credentials in ``__init__`` and refuses a config carrying no
    key_file/service_account_json/refresh_token), and the stand-in answers to any network
    code. With *network*, the configured GAM network the suite books against, the tenant is onboarded to it by
    service account (``scripts/gam_prerequisites_check.adapter_config_columns``), and the key is stored
    encrypted, as the column stores it. *trafficker_id* ``None`` is no trafficker, which the
    adapter resolves to the service account's own user. *name_prefix* leads every order and
    line item name the seller gives GAM (the seller's naming templates), so a run's objects on
    a shared network are its own.

    The caller binds the factories to its session (an env does so on entry; otherwise wrap
    the call in ``bind_factories_to_session``).
    """
    from scripts.gam_prerequisites_check import adapter_config_columns
    from src.core.utils.naming import DEFAULT_ORDER_NAME_TEMPLATE
    from tests.factories import AdapterConfigFactory

    network_columns: dict[str, Any] = (
        adapter_config_columns(network)
        if network is not None
        else {
            "adapter_type": "google_ad_manager",
            "gam_network_code": "123456",
            "gam_refresh_token": "test_refresh_token",
        }
    )
    names: dict[str, str] = (
        {
            "gam_order_name_template": f"{name_prefix} {DEFAULT_ORDER_NAME_TEMPLATE}",
            "gam_line_item_name_template": f"{name_prefix} {{product_name}}",
        }
        if name_prefix
        else {}
    )
    tenant.ad_server = "google_ad_manager"
    return AdapterConfigFactory(tenant=tenant, gam_trafficker_id=trafficker_id, **network_columns, **names)


def seed_setup_checklist_rows(session: Any, tenant: Any) -> None:
    """Give *tenant* the two setup-checklist rows a create_media_buy test needs.

    *tenant* is the persisted ``Tenant``, passed rather than looked up: the factories
    declare ``tenant`` as a SubFactory, so omitting it would seed a SECOND tenant instead
    of attaching to this one. The caller must flush the tenant first.
    """
    from tests.factories import TenantAuthConfigFactory
    from tests.factories.product import DEFAULT_PUBLISHER_DOMAIN
    from tests.factories.publisher import authorize_publishers

    with bind_factories_to_session(session):
        # The default publisher (the one create_test_db_product's products name) authorizes
        # the tenant in its stored adagents.json.
        authorize_publishers(tenant, DEFAULT_PUBLISHER_DOMAIN)
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
