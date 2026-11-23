"""Flask application factory for Admin UI."""

import json
import logging
import posixpath
from functools import partial
from typing import Any

from flask import Flask, abort, request
from werkzeug.middleware.proxy_fix import ProxyFix as WerkzeugProxyFix

from src.admin.blueprints.accounts import accounts_bp
from src.admin.blueprints.activity_stream import activity_stream_bp
from src.admin.blueprints.adapters import adapters_bp
from src.admin.blueprints.api import api_bp
from src.admin.blueprints.auth import auth_bp, init_oauth
from src.admin.blueprints.authorized_properties import authorized_properties_bp
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
from src.admin.blueprints.schemas import schemas_bp
from src.admin.blueprints.settings import settings_bp, tenant_management_settings_bp
from src.admin.blueprints.signals_agents import signals_agents_bp
from src.admin.blueprints.signing_keys import signing_keys_bp

# from src.admin.blueprints.tasks import tasks_bp  # Disabled - tasks eliminated in favor of workflow system
from src.admin.blueprints.tenants import ALLOWED_FAVICON_EXTENSIONS, tenants_bp
from src.admin.blueprints.users import users_bp
from src.admin.blueprints.workflows import workflows_bp
from src.admin.csrf import init_csrf_protection
from src.admin.utils.operator_errors import register_error_handler
from src.core.config import load_settings
from src.core.config_loader import is_single_tenant_mode
from src.core.domain_config import (
    get_session_cookie_domain,
)
from src.core.http_utils import safe_url
from src.core.media_channels import media_channel_options
from src.core.operator_errors import best_effort

logger = logging.getLogger(__name__)

#: Hosts the admin pages' scripts may fetch from, besides their own origin. The Approximated
#: DNS widget on the settings page calls its own API (``api_url`` in tenant_settings.html).
ADMIN_CONNECT_SOURCES = ("https://cloud.approximated.app",)

#: Sent on every admin response (see ``set_content_security_policy`` in ``create_app``).
ADMIN_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "form-action 'self'",
        " ".join(["connect-src 'self'", *ADMIN_CONNECT_SOURCES]),
    ]
)

#: Tenant-uploaded favicons: a document there may run nothing and load nothing.
UPLOADED_FILE_CONTENT_SECURITY_POLICY = "sandbox; default-src 'none'"
#: The endpoints that serve files from static/: Flask's own and core.send_static.
_STATIC_ENDPOINTS = {"static": "filename", "core.send_static": "path"}


def _uploaded_favicon_name(endpoint: str | None, view_args: dict | None) -> str | None:
    """The normalized, lower-cased static path if this request serves a file under favicons/; else None.

    Read from the routed file name, not the URL: a server decodes ``%2F`` before routing,
    and ``send_from_directory`` resolves ``./`` and ``../``, so ``/static/.%2Ffavicons/x``
    and ``/static/a/..%2Ffavicons/x`` reach the same file as ``/static/favicons/x``.
    """
    arg = _STATIC_ENDPOINTS.get(endpoint or "")
    if arg is None:
        return None
    name = str((view_args or {}).get(arg, "")).replace("\\", "/")
    normalized = posixpath.normpath("/" + name).lstrip("/").lower()
    return normalized if normalized.startswith("favicons/") else None


def tenant_favicon_href(url: Any, script_root: str) -> str | None:
    """Where a page loads the favicon stored as *url* (``tenant.favicon_url``) from, or None for the default.

    An uploaded favicon is a path under the admin root, and only the formats an upload
    accepts are served (``refuse_uploaded_svg``); any other URL is the one the tenant gave.
    The caller still writes it through ``safe_url``. A page without a tenant passes an
    undefined, which is falsy.
    """
    if not url:
        return None
    if not url.startswith("/"):
        return url
    return script_root + url if url.rsplit(".", 1)[-1].lower() in ALLOWED_FAVICON_EXTENSIONS else None


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
            # Store for use in response wrapper
            self.active_script_name = script_name
            # Set SCRIPT_NAME so Flask knows it's mounted at this path
            environ["SCRIPT_NAME"] = script_name
            # Also ensure PATH_INFO is correct
            path_info = environ.get("PATH_INFO", "")
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


def create_app(config=None, settings=None):
    """Create and configure the Flask application.

    The composition root for the admin UI: the app is composed from *settings* (a
    :class:`src.core.config.Settings`). A root that already read the environment
    (src/app.py) hands its object in; the standalone admin server and the tests let this
    factory read it, once, here. The signature stays unannotated on purpose: annotating it
    puts the factory body under mypy, and the body carries Flask ``wsgi_app`` and ``cache``
    assignments mypy rejects that are not this change's to fix.
    """
    settings = settings or load_settings()
    is_production = settings.runtime.is_production

    app = Flask(__name__, template_folder="../../templates", static_folder="../../static")

    # Configuration
    app.secret_key = settings.runtime.flask_secret_key
    app.logger.setLevel(logging.INFO)

    # Session cookies. Lax keeps the cookie off cross-site posts, frames and fetches while
    # still sending it on top-level navigations, which is all the OAuth/OIDC callbacks need
    # (they arrive as top-level GETs). The admin UI's subdomains are same-site, so a shared
    # SESSION_COOKIE_DOMAIN is unaffected. No script reads the session cookie: same-origin
    # fetch and EventSource send it without JavaScript access, so it is HttpOnly.
    # init_csrf_protection below is the main CSRF defence; Lax backs it up.
    if is_production:
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
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

    app.jinja_env.filters["from_json"] = from_json_filter
    app.jinja_env.filters["safe_url"] = safe_url

    # Trust proxy headers in production
    if is_production:
        app.config["PREFERRED_URL_SCHEME"] = "https"
        # Force external URLs to use HTTPS
        app.config["SERVER_NAME"] = None  # Let Flask detect from request
        app.config["APPLICATION_ROOT"] = "/"

    # Apply any additional config
    if config:
        app.config.update(config)

    # Apply proxy fixes for production
    if is_production:
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
        # In development, still apply custom proxy fix if needed
        app.wsgi_app = CustomProxyFix(app.wsgi_app)

    # Refuse cross-origin state-changing requests (CSRF) before any view runs
    init_csrf_protection(app)

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

    # NO before_request hook for "external domain" /admin requests, and there is nothing
    # for one to do: a tenant is served at the host it declares, so there is no second
    # address to bounce an /admin request to, and the edge folds the Approximated vendor
    # header into Host and drops it, so there is no second host to classify either. A hook
    # that computes a classification in order to log a value nothing acts on is a cost on
    # every request.

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

    @app.after_request
    def set_content_security_policy(response):
        """A Content-Security-Policy that the current pages can live with, and ``nosniff``.

        The admin pages rely on inline scripts and inline event handlers, so this cannot
        restrict script sources yet, and it does not stop a script that is already running.
        It limits what the pages can do: no plugins, no ``<base>`` that re-points every
        relative URL, no framing by another site, forms post only to this origin, and
        fetch/XHR/EventSource/WebSocket reach only this origin and ``ADMIN_CONNECT_SOURCES``.
        A tenant-uploaded favicon gets a sandbox that runs and loads nothing.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if _uploaded_favicon_name(request.endpoint, request.view_args) is not None:
            response.headers["Content-Security-Policy"] = UPLOADED_FILE_CONTENT_SECURITY_POLICY
        else:
            response.headers.setdefault("Content-Security-Policy", ADMIN_CONTENT_SECURITY_POLICY)
        return response

    @app.before_request
    def refuse_uploaded_svg():
        """Under favicons/ only the raster formats an upload accepts are served.

        An SVG uploaded before uploads were limited to raster formats can still be on disk;
        an SVG is a document that can carry script, and this is the admin origin, so it is
        never served. Nothing deletes the file: refusing it here is enough.
        """
        name = _uploaded_favicon_name(request.endpoint, request.view_args)
        if name is not None and name.rsplit(".", 1)[-1] not in ALLOWED_FAVICON_EXTENSIONS:
            abort(404)

    # Add context processor to make script_name and tenant available in templates
    @app.context_processor
    def inject_context():
        """Make the script_name (e.g., /admin) and current tenant available in all templates."""
        from flask import session
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant
        from src.core.domain_config import get_support_email

        context = {}

        context["script_name"] = request.script_root or request.environ.get("SCRIPT_NAME", "")
        context["tenant_favicon_href"] = partial(tenant_favicon_href, script_root=context["script_name"])

        # Inject support email (configurable via SUPPORT_EMAIL env var)
        context["support_email"] = get_support_email()

        # The product forms' channel picker iterates this, so it offers exactly the
        # MediaChannel enum (GH #1532).
        context["media_channel_options"] = media_channel_options()

        # NO sales_agent_domain. Templates built per-tenant URLs as
        # `https://{{ tenant.subdomain }}.{{ sales_agent_domain }}/...`, which is the
        # derivation removes — and its `or "example.com"` default made
        # every such URL a plausible-looking fiction when the setting was unset. A tenant's
        # URL comes from `tenant.virtual_host`, the host it is actually served at.

        # Inject fresh tenant data if user is logged in with a tenant. Every page renders
        # through here, the error page included, so a failed lookup must not fail the render.
        tenant_id = session.get("tenant_id")
        if tenant_id:
            with best_effort(f"Could not load tenant {tenant_id} for the page context"):
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                    tenant = db_session.scalars(stmt).first()
                    if tenant:
                        context["tenant"] = tenant

        return context

    # Register blueprints
    app.register_blueprint(public_bp)  # Public routes (no auth required) - MUST BE FIRST
    app.register_blueprint(core_bp)  # Core routes (/, /health, /static)
    app.register_blueprint(auth_bp)  # No url_prefix - auth routes are at root
    app.register_blueprint(oidc_bp)  # OIDC/OAuth routes at /auth/oidc
    app.register_blueprint(tenant_management_settings_bp)  # Tenant management settings at /settings
    app.register_blueprint(tenants_bp, url_prefix="/tenant")
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
    app.register_blueprint(signing_keys_bp, url_prefix="/tenant/<tenant_id>/signing-keys")
    app.register_blueprint(inventory_bp)  # Has its own internal routing
    app.register_blueprint(inventory_profiles_bp, url_prefix="/tenant/<tenant_id>/inventory-profiles")
    app.register_blueprint(publisher_partners_bp, url_prefix="/tenant")  # Publisher partnerships
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(format_search_bp)  # Format search API (/api/formats)
    app.register_blueprint(activity_stream_bp)  # SSE endpoints - Flask handles /admin via script_name from nginx proxy
    app.register_blueprint(schemas_bp)  # JSON Schema validation service
    app.register_blueprint(workflows_bp, url_prefix="/tenant")  # Workflow approval and review
    # app.register_blueprint(tasks_bp)  # Tasks management - Disabled, tasks eliminated in favor of workflow system

    # Import and register existing blueprints
    try:
        from src.admin.tenant_management_api import tenant_management_api

        app.register_blueprint(tenant_management_api)
    except ImportError:
        logger.warning("tenant_management_api blueprint not found")

    try:
        from src.admin.sync_api import sync_api

        app.register_blueprint(sync_api, url_prefix="/api/sync")
    except ImportError:
        logger.warning("sync_api blueprint not found")

    try:
        from src.admin.blueprints.gam_reporting_api import gam_reporting_api

        app.register_blueprint(gam_reporting_api)
    except ImportError:
        logger.warning("gam_reporting_api blueprint not found")

    # One answer for any exception a route lets escape: fixed text plus a log reference.
    register_error_handler(app)

    return app
