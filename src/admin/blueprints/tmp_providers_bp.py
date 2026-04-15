"""TMP Provider management blueprint for admin UI.

Trusted Match Protocol providers are buyer-side agents that the TMP Router
fans out to during ad selection. Each provider evaluates context signals
and/or identity signals against their synced package set and returns scored
offers. The TMP Router calls all active providers in parallel within the
configured latency budget, then merges results for the publisher-side join.
"""

import logging

import requests
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import TMPProvider, Tenant
from src.core.security.url_validator import check_url_ssrf

logger = logging.getLogger(__name__)

# Create Blueprint
tmp_providers_bp = Blueprint("tmp_providers", __name__)


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

            stmt = select(TMPProvider).filter_by(tenant_id=tenant_id).order_by(TMPProvider.name)
            providers = session.scalars(stmt).all()

            providers_list = []
            for p in providers:
                providers_list.append(
                    {
                        "provider_id": p.provider_id,
                        "name": p.name,
                        "endpoint": p.endpoint,
                        "context_match": p.context_match,
                        "identity_match": p.identity_match,
                        "timeout_ms": p.timeout_ms,
                        "is_active": p.is_active,
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

            name = request.form.get("name", "").strip()
            endpoint = request.form.get("endpoint", "").strip()
            context_match = request.form.get("context_match") == "on"
            identity_match = request.form.get("identity_match") == "on"
            timeout_ms = int(request.form.get("timeout_ms", "50"))

            if not endpoint:
                flash("Endpoint URL is required", "error")
                return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))

            is_safe, ssrf_error = check_url_ssrf(endpoint)
            if not is_safe:
                logger.warning("[SECURITY] TMP provider add rejected unsafe URL %r: %s", endpoint, ssrf_error)
                flash(f"Endpoint URL is not allowed: {ssrf_error}", "error")
                return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))

            if not name:
                flash("Provider name is required", "error")
                return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))

            provider = TMPProvider(
                tenant_id=tenant_id,
                name=name,
                endpoint=endpoint,
                context_match=context_match,
                identity_match=identity_match,
                timeout_ms=timeout_ms,
            )
            session.add(provider)
            session.commit()

            flash(f"TMP provider '{name}' added successfully", "success")
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

            stmt = select(TMPProvider).filter_by(provider_id=provider_id, tenant_id=tenant_id)
            provider = session.scalars(stmt).first()
            if not provider:
                flash("TMP provider not found", "error")
                return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

            provider_dict = {
                "provider_id": provider.provider_id,
                "name": provider.name,
                "endpoint": provider.endpoint,
                "context_match": provider.context_match,
                "identity_match": provider.identity_match,
                "timeout_ms": provider.timeout_ms,
                "is_active": provider.is_active,
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
            stmt = select(TMPProvider).filter_by(provider_id=provider_id, tenant_id=tenant_id)
            provider = session.scalars(stmt).first()
            if not provider:
                flash("TMP provider not found", "error")
                return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

            provider.endpoint = request.form.get("endpoint", "").strip()
            provider.name = request.form.get("name", "").strip()
            provider.context_match = request.form.get("context_match") == "on"
            provider.identity_match = request.form.get("identity_match") == "on"
            provider.timeout_ms = int(request.form.get("timeout_ms", "50"))

            if not provider.endpoint:
                flash("Endpoint URL is required", "error")
                return redirect(
                    url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
                )

            is_safe, ssrf_error = check_url_ssrf(provider.endpoint)
            if not is_safe:
                logger.warning("[SECURITY] TMP provider edit rejected unsafe URL %r: %s", provider.endpoint, ssrf_error)
                flash(f"Endpoint URL is not allowed: {ssrf_error}", "error")
                return redirect(
                    url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
                )

            if not provider.name:
                flash("Provider name is required", "error")
                return redirect(
                    url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
                )

            session.commit()

            flash(f"TMP provider '{provider.name}' updated successfully", "success")
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
    """Soft-deactivate a TMP provider (set is_active=false)."""
    try:
        with get_db_session() as session:
            stmt = select(TMPProvider).filter_by(provider_id=provider_id, tenant_id=tenant_id)
            provider = session.scalars(stmt).first()
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            provider.is_active = False
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
            stmt = select(TMPProvider).filter_by(provider_id=provider_id, tenant_id=tenant_id)
            provider = session.scalars(stmt).first()
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            provider_name = provider.name
            session.delete(provider)
            session.commit()

            return jsonify({"success": True, "message": f"TMP provider '{provider_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting TMP provider: {e}", exc_info=True)
        return jsonify({"error": "Error deleting TMP provider"}), 500


@tmp_providers_bp.route("/<provider_id>/health", methods=["GET"])
@require_tenant_access()
def health_check_tmp_provider(tenant_id, provider_id):
    """HTTP GET to provider.endpoint/health — returns JSON status."""
    try:
        with get_db_session() as session:
            stmt = select(TMPProvider).filter_by(provider_id=provider_id, tenant_id=tenant_id)
            provider = session.scalars(stmt).first()
            if not provider:
                return jsonify({"error": "TMP provider not found"}), 404

            health_url = provider.endpoint.rstrip("/") + "/health"

            # SSRF validation was already applied when the endpoint was
            # registered (add/edit routes). The stored URL is trusted.
            try:
                resp = requests.get(health_url, timeout=5)
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
