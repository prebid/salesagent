"""Flask application factory for Admin UI."""

import hmac
import json
import logging
import os
import secrets

import markdown
from flask import Flask, g, jsonify, request, session
from markupsafe import Markup
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix as WerkzeugProxyFix

from src.admin.blueprints.accounts import accounts_bp
from src.admin.blueprints.activity_stream import activity_stream_bp
from src.admin.blueprints.adapters import adapters_bp
from src.admin.blueprints.api import api_bp
from src.admin.blueprints.auth import auth_bp, init_oauth
from src.admin.blueprints.authorized_properties import authorized_properties_bp

# from src.admin.blueprints.tasks import tasks_bp  # Disabled - tasks eliminated in favor of workflow system
from src.admin.blueprints.buyer_routing import buyer_routing_bp
from src.admin.blueprints.core import core_bp
from src.admin.blueprints.creative_agents import creative_agents_bp
from src.admin.blueprints.creatives import creatives_bp
from src.admin.blueprints.format_search import bp as format_search_bp
from src.admin.blueprints.gam import gam_bp
from src.admin.blueprints.inventory import inventory_bp
from src.admin.blueprints.inventory_profiles import inventory_profiles_bp
from src.admin.blueprints.oidc import oidc_bp
from src.admin.blueprints.operations import operations_bp
from src.admin.blueprints.policy import policy_bp
from src.admin.blueprints.principals import principals_bp
from src.admin.blueprints.products import products_bp
from src.admin.blueprints.public import public_bp
from src.admin.blueprints.publisher_partners import publisher_partners_bp
from src.admin.blueprints.scheduling import scheduling_bp
from src.admin.blueprints.schemas import schemas_bp
from src.admin.blueprints.settings import settings_bp, tenant_management_settings_bp
from src.admin.blueprints.signals_agents import signals_agents_bp
from src.admin.blueprints.tenants import tenants_bp
from src.admin.blueprints.users import users_bp
from src.admin.blueprints.workflows import workflows_bp
from src.core.config_loader import is_single_tenant_mode
from src.core.domain_config import (
    get_session_cookie_domain,
    get_tenant_url,
    is_sales_agent_domain,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ADMIN_CSRF_SESSION_KEY = "_admin_csrf_token"
_ADMIN_CSRF_HEADER_NAMES = ("X-CSRF-Token", "X-CSRFToken")
_ADMIN_CSRF_FORM_FIELD = "csrf_token"


def _get_submitted_admin_csrf_token() -> str:
    """Return the CSRF token supplied on an unsafe admin request."""
    for header_name in _ADMIN_CSRF_HEADER_NAMES:
        token = request.headers.get(header_name)
        if token:
            return token
    return request.form.get(_ADMIN_CSRF_FORM_FIELD, "")


def _get_or_create_admin_csrf_token() -> str:
    """Return the browser-session CSRF token used by admin templates."""
    token = session.get(_ADMIN_CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[_ADMIN_CSRF_SESSION_KEY] = token
    return token


def _sanitize_for_log(value, *, max_len: int = 200) -> str:
    """Defang attacker-controllable values before they hit the log formatter.

    The 500 handler interpolates fields that can carry attacker content —
    ``X-Identity-Email`` (used as ``user_email``) and ``request.path`` are
    the obvious ones. Python's stdlib ``logging`` does not strip control
    bytes, so a header value with embedded ``\\n`` would forge a log line.

    Truncates at ``max_len`` and replaces CR/LF/NUL with literal escape
    sequences so a multi-line value can't span log records.
    """
    if value is None:
        return "None"
    if not isinstance(value, str):
        value = str(value)
    if len(value) > max_len:
        value = value[:max_len] + "…"
    return value.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "\\x00")


# Custom ProxyFix for handling X-Script-Name and fixing redirect URLs
class CustomProxyFix:
    """Fix for proxy headers when running behind a reverse proxy with path prefix.

    Also fixes hardcoded URLs in redirects to include the script name prefix.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        # Handle X-Script-Name (standard for mounting path) or X-Forwarded-Prefix
        script_name = environ.get("HTTP_X_SCRIPT_NAME", "")
        if not script_name:
            script_name = environ.get("HTTP_X_FORWARDED_PREFIX", "")

        if script_name:
            # Embedded-mode reverse-proxy quirk: when Storefront sets
            # X-Forwarded-Prefix=/storefront/psa/tenant/<id> AND forwards the
            # original /tenant/<id>/<page> path through unchanged, naively
            # using the prefix as SCRIPT_NAME causes url_for() to emit
            # /storefront/psa/tenant/<id>/tenant/<id>/<page> — the tenant
            # segment doubles because both the prefix and the route URL
            # reference it. Strip the overlapping /tenant/<id> tail from
            # script_name so SCRIPT_NAME stops at the storefront mount and
            # Flask's url_for produces correctly-nested paths.
            #
            # Heuristic: prefix ends with /tenant/<segment> AND PATH_INFO
            # also starts with that exact /tenant/<segment>. Using <segment>
            # generically (not regex-matching tenant_id format) keeps this
            # robust to whatever id scheme the storefront uses.
            path_info = environ.get("PATH_INFO", "")
            if "/tenant/" in script_name:
                _root, _, tail = script_name.rpartition("/tenant/")
                tenant_segment = f"/tenant/{tail}"
                if (
                    _root  # there's something before /tenant/<id>
                    and tail  # tenant_id non-empty
                    and "/" not in tail  # tail is a single segment, not /tenant/X/foo
                    and path_info.startswith(tenant_segment + "/")
                ):
                    script_name = _root  # drop the /tenant/<id> tail

            # Store for use in response wrapper
            self.active_script_name = script_name
            # Set SCRIPT_NAME so Flask knows it's mounted at this path
            environ["SCRIPT_NAME"] = script_name
            # Also ensure PATH_INFO is correct
            if path_info.startswith(script_name):
                environ["PATH_INFO"] = path_info[len(script_name) :]
                if not environ["PATH_INFO"]:
                    environ["PATH_INFO"] = "/"
        else:
            self.active_script_name = ""

        # Wrap start_response to fix redirect headers
        def custom_start_response(status, headers, exc_info=None):
            # Check if this is a redirect and we have a script_name
            if status.startswith("30") and self.active_script_name:
                # Fix Location header to include script_name if needed
                new_headers = []
                for name, value in headers:
                    if name.lower() == "location":
                        # If location starts with / but not /admin, prepend /admin
                        if value.startswith("/") and not value.startswith(self.active_script_name):
                            # Skip external URLs
                            if "://" not in value:
                                value = self.active_script_name + value
                        new_headers.append((name, value))
                    else:
                        new_headers.append((name, value))
                headers = new_headers
            return start_response(status, headers, exc_info)

        return self.app(environ, custom_start_response)


def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder="../../templates", static_folder="../../static")

    # Configuration
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
    app.logger.setLevel(logging.INFO)

    # Configure session cookies for EventSource compatibility
    if os.environ.get("PRODUCTION") == "true":
        app.config["SESSION_COOKIE_SECURE"] = True  # Required for SameSite=None over HTTPS
        app.config["SESSION_COOKIE_HTTPONLY"] = False  # Allow EventSource to access cookies
        app.config["SESSION_COOKIE_SAMESITE"] = "None"  # Required for EventSource cross-origin requests
        # Use root path so session works for both /admin/* and /auth/* (OAuth callbacks)
        app.config["SESSION_COOKIE_PATH"] = "/"
        # Only set cookie domain in multi-tenant mode for subdomain sharing
        # In single-tenant mode, let Flask use the actual request domain
        if not is_single_tenant_mode():
            app.config["SESSION_COOKIE_DOMAIN"] = (
                get_session_cookie_domain()
            )  # Allow cookies across subdomains for OAuth
    else:
        app.config["SESSION_COOKIE_SECURE"] = False  # Allow HTTP in dev
        app.config["SESSION_COOKIE_HTTPONLY"] = True  # Standard setting for dev
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # Works with HTTP in development
        app.config["SESSION_COOKIE_PATH"] = "/"  # Standard root path for dev
        # No domain restriction in dev (localhost)

    # Add custom Jinja2 filters
    def from_json_filter(s):
        """Parse JSON string to Python object."""
        if not s:
            return {}
        try:
            return json.loads(s) if isinstance(s, str) else s
        except (json.JSONDecodeError, TypeError):
            return {}

    def markdown_filter(text):
        """Convert markdown text to HTML."""
        if not text:
            return ""
        # Convert markdown to HTML with extensions for better formatting
        html = markdown.markdown(
            text,
            extensions=["extra", "nl2br"],  # 'extra' adds tables, fenced code, etc. 'nl2br' converts newlines to <br>
        )
        return Markup(html)  # Mark as safe HTML

    app.jinja_env.filters["from_json"] = from_json_filter
    app.jinja_env.filters["markdown"] = markdown_filter

    # Trust proxy headers in production
    if os.environ.get("PRODUCTION") == "true":
        app.config["PREFERRED_URL_SCHEME"] = "https"
        # Force external URLs to use HTTPS
        app.config["SERVER_NAME"] = None  # Let Flask detect from request
        app.config["APPLICATION_ROOT"] = "/"

    # Apply any additional config
    if config:
        app.config.update(config)

    # Apply proxy fixes for production
    if os.environ.get("PRODUCTION") == "true":
        # Create a middleware to copy Fly.io headers to standard headers
        # Fly sends Fly-Forwarded-Proto but Werkzeug expects X-Forwarded-Proto
        class FlyHeadersMiddleware:
            def __init__(self, app):
                self.app = app

            def __call__(self, environ, start_response):
                # Copy Fly-Forwarded-Proto to X-Forwarded-Proto if not already set
                if "HTTP_FLY_FORWARDED_PROTO" in environ and "HTTP_X_FORWARDED_PROTO" not in environ:
                    environ["HTTP_X_FORWARDED_PROTO"] = environ["HTTP_FLY_FORWARDED_PROTO"]
                # Copy Fly-Client-Ip to X-Forwarded-For if not already set
                if "HTTP_FLY_CLIENT_IP" in environ and "HTTP_X_FORWARDED_FOR" not in environ:
                    environ["HTTP_X_FORWARDED_FOR"] = environ["HTTP_FLY_CLIENT_IP"]
                return self.app(environ, start_response)

        # Apply middlewares in correct order (last applied = first to run)
        # 1. WerkzeugProxyFix processes X-Forwarded headers and sets wsgi.url_scheme
        app.wsgi_app = WerkzeugProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)
        # 2. FlyHeadersMiddleware copies Fly headers to X-Forwarded headers BEFORE ProxyFix runs
        app.wsgi_app = FlyHeadersMiddleware(app.wsgi_app)
        # 3. CustomProxyFix handles X-Forwarded-Prefix (runs first, before Fly headers)
        app.wsgi_app = CustomProxyFix(app.wsgi_app)
    else:
        # In development, apply WerkzeugProxyFix too so X-Forwarded-Host /
        # X-Forwarded-Proto from a embedded-mode upstream proxy (Scope3
        # Storefront iframe) are honored. Without it, Flask's automatic
        # redirects (e.g. trailing-slash 308 on /creatives → /creatives/)
        # use ``request.host`` = ``localhost:3091``, leaking the upstream
        # origin into the Location header. ProxyFix is a no-op when the
        # forwarded headers are absent (pure local curl / non-proxied
        # browser hits), so it's safe to always wire.
        app.wsgi_app = WerkzeugProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)
        app.wsgi_app = CustomProxyFix(app.wsgi_app)

    # Initialize OAuth
    init_oauth(app)

    # Initialize Flask-Caching for improved performance
    from flask_caching import Cache

    cache_config = {
        "CACHE_TYPE": "SimpleCache",  # In-memory cache (good for single-process deployments)
        "CACHE_DEFAULT_TIMEOUT": 300,  # 5 minutes default
    }
    app.config.update(cache_config)
    cache = Cache(app)
    app.cache = cache  # Make cache available to blueprints

    # CSRF defense: refuse mutating cookie-authed POSTs from third-party
    # origins and require a positive session-bound token. The session
    # cookie is ``SameSite=None`` for OAuth flow reasons, so the cookie
    # rides cross-origin POSTs — Origin/Referer distinguishes a legitimate
    # admin form submission from an attacker's auto-submitting form on
    # ``evil.example.com`` while the token is defense-in-depth against
    # proxy/header misconfiguration.
    #
    # The guard runs as ``before_request`` on the whole app, but only
    # cookie-authed routes are vulnerable. The structural bypass — no
    # session cookie on the request — keeps every header/token-authed
    # caller out of scope: S2S API-key clients (tenant_management_api,
    # sync_api), embedded mode (upstream proxy auths via X-Identity-*
    # without setting a session cookie, per
    # docs/integration/embedded-mode-operational.md), and any other
    # request that doesn't establish a browser session. Per-route auth
    # decorators (@require_api_key_auth, @require_auth) still enforce
    # their own credentials. Closes #32.
    _CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

    @app.context_processor
    def inject_csrf_token():
        def csrf_token() -> str:
            # Keep anonymous/public pages from minting a session cookie just
            # because base.html renders the meta tag. Authenticated admin
            # sessions already have keys in Flask's session object.
            if not session:
                return ""
            return _get_or_create_admin_csrf_token()

        return {"csrf_token": csrf_token}

    @app.before_request
    def enforce_admin_csrf():
        from flask import abort, request

        if app.config.get("TESTING"):
            # Blanket bypass under TESTING=True so the thousands of
            # legacy admin tests that POST without an Origin header
            # stay green. Tests that need to exercise the production
            # CSRF path (see test_admin_csrf_global.py) flip TESTING
            # off explicitly via a per-test fixture.
            return None

        if request.method in _CSRF_SAFE_METHODS:
            return None

        # CSRF can only be mounted when the victim's browser
        # auto-attaches a session cookie to the attacker's cross-
        # origin POST. If this request carries no session cookie,
        # there is nothing to ride — header/token-authed callers
        # (S2S API-key clients like tenant_management_api/sync_api,
        # upstream-proxy embedded mode, the GAM reporting API
        # without a logged-in session) land here. Per-route auth
        # decorators (@require_api_key_auth, @require_auth) still
        # enforce their own credentials, so a missing session
        # cookie does not grant access — it only declines to apply
        # browser-CSRF logic that doesn't fit the threat shape.
        #
        # Bypass is structural (cookie presence is set by the
        # browser/proxy, not the attacker's POST body), so it can't
        # be forged by an attacker on a cookie-authed admin route.
        session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        if not request.cookies.get(session_cookie_name):
            return None

        candidate = request.headers.get("Origin") or request.headers.get("Referer") or ""
        expected = request.host_url.rstrip("/")
        if candidate and (candidate == expected or candidate.startswith(expected + "/")):
            expected_token = session.get(_ADMIN_CSRF_SESSION_KEY)
            submitted_token = _get_submitted_admin_csrf_token()
            if (
                isinstance(expected_token, str)
                and submitted_token
                and hmac.compare_digest(expected_token, submitted_token)
            ):
                return None

            logger.warning(
                "Refusing admin %s to %s with missing/invalid CSRF token — origin=%r referer=%r host_url=%r",
                request.method,
                request.path,
                request.headers.get("Origin"),
                request.headers.get("Referer"),
                expected,
            )
            abort(403, description="Admin CSRF token missing or invalid.")

        logger.warning(
            "Refusing cross-origin admin %s to %s — origin=%r referer=%r host_url=%r",
            request.method,
            request.path,
            request.headers.get("Origin"),
            request.headers.get("Referer"),
            expected,
        )
        abort(403, description="Cross-origin admin request refused. See issue #32.")

    # Diagnostic: embedded-mode requests must include X-Forwarded-Prefix
    # (and/or X-Script-Name) so url_for() generates URLs the upstream
    # proxy can route back to. Without it, redirects to ``/tenant/<id>/...``
    # bypass the proxy mount (e.g. ``/storefront/psa/``) and land on a
    # path the upstream's own router can't serve — producing an opaque
    # "Cannot GET ..." or "Internal Server Error" page that's easy to
    # mistake for a salesagent bug. Warn once per such request so the
    # storefront operator sees the misconfiguration in salesagent logs.
    # Per docs/integration/embedded-mode-identity-contract.md:124, the
    # upstream is required to forward this header. The salesagent cannot
    # safely infer the mount from request data alone, so we surface the
    # gap loudly rather than guessing.
    @app.before_request
    def warn_embedded_missing_prefix():
        from flask import request

        if app.config.get("TESTING"):
            return None
        if not request.headers.get("X-Identity-Subject"):
            return None
        has_prefix = bool(request.headers.get("X-Forwarded-Prefix") or request.headers.get("X-Script-Name"))
        if has_prefix:
            return None
        logger.warning(
            "[EMBEDDED_PREFIX_MISSING] Embedded auth present (X-Identity-Subject set) "
            "but no X-Forwarded-Prefix/X-Script-Name header on %s %s — generated "
            "redirect URLs will NOT include the proxy mount and will land outside "
            "it. See docs/integration/embedded-mode-identity-contract.md.",
            request.method,
            request.path,
        )

    # Redirect external domain /admin requests to tenant subdomain
    @app.before_request
    def redirect_external_domain_admin():
        """Redirect /admin/* requests from external domains to tenant subdomain.

        External domains (via Approximated) should not serve admin UI due to OAuth cookie issues.
        Instead, redirect to the tenant's subdomain where OAuth works correctly.
        """
        from flask import redirect, request

        from src.core.config_loader import get_tenant_by_virtual_host

        # Check if this is an /admin request
        # Note: CustomProxyFix middleware strips /admin from request.path, so we check script_root
        # In production with SCRIPT_NAME=/admin, script_root will be '/admin'
        # But we need to also check that the path isn't just root (/)
        is_admin_request = (request.script_root == "/admin" and request.path != "/") or request.path.startswith(
            "/admin"
        )
        if not is_admin_request:
            return None

        # Check for Apx-Incoming-Host header (indicates request from Approximated)
        apx_host = request.headers.get("Apx-Incoming-Host") or request.headers.get("apx-incoming-host")
        if not apx_host:
            logger.debug(f"No Apx-Incoming-Host header for /admin request: {request.path}")
            return None  # Not from Approximated, allow normal routing

        # Check if it's an external domain (not part of sales agent domain)
        if is_sales_agent_domain(apx_host):
            logger.debug(f"Subdomain request to /admin, allowing: {apx_host}")
            return None  # Subdomain request, allow normal routing

        # External domain detected - redirect to tenant subdomain
        logger.info(f"External domain /admin request detected: {apx_host} -> {request.path}")
        tenant = get_tenant_by_virtual_host(apx_host)
        if not tenant:
            logger.warning(f"No tenant found for external domain: {apx_host}")
            return None  # Can't determine tenant, let normal routing handle it

        tenant_subdomain = tenant.get("subdomain")
        if not tenant_subdomain:
            logger.warning(f"Tenant {tenant.get('tenant_id')} has no subdomain configured")
            return None  # No subdomain configured, let normal routing handle it

        # Build redirect URL to tenant subdomain
        # Note: request.full_path is relative to script_root, so we need to add /admin back
        path_with_admin = (
            f"/admin{request.full_path}" if not request.full_path.startswith("/admin") else request.full_path
        )

        if os.environ.get("PRODUCTION") == "true":
            redirect_url = f"{get_tenant_url(tenant_subdomain)}{path_with_admin}"
        else:
            # Local dev: Use localhost with port (unified FastAPI port)
            port = os.environ.get("ADCP_SALES_PORT", "8080")
            redirect_url = f"http://{tenant_subdomain}.localhost:{port}{path_with_admin}"

        logger.info(f"Redirecting external domain {apx_host}/admin to subdomain: {redirect_url}")
        return redirect(redirect_url, code=302)

    # Debug: Log Set-Cookie headers on auth-related responses
    @app.after_request
    def log_auth_cookies(response):
        """Log Set-Cookie headers for auth-related routes to debug session persistence."""
        # Only log for auth-related paths
        if request.path.startswith(("/auth", "/login", "/admin")):
            set_cookies = response.headers.getlist("Set-Cookie")
            if set_cookies:
                # Log just the cookie names and domain/path attributes (not values for security)
                for cookie in set_cookies:
                    # Parse cookie to show name and attributes
                    parts = cookie.split(";")
                    cookie_name = parts[0].split("=")[0] if parts else "unknown"
                    attrs = "; ".join(p.strip() for p in parts[1:] if p.strip())
                    logger.warning(f"[SESSION_DEBUG] Set-Cookie on {request.path}: name={cookie_name}, attrs=[{attrs}]")
            else:
                # Only log if session was modified
                from flask import session

                if session.modified:
                    logger.warning(
                        f"[SESSION_DEBUG] NO Set-Cookie on {request.path} "
                        f"(session.modified={session.modified}, keys={list(session.keys())})"
                    )
        return response

    # 500 error handler: log the full traceback + request context so a
    # production crash is diagnoseable without re-attaching a debugger.
    # Without this handler, Flask hands the exception back to the upstream
    # WSGI server (WSGIMiddleware → Starlette) which renders a generic
    # "Internal Server Error" page with zero context — and the upstream
    # proxy (e.g. storefront) often substitutes its OWN error page on top,
    # making it look like a salesagent bug when the salesagent log is the
    # only place the real traceback lives.
    #
    # Registered against ``Exception`` (not just 500) so application
    # errors that Flask hasn't already mapped to an HTTPException are
    # caught — Flask invokes 500 handlers AFTER converting un-mapped
    # exceptions, but only if the handler is registered for the original
    # exception class. ``Exception`` is the most permissive.
    @app.errorhandler(Exception)
    def handle_uncaught_exception(exc):
        # Let HTTPExceptions (404/403/etc.) render their own pages —
        # those are intentional, not internal errors.
        if isinstance(exc, HTTPException):
            return exc

        # Stable short ID so a user can paste it into a support ticket
        # and ops can grep the log.
        error_id = secrets.token_hex(6)

        # Resolve user email best-effort from g.user (embedded mode) or
        # the OAuth session dict. Never fail the handler over this — the
        # whole point of the handler is to surface the OUTER exception,
        # so a session-shape oddity here would only mask the real signal.
        user_email = "unknown"
        try:
            user = getattr(g, "user", None)
            if isinstance(user, dict):
                user_email = user.get("email", "unknown")
            elif isinstance(user, str):
                user_email = user
            elif "user" in session:
                sess_user = session["user"]
                user_email = sess_user.get("email", "unknown") if isinstance(sess_user, dict) else str(sess_user)
        except Exception:
            logger.debug("user_email resolution failed in 500 handler", exc_info=True)

        # tenant_id from view args is the most reliable source — the
        # decorator chain stamps it into kwargs for every tenant route.
        tenant_id = None
        try:
            view_args = request.view_args or {}
            tenant_id = view_args.get("tenant_id")
        except Exception:
            logger.debug("tenant_id resolution failed in 500 handler", exc_info=True)

        # ``user_email`` and ``request.path`` are attacker-controllable
        # (X-Identity-Email header and the URL respectively). Sanitize
        # before interpolating so a header with embedded ``\n`` can't
        # forge a log line. ``request.endpoint`` / ``script_root`` /
        # ``tenant_id`` are server-controlled but cheap to defang too.
        logger.error(
            "[ERROR_%s] Uncaught exception in %s %s — tenant_id=%r user=%r endpoint=%r script_root=%r",
            error_id,
            _sanitize_for_log(request.method, max_len=10),
            _sanitize_for_log(request.path, max_len=500),
            _sanitize_for_log(tenant_id, max_len=100),
            _sanitize_for_log(user_email, max_len=200),
            _sanitize_for_log(request.endpoint, max_len=100),
            _sanitize_for_log(request.script_root, max_len=200),
            exc_info=True,
        )

        # Return a JSON envelope if the client asked for it; otherwise
        # plain text. Avoid rendering a template — template rendering
        # can itself fail and we never want a 500 handler to throw.
        accept = request.headers.get("Accept", "")
        body = (
            f"Internal Server Error\n\n"
            f"Error ID: {error_id}\n"
            f"This has been logged. Please include the Error ID when reporting.\n"
        )
        if "application/json" in accept:
            return jsonify({"error": "internal_server_error", "error_id": error_id}), 500
        return body, 500, {"Content-Type": "text/plain; charset=utf-8"}

    # Add context processor to make script_name and tenant available in templates
    @app.context_processor
    def inject_context():
        """Make the script_name (e.g., /admin) and current tenant available in all templates."""
        from flask import g, session
        from sqlalchemy import func, select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Product, Tenant
        from src.core.domain_config import get_sales_agent_domain, get_support_email
        from src.core.version import get_build_info

        context = {}

        context["script_name"] = request.script_root or request.environ.get("SCRIPT_NAME", "")

        # Build/commit stamp surfaced in the base.html footer so bug
        # reports can be traced back to the exact image build.
        context["build_info"] = get_build_info()

        # Inject support email (configurable via SUPPORT_EMAIL env var)
        context["support_email"] = get_support_email()

        # Inject sales agent domain for URL generation in templates
        context["sales_agent_domain"] = get_sales_agent_domain() or "example.com"

        # Embedded-mode chrome flag — true when this request authorized via
        # the X-Identity-* bypass (sprint 2).
        embedded_user = isinstance(getattr(g, "user", None), dict) and bool(g.user.get("embedded_mode"))
        context["embedded_mode"] = embedded_user

        # ``embedded`` is the broader iframe-rendering flag — true when:
        # (a) the upstream proxy passes ``?embedded=1`` on the iframe URL
        #     (explicit, per-load opt-in; works for any tenant), OR
        # (b) the request authorized via X-Identity-* (embedded mode is
        #     always embedded in the upstream proxy's chrome).
        # Templates use this to hide the salesagent's own top nav strip
        # and any global controls (logout / switch tenant) — the upstream
        # proxy owns those in its outer chrome. Per-page sub-nav (breadcrumbs,
        # action buttons) stays.
        explicit_embed = request.args.get("embedded") in ("1", "true", "yes")
        context["embedded"] = explicit_embed or embedded_user

        # Inject fresh tenant data if user is logged in with a tenant.
        # First check the session, then fall back to the URL ``tenant_id``
        # route argument — admin pages are scoped to a tenant via the URL,
        # not via the session, in test/embedded flows.
        tenant_id = session.get("tenant_id")
        if not tenant_id and request.view_args:
            tenant_id = request.view_args.get("tenant_id")
        if tenant_id and tenant_id != "*":
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                    tenant = db_session.scalars(stmt).first()
                    if tenant:
                        context["tenant"] = tenant
                        context["nav_product_count"] = (
                            db_session.scalar(select(func.count()).select_from(Product).filter_by(tenant_id=tenant_id))
                            or 0
                        )
            except Exception as e:
                logger.warning(f"Could not load tenant {tenant_id} for context: {e}")

        # Resolve the embed-mode host-link override (header > tenant
        # column > None). Read by ``base.html`` to render the leftmost
        # subnav slot when the upstream proxy has configured one.
        from src.admin.utils.breadcrumbs import resolve_embed_breadcrumb_root
        from src.admin.utils.embedded_mode_auth import is_embedded_view

        tenant_for_breadcrumb = context.get("tenant") or getattr(g, "tenant", None)
        embed_root = resolve_embed_breadcrumb_root(tenant_for_breadcrumb)
        context["embed_breadcrumb_root"] = embed_root

        # Effective embedded-rendering flag for templates. Combines the
        # per-request signal (header auth) with the persistent tenant flag
        # so preview requests on a non-embedded tenant render the same
        # chrome/lock surfaces as a permanently-embedded tenant. Templates
        # should prefer ``embedded_view`` over ``tenant.is_embedded`` for
        # rendering decisions; the raw flag is still exposed for tenant-
        # policy expressions that describe the tenant itself.
        context["embedded_view"] = is_embedded_view(tenant_for_breadcrumb)

        # Headless-direction capability helpers. Templates use these to
        # gate subsections that have been taken over by the storefront on
        # this embedded instance — e.g. ``{% if publisher_owns('slack') %}``.
        # No-op on open instances (always returns publisher-owned).
        from src.admin.utils.embedded_capabilities import (
            INTEGRATION_CAPABILITIES,
            capability_owner,
            publisher_owns,
            publisher_owns_any,
        )

        context["capability_owner"] = capability_owner
        context["integration_capabilities"] = INTEGRATION_CAPABILITIES
        context["publisher_owns"] = publisher_owns
        context["publisher_owns_any"] = publisher_owns_any

        return context

    # Iframe embedding policy. ``MANAGED_MODE_FRAME_ANCESTORS`` is a CSP
    # frame-ancestors directive value (e.g., ``'self' https://*.scope3.com``).
    # Set on embedded-mode deployments to allow the upstream Storefront to
    # embed the admin UI as an iframe; legacy deployments leave it unset
    # and the existing X-Frame-Options-less behavior persists.
    @app.after_request
    def apply_frame_ancestors(response):
        ancestors = os.environ.get("MANAGED_MODE_FRAME_ANCESTORS")
        if ancestors:
            existing = response.headers.get("Content-Security-Policy")
            directive = f"frame-ancestors {ancestors}"
            if existing:
                response.headers["Content-Security-Policy"] = f"{existing}; {directive}"
            else:
                response.headers["Content-Security-Policy"] = directive
        return response

    # Register blueprints
    app.register_blueprint(public_bp)  # Public routes (no auth required) - MUST BE FIRST
    app.register_blueprint(core_bp)  # Core routes (/, /health, /static)
    app.register_blueprint(auth_bp)  # No url_prefix - auth routes are at root
    # OIDC/OAuth routes at /auth/oidc — skipped on embedded instances where
    # identity comes from X-Identity-* headers, not per-tenant OIDC config.
    # Sprint 7 Phase 4c.
    from src.admin.utils.embedded_mode_auth import is_managed_instance

    if not is_managed_instance():
        app.register_blueprint(oidc_bp)
    app.register_blueprint(tenant_management_settings_bp)  # Tenant management settings at /settings
    app.register_blueprint(tenants_bp, url_prefix="/tenant")
    app.register_blueprint(buyer_routing_bp)  # /tenant/<tid>/buyer-routing — Sprint 5 workstream B
    app.register_blueprint(accounts_bp, url_prefix="/tenant/<tenant_id>/accounts")
    app.register_blueprint(products_bp, url_prefix="/tenant/<tenant_id>/products")
    app.register_blueprint(principals_bp, url_prefix="/tenant/<tenant_id>")
    app.register_blueprint(users_bp)  # Already has url_prefix in blueprint
    app.register_blueprint(gam_bp)
    app.register_blueprint(operations_bp, url_prefix="/tenant/<tenant_id>")
    app.register_blueprint(creatives_bp, url_prefix="/tenant/<tenant_id>/creatives")
    app.register_blueprint(policy_bp, url_prefix="/tenant/<tenant_id>/policy")
    app.register_blueprint(settings_bp, url_prefix="/tenant/<tenant_id>/settings")
    app.register_blueprint(
        adapters_bp
    )  # No url_prefix - routes define their own paths like /adapters/{adapter}/config/{tenant_id}/{product_id}
    app.register_blueprint(authorized_properties_bp, url_prefix="/tenant")
    app.register_blueprint(creative_agents_bp, url_prefix="/tenant/<tenant_id>/creative-agents")
    app.register_blueprint(signals_agents_bp, url_prefix="/tenant/<tenant_id>/signals-agents")
    app.register_blueprint(inventory_bp)  # Has its own internal routing
    app.register_blueprint(inventory_profiles_bp, url_prefix="/tenant/<tenant_id>/inventory-profiles")
    try:
        from src.admin.blueprints.tenant_signals import tenant_signals_bp

        app.register_blueprint(tenant_signals_bp, url_prefix="/tenant/<tenant_id>/signals")
    except ImportError:
        logger.warning("tenant_signals blueprint not found")
    app.register_blueprint(publisher_partners_bp, url_prefix="/tenant")  # Publisher partnerships
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(format_search_bp)  # Format search API (/api/formats)
    app.register_blueprint(activity_stream_bp)  # SSE endpoints - Flask handles /admin via script_name from nginx proxy
    app.register_blueprint(schemas_bp)  # JSON Schema validation service
    app.register_blueprint(scheduling_bp)  # /admin/scheduling cross-adapter view (#382 Stage 4)
    app.register_blueprint(workflows_bp, url_prefix="/tenant")  # Workflow approval and review
    # app.register_blueprint(tasks_bp)  # Tasks management - Disabled, tasks eliminated in favor of workflow system

    # Import and register existing blueprints
    try:
        from src.admin.tenant_management_api import tenant_management_api

        app.register_blueprint(tenant_management_api)
    except ImportError:
        logger.warning("tenant_management_api blueprint not found")

    try:
        from src.admin.composition_api import composition_api

        app.register_blueprint(composition_api)
    except ImportError:
        logger.warning("composition_api blueprint not found")

    try:
        from src.admin.sync_api import sync_api

        app.register_blueprint(sync_api, url_prefix="/api/sync")
    except ImportError:
        logger.warning("sync_api blueprint not found")

    try:
        from src.adapters.gam_reporting_api import gam_reporting_api

        app.register_blueprint(gam_reporting_api)
    except ImportError:
        logger.warning("gam_reporting_api blueprint not found")

    # Register adapter-specific routes
    register_adapter_routes(app)

    # Register GAM inventory endpoints
    try:
        from src.services.gam_inventory_service import create_inventory_endpoints

        create_inventory_endpoints(app)
        logger.info("Registered GAM inventory endpoints")
    except ImportError:
        logger.warning("gam_inventory_service not found")

    return app


def register_adapter_routes(app):
    """Register adapter-specific configuration routes."""
    try:
        # Import adapter modules that have UI routes
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.adapters.mock_ad_server import MockAdServer

        # Register routes for each adapter that supports UI routes
        # Note: We skip instantiation errors since routes are optional
        adapter_configs = [
            (GoogleAdManager, {"config": {}, "principal": None}),
            (MockAdServer, {"principal": None, "dry_run": False}),
        ]

        for adapter_class, kwargs in adapter_configs:
            try:
                # Try to create instance for route registration
                adapter_instance = adapter_class(**kwargs)
                if hasattr(adapter_instance, "register_ui_routes"):
                    adapter_instance.register_ui_routes(app)
                    logger.info(f"Registered UI routes for {adapter_class.__name__}")
            except Exception as e:
                # This is expected for some adapters that require specific config
                logger.debug(f"Could not register {adapter_class.__name__} routes: {e}")

    except Exception as e:
        logger.warning(f"Error importing adapter modules: {e}")
