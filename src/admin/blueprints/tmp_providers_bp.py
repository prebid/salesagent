"""TMP Provider management blueprint for admin UI.

Trusted Match Protocol providers are buyer-side agents that the TMP Router
fans out to during ad selection. Each provider evaluates context signals
and/or identity signals against their synced package set and returns scored
offers. The TMP Router calls all active providers in parallel within the
configured latency budget, then merges results for the publisher-side join.

The Sales Agent exposes active registrations via ``GET /tmp/providers``
so the router can poll for discovery — it never reads the DB directly.
"""

from __future__ import annotations

import logging

import requests
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import TMPProvider, Tenant
from src.core.database.repositories.tmp_provider import TMPProviderRepository
from src.core.security.url_validator import check_url_ssrf

logger = logging.getLogger(__name__)

# Valid uid_type values per AdCP spec (uid-type enum).
VALID_UID_TYPES = frozenset([
    "uid2", "rampid", "id5", "euid", "pairid",
    "maid", "hashed_email", "publisher_first_party", "other",
])

# Create Blueprint
tmp_providers_bp = Blueprint("tmp_providers", __name__)


# ---------------------------------------------------------------------------
# Shared form validation helper (DRY: used by both add and edit routes)
# ---------------------------------------------------------------------------


def _validate_provider_form(form: dict) -> tuple[dict, str | None]:
    """Parse and validate TMP provider form data.

    Returns:
        (data, error_message) — *error_message* is ``None`` on success.
        *data* contains the parsed/normalised fields ready for DB write.
    """
    name = form.get("name", "").strip()
    endpoint = form.get("endpoint", "").strip()
    context_match = form.get("context_match") == "on"
    identity_match = form.get("identity_match") == "on"
    countries_raw = form.get("countries", "").strip()
    uid_types_raw = form.get("uid_types", "").strip()
    properties_raw = form.get("properties", "").strip()
    timeout_ms = int(form.get("timeout_ms", "50"))
    priority = int(form.get("priority", "0"))
    status = form.get("status", "active").strip()

    # Parse comma-separated lists
    countries = [c.strip().upper() for c in countries_raw.split(",") if c.strip()] or None
    uid_types = [u.strip() for u in uid_types_raw.split(",") if u.strip()] or None
    properties_list = [p.strip() for p in properties_raw.split(",") if p.strip()] or None

    if not endpoint:
        return {}, "Endpoint URL is required"

    is_safe, ssrf_error = check_url_ssrf(endpoint)
    if not is_safe:
        logger.warning("[SECURITY] TMP provider rejected unsafe URL %r: %s", endpoint, ssrf_error)
        return {}, f"Endpoint URL is not allowed: {ssrf_error}"

    if not name:
        return {}, "Provider name is required"

    # At least one of context_match or identity_match must be true
    if not context_match and not identity_match:
        return {}, "Provider must support at least one of context_match or identity_match"

    # Per AdCP spec: countries and uid_types MUST be non-empty when identity_match is true
    if identity_match:
        if not countries:
            return {}, "Countries are required when identity_match is enabled (ISO 3166-1 alpha-2 codes)"
        if not uid_types:
            return {}, "UID types are required when identity_match is enabled (e.g. uid2, publisher_first_party)"
        # Validate uid_type values against the AdCP enum
        invalid_types = [u for u in uid_types if u not in VALID_UID_TYPES]
        if invalid_types:
            return {}, (
                f"Invalid uid_type(s): {', '.join(invalid_types)}. "
                f"Valid values: {', '.join(sorted(VALID_UID_TYPES))}"
            )

    data = {
        "name": name,
        "endpoint": endpoint,
        "context_match": context_match,
        "identity_match": identity_match,
        "countries": countries,
        "uid_types": uid_types,
        "properties": properties_list,
        "timeout_ms": timeout_ms,
        "priority": priority,
        "status": status,
    }
    return data, None


@tmp_providers_bp.route("/")
@require_tenant_access()
def list_tmp_providers(tenant_id):
    """List all TMP providers for a tenant."""
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            repo = TMPProviderRepository(session, tenant_id)
            providers = repo.list_all()

            providers_list = []
            for p in providers:
                providers_list.append(
                    {
                        "provider_id": p.provider_id,
                        "name": p.name,
                        "endpoint": p.endpoint,
                        "context_match": p.context_match,
                        "identity_match": p.identity_match,
                        "countries": p.countries or [],
                        "uid_types": p.uid_types or [],
                        "properties": p.properties or [],
                        "timeout_ms": p.timeout_ms,
                        "priority": p.priority,
                        "status": p.status,
                        "created_at": p.created_at,
                    }
                )

            return render_template(
                "tmp_providers.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                providers=providers_list,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    except Exception as e:
        logger.error(f"Error loading TMP providers: {e}", exc_info=True)
        flash("Error loading TMP providers", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))


@tmp_providers_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_tmp_provider")
@require_tenant_access()
def add_tmp_provider(tenant_id):
    """Add a new TMP provider."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=None,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST — create new TMP provider
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            data, error = _validate_provider_form(request.form)
            if error:
                flash(error, "error")
                return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))

            provider = TMPProvider(
                tenant_id=tenant_id,
                name=data["name"],
                endpoint=data["endpoint"],
                context_match=data["context_match"],
                identity_match=data["identity_match"],
                countries=data["countries"],
                uid_types=data["uid_types"],
                properties=data["properties"],
                timeout_ms=data["timeout_ms"],
                priority=data["priority"],
            )
            repo = TMPProviderRepository(session, tenant_id)
            repo.create(provider)
            session.commit()

            flash(f"TMP provider '{data['name']}' added successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error adding TMP provider: {e}", exc_info=True)
        flash("Error adding TMP provider", "error")
        return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))


@tmp_providers_bp.route("/<provider_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_tmp_provider")
@require_tenant_access()
def edit_tmp_provider(tenant_id, provider_id):
    """Edit an existing TMP provider."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            repo = TMPProviderRepository(session, tenant_id)
            provider = repo.get_by_id(provider_id)
            if not provider:
                flash("TMP provider not found", "error")
                return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

            provider_dict = {
                "provider_id": provider.provider_id,
                "name": provider.name,
                "endpoint": provider.endpoint,
                "context_match": provider.context_match,
                "identity_match": provider.identity_match,
                "countries": ",".join(provider.countries or []),
                "uid_types": ",".join(provider.uid_types or []),
                "properties": ",".join(provider.properties or []),
                "timeout_ms": provider.timeout_ms,
                "priority": provider.priority,
                "status": provider.status,
            }

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=provider_dict,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST — update TMP provider
    try:
        with get_db_session() as session:
            repo = TMPProviderRepository(session, tenant_id)
            provider = repo.get_by_id(provider_id)
            if not provider:
                flash("TMP provider not found", "error")
                return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

            data, error = _validate_provider_form(request.form)
            if error:
                flash(error, "error")
                return redirect(
                    url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
                )

            repo.update_fields(
                provider_id,
                name=data["name"],
                endpoint=data["endpoint"],
                context_match=data["context_match"],
                identity_match=data["identity_match"],
                countries=data["countries"],
                uid_types=data["uid_types"],
                properties=data["properties"],
                timeout_ms=data["timeout_ms"],
                priority=data["priority"],
                status=data["status"],
            )
            session.commit()

            flash(f"TMP provider '{data['name']}' updated successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error updating TMP provider: {e}", exc_info=True)
        flash("Error updating TMP provider", "error")
        return redirect(
            url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
        )


@tmp_providers_bp.route("/<provider_id>/deactivate", methods=["POST"])
@require_tenant_access()
def deactivate_tmp_provider(tenant_id, provider_id):
    """Soft-deactivate a TMP provider (set status='inactive')."""
    try:
        with get_db_session() as session:
            repo = TMPProviderRepository(session, tenant_id)
            provider = repo.deactivate(provider_id)
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            session.commit()

            return jsonify({"success": True, "message": f"TMP provider '{provider.name}' deactivated"})

    except Exception as e:
        logger.error(f"Error deactivating TMP provider: {e}", exc_info=True)
        return jsonify({"error": "Error deactivating TMP provider"}), 500


@tmp_providers_bp.route("/<provider_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_tmp_provider(tenant_id, provider_id):
    """Hard-delete a TMP provider."""
    try:
        with get_db_session() as session:
            repo = TMPProviderRepository(session, tenant_id)
            # Get name before deleting for the response message
            provider = repo.get_by_id(provider_id)
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            provider_name = provider.name
            repo.delete(provider_id)
            session.commit()

            return jsonify({"success": True, "message": f"TMP provider '{provider_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting TMP provider: {e}", exc_info=True)
        return jsonify({"error": "Error deleting TMP provider"}), 500


@tmp_providers_bp.route("/<provider_id>/health", methods=["GET"])
@require_tenant_access()
def health_check_tmp_provider(tenant_id, provider_id):
    """HTTP GET to provider.endpoint/health — returns JSON status.

    .. warning:: Worker starvation risk

       This performs a **synchronous** HTTP call in the request handler,
       blocking the worker thread for up to 5 s.  If the provider is slow
       or unreachable, this stalls the admin UI for the requesting user.
    """
    try:
        with get_db_session() as session:
            repo = TMPProviderRepository(session, tenant_id)
            provider = repo.get_by_id(provider_id)
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            health_url = provider.endpoint.rstrip("/") + "/health"

            # SSRF validation was already applied when the endpoint was
            # registered (add/edit routes). The stored URL is trusted.
            # allow_redirects=False prevents SSRF via open-redirect even
            # though the base URL was validated at registration time.
            try:
                resp = requests.get(health_url, timeout=5, allow_redirects=False)
                if resp.status_code == 200:
                    return jsonify({"success": True, "status": "healthy", "provider": provider.name})
                else:
                    return jsonify(
                        {"success": False, "status": f"HTTP {resp.status_code}", "provider": provider.name}
                    )
            except requests.RequestException as req_err:
                return jsonify({"success": False, "error": str(req_err), "provider": provider.name})

    except Exception as e:
        logger.error(f"Error checking TMP provider health: {e}", exc_info=True)
        return jsonify({"error": "Error checking provider health"}), 500


# ------------------------------------------------------------------
# Discovery endpoint — unauthenticated, polled by the Go TMP Router
# ------------------------------------------------------------------
# This endpoint is intentionally unauthenticated because the TMP Router
# polls it on the internal Docker/VPC network every 30 s.  The tenant_id
# comes from the URL path (set by the blueprint prefix) — NOT from a
# query parameter — so one tenant cannot enumerate another tenant's
# providers.  Do NOT add a query-param override for tenant_id here.
# ------------------------------------------------------------------


@tmp_providers_bp.route("/discovery", methods=["GET"])
def discover_tmp_providers(tenant_id):
    """Return active TMP providers for a tenant as JSON.

    The Go TMP Router polls this endpoint to discover which buyer-side
    agents to fan out to.  **Intentionally unauthenticated** — scoped
    by the ``tenant_id`` in the URL path (blueprint prefix).

    Returns:
        JSON array of active provider objects.
    """
    try:
        with get_db_session() as session:
            repo = TMPProviderRepository(session, tenant_id)
            providers = repo.list_active()

            result = []
            for p in providers:
                entry: dict = {
                    "provider_id": p.provider_id,
                    "name": p.name,
                    "endpoint": p.endpoint,
                    "context_match": p.context_match,
                    "identity_match": p.identity_match,
                    "timeout_ms": p.timeout_ms,
                    "priority": p.priority,
                    "status": p.status,
                }
                # Conditional fields per provider-registration.json schema
                if p.countries:
                    entry["countries"] = p.countries
                if p.uid_types:
                    entry["uid_types"] = p.uid_types
                if p.properties:
                    entry["properties"] = p.properties
                result.append(entry)

            return jsonify(result)

    except Exception as e:
        logger.error(f"Error in TMP provider discovery: {e}", exc_info=True)
        return jsonify({"error": "Error fetching TMP providers"}), 500
