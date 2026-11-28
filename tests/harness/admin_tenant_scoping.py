"""Admin tenant-scoping harness for the routes of prebid/salesagent#2203 and #2204.

Drives ``GET /api/tenant/<tenant_id>/revenue-chart``, ``GET /api/tenant/<tenant_id>/products``,
``GET /api/tenant/<tenant_id>/products/suggestions``, ``GET,POST /tenant/<tenant_id>/policy/rules``
and ``POST /auth/oidc/test/<tenant_id>`` (#2203), the six GAM reporting routes of
``src/admin/blueprints/gam_reporting_api.py`` (#2204), ``GET /tenant/<tenant_id>/media-buys``,
which names products from the tenant's own catalogue, the Policy page with the admin-only
``POST /tenant/<tenant_id>/policy/update`` that saves it (#2692), and the five inventory views
that read the tenant's synced GAM inventory (``GET /tenant/<tenant_id>/check-inventory-sync``,
``GET /api/tenant/<tenant_id>/sync-status``, ``GET /api/tenant/<tenant_id>/targeting/all``,
``GET /api/tenant/<tenant_id>/inventory/tree`` and ``GET /api/tenant/<tenant_id>/inventory-stats``),
on the two admin transports ``AdminAccountEnv``
already provides — Flask ``test_client`` (integration) and ``requests.Session`` against the
Docker stack (e2e) — and seeds the tenants, memberships, catalogue, media buys and synced
inventory the scenarios grade.

Subclasses ``AdminAccountEnv`` rather than extracting a base: the transport plumbing is
inherited unchanged, and the harness lifecycle guard
(``tests/harness/test_harness_base.py::test_harness_envs_define_no_enter_exit``) pins the
one hand-rolled ``__enter__``/``__exit__`` home to ``AdminAccountEnv``, so a second admin
env inherits that lifecycle instead of restating it.

The caller's session is STATED on both transports (``AdminClient.state_session``), with the
keys the deployment's own (Google) sign-in writes for a caller who is not a super admin
(``member_session_payload(..., auth_method=None)``): ``user`` as a bare email and a HOME
tenant that is never the target, with no ``test_user`` key. Not an OIDC session: a sign-in
through a tenant's own SSO is bound to that tenant and reaches no other, so it would be
refused on the target before its membership row was ever read. ``require_tenant_access`` therefore takes its real branch, and the target tenant's
``User`` row is the only thing that decides the outcome: whether it exists, whether it is
active, and the role it holds. A refusal of that session is the decorator's membership or
role check, on the same branch in-process as on the stack.

The contract itself is stated once, in ``tests/bdd/features/BR-ADMIN-TENANT-SCOPING.feature``,
and runs on both transports through the ``admin-tenant-scoping`` row of ``ENV_ROUTES`` in
tests/bdd/conftest.py. The step definitions in tests/bdd/steps/domain/admin_tenant_scoping.py
read the redirect table, the buy's budget and the expected-body builders from this module;
seeding and read-backs are methods of the env.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, UserRole
from src.core.schemas.advertising_policy import AdvertisingPolicy
from src.services.default_products import get_default_products
from tests.factories import (
    GAMInventoryFactory,
    MediaBuyFactory,
    ProductFactory,
    SyncJobFactory,
    TenantFactory,
    UserFactory,
)
from tests.factories.mint import mint_id
from tests.harness.admin_accounts import AdminAccountEnv
from tests.harness.admin_client import AdminResponse
from tests.helpers.admin_session import cached_super_admin, member_session_payload, no_configured_super_admins
from tests.utils.database_helpers import bound_factory_session, delete_tenant, tenant_row_counts

#: The identity the scenarios log in as: a tenant user, never a super admin. Both transports
#: use the same address so they grade identical ``User`` rows.
MEMBER_EMAIL = "test_tenant_user@example.com"
#: Somebody else, who IS an active member of the target tenant in every scenario. Without
#: this row a lookup that forgot the caller's email would still find nobody, and a refusal
#: would prove nothing about whose membership was checked.
OTHER_MEMBER_EMAIL = "target_tenant_member@example.com"
#: The role a caller's active row in the target tenant holds unless the scenario names
#: another: the least one that may send every method the four routes of #2203 serve,
#: because ``role_permits`` asks ``manager`` for a write. The OIDC test route asks for
#: ``admin``, so T-ADMIN-SCOPE-011 and T-ADMIN-SCOPE-012 name the role they seed.
MEMBER_ROLE = UserRole.MANAGER
#: The role of a row the membership lookup must not match: the other member of the target
#: tenant, the caller's row in another tenant, and the caller's inactive row in the target.
#: ``admin`` passes the role check of every route in ``ROUTES``, so a lookup that matched
#: one of these rows would let the caller in and the refusal scenario would fail. Under a
#: lower role the OIDC test route's role check would refuse in the lookup's place.
NON_MATCHING_ROW_ROLE = UserRole.ADMIN
ACTIVE_BUY_BUDGET = Decimal("1500.00")
#: Any numeric id passes ``validate_numeric_id``; the handlers stop before they would use it.
GAM_ADVERTISER_ID = "12345"


class TenantScopedRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]
    #: The form a POST to the route carries; an empty one when the route names none.
    form: Mapping[str, str] | None = None


#: Every field of the Policy page's form, filled in as an operator saves it: both checks on,
#: and each list differing from the baseline the page offers a tenant with no saved policy,
#: so a page showing these values is showing the saved policy. Each list is one entry per
#: line, which is how the page renders a list back into its textarea.
POLICY_FORM: dict[str, str] = {
    "enabled": "on",
    "require_manual_review": "on",
    "prohibited_advertisers": "blocked.example\ncompetitor.example",
    "prohibited_categories": "gambling",
    "prohibited_tactics": "false urgency",
    "default_prohibited_categories": "illegal_content\nhate_speech",
    "default_prohibited_tactics": "impersonation",
}


_GAM_API = "/api/tenant/{tenant_id}"

#: The routes the scenarios send to, keyed by the name the Gherkin scenarios use.
ROUTES: dict[str, TenantScopedRoute] = {
    "revenue chart API": TenantScopedRoute("/api/tenant/{tenant_id}/revenue-chart", ("GET",)),
    "products API": TenantScopedRoute("/api/tenant/{tenant_id}/products", ("GET",)),
    "product suggestions API": TenantScopedRoute("/api/tenant/{tenant_id}/products/suggestions", ("GET",)),
    "policy rules page": TenantScopedRoute("/tenant/{tenant_id}/policy/rules", ("GET", "POST")),
    "OIDC test page": TenantScopedRoute("/auth/oidc/test/{tenant_id}", ("POST",)),
    "GAM reporting API": TenantScopedRoute(f"{_GAM_API}/gam/reporting", ("GET",)),
    "GAM advertiser summary API": TenantScopedRoute(
        f"{_GAM_API}/gam/reporting/advertiser/{{advertiser_id}}/summary", ("GET",)
    ),
    "GAM principal reporting API": TenantScopedRoute(f"{_GAM_API}/principals/{{principal_id}}/gam/reporting", ("GET",)),
    "GAM country breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/countries", ("GET",)),
    "GAM ad unit breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/ad-units", ("GET",)),
    "GAM principal summary API": TenantScopedRoute(
        f"{_GAM_API}/principals/{{principal_id}}/gam/reporting/summary", ("GET",)
    ),
    "media buys page": TenantScopedRoute("/tenant/{tenant_id}/media-buys", ("GET",)),
    "policy page": TenantScopedRoute("/tenant/{tenant_id}/policy/", ("GET",)),
    "policy update route": TenantScopedRoute("/tenant/{tenant_id}/policy/update", ("POST",), POLICY_FORM),
    "inventory sync check": TenantScopedRoute("/tenant/{tenant_id}/check-inventory-sync", ("GET",)),
    "sync status API": TenantScopedRoute("/api/tenant/{tenant_id}/sync-status", ("GET",)),
    "targeting data API": TenantScopedRoute("/api/tenant/{tenant_id}/targeting/all", ("GET",)),
    "inventory tree API": TenantScopedRoute("/api/tenant/{tenant_id}/inventory/tree", ("GET",)),
    "inventory stats API": TenantScopedRoute("/api/tenant/{tenant_id}/inventory-stats", ("GET",)),
}

#: Where a scenario may say a route redirected to, keyed by the name the Gherkin uses. The
#: login page is the TENANT's, not the generic ``/login`` ``require_auth`` sends to; the
#: policy page is where ``policy.rules`` sends an authorized member, and the settings page
#: is where ``oidc.test_initiate`` sends one when the tenant has no OIDC configuration.
REDIRECTS: dict[str, str] = {
    "login page": "/tenant/{tenant_id}/login",
    "policy page": "/tenant/{tenant_id}/policy/",
    "settings page": "/tenant/{tenant_id}/settings",
}


class TargetTenant(NamedTuple):
    tenant_id: str
    principal_id: str
    principal_name: str
    products_payload: list[dict[str, str]]
    media_buy_id: str
    #: The product id the buy's request packages name: one the tenant's own catalogue does not hold.
    media_buy_product_id: str


class TenantState(NamedTuple):
    """What a request can change for a tenant: how many rows it holds per table, and its advertising policy."""

    row_counts: dict[str, int]
    advertising_policy: AdvertisingPolicy | None


def _seed_member(tenant: Tenant, email: str, *, role: UserRole, is_active: bool = True) -> None:
    """A ``User`` row for *email* in *tenant*, holding *role*. Call it inside ``bound_factory_session()``."""
    UserFactory(tenant=tenant, user_id=mint_id("user"), email=email, role=role, is_active=is_active)


def redirect_path(response: AdminResponse) -> str:
    """Path of a redirect's Location, without the ``next`` query the login page carries."""
    return urlsplit(response.headers["Location"]).path


#: The order today's handler emits get_default_products() in when no query parameters are
#: sent (sorted by industry-specificity, average CPM, then format count). Pinned as a literal
#: so the expectation does not re-implement the sort it grades.
_DEFAULT_SUGGESTION_ORDER = (
    "run_of_site_display",
    "contextual_display",
    "video_preroll",
    "native_infeed",
    "mobile_interstitial",
    "homepage_takeover",
)


def expected_suggestions_body(existing_ids: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Today's body of ``GET .../products/suggestions`` with no query parameters.

    *existing_ids* are the default product ids the tenant's catalogue holds: each of them is
    marked ``already_exists``, and every other suggestion is new. With none, the catalogue
    shares no id with the default products (the seeded product id is unique). Every
    suggestion is industry-neutral and unboosted either way.
    """
    by_id = {product["product_id"]: product for product in get_default_products()}
    assert set(by_id) == set(_DEFAULT_SUGGESTION_ORDER), sorted(by_id)
    assert existing_ids <= set(by_id), f"not a default product id: {sorted(existing_ids - set(by_id))}"
    suggestions = [
        {
            **by_id[product_id],
            "already_exists": product_id in existing_ids,
            "is_industry_specific": False,
            "match_score": 100,
        }
        for product_id in _DEFAULT_SUGGESTION_ORDER
    ]
    return {
        "suggestions": suggestions,
        "total_count": len(suggestions),
        "criteria": {"industry": None, "delivery_type": None, "max_cpm": None, "formats": []},
    }


# ── Synced GAM inventory ─────────────────────────────────────────────────
#
# The five inventory views read the tenant's synced copy of its Google Ad Manager inventory
# (``gam_inventory``), and the sync status reads its sync jobs (``sync_jobs``) too. The rows
# seeded below and the bodies the views answer over them are pinned side by side, the bodies
# as literals, so an expectation does not re-implement the count it grades. A time is written
# as the views write it: ``isoformat()`` of the UTC time PostgreSQL returns, ``+00:00``.

#: When the target tenant's latest inventory sync ran.
LATEST_SYNC = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
_DAY = timedelta(days=1)


class SyncedItem(NamedTuple):
    """One row of synced GAM inventory: the columns ``GAMInventoryFactory`` is given for it."""

    inventory_type: str
    inventory_id: str
    name: str
    status: str
    created_at: datetime
    last_synced: datetime
    path: list[str]
    inventory_metadata: dict[str, Any]


def _days_before_latest_sync(days: int) -> datetime:
    """*days* days before ``LATEST_SYNC``; after it when *days* is negative."""
    return LATEST_SYNC - days * _DAY


def _ad_unit(
    inventory_id: str,
    name: str,
    status: str,
    *,
    created: int,
    synced: int,
    parent: str | None = None,
    path: list[str] | None = None,
    has_children: bool = False,
) -> SyncedItem:
    """An ad unit of one leaderboard size, *created* and last *synced* that many days before ``LATEST_SYNC``.

    Its metadata is what the sync stores for an ad unit and the inventory tree reads.
    """
    metadata = {
        "ad_unit_code": inventory_id.removeprefix("au_"),
        "parent_id": parent,
        "has_children": has_children,
        "sizes": [{"width": 728, "height": 90}],
    }
    created_at, last_synced = _days_before_latest_sync(created), _days_before_latest_sync(synced)
    return SyncedItem("ad_unit", inventory_id, name, status, created_at, last_synced, path or [name], metadata)


def _item(inventory_type: str, inventory_id: str, name: str, status: str, synced: int, **metadata: Any) -> SyncedItem:
    """An item that is not an ad unit, created a month before ``LATEST_SYNC`` and last *synced* days before it."""
    created_at, last_synced = _days_before_latest_sync(30), _days_before_latest_sync(synced)
    return SyncedItem(inventory_type, inventory_id, name, status, created_at, last_synced, [name], metadata)


#: The target tenant's synced inventory: every type the views count, in three statuses. The
#: latest sync synced every ad unit but the STALE one, which is the newest item of all: created,
#: and last synced, the day before, and not found in GAM since. Each later type was last synced
#: a day before the type above it, and its STALE item two days before that.
SYNCED_INVENTORY: tuple[SyncedItem, ...] = (
    _ad_unit("au_homepage", "Homepage", "ACTIVE", created=30, synced=0, has_children=True),
    _ad_unit(
        "au_homepage_top",
        "Homepage Top",
        "ACTIVE",
        created=30,
        synced=0,
        parent="au_homepage",
        path=["Homepage", "Homepage Top"],
    ),
    _ad_unit("au_archive", "Archive", "INACTIVE", created=20, synced=0),
    _ad_unit("au_microsite", "Microsite", "STALE", created=1, synced=1),
    _item("placement", "pl_sports", "Sports Package", "ACTIVE", 1, description="Every sports ad unit"),
    _item("placement", "pl_seasonal", "Seasonal Package", "INACTIVE", 1, description="Holiday campaigns"),
    _item("placement", "pl_retired", "Retired Package", "STALE", 3, description="Removed from GAM"),
    _item("label", "lb_family", "Family Safe", "ACTIVE", 2, description="Safe for a family audience"),
    _item("label", "lb_political", "Political", "INACTIVE", 2, description="Political advertising"),
    _item("label", "lb_legacy", "Legacy", "STALE", 4, description="Removed from GAM"),
    _item("custom_targeting_key", "ck_section", "section", "ACTIVE", 3, display_name="Section", type="PREDEFINED"),
    _item("custom_targeting_key", "ck_campaign", "campaign", "INACTIVE", 3, display_name="Campaign", type="FREEFORM"),
    _item("custom_targeting_key", "ck_legacy", "legacy_key", "STALE", 5, display_name="Legacy key", type="FREEFORM"),
    _item("audience_segment", "as_sports_fans", "Sports Fans", "ACTIVE", 4, type="FIRST_PARTY", size=120000),
    _item("audience_segment", "as_auto_intenders", "Auto Intenders", "INACTIVE", 4, type="THIRD_PARTY", size=50000),
    _item("audience_segment", "as_lapsed", "Lapsed Visitors", "STALE", 6, type="FIRST_PARTY", size=3000),
)

#: Another tenant's inventory: an ACTIVE ad unit, created and synced a day after the target
#: tenant's latest sync, and a STALE label synced then too.
OTHER_TENANT_INVENTORY: tuple[SyncedItem, ...] = (
    _ad_unit("au_other_homepage", "Homepage of another tenant", "ACTIVE", created=-1, synced=-1),
    _item("label", "lb_other", "Label of another tenant", "STALE", -1, description="Another tenant's label"),
)


class SyncRun(NamedTuple):
    """One row of ``sync_jobs``: the columns ``SyncJobFactory`` is given for it."""

    sync_type: str
    status: str
    started_at: datetime
    completed_at: datetime
    error_message: str | None = None


#: What the target tenant's failed inventory sync recorded, which the sync status shows.
FAILED_SYNC_ERROR = "Inventory sync failed. Reference: bdd-inventory-sync"
#: The target tenant's sync jobs: the inventory sync that ran the latest sync completed, a
#: second one started two hours later and failed, and an orders sync started after both.
SYNC_JOBS: tuple[SyncRun, ...] = (
    SyncRun("inventory", "completed", LATEST_SYNC - timedelta(minutes=30), LATEST_SYNC),
    SyncRun(
        "inventory",
        "failed",
        LATEST_SYNC + timedelta(hours=2),
        LATEST_SYNC + timedelta(hours=2, minutes=5),
        FAILED_SYNC_ERROR,
    ),
    SyncRun("orders", "completed", LATEST_SYNC + timedelta(hours=5), LATEST_SYNC + timedelta(hours=5, minutes=1)),
)
#: Another tenant's inventory sync, started after every sync job of the target tenant.
OTHER_TENANT_SYNC_JOBS: tuple[SyncRun, ...] = (
    SyncRun("inventory", "completed", LATEST_SYNC + timedelta(hours=6), LATEST_SYNC + timedelta(hours=6, minutes=30)),
)

#: The inventory types the sync status reports on, each whether the tenant holds any or not.
_SYNC_STATUS_TYPES = ("ad_unit", "placement", "label", "custom_targeting_key", "audience_segment")
#: How many of the target tenant's ``SYNCED_INVENTORY`` items of each type are in each status.
_ONE_IN_EACH_STATUS = {"ACTIVE": 1, "INACTIVE": 1, "STALE": 1}
_STATUS_COUNTS: dict[str, dict[str, int]] = {
    "ad_unit": {"ACTIVE": 2, "INACTIVE": 1, "STALE": 1},
    "placement": _ONE_IN_EACH_STATUS,
    "label": _ONE_IN_EACH_STATUS,
    "custom_targeting_key": _ONE_IN_EACH_STATUS,
    "audience_segment": _ONE_IN_EACH_STATUS,
}


def expected_inventory_sync_check_body() -> dict[str, Any]:
    """Today's body of ``GET .../check-inventory-sync`` over ``SYNCED_INVENTORY``.

    Every item the tenant holds, of every type and status, and the latest time any of them was
    synced: ``LATEST_SYNC``, not the newest item's creation the day before.
    """
    return {"has_inventory": True, "inventory_count": 16, "last_sync": "2026-10-30T12:00:00+00:00"}


def expected_sync_status_body(tenant_id: str) -> dict[str, Any]:
    """Today's body of ``GET .../sync-status`` over ``SYNCED_INVENTORY`` and ``SYNC_JOBS``.

    Per type: how many items it holds, how many in each status, and when the type was last
    synced. Every type shows the tenant's newest inventory sync job, the failed one: not the
    orders sync that started after it, and not the other tenant's inventory sync.
    """
    newest_inventory_job = {
        "last_job_status": "failed",
        "last_job_time": "2026-10-30T14:00:00+00:00",
        "last_job_error": FAILED_SYNC_ERROR,
    }

    def of_type(total_count: int, status_counts: dict[str, int], last_synced: str) -> dict[str, Any]:
        """One type's entry: its counts and last sync, with the newest inventory sync job."""
        return {
            "total_count": total_count,
            "status_counts": status_counts,
            "last_synced": last_synced,
            **newest_inventory_job,
        }

    return {
        "tenant_id": tenant_id,
        "sync_status": {
            "ad_unit": of_type(4, _STATUS_COUNTS["ad_unit"], "2026-10-30T12:00:00+00:00"),
            "placement": of_type(3, _STATUS_COUNTS["placement"], "2026-10-29T12:00:00+00:00"),
            "label": of_type(3, _STATUS_COUNTS["label"], "2026-10-28T12:00:00+00:00"),
            "custom_targeting_key": of_type(3, _STATUS_COUNTS["custom_targeting_key"], "2026-10-27T12:00:00+00:00"),
            "audience_segment": of_type(3, _STATUS_COUNTS["audience_segment"], "2026-10-26T12:00:00+00:00"),
        },
    }


def expected_inventory_stats_body(tenant_id: str) -> dict[str, Any]:
    """Today's body of ``GET .../inventory-stats`` over ``SYNCED_INVENTORY``.

    Per type the tenant holds, how many of its items are in each status, STALE included: none of
    the other tenant's items, though one is a STALE label.
    """
    return {"tenant_id": tenant_id, "stats": _STATUS_COUNTS}


def expected_targeting_data_body() -> dict[str, Any]:
    """Today's body of ``GET .../targeting/all`` over ``SYNCED_INVENTORY``, each list in ``id`` order.

    Every custom targeting key, audience segment and label the tenant holds, in every status, and
    the tenant's latest sync. A label is active only in the ACTIVE status; an audience segment the
    sync stored no description for has none.
    """
    return {
        "customKeys": [
            {
                "id": "ck_campaign",
                "name": "campaign",
                "display_name": "Campaign",
                "status": "INACTIVE",
                "type": "FREEFORM",
            },
            {
                "id": "ck_legacy",
                "name": "legacy_key",
                "display_name": "Legacy key",
                "status": "STALE",
                "type": "FREEFORM",
            },
            {
                "id": "ck_section",
                "name": "section",
                "display_name": "Section",
                "status": "ACTIVE",
                "type": "PREDEFINED",
            },
        ],
        "audiences": [
            {
                "id": "as_auto_intenders",
                "name": "Auto Intenders",
                "description": None,
                "status": "INACTIVE",
                "size": 50000,
                "type": "THIRD_PARTY",
            },
            {
                "id": "as_lapsed",
                "name": "Lapsed Visitors",
                "description": None,
                "status": "STALE",
                "size": 3000,
                "type": "FIRST_PARTY",
            },
            {
                "id": "as_sports_fans",
                "name": "Sports Fans",
                "description": None,
                "status": "ACTIVE",
                "size": 120000,
                "type": "FIRST_PARTY",
            },
        ],
        "labels": [
            {"id": "lb_family", "name": "Family Safe", "description": "Safe for a family audience", "is_active": True},
            {"id": "lb_legacy", "name": "Legacy", "description": "Removed from GAM", "is_active": False},
            {"id": "lb_political", "name": "Political", "description": "Political advertising", "is_active": False},
        ],
        "last_sync": "2026-10-30T12:00:00+00:00",
    }


def in_id_order(targeting_body: dict[str, Any]) -> dict[str, Any]:
    """*targeting_body* of ``GET .../targeting/all`` with its three lists in ``id`` order, and nothing else changed.

    The route selects each list with no ORDER BY, so the order PostgreSQL returns the rows in is
    no part of what it answers.
    """
    lists = ("customKeys", "audiences", "labels")
    return {**targeting_body, **{key: sorted(targeting_body[key], key=lambda entry: entry["id"]) for key in lists}}


def expected_inventory_tree_body() -> dict[str, Any]:
    """Today's body of ``GET .../inventory/tree``, with no search and no parent, over ``SYNCED_INVENTORY``.

    The roots are the ACTIVE ad units with no parent: Homepage alone, whose ACTIVE child is no
    root and whose INACTIVE and STALE siblings are not ACTIVE. The counts are of ACTIVE items: two
    ad units, and one of each other type. ``last_sync`` is the tenant's latest sync.
    """
    homepage = {
        "id": "au_homepage",
        "name": "Homepage",
        "status": "ACTIVE",
        "code": "homepage",
        "path": ["Homepage"],
        "parent_id": None,
        "has_children": True,
        "matched_search": False,
        "sizes": [{"width": 728, "height": 90}],
        "children": [],
    }
    return {
        "root_units": [homepage],
        "total_units": 1,
        "total_active_count": 2,
        "truncated": False,
        "root_count": 1,
        "search_active": False,
        "matching_count": 0,
        "placements": 1,
        "labels": 1,
        "custom_targeting_keys": 1,
        "audience_segments": 1,
        "last_sync": "2026-10-30T12:00:00+00:00",
    }


def expected_unsynced_body(route_name: str, tenant_id: str) -> dict[str, Any]:
    """Today's body of the inventory view *route_name* for a tenant that holds no synced inventory and ran no sync."""
    never_synced = {
        "total_count": 0,
        "status_counts": {},
        "last_synced": None,
        "last_job_status": None,
        "last_job_time": None,
        "last_job_error": None,
    }
    bodies: dict[str, dict[str, Any]] = {
        "inventory sync check": {"has_inventory": False, "inventory_count": 0, "last_sync": None},
        "sync status API": {"tenant_id": tenant_id, "sync_status": dict.fromkeys(_SYNC_STATUS_TYPES, never_synced)},
        "targeting data API": {"customKeys": [], "audiences": [], "labels": [], "last_sync": None},
        "inventory stats API": {"tenant_id": tenant_id, "stats": {}},
        "inventory tree API": {
            "root_units": [],
            "total_units": 0,
            "total_active_count": 0,
            "truncated": False,
            "root_count": 0,
            "search_active": False,
            "matching_count": 0,
            "placements": 0,
            "labels": 0,
            "custom_targeting_keys": 0,
            "audience_segments": 0,
            "last_sync": None,
        },
    }
    return bodies[route_name]


class AdminTenantScopingEnv(AdminAccountEnv):
    """Test environment for the tenant-scoping scenarios of #2203 and #2204.

    Same two transports as ``AdminAccountEnv``; adds the target tenant, the three caller
    memberships (elsewhere / inactive here / active here, in the role the scenario names),
    a super admin of another tenant, the target tenant's synced inventory, and the routes in
    ``ROUTES``.
    """

    def __init__(self, *, mode: str = "integration", base_url: str | None = None) -> None:
        super().__init__(mode=mode, base_url=base_url)
        self._target: TargetTenant | None = None
        self._state_before_request: TenantState | None = None
        self._cleanups = ExitStack()

    def _setup(self) -> None:
        """No ambient super-admin grant for the member identity, on the app in this process."""
        super()._setup()
        self._cleanups.enter_context(no_configured_super_admins())

    def _cleanup(self) -> None:
        """Delete the tenants this env named through ``_new_tenant_id``, with their rows, and restore the settings.

        The e2e stack's database outlives the scenario: ``_reset_e2e_db`` in
        tests/bdd/conftest.py empties it before the next e2e scenario, and no fixture
        empties it after the last one.
        """
        with self._cleanups:
            super()._cleanup()

    def _new_tenant_id(self, prefix: str) -> str:
        """A unique tenant id whose deletion is registered before the tenant exists."""
        tenant_id = mint_id(prefix)
        self._cleanups.callback(delete_tenant, tenant_id)
        return tenant_id

    # ── Target tenant ─────────────────────────────────────────────────────

    @property
    def target(self) -> TargetTenant:
        assert self._target is not None, "seed_target_tenant() has not run"
        return self._target

    def seed_target_tenant(self) -> TargetTenant:
        """A tenant with one catalogue product, one active media buy and an active member who is not the caller."""
        with bound_factory_session():
            tenant = TenantFactory(tenant_id=self._new_tenant_id("scope"))
            product = ProductFactory(tenant=tenant, product_id=mint_id("prod"))
            buy = MediaBuyFactory(
                tenant=tenant,
                media_buy_id=mint_id("mb"),
                status="active",
                budget=ACTIVE_BUY_BUDGET,
                # Only the principal id is set: PrincipalFactory derives token_hash/token_prefix
                # from it (tests/factories/principal.py), so a unique id is a unique credential.
                principal__principal_id=mint_id("principal"),
            )
            _seed_member(tenant, OTHER_MEMBER_EMAIL, role=NON_MATCHING_ROW_ROLE)
            products_payload = [
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "delivery_type": product.delivery_type,
                }
            ]
            (package,) = buy.raw_request["packages"]
            assert package["product_id"] != product.product_id, "the buy must name a product id the tenant lacks"
            self._target = TargetTenant(
                tenant.tenant_id,
                buy.principal.principal_id,
                buy.principal.name,
                products_payload,
                buy.media_buy_id,
                package["product_id"],
            )
        return self._target

    def seed_target_product(self, product_id: str) -> None:
        """A product of the target tenant under *product_id*, such as the id of a default product."""
        with bound_factory_session() as session:
            ProductFactory(tenant=session.get(Tenant, self.target.tenant_id), product_id=product_id)

    def seed_product_of_other_tenant(self, name: str) -> None:
        """Another tenant's product named *name*, under the product id the target tenant's media buy names."""
        with bound_factory_session():
            other = TenantFactory(tenant_id=self._new_tenant_id("other"))
            ProductFactory(tenant=other, product_id=self.target.media_buy_product_id, name=name)

    def seed_synced_inventory(self) -> None:
        """``SYNCED_INVENTORY`` and ``SYNC_JOBS`` in the target tenant; another tenant's later items and sync job."""
        with bound_factory_session() as session:
            target = session.get(Tenant, self.target.tenant_id)
            other = TenantFactory(tenant_id=self._new_tenant_id("other"))
            for factory, target_rows, other_rows in (
                (GAMInventoryFactory, SYNCED_INVENTORY, OTHER_TENANT_INVENTORY),
                (SyncJobFactory, SYNC_JOBS, OTHER_TENANT_SYNC_JOBS),
            ):
                for tenant, rows in ((target, target_rows), (other, other_rows)):
                    for row in rows:
                        factory(tenant=tenant, **row._asdict())

    def target_state(self) -> TenantState:
        """The target tenant's state now, read from the database."""
        with get_db_session() as session:
            tenant = session.get(Tenant, self.target.tenant_id)
            assert tenant is not None
            return TenantState(tenant_row_counts(session, tenant.tenant_id), tenant.advertising_policy)

    @property
    def state_before_request(self) -> TenantState:
        """The target tenant's state as ``send()`` found it; compare ``target_state()`` against it after a rejection."""
        assert self._state_before_request is not None, "send() has not run"
        return self._state_before_request

    # ── Caller memberships ────────────────────────────────────────────────

    def login_as_member_of_other_tenant(self) -> None:
        """Active ``User`` row in a fresh home tenant, no row in the target."""
        self._authenticate_member(self._new_home_tenant(with_member=True))

    def login_with_target_membership(self, *, is_active: bool, role: UserRole = MEMBER_ROLE) -> None:
        """A ``User`` row in the TARGET tenant with ``is_active`` and *role* as given; session held via a fresh home tenant."""
        home_tenant_id = self._new_home_tenant(with_member=False)
        with bound_factory_session() as session:
            target = session.get(Tenant, self.target.tenant_id)
            _seed_member(target, MEMBER_EMAIL, is_active=is_active, role=role)
        self._authenticate_member(home_tenant_id)

    def login_as_super_admin_of_other_tenant(self) -> None:
        """A super admin signed in at a fresh home tenant, with no row in the target (#2204, AC4 as amended).

        The session carries the keys ``is_super_admin()`` caches after a configured super
        admin's first check (``cached_super_admin``), so the grant reads the same on both
        transports without touching the stack's ``SUPER_ADMIN_EMAILS``. It is the deployment's
        own (Google) sign-in, as every member session here is: a session from a tenant's SSO
        is bound to that tenant and is never a super admin (``session_is_tenant_scoped``).
        """
        home_tenant_id = self._new_home_tenant(with_member=True)
        payload = member_session_payload(MEMBER_EMAIL, home_tenant_id, auth_method=None)
        self.client.state_session({**payload, **cached_super_admin(MEMBER_EMAIL)})

    def _new_home_tenant(self, *, with_member: bool) -> str:
        """A tenant the session is logged in against. Never the target, so the bypass cannot fire."""
        with bound_factory_session():
            home = TenantFactory(tenant_id=self._new_tenant_id("home"))
            if with_member:
                _seed_member(home, MEMBER_EMAIL, role=NON_MATCHING_ROW_ROLE)
            return home.tenant_id

    def _authenticate_member(self, login_tenant_id: str) -> None:
        """State a tenant user's session for ``MEMBER_EMAIL`` against ``login_tenant_id`` (never the target)."""
        self.client.state_session(member_session_payload(MEMBER_EMAIL, login_tenant_id, auth_method=None))

    # ── Requests ──────────────────────────────────────────────────────────

    def send(self, method: str, route_name: str, *, asks_for_json: bool = False) -> AdminResponse:
        """One request to *route_name* of the target tenant.

        API callers send ``Accept: application/json``. *asks_for_json* sends that header
        on a page route too, so a refusal states its reason instead of rendering the 403 page.
        """
        route = ROUTES[route_name]
        assert method in route.methods, f"{route_name} does not serve {method}"
        path = route.path.format(
            tenant_id=self.target.tenant_id, principal_id=self.target.principal_id, advertiser_id=GAM_ADVERTISER_ID
        )
        # Read here, not at seeding: a Given after the Background adds the caller's own row.
        self._state_before_request = self.target_state()
        return self.client.request(
            method,
            path,
            data=dict(route.form or {}) if method == "POST" else None,
            headers={"Accept": "application/json"} if asks_for_json or path.startswith("/api/") else None,
        )

    def reload_policy_page(self) -> AdminResponse:
        """The target tenant's Policy page as the current caller loads it now, as a browser does."""
        return self.client.request("GET", ROUTES["policy page"].path.format(tenant_id=self.target.tenant_id))
