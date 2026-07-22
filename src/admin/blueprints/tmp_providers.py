"""TMP Provider management blueprint for admin UI.

Trusted Match Protocol providers are buyer-side agents that the TMP Router
fans out to during ad selection. Each provider evaluates context signals
and/or identity signals against their synced package set and returns scored
offers. The TMP Router calls all active providers in parallel within the
configured latency budget, then merges results for the publisher-side join.

The Sales Agent exposes active registrations via the FastAPI discovery
endpoint (``GET /tenant/{tenant_id}/tmp-providers/discovery``) so the
router can poll for discovery — it never reads the DB directly.

The Flask blueprint here handles the **admin CRUD UI** only.  The
machine-to-machine discovery endpoint lives in ``src/routes/tmp_providers.py``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.repositories.uow import TMPProviderUoW
from src.core.security.url_validator import check_url_ssrf, sanitize_for_log

logger = logging.getLogger(__name__)

# Valid uid_type values per AdCP spec (uid-type enum).
# Authority: dist/schemas/3.1.0/enums/uid-type.json (AdCP 3.1.0-beta.3).
# Pinned by test_valid_uid_types_matches_pinned_schema in test_tmp_providers_blueprint.py.
VALID_UID_TYPES = frozenset(
    [
        "uid2",
        "rampid",
        "rampid_derived",
        "id5",
        "euid",
        "pairid",
        "maid",
        "hashed_email",
        "publisher_first_party",
        "world_id_nullifier",
        "other",
    ]
)

# Valid status values for TMP providers.
VALID_STATUSES = frozenset(["active", "inactive", "draining"])

# Create Blueprint
tmp_providers_bp = Blueprint("tmp_providers", __name__)


# ---------------------------------------------------------------------------
# Guard helpers (DRY: used by multiple route handlers)
# ---------------------------------------------------------------------------


def _tenant_not_found_redirect():
    """Flash an error and redirect to the index when a tenant cannot be found.

    Returns the redirect response so callers can ``return`` it directly::

        tenant = uow.tenant_config.get_tenant()
        if not tenant:
            return _tenant_not_found_redirect()
    """
    flash("Tenant not found", "error")
    return redirect(url_for("core.index"))


def _provider_not_found_json():
    """Return a JSON 404 response when a TMP provider cannot be found.

    Returns the response so callers can ``return`` it directly::

        provider = uow.tmp_providers.get_by_id(provider_id)
        if not provider:
            return _provider_not_found_json()
    """
    return jsonify({"error": "TMP provider not found"}), 404


def _provider_not_found_redirect(tenant_id: str):
    """Flash an error and redirect to the provider list when a TMP provider cannot be found.

    Returns the redirect response so callers can ``return`` it directly::

        provider = uow.tmp_providers.get_by_id(provider_id)
        if not provider:
            return _provider_not_found_redirect(tenant_id)
    """
    flash("TMP provider not found", "error")
    return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))


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
    status = form.get("status", "active").strip()

    # Validate timeout_ms is numeric
    try:
        timeout_ms = int(form.get("timeout_ms", "50"))
    except (ValueError, TypeError):
        return {}, "Timeout (ms) must be a numeric value"

    # Validate priority is numeric
    try:
        priority = int(form.get("priority", "0"))
    except (ValueError, TypeError):
        return {}, "Priority must be a numeric value"

    # Validate status against allowed values
    if status not in VALID_STATUSES:
        return {}, (f"Invalid status '{status}'. Valid values: {', '.join(sorted(VALID_STATUSES))}")

    # Parse comma-separated lists
    countries = [c.strip().upper() for c in countries_raw.split(",") if c.strip()] or None
    uid_types = [u.strip() for u in uid_types_raw.split(",") if u.strip()] or None
    properties_list = [p.strip() for p in properties_raw.split(",") if p.strip()] or None

    if not name:
        return {}, "Provider name is required"

    if not endpoint:
        return {}, "Endpoint URL is required"

    is_safe, ssrf_error = check_url_ssrf(endpoint)
    if not is_safe:
        logger.warning(
            "[SECURITY] TMP provider rejected unsafe URL %s: %s",
            sanitize_for_log(endpoint),
            sanitize_for_log(ssrf_error),
        )
        return {}, f"Endpoint URL is not allowed: {ssrf_error}"

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
                f"Invalid uid_type(s): {', '.join(invalid_types)}. Valid values: {', '.join(sorted(VALID_UID_TYPES))}"
            )

    auth_type = form.get("auth_type", "").strip() or None
    auth_credentials = form.get("auth_credentials", "").strip() or None

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
        "auth_type": auth_type,
        "auth_credentials": auth_credentials,
    }
    return data, None


@tmp_providers_bp.route("/")
@require_tenant_access()
def list_tmp_providers(tenant_id):
    """List all TMP providers for a tenant."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            assert uow.tmp_providers is not None
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return _tenant_not_found_redirect()

            providers = uow.tmp_providers.list_all()

            providers_list = []
            for p in providers:
                # include_conditional=False: always emit countries/uid_types/properties
                # (None means "accepts all") — same contract as the edit path and the
                # discovery endpoint. Avoids manually overwriting the same three fields
                # that to_dict(include_conditional=False) already handles.
                entry = p.to_dict(include_conditional=False)
                entry["created_at"] = p.created_at
                providers_list.append(entry)

            return render_template(
                "tmp_providers.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                providers=providers_list,
            )

    except Exception as e:
        logger.error("Error loading TMP providers: %s", e, exc_info=True)
        flash("Error loading TMP providers", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))


@tmp_providers_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_tmp_provider")
@require_tenant_access()
def add_tmp_provider(tenant_id):
    """Add a new TMP provider."""
    if request.method == "GET":
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return _tenant_not_found_redirect()

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=None,
            )

    # POST — create new TMP provider
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            assert uow.tmp_providers is not None
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return _tenant_not_found_redirect()

            data, error = _validate_provider_form(request.form)
            if error:
                flash(error, "error")
                return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))

            # create_from_fields is symmetric with update_fields used in the edit path:
            # both accept the same validated-form dict without inline ORM construction.
            uow.tmp_providers.create_from_fields(**data)

            flash(f"TMP provider '{data['name']}' added successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        logger.error("Error adding TMP provider: %s", e, exc_info=True)
        flash("Error adding TMP provider", "error")
        return redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id))


@tmp_providers_bp.route("/<provider_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_tmp_provider")
@require_tenant_access()
def edit_tmp_provider(tenant_id, provider_id):
    """Edit an existing TMP provider."""
    if request.method == "GET":
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            assert uow.tmp_providers is not None
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return _tenant_not_found_redirect()

            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_redirect(tenant_id)

            provider_dict = provider.to_dict(include_conditional=False)
            # Form fields need comma-separated strings, not lists
            provider_dict["countries"] = ",".join(provider.countries or [])
            provider_dict["uid_types"] = ",".join(provider.uid_types or [])
            provider_dict["properties"] = ",".join(provider.properties or [])
            # Auth fields are not in to_dict() (sensitive / not part of TMP Router contract).
            # Render a placeholder instead of the plaintext credential so it is never
            # echoed back to the browser — the POST side preserves the existing value
            # when the field is left empty.
            provider_dict["auth_type"] = provider.auth_type
            provider_dict["auth_credentials"] = "••••••••" if provider._auth_credentials else ""

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=provider_dict,
            )

    # POST — update TMP provider
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_redirect(tenant_id)

            data, error = _validate_provider_form(request.form)
            if error:
                flash(error, "error")
                return redirect(
                    url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)
                )

            # Build kwargs — only include auth_credentials when a new non-empty
            # value was submitted (preserves existing encrypted value otherwise).
            update_kwargs: dict = {
                "name": data["name"],
                "endpoint": data["endpoint"],
                "context_match": data["context_match"],
                "identity_match": data["identity_match"],
                "countries": data["countries"],
                "uid_types": data["uid_types"],
                "properties": data["properties"],
                "timeout_ms": data["timeout_ms"],
                "priority": data["priority"],
                "status": data["status"],
                "auth_type": data["auth_type"],
            }
            if data["auth_credentials"]:
                update_kwargs["auth_credentials"] = data["auth_credentials"]

            uow.tmp_providers.update_fields(provider_id, **update_kwargs)

            flash(f"TMP provider '{data['name']}' updated successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        logger.error("Error updating TMP provider: %s", e, exc_info=True)
        flash("Error updating TMP provider", "error")
        return redirect(url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id))


@tmp_providers_bp.route("/<provider_id>/deactivate", methods=["POST"])
@log_admin_action("deactivate_tmp_provider")
@require_tenant_access()
def deactivate_tmp_provider(tenant_id, provider_id):
    """Soft-deactivate a TMP provider (set status='inactive')."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            provider = uow.tmp_providers.deactivate(provider_id)
            if not provider:
                return _provider_not_found_json()

            return jsonify({"success": True, "message": f"TMP provider '{provider.name}' deactivated"})

    except Exception as e:
        logger.error("Error deactivating TMP provider: %s", e, exc_info=True)
        return jsonify({"error": "Error deactivating TMP provider"}), 500


@tmp_providers_bp.route("/<provider_id>/delete", methods=["DELETE"])
@log_admin_action("delete_tmp_provider")
@require_tenant_access()
def delete_tmp_provider(tenant_id, provider_id):
    """Hard-delete a TMP provider."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            # Get name before deleting for the response message
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_json()

            provider_name = provider.name
            uow.tmp_providers.delete(provider_id)

            return jsonify({"success": True, "message": f"TMP provider '{provider_name}' deleted successfully"})

    except Exception as e:
        logger.error("Error deleting TMP provider: %s", e, exc_info=True)
        return jsonify({"error": "Error deleting TMP provider"}), 500


@tmp_providers_bp.route("/<provider_id>/health", methods=["GET"])
@log_admin_action("health_check_tmp_provider")
@require_tenant_access()
def health_check_tmp_provider(tenant_id, provider_id):
    """Return the last health-check result from the background scheduler.

    The TMP health scheduler polls each provider's ``/health`` endpoint
    every 60 s and writes the result to ``health_status`` /
    ``last_health_checked_at``.  This route reads from the DB — no live
    HTTP call, no worker starvation risk.
    """
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_json()

            if provider.health_status is None:
                return jsonify(
                    {
                        "success": True,
                        "status": "pending",
                        "provider": provider.name,
                        "message": "Health check has not run yet",
                    }
                )

            return jsonify(
                {
                    "success": provider.health_status == "healthy",
                    "status": provider.health_status,
                    "provider": provider.name,
                    "last_checked": (
                        provider.last_health_checked_at.isoformat() if provider.last_health_checked_at else None
                    ),
                }
            )

    except Exception as e:
        logger.error("Error checking TMP provider health: %s", e, exc_info=True)
        return jsonify({"error": "Error checking provider health"}), 500
