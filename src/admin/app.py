"""Flask application factory for Admin UI."""

import json
import logging
import os
import secrets

from flask import Flask, request
from flask_socketio import SocketIO, join_room
from werkzeug.middleware.proxy_fix import ProxyFix as WerkzeugProxyFix

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
from src.admin.blueprints.mcp_test import mcp_test_bp
from src.admin.blueprints.operations import operations_bp
from src.admin.blueprints.policy import policy_bp
from src.admin.blueprints.principals import principals_bp
from src.admin.blueprints.products import products_bp
from src.admin.blueprints.public import public_bp
from src.admin.blueprints.schemas import schemas_bp
from src.admin.blueprints.settings import settings_bp, tenant_management_settings_bp

# from src.admin.blueprints.tasks import tasks_bp  # Disabled - tasks eliminated in favor of workflow system
from src.admin.blueprints.tenants import tenants_bp
from src.admin.blueprints.users import users_bp
from src.admin.blueprints.workflows import workflows_bp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Custom ProxyFix for handling X-Script-Name and fixing redirect URLs
class CustomProxyFix:
    """Fix for proxy headers when running behind a reverse proxy with path prefix.

    Also fixes hardcoded URLs in redirects to include the script name prefix.
    """

    def __init__(self, app, script_name="/admin"):
        self.app = app
        self.script_name = script_name

    def __call__(self, environ, start_response):
        # Handle X-Script-Name (standard for mounting path) or X-Forwarded-Prefix
        script_name = environ.get("HTTP_X_SCRIPT_NAME", "")
        if not script_name:
            script_name = environ.get("HTTP_X_FORWARDED_PREFIX", "")

        # Use configured script_name if provided in production
        if not script_name and os.environ.get("PRODUCTION") == "true":
            script_name = self.script_name

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
        app.config["SESSION_COOKIE_PATH"] = "/admin/"  # Ensure cookies work for all /admin/* paths
        app.config["SESSION_COOKIE_DOMAIN"] = ".sales-agent.scope3.com"  # Allow cookies across subdomains for OAuth
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
        # Use Werkzeug's ProxyFix to handle X-Forwarded headers
        # x_for=1 for X-Forwarded-For, x_proto=1 for X-Forwarded-Proto, x_host=1 for X-Forwarded-Host
        app.wsgi_app = WerkzeugProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)
        # Apply custom fix for X-Forwarded-Prefix
        app.wsgi_app = CustomProxyFix(app.wsgi_app)
    else:
        # In development, still apply custom proxy fix if needed
        app.wsgi_app = CustomProxyFix(app.wsgi_app)

    # Initialize OAuth
    init_oauth(app)

    # Initialize SocketIO
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    app.socketio = socketio

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

        # Check if it's an external domain (not ending in .sales-agent.scope3.com)
        if apx_host.endswith(".sales-agent.scope3.com"):
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
            redirect_url = f"https://{tenant_subdomain}.sales-agent.scope3.com{path_with_admin}"
        else:
            # Local dev: Use localhost with port
            port = os.environ.get("ADMIN_UI_PORT", "8001")
            redirect_url = f"http://{tenant_subdomain}.localhost:{port}{path_with_admin}"

        logger.info(f"Redirecting external domain {apx_host}/admin to subdomain: {redirect_url}")
        return redirect(redirect_url, code=302)

    # Add context processor to make script_name and tenant available in templates
    @app.context_processor
    def inject_context():
        """Make the script_name (e.g., /admin) and current tenant available in all templates."""
        from flask import session
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        context = {}

        # Inject script_name
        if os.environ.get("PRODUCTION") == "true":
            context["script_name"] = "/admin"
        else:
            context["script_name"] = ""

        # Inject fresh tenant data if user is logged in with a tenant
        tenant_id = session.get("tenant_id")
        if tenant_id:
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                    tenant = db_session.scalars(stmt).first()
                    if tenant:
                        context["tenant"] = tenant
            except Exception as e:
                logger.warning(f"Could not load tenant {tenant_id} for context: {e}")

        return context

    # Add after_request handler to fix hardcoded URLs in HTML responses
    @app.after_request
    def fix_hardcoded_urls(response):
        """Fix hardcoded URLs in HTML responses to include script_name prefix."""
        if os.environ.get("PRODUCTION") == "true" and response.content_type and "text/html" in response.content_type:
            # Only process HTML responses
            try:
                html = response.get_data(as_text=True)
                # Fix common hardcoded patterns
                html = html.replace('href="/', 'href="/admin/')
                html = html.replace("href='/", "href='/admin/")
                html = html.replace('action="/', 'action="/admin/')
                html = html.replace("action='/", "action='/admin/")
                # Fix any that were already prefixed (avoid double prefixing)
                html = html.replace("/admin/admin/", "/admin/")
                response.set_data(html)
            except Exception as e:
                logger.error(f"Error fixing URLs in response: {e}")
        return response

    # Register blueprints
    app.register_blueprint(public_bp)  # Public routes (no auth required) - MUST BE FIRST
    app.register_blueprint(core_bp)  # Core routes (/, /health, /static, /mcp-test)
    app.register_blueprint(auth_bp)  # No url_prefix - auth routes are at root
    app.register_blueprint(tenant_management_settings_bp)  # Tenant management settings at /settings
    app.register_blueprint(tenants_bp, url_prefix="/tenant")
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
    app.register_blueprint(authorized_properties_bp, url_prefix="/tenant")  # Tenant-specific routes
    app.register_blueprint(creative_agents_bp, url_prefix="/tenant/<tenant_id>/creative-agents")
    app.register_blueprint(inventory_bp)  # Has its own internal routing
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(format_search_bp)  # Format search API (/api/formats)
    app.register_blueprint(activity_stream_bp)  # SSE endpoints - Flask handles /admin via script_name from nginx proxy
    app.register_blueprint(mcp_test_bp)
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

    # WebSocket handlers
    @socketio.on("connect")
    def handle_connect():
        """Handle WebSocket connection."""
        logger.info(f"Client connected: {request.sid}")

    @socketio.on("disconnect")
    def handle_disconnect():
        """Handle WebSocket disconnection."""
        logger.info(f"Client disconnected: {request.sid}")

    @socketio.on("subscribe")
    def handle_subscribe(data):
        """Handle subscription to tenant events."""
        tenant_id = data.get("tenant_id")
        if tenant_id:
            join_room(f"tenant_{tenant_id}")
            logger.info(f"Client {request.sid} subscribed to tenant {tenant_id}")

    return app, socketio


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


def broadcast_activity_to_websocket(tenant_id: str, activity: dict):
    """Broadcast activity to WebSocket clients."""
    try:
        from flask import current_app

        if hasattr(current_app, "socketio"):
            current_app.socketio.emit(
                "activity",
                activity,
                room=f"tenant_{tenant_id}",
                namespace="/",
            )
    except Exception as e:
        logger.error(f"Error broadcasting to WebSocket: {e}")
