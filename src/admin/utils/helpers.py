"""Utility functions shared across admin UI modules."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from functools import wraps
from typing import TYPE_CHECKING, Literal, NamedTuple, NoReturn, TypeVar

from flask import abort, g, has_request_context, jsonify, make_response, redirect, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from werkzeug.exceptions import Forbidden

from src.admin.csrf import SAFE_METHODS
from src.core.config import get_settings, split_csv
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, UserRole
from src.core.database.repositories.tenant_management_config import TenantManagementConfigRepository
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.logging_config import log_safe
from src.core.operator_errors import OperatorRefusal, StateConflict, UpstreamFailure

T = TypeVar("T")

if TYPE_CHECKING:
    from typing import Any

    from flask import Response

    from src.core.exceptions import AdCPSalesAgentError
    from src.core.tools.media_buy_create import ApprovalResult

logger = logging.getLogger(__name__)


def is_admin_production() -> bool:
    """Return True when admin should behave in production-safe mode.

    One answer for every spelling of "production", so security-sensitive checks
    do not drift between deployment styles.
    """
    return get_settings().runtime.is_production


#: The blueprint name of the test-credential login path (src/admin/blueprints/test_auth.py).


def parse_json_config(config_str):
    """Parse JSON config string."""
    if not config_str:
        return {}
    try:
        return json.loads(config_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def get_tenant_config_from_db(tenant_id):
    """Get tenant configuration from database.

    Args:
        tenant_id: The tenant ID to fetch config for

    Returns:
        dict: The tenant configuration with adapter settings, features, etc.
    """
    if not tenant_id:
        logger.warning("get_tenant_config_from_db called with empty tenant_id")
        return {}

    with get_db_session() as db_session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            logger.warning(f"Tenant not found: {tenant_id}")
            return {}

        # Build config from individual columns
        config = {
            "adapters": {},
            "features": {},
            "creative_engine": {},
            "slack_webhook_url": tenant.slack_webhook_url or "",
            "policy_settings": {},
        }

        # Build adapter config from relationship
        if tenant.adapter_config:
            adapter_obj = tenant.adapter_config
            adapter_type = adapter_obj.adapter_type

            # Build the legacy JSON structure for backward compatibility
            adapter_config = {adapter_type: {"enabled": True}}

            # Add adapter-specific fields
            if adapter_type == "google_ad_manager":
                if adapter_obj.gam_network_code:
                    adapter_config[adapter_type]["network_code"] = adapter_obj.gam_network_code
                # NOTE: gam_company_id removed - advertiser_id is per-principal
                if adapter_obj.gam_trafficker_id:
                    adapter_config[adapter_type]["trafficker_id"] = adapter_obj.gam_trafficker_id
                adapter_config[adapter_type]["manual_approval_required"] = (
                    adapter_obj.gam_manual_approval_required or False
                )
            elif adapter_type == "kevel":
                if adapter_obj.kevel_network_id:
                    adapter_config[adapter_type]["network_id"] = adapter_obj.kevel_network_id
                if adapter_obj.kevel_api_key:
                    adapter_config[adapter_type]["api_key"] = adapter_obj.kevel_api_key
                adapter_config[adapter_type]["manual_approval_required"] = (
                    adapter_obj.kevel_manual_approval_required or False
                )
            elif adapter_type == "triton":
                if adapter_obj.triton_station_id:
                    adapter_config[adapter_type]["station_id"] = adapter_obj.triton_station_id
                if adapter_obj.triton_api_key:
                    adapter_config[adapter_type]["api_key"] = adapter_obj.triton_api_key

            config["adapters"] = adapter_config

        # Build features config from individual columns
        # Note: max_daily_budget moved to currency_limits table (per-currency limits)
        config["features"] = {
            "enable_axe_signals": tenant.enable_axe_signals,
        }

        # Build creative engine config from individual columns
        config["creative_engine"] = {
            "auto_approve_formats": tenant.auto_approve_format_ids or [],
            "human_review_required": tenant.human_review_required,
        }

        # Add policy settings
        if tenant.policy_settings:
            policy_settings = parse_json_config(tenant.policy_settings)
            if policy_settings:
                config["policy_settings"] = policy_settings

        return config


#: How long a cached super-admin answer in the session is trusted before it is checked again.
SUPER_ADMIN_RECHECK_SECONDS = 300


class SuperAdminGrants(NamedTuple):
    """The emails and email domains this deployment makes super admins of, lower-cased."""

    emails: frozenset[str]
    domains: frozenset[str]


def _csv_lower(value: str | None) -> set[str]:
    """The lower-cased items of a comma-separated *value*, as the settings lists read them."""
    return {item.lower() for item in split_csv(value)}


def super_admin_grants() -> SuperAdminGrants:
    """Every email and domain that :func:`is_super_admin` grants super admin through.

    The ``SUPER_ADMIN_EMAILS`` / ``SUPER_ADMIN_DOMAINS`` settings, then the
    ``super_admin_emails`` / ``super_admin_domains`` rows of the global config table,
    the latter read through ``TenantManagementConfigRepository``. A failed lookup fails
    the request rather than answering "not a super admin", which the caller would cache.
    """
    auth_settings = get_settings().auth
    emails = set(auth_settings.super_admin_email_list)
    domains = set(auth_settings.super_admin_domain_list)
    with get_db_session() as db_session:
        config = TenantManagementConfigRepository(db_session)
        emails |= _csv_lower(config.get_value("super_admin_emails"))
        domains |= _csv_lower(config.get_value("super_admin_domains"))
    return SuperAdminGrants(frozenset(emails), frozenset(domains))


#: Session keys that say who the caller is and what they may reach. A sign-in writes a
#: new identity, and none of these may survive from an earlier one in the same browser:
#: a deployment sign-in's ``role``/``is_super_admin`` must not ride along into a tenant's
#: SSO session, nor a tenant SSO's ``auth_method`` into a deployment sign-in.
_IDENTITY_SESSION_KEYS = (
    "user",
    "user_name",
    "user_picture",
    "email",
    "authenticated",
    "auth_method",
    "tenant_id",
    "role",
    "is_super_admin",
    "admin_email",
    "admin_checked_at",
    "is_tenant_admin",
    "available_tenants",
)


def clear_session_identity() -> None:
    """Drop every identity key from the session, ahead of a sign-in writing its own."""
    for key in _IDENTITY_SESSION_KEYS:
        session.pop(key, None)


def session_caller_email() -> str:
    """The email the current session is signed in as, or ``""`` when it is not signed in."""
    return _session_email(session["user"]) if "user" in session else ""


def session_is_tenant_scoped() -> bool:
    """Whether the current request's session was issued by a tenant's own identity provider.

    A tenant admin chooses that tenant's OIDC provider, so the provider vouches for an
    identity INSIDE that tenant and nothing more: the email it asserts can be any string
    the provider's operator likes. Such a session is bound to the tenant it was issued
    for (``session["tenant_id"]``) and never carries deployment-wide privilege.

    False outside a request context: there is no session to be scoped.
    """
    return has_request_context() and session.get("auth_method") == "oidc"


def session_reaches_tenant(tenant_id: str) -> bool:
    """Whether the current session's sign-in method lets it act on *tenant_id* at all.

    A session from a tenant's own identity provider reaches only the tenant it was
    issued for; one that names no tenant reaches none. Every other session is not
    limited here, and the caller goes on to its membership and role checks.
    """
    if not session_is_tenant_scoped():
        return True
    return bool(tenant_id) and session.get("tenant_id") == tenant_id


def is_super_admin(email):
    """Whether the current request's caller, signed in as *email*, is a super admin.

    *email* must be the caller's own: the answer is about the session as much as the
    address. A session issued by a tenant's identity provider is never a super admin,
    whatever email it carries (:func:`session_is_tenant_scoped`), because deployment-wide
    privilege cannot come from an identity provider a tenant admin configured.

    Otherwise the email or its domain is matched against :func:`super_admin_grants`.
    """
    if not email:
        return False

    if session_is_tenant_scoped():
        return False

    email_lower = email.lower()

    # 0. Check session cache first (if available) to avoid redundant checks. The cache is
    # trusted for SUPER_ADMIN_RECHECK_SECONDS only, so a super admin removed from the
    # configured lists loses access within that time rather than for the session's life.
    try:
        fresh = time.time() - float(session.get("admin_checked_at", 0)) < SUPER_ADMIN_RECHECK_SECONDS
        if fresh and session.get("is_super_admin") and session.get("admin_email") == email_lower:
            logger.debug(f"Super admin access granted via session cache: {email}")
            return True
    except (RuntimeError, KeyError):
        # No session context available (e.g., outside request context)
        pass

    grants = super_admin_grants()
    email_domain = email_lower.rpartition("@")[2] if "@" in email_lower else ""
    if email_lower in grants.emails or (email_domain and email_domain in grants.domains):
        logger.debug(f"Super admin access granted: {email}")
        _cache_admin_status(email_lower, True)
        return True

    # Cache negative result too (to avoid repeated expensive checks)
    _cache_admin_status(email_lower, False)
    return False


def _cache_admin_status(email, is_admin):
    """Cache admin status in session if available."""
    try:
        session["is_super_admin"] = is_admin
        session["admin_email"] = email
        session["admin_checked_at"] = time.time()
        if is_admin:
            logger.debug(f"Admin status cached in session: {email}")
    except (RuntimeError, KeyError):
        # Session not available or read-only
        pass


def require_auth(admin_only=False):
    """Decorator to require authentication for routes."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user" not in session:
                logger.info(f"require_auth: No 'user' in session. Session keys: {list(session.keys())}")
                # Store the original URL to redirect back after login
                from flask import request

                return redirect(url_for("auth.login", next=request.url))

            # Store user in g for access in view functions
            g.user = session["user"]

            email = session_caller_email()

            # Check admin requirement
            if admin_only and not is_super_admin(email):
                abort(403)

            # A tenant-scoped session acts only on its own tenant, here as in require_tenant_access.
            if "tenant_id" in kwargs and not session_reaches_tenant(kwargs["tenant_id"]):
                abort(403)

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def _session_email(user_info: object) -> str:
    """The caller's email, whichever shape the session stored.

    A dict from the OIDC/OAuth callbacks, a bare string from older sessions. One place to
    read it, so the decorators do not each carry the isinstance.
    """
    return user_info.get("email", "") if isinstance(user_info, dict) else str(user_info)


def _setup_session_reaches(tenant_id: str) -> bool:
    """Whether the caller's setup-mode session grants access to *tenant_id*.

    Decided from the session alone — NO database read. Reading the tenant's auth_setup_mode
    here would need one, and ``get_db_session()`` is scoped: opening a session inside an
    auth decorator nests within whatever session the caller already holds, and the inner
    exit removes the scoped session and detaches the outer one, discarding rows the caller
    had flushed but not committed. ``enable_oidc`` carries the same rule.

    The scope check needs no database. The session states which tenant it is for, and this
    server signed it, so that statement is as trustworthy as the ``user`` the caller's own
    path reads. A caller who is not a super admin reaches exactly the tenant its session
    names — which is what the approval-refusal tests grade, since a super admin crosses
    tenants by design and a refusal test authenticated as one proves nothing.

    Never true in production: no route in a deployment mints such a session, so the branch
    is unreachable there, and this says so rather than leaving it to be inferred.
    """
    if "test_user" not in session or get_settings().runtime.is_production:
        return False
    return session.get("test_tenant_id") == tenant_id or session.get("test_user_role") == "super_admin"


def role_permits(role: str, method: str, *, minimum: UserRole = UserRole.VIEWER) -> bool:
    """Whether a tenant user with *role* may make a *method* request to a route that needs *minimum*.

    Any write (a method outside ``SAFE_METHODS``) needs at least ``manager``; a route
    can ask for more with *minimum* (``admin`` for managing users, sign-in and the tenant
    itself). A role this code does not know ranks as ``viewer``, so it is read-only.
    """
    ranks = list(UserRole)
    held = UserRole(role) if role in UserRole else UserRole.VIEWER
    needed = (
        ranks.index(minimum) if method in SAFE_METHODS else max(ranks.index(minimum), ranks.index(UserRole.MANAGER))
    )
    return ranks.index(held) >= needed


def caller_reads_json() -> bool:
    """Whether the current request's caller reads a JSON answer rather than an HTML page.

    Decided from the caller, never the route: a browser asking for a page gets HTML, and
    every other caller gets JSON. A page is asked for by a navigation (``Sec-Fetch-Mode:
    navigate``, which every current browser sends on a page load and a form submit) or by an
    ``Accept`` that ranks ``text/html`` strictly above ``application/json``, as a form post
    and an older browser's page load do. Everything else is JSON: a script's ``fetch`` or
    XHR, ``Accept: */*`` (curl, ``requests``), no ``Accept`` at all, a JSON body. A tie is
    JSON. The one answer to this question for the admin UI: the refusals in
    :func:`require_tenant_access`, the admin error handler and the routes that answer both a
    form and a script all ask it.
    """
    if request.headers.get("Sec-Fetch-Mode") == "navigate":
        return False
    accept = request.accept_mimetypes
    return not accept["text/html"] > accept["application/json"]


def json_failure(message: str, status: int, fields: Mapping[str, object] | None = None) -> Response:
    """The admin app's one JSON failure body: *fields*, then ``success`` and ``error``, which no field can replace."""
    return make_response(jsonify({**(fields or {}), "success": False, "error": message}), status)


def _json_refusal(status: int, message: str) -> Response | None:
    """A :func:`json_failure` answer with *status* if the caller reads JSON, else ``None``.

    The one decision for every refusal in :func:`require_tenant_access`; each refusal
    keeps its own answer for a caller that reads HTML.
    """
    return json_failure(message, status) if caller_reads_json() else None


def _forbid(message: str) -> NoReturn:
    """Refuse the request with a 403: a :func:`json_failure` if the caller reads JSON, else the HTML page."""
    raise Forbidden(response=_json_refusal(403, message))


#: How :func:`tenant_access` answers. ``setup_session`` and ``super_admin`` pass without a
#: tenant membership; ``member`` passes on the caller's own active ``User`` row; the rest refuse.
TenantAccess = Literal["setup_session", "super_admin", "member", "unauthenticated", "not_a_member", "role_refused"]

#: The :data:`TenantAccess` answers that grant the request.
TENANT_ACCESS_GRANTED: frozenset[TenantAccess] = frozenset({"setup_session", "super_admin", "member"})


def tenant_access(tenant_id: str, role: UserRole, method: str) -> TenantAccess:
    """Whether the current session may make a *method* request needing *role* on *tenant_id*.

    The one decision behind :func:`require_tenant_access`, callable on its own by a flow
    that must re-check the caller after leaving the request that was authorized (the
    OIDC test callback returns from the identity provider in a new request).

    A database failure reading the caller's ``User`` row propagates to the admin error
    handler, which answers with a reference.
    """
    # A SETUP-MODE SESSION, decided from the session alone — NO DATABASE READ. See
    # _setup_session_reaches for why, and why it is never true in production.
    if _setup_session_reaches(tenant_id):
        return "setup_session"

    if "user" not in session:
        return "unauthenticated"

    # A session from a tenant's own identity provider stops at its own tenant, before the
    # super-admin and membership checks: the email it carries is only as good as that
    # provider, which the tenant's admin chose.
    if not session_reaches_tenant(tenant_id):
        return "not_a_member"

    email = session_caller_email()

    # Check super admin status (is_super_admin handles env + db + session caching internally)
    if is_super_admin(email):
        return "super_admin"

    # The lookup's session closes before the route runs, so what the route raises reaches
    # the admin error handler on its own.
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        user = uow.tenant_config.get_active_user_by_email(email)
        user_role = user.role if user else None

    if user_role is None:
        return "not_a_member"
    if not role_permits(user_role, method, minimum=role):
        return "role_refused"
    return "member"


def tenant_access_refusal(tenant_id: str, *, role: UserRole = UserRole.VIEWER):
    """The answer refusing the current caller access to *tenant_id*, or ``None`` when it may proceed.

    :func:`tenant_access` decides; this answers. :func:`require_tenant_access` applies it to
    a route whose URL names the tenant, and a view that takes the tenant from a query
    parameter or a body calls it directly. A missing sign-in is answered (JSON for a caller
    that reads it, else the tenant's login page); a refusal is raised as a 403, which the
    admin error handler answers.
    """
    logger.info(
        f"Auth check - tenant: {log_safe(tenant_id)}, method: {request.method}, "
        f"has_session: {'user' in session}, has_cookies: {bool(request.cookies)}, session_keys: {list(session.keys())}"
    )

    access = tenant_access(tenant_id, role, request.method)

    if access == "setup_session":
        g.user = session["test_user"]
    elif access == "unauthenticated":
        if (refusal := _json_refusal(401, "Authentication required")) is not None:
            return refusal
        # Redirect to tenant-specific login (preserves tenant context)
        return redirect(url_for("auth.tenant_login", tenant_id=tenant_id, next=request.url))
    elif access == "not_a_member":
        _forbid("Access denied")
    elif access == "role_refused":
        logger.warning(
            "Refused %s %s for tenant %s: role too low",
            log_safe(request.method),
            log_safe(request.path),
            log_safe(tenant_id),
        )
        _forbid("Your role does not allow this")
    return None


def require_tenant_access(*, role: UserRole = UserRole.VIEWER):
    """Decorator applying :func:`tenant_access_refusal` to a route whose URL names the tenant.

    *role* is the least role the route needs, so ``role=UserRole.ADMIN`` limits it to
    tenant admins. Super admins pass regardless. Each refusal is answered in the form the
    caller reads (:func:`caller_reads_json`): JSON, or for a browser the login redirect or
    the HTML page. The decision is :func:`tenant_access`.
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(tenant_id, *args, **kwargs):
            if (refusal := tenant_access_refusal(tenant_id, role=role)) is not None:
                return refusal
            return f(tenant_id, *args, **kwargs)

        # The least role, read by tests/integration/test_admin_role_enforcement.py to list the
        # admin-only routes. ``functools.wraps`` copies the attribute onto any decorator
        # stacked outside this one.
        decorated_function.__dict__["tenant_access_role"] = role
        return decorated_function

    return decorator


def validate_gam_network_response(network):
    """Validate GAM network API response structure."""
    if not network:
        return False, "Network response is empty"

    # Check required fields
    required_fields = ["networkCode", "displayName", "id"]
    for field in required_fields:
        if field not in network:
            return False, f"Missing required field: {field}"

    # Validate field types
    try:
        int(network["networkCode"])
        int(network["id"])
    except (ValueError, TypeError):
        return False, "Network code and ID must be numeric"

    if not isinstance(network["displayName"], str):
        return False, "Display name must be a string"

    return True, None


def validate_gam_user_response(user):
    """Validate GAM user API response structure."""
    if not user:
        return False, "User response is empty"

    # Check required fields
    if "id" not in user:
        return False, "Missing user ID"

    # Validate ID is numeric
    try:
        int(user["id"])
    except (ValueError, TypeError):
        return False, "User ID must be numeric"

    return True, None


def get_custom_targeting_mappings():
    """Get the custom targeting key and value mappings.

    Returns tuple of (key_mappings, value_mappings) dicts.
    """
    # Default mappings for header bidding (common across many publishers)
    key_mappings = {
        "13748922": "hb_pb",
        "14095946": "hb_source",
        "14094596": "hb_format",
    }

    value_mappings = {
        "448589710493": "0.01",
        "448946107548": "freestar",
        "448946356517": "prebid",
        "448946353802": "video",
    }

    return key_mappings, value_mappings


def translate_custom_targeting(custom_targeting_node):
    """Translate GAM custom targeting structure to readable format."""
    if not custom_targeting_node:
        return None

    key_mappings, value_mappings = get_custom_targeting_mappings()

    def translate_node(node):
        if not node:
            return None

        if isinstance(node, dict):
            # Handle dict-based nodes (from tests/API)
            if "logicalOperator" in node:
                # This is a group node with AND/OR logic
                operator = node["logicalOperator"].lower()
                children = []
                if "children" in node and node["children"]:
                    for child in node["children"]:
                        translated_child = translate_node(child)
                        if translated_child:
                            children.append(translated_child)

                if len(children) == 1:
                    return children[0]
                elif len(children) > 1:
                    return {operator: children}
                return None

            elif "keyId" in node:
                # This is a key-value targeting node
                key_id = str(node["keyId"])
                key_name = key_mappings.get(key_id, f"key_{key_id}")

                operator = node.get("operator", "IS")
                value_ids = node.get("valueIds", [])

                # Translate value IDs to names
                values = []
                for value_id in value_ids:
                    value_name = value_mappings.get(str(value_id), str(value_id))
                    values.append(value_name)

                if operator == "IS":
                    return {"key": key_name, "in": values}
                elif operator == "IS_NOT":
                    return {"key": key_name, "not_in": values}
                else:
                    return {"key": key_name, "operator": operator, "values": values}

        elif hasattr(node, "logicalOperator"):
            # Handle SOAP/object-based nodes (from GAM)
            operator = node.logicalOperator.lower()
            children = []
            if hasattr(node, "children") and node.children:
                for child in node.children:
                    translated_child = translate_node(child)
                    if translated_child:
                        children.append(translated_child)

            if len(children) == 1:
                return children[0]
            elif len(children) > 1:
                return {operator: children}
            return None

        elif hasattr(node, "keyId"):
            # This is a SOAP key-value targeting node
            key_id = str(node.keyId)
            key_name = key_mappings.get(key_id, f"key_{key_id}")

            operator = getattr(node, "operator", "IS")
            value_ids = getattr(node, "valueIds", [])

            # Translate value IDs to names
            values = []
            for value_id in value_ids:
                value_name = value_mappings.get(str(value_id), str(value_id))
                values.append(value_name)

            if operator == "IS":
                return {"key": key_name, "in": values}
            elif operator == "IS_NOT":
                return {"key": key_name, "not_in": values}
            else:
                return {"key": key_name, "operator": operator, "values": values}

        return None

    return translate_node(custom_targeting_node)


# ---------------------------------------------------------------------------
# Query limiting
# ---------------------------------------------------------------------------


class LimitedResult(NamedTuple):
    """Result of a limited query — rows plus a truncation flag."""

    rows: list
    truncated: bool


def execute_limited(db_session: Session, stmt: Select, limit: int) -> LimitedResult:
    """Execute *stmt* with a row limit and report whether the result was truncated.

    Returns ``LimitedResult(rows, truncated)`` where *truncated* is ``True``
    when the database returned exactly *limit* rows (meaning more may exist).
    """
    rows = list(db_session.scalars(stmt.limit(limit)).all())
    return LimitedResult(rows=rows, truncated=len(rows) >= limit)


def approve_media_buy_through_writer(media_buy_id: str, tenant_id: str, *, approved_by: str) -> ApprovalResult:
    """Run an approval through the single post-adapter writer and report it to the user.

    Every admin approval route reaches the same three outcomes and says the same thing
    about two of them; only the response shape differs (JSON on one route, a redirect on
    another). Leaving the call and its flash in each route is how three routes came to
    disagree about approval in the first place, so the shared part lives here and each
    caller is left with just its own ``return``.

    Returns the ``ApprovalResult`` so the caller can branch on ``outcome``: it is told
    what happened rather than re-reading the row to find out, which is what stops a route
    holding a media buy across the adapter call.
    """
    from datetime import UTC, datetime

    from flask import flash

    from src.core.tools.media_buy_create import ApprovalOutcome, execute_approved_media_buy

    logger.info(log_safe(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}"))
    approval: ApprovalResult = execute_approved_media_buy(
        media_buy_id,
        tenant_id,
        approved_by=approved_by,
        approved_at=datetime.now(UTC),
    )

    if approval.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES:
        flash(
            f"Media buy approved! Waiting for creative approval before creating in the ad server "
            f"({describe_approval_error(approval.error)}).",
            "info",
        )
    elif not approval.ok:
        described = describe_approval_error(approval.error)
        logger.error(log_safe(f"[APPROVAL] Adapter creation failed for {media_buy_id}: {described}"))
        flash(f"Media buy approved but adapter creation failed: {described}", "error")

    return approval


def approval_error_lines(error: AdCPSalesAgentError[Any]) -> list[str]:
    """The seller's lines for an approval's typed error: its code and message, the field it names, each problem.

    Read off the error, which is the one carrier (``ApprovalResult.error``): a refusal before
    booking is the object a create answers the buyer with, so the seller sees its code,
    ``field`` and ``details.problems`` as the buyer would. Every caller reads these lines: the
    refusal a JSON route raises carries them as its details, and a flash or a log line joins
    them (:func:`describe_approval_error`).
    """
    from src.core.errors.details import ProblemsDetails

    lines = [f"{error.error_code}: {error.message}"]
    if error.field:
        lines.append(f"field {error.field}")
    if isinstance(error.details, ProblemsDetails):
        lines.extend(
            f"{problem.subject_type or 'problem'} {problem.subject_id or ''} {problem.code or ''}".strip()
            for problem in error.details.problems or ()
        )
    return lines


def describe_approval_error(error: AdCPSalesAgentError[Any] | None) -> str:
    """The approval error's lines (:func:`approval_error_lines`) as one line, for a flash or a log record."""
    return "; ".join(approval_error_lines(error)) if error is not None else ""


class MediaBuyApprovalFailed(OperatorRefusal, abstract=True):
    """A media buy the operator approved was not booked; its details are the typed error's lines.

    The error the approval stopped on decides the class, so no route chooses a status:
    :func:`approval_refusal` builds the one that fits, and the admin error handler answers it.
    """


class MediaBuyBookingFailed(MediaBuyApprovalFailed, UpstreamFailure):
    """The ad server failed to book the approved media buy (``AdCPAdapterError``)."""

    MESSAGE = "The ad server could not book the approved media buy"


class MediaBuyApprovalRefused(MediaBuyApprovalFailed, StateConflict):
    """The approved media buy cannot be booked as it stands, and still awaits approval.

    A refusal before booking: a creative its product refuses, a kind the ad server does not
    traffic. The buy's own state conflicts with booking it, which no form value changes.
    """

    MESSAGE = "The media buy cannot be booked as it stands, so it still awaits approval"


def approval_refusal(media_buy_id: str, error: AdCPSalesAgentError[Any]) -> MediaBuyApprovalFailed:
    """The refusal an approval that stopped on *error* answers the operator with, carrying *error*'s lines."""
    from src.core.exceptions import AdCPAdapterError

    refusal = MediaBuyBookingFailed if isinstance(error, AdCPAdapterError) else MediaBuyApprovalRefused
    return refusal(f"Media buy {media_buy_id}: {error.error_code}", details=approval_error_lines(error))
