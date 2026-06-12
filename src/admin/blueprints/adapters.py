"""Adapters management blueprint."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.adapters import get_adapter_schemas
from src.adapters._logging import safe_upstream_body_excerpt
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Product

logger = logging.getLogger(__name__)

# Create blueprint
adapters_bp = Blueprint("adapters", __name__)


def _freewheel_auth_mode(config_data: dict) -> str | None:
    """Return the intended FreeWheel auth mode and remove UI-only metadata."""
    explicit_mode = config_data.pop("auth_mode", None)
    if explicit_mode in {"password_grant", "api_token", "client_credentials"}:
        return explicit_mode
    if config_data.get("client_id"):
        return "client_credentials"
    if config_data.get("api_token"):
        return "api_token"
    if config_data.get("username"):
        return "password_grant"
    return None


def _secret_fields_for_connection_schema(connection_config: type[BaseModel] | None) -> list[str]:
    if connection_config is None:
        return []
    return [
        name
        for name, field in connection_config.model_fields.items()
        if isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("secret")
    ]


def _freewheel_secret_fields_to_clear(auth_mode: str | None) -> set[str]:
    # Switching to a mode clears the secrets belonging to the other modes so a
    # stale password/token/secret can't linger and shadow the active credential.
    if auth_mode == "password_grant":
        return {"api_token", "client_secret"}
    if auth_mode == "api_token":
        return {"password", "client_secret"}
    if auth_mode == "client_credentials":
        return {"password", "api_token"}
    return set()


def _preserve_or_clear_secret_fields(
    config_data: dict,
    existing_config: dict,
    *,
    secret_fields: list[str],
    clear_secret_fields: set[str],
) -> None:
    """Preserve omitted stored secrets, except secrets explicitly cleared by mode switches."""
    for field_name in [f for f in secret_fields if not config_data.get(f)]:
        if field_name in clear_secret_fields:
            config_data.pop(field_name, None)
            continue
        existing_value = existing_config.get(field_name)
        if existing_value:
            config_data[field_name] = existing_value


@adapters_bp.route("/adapters/mock/config/<tenant_id>/<product_id>", methods=["GET", "POST"])
@require_tenant_access(role=("admin",))
def mock_config(tenant_id, product_id, **kwargs):
    """Configure mock adapter settings for a product."""
    with get_db_session() as session:
        stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
        product = session.scalars(stmt).first()

        if not product:
            flash("Product not found", "error")
            return redirect(url_for("products.list_products", tenant_id=tenant_id))

        if request.method == "POST":
            # Handle form submission to update mock config
            try:
                config = product.implementation_config or {}

                # Helper function to safely parse and validate numeric values
                def parse_int(field_name, default, min_val=None, max_val=None):
                    try:
                        value = int(request.form.get(field_name, default))
                        if min_val is not None and value < min_val:
                            raise ValueError(f"{field_name} must be at least {min_val}")
                        if max_val is not None and value > max_val:
                            raise ValueError(f"{field_name} must be at most {max_val}")
                        return value
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid value for {field_name}: {e}") from e

                def parse_float(field_name, default, min_val=None, max_val=None):
                    try:
                        value = float(request.form.get(field_name, default))
                        if min_val is not None and value < min_val:
                            raise ValueError(f"{field_name} must be at least {min_val}")
                        if max_val is not None and value > max_val:
                            raise ValueError(f"{field_name} must be at most {max_val}")
                        return value
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid value for {field_name}: {e}") from e

                # Traffic simulation (with validation)
                config["daily_impressions"] = parse_int("daily_impressions", 100000, min_val=0)
                config["fill_rate"] = parse_float("fill_rate", 85, min_val=0, max_val=100)
                config["ctr"] = parse_float("ctr", 0.5, min_val=0, max_val=100)
                config["viewability_rate"] = parse_float("viewability_rate", 70, min_val=0, max_val=100)

                # Performance simulation (with validation)
                config["latency_ms"] = parse_int("latency_ms", 50, min_val=0, max_val=60000)
                config["error_rate"] = parse_float("error_rate", 0.1, min_val=0, max_val=100)

                # Test scenarios (validated choices)
                test_mode = request.form.get("test_mode", "normal")
                valid_modes = ["normal", "high_demand", "degraded", "outage"]
                if test_mode not in valid_modes:
                    raise ValueError(f"Invalid test_mode: {test_mode}")
                config["test_mode"] = test_mode
                config["price_variance"] = parse_float("price_variance", 10, min_val=0, max_val=100)
                config["seasonal_factor"] = parse_float("seasonal_factor", 1.0, min_val=0.1, max_val=10.0)

                # Delivery simulation (with validation)
                config["delivery_simulation"] = {
                    "enabled": "delivery_simulation_enabled" in request.form,
                    "time_acceleration": parse_int("time_acceleration", 3600, min_val=1, max_val=86400),
                    "update_interval_seconds": parse_float("update_interval_seconds", 1.0, min_val=0.1, max_val=60),
                }

                # Note: Creative formats are managed in product.format_ids (via add/edit product page)
                # NOT in implementation_config - removing format handling to avoid duplication

                # Debug settings (boolean - safe)
                config["verbose_logging"] = "verbose_logging" in request.form
                config["predictable_ids"] = "predictable_ids" in request.form

                product.implementation_config = config
                attributes.flag_modified(product, "implementation_config")
                session.commit()

                flash("Mock adapter configuration saved successfully!", "success")
                return redirect(url_for("adapters.mock_config", tenant_id=tenant_id, product_id=product_id))
            except ValueError as e:
                logger.warning(f"Validation error in mock config: {e}")
                flash(f"Invalid configuration: {str(e)}", "error")
            except Exception as e:
                logger.error(f"Error saving mock config: {e}", exc_info=True)
                flash(f"Error saving configuration: {str(e)}", "error")

        # GET request - render template with product config
        config = product.implementation_config or {}

        return render_template(
            "adapters/mock_product_config.html",
            tenant_id=tenant_id,
            product=product,
            config=config,
        )


@adapters_bp.route("/adapter/<adapter_name>/inventory_schema", methods=["GET"])
@require_tenant_access()
def adapter_adapter_name_inventory_schema(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@adapters_bp.route("/setup_adapter", methods=["POST"])
@log_admin_action("setup_adapter")
@require_tenant_access(role=("admin",))
def setup_adapter(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@adapters_bp.route("/api/tenant/<tenant_id>/adapter-config", methods=["POST"])
@log_admin_action("update_adapter_config")
@require_tenant_access(role=("admin",))
def save_adapter_config(tenant_id, **kwargs):
    """Save adapter connection configuration.

    Validates config using Pydantic schema, then writes to both:
    - Legacy columns (for backwards compatibility)
    - config_json column (for schema-driven access)

    Request body:
    {
        "adapter_type": "mock" | "google_ad_manager" | etc,
        "config": { ... adapter-specific config ... }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        adapter_type = data.get("adapter_type")
        config_data = dict(data.get("config") or {})
        freewheel_auth_mode = _freewheel_auth_mode(config_data) if adapter_type == "freewheel" else None

        if not adapter_type:
            return jsonify({"success": False, "error": "adapter_type is required"}), 400

        # For adapters whose schemas mark a field as `secret`, two rules:
        # (a) reject any submitted ciphertext on the wire — a tenant admin must
        #     not be able to authenticate a session by replaying another tenant's
        #     leaked DB-row ciphertext (cross-tenant credential smuggling),
        # (b) preserve the previously-stored value when the caller omits the
        #     field (UX: leave password blank to keep existing credential).
        from src.core.utils.encryption import is_encrypted

        schemas = get_adapter_schemas(adapter_type)
        if schemas and schemas.connection_config:
            secret_fields = _secret_fields_for_connection_schema(schemas.connection_config)
            clear_secret_fields = (
                _freewheel_secret_fields_to_clear(freewheel_auth_mode) if adapter_type == "freewheel" else set()
            )
            for field_name in secret_fields:
                submitted = config_data.get(field_name)
                if submitted and is_encrypted(submitted):
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": f"{field_name} must be plaintext (encrypted-token replay rejected)",
                            }
                        ),
                        400,
                    )
            if secret_fields:
                missing = [f for f in secret_fields if not config_data.get(f)]
                if missing:
                    with get_db_session() as session:
                        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
                        existing = session.scalars(stmt).first()
                        if existing and existing.config_json:
                            _preserve_or_clear_secret_fields(
                                config_data,
                                existing.config_json,
                                secret_fields=secret_fields,
                                clear_secret_fields=clear_secret_fields,
                            )

        # Validate config against adapter schema (keeps Pydantic model flowing)
        validated_config = None
        if schemas and schemas.connection_config:
            try:
                validated_config = schemas.connection_config(**config_data)
            except ValidationError as e:
                return jsonify({"success": False, "error": f"Validation error: {e}"}), 400

        # Use the validated model for DB write (JSONType + engine json_serializer
        # handle BaseModel serialization). Fall back to raw dict if no schema.
        config_value = validated_config if validated_config is not None else config_data

        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config:
                adapter_config = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    config_json=config_value,
                )
                session.add(adapter_config)
            else:
                adapter_config.adapter_type = adapter_type
                adapter_config.config_json = config_value
                attributes.flag_modified(adapter_config, "config_json")

            # Write to legacy columns for backwards compatibility
            if adapter_type == "mock" and validated_config is not None:
                adapter_config.mock_dry_run = getattr(validated_config, "dry_run", False)
                adapter_config.mock_manual_approval_required = getattr(
                    validated_config, "manual_approval_required", False
                )
            # Note: GAM will be added as its schema is created. FreeWheel
            # already uses config_json via its connection schema.

            session.commit()
            if adapter_type == "freewheel":
                logger.info(
                    "Saved FreeWheel adapter config: tenant_id=%s environment=%s auth_mode=%s "
                    "has_username=%s has_password=%s has_api_token=%s cleared_secrets=%s",
                    tenant_id,
                    config_data.get("environment", "production"),
                    freewheel_auth_mode or "unchanged",
                    bool(config_data.get("username")),
                    bool(config_data.get("password")),
                    bool(config_data.get("api_token")),
                    sorted(_freewheel_secret_fields_to_clear(freewheel_auth_mode)),
                )
            else:
                logger.info(f"Saved adapter config for tenant {tenant_id}: {adapter_type}")

        return jsonify({"success": True, "adapter_type": adapter_type})

    except Exception as e:
        logger.error(f"Error saving adapter config: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@adapters_bp.route("/api/adapters/<adapter_type>/capabilities", methods=["GET"])
@require_tenant_access()
def get_adapter_capabilities(adapter_type, tenant_id, **kwargs):
    """Get capabilities for an adapter type.

    Returns the AdapterCapabilities for UI to show/hide sections.
    """
    from dataclasses import asdict

    schemas = get_adapter_schemas(adapter_type)
    if not schemas:
        return jsonify({"error": f"Unknown adapter type: {adapter_type}"}), 404

    if schemas.capabilities:
        return jsonify(asdict(schemas.capabilities))
    else:
        return jsonify({})


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/<adapter_type>/check-permissions", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
def check_adapter_permissions(tenant_id, adapter_type, **kwargs):
    """Probe upstream API for permission gaps before they bite us in production.

    Instantiates the configured adapter and calls its ``check_permissions()``
    method, which probes every endpoint the adapter depends on with cheap
    GETs. Returns a structured report — operators see at-connect time which
    AdCP features will work vs which need additional upstream IAM grants.

    Read-only by design — every probe is a GET. Opts into the embedded-write
    gate accordingly. Adapter must be configured on the tenant; an unconfigured
    adapter returns 400.
    """
    from dataclasses import asdict

    from src.adapters import ADAPTER_REGISTRY
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if not adapter_class:
        return jsonify({"success": False, "error": f"Unknown adapter type: {adapter_type}"}), 404

    with get_db_session() as session:
        repo = AdapterConfigRepository(session, tenant_id)
        config_row = repo.find_by_tenant()
        if config_row is None or config_row.adapter_type != adapter_type.lower():
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"No {adapter_type} adapter configured for tenant {tenant_id}",
                    }
                ),
                400,
            )
        adapter_config = dict(config_row.config_json or {})

    # The probe only needs a minimal principal — no real advertiser-scoped
    # calls happen. A stub principal_id keeps the adapter constructor happy.
    from src.core.schemas import Principal

    stub_principal = Principal(
        tenant_id=tenant_id,
        principal_id="__permissions_probe__",
        name="permissions-probe",
        platform_mappings={adapter_type: {"advertiser_id": "0"}},
    )

    try:
        adapter = adapter_class(
            config=adapter_config,
            principal=stub_principal,
            dry_run=False,
            tenant_id=tenant_id,
        )
        report = adapter.check_permissions()
    except Exception as exc:
        logger.warning("Permissions probe failed for tenant=%s adapter=%s: %s", tenant_id, adapter_type, exc)
        return jsonify({"success": False, "error": f"Could not run probe: {exc}"}), 500

    return jsonify(
        {
            "success": True,
            "report": {
                "adapter": report.adapter,
                "tenant_id": report.tenant_id,
                "checked_at": report.checked_at.isoformat(),
                "fully_operational": report.fully_operational,
                "error": report.error,
                "checks": [asdict(c) for c in report.checks],
            },
        }
    )


# FreeWheel-specific endpoints


def _execute_freewheel_sync(
    tenant_id: str,
    *,
    sync_kind: str,
    triggered_by: str,
    run_kwargs: dict | None = None,
):
    """Thin wrapper around the shared sync orchestration for the FW
    per-adapter buttons. Returns ``None`` when FW isn't configured for
    this tenant; otherwise returns the
    :class:`SyncExecutionResult` from the orchestration."""
    from src.services.adapter_sync_orchestration import execute_adapter_sync

    return execute_adapter_sync(
        tenant_id=tenant_id,
        adapter_type="freewheel",
        sync_kind=sync_kind,
        triggered_by=triggered_by,
        run_kwargs=run_kwargs,
    )


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",), allow_embedded_writes=True)
def test_freewheel_connection(tenant_id, **kwargs):
    """Verify FreeWheel credentials by minting a bearer (password grant) or
    validating a pre-minted bearer via /auth/token/info.

    Accepts (in priority order):
      - ``username`` + ``password`` for OAuth2 password grant — the canonical path
      - ``api_token`` for pre-minted-bearer use (escape hatch)

    Missing fields fall back to the encrypted values already on
    ``AdapterConfig.config_json``. Submitted ciphertext is rejected to
    prevent cross-tenant replay (see save_adapter_config).

    Read-only probe — validates credentials against the upstream provider and
    never writes to AdapterConfig — so it opts into the embedded-write gate.
    """
    from src.core.utils.encryption import is_encrypted

    try:
        data = request.get_json() or {}
        username = data.get("username")
        password = data.get("password")
        api_token = data.get("api_token")
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        token_url = data.get("token_url")
        environment = data.get("environment", "production")

        # Reject submitted ciphertext on secret fields — only the DB-fallback
        # path is allowed to use stored ciphertext.
        for field_name, field_value in [
            ("password", password),
            ("api_token", api_token),
            ("client_secret", client_secret),
        ]:
            if field_value and is_encrypted(field_value):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"{field_name} must be plaintext (encrypted-token replay rejected)",
                        }
                    ),
                    400,
                )

        has_cc = bool(client_id) and bool(client_secret)
        # Fill in missing fields from stored config so partial submissions work.
        if not has_cc and not (username and password) and not api_token:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            with get_db_session() as session:
                existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if existing and existing.config_json:
                    from src.adapters.freewheel import FreeWheelConnectionConfig

                    try:
                        rehydrated = FreeWheelConnectionConfig.model_validate(existing.config_json)
                        username = username or rehydrated.username
                        password = password or rehydrated.password
                        api_token = api_token or rehydrated.api_token
                        client_id = client_id or rehydrated.client_id
                        client_secret = client_secret or rehydrated.client_secret
                        token_url = token_url or rehydrated.token_url
                    except ValidationError:
                        pass
            has_cc = bool(client_id) and bool(client_secret)

        if has_cc:
            auth_mode = "client_credentials"
        elif api_token:
            auth_mode = "api_token"
        elif username and password:
            auth_mode = "password_grant"
        else:
            auth_mode = "stored"
        if not (has_cc or (username and password) or api_token):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Connection test requires one of: (client_id + client_secret), "
                            "(username + password), or api_token"
                        ),
                    }
                ),
                400,
            )

        from src.adapters.freewheel import FreeWheelClient, FreeWheelError
        from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS

        base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])
        client_kwargs: dict = {
            "username": username,
            "password": password,
            "api_token": api_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "base_url": base_url,
        }
        if token_url:
            client_kwargs["token_url"] = token_url
        client = FreeWheelClient(**client_kwargs)
        # client_credentials tokens 401 on /auth/token/info (legacy introspection),
        # so an inventory read proves validity + publisher binding instead.
        probe_phase = "list_sites" if has_cc else "token_info"
        try:
            if has_cc:
                client.inventory.list_sites(per_page=1)
                expires_in = None
            else:
                info = client.token_info()
                expires_in = info.get("expires_in") or info.get("expires_in_seconds")
        except FreeWheelError as exc:
            # Keep the UI response generic, but log enough phase/status
            # context for operators to diagnose upstream auth failures.
            logger.warning(
                "FreeWheel connection test failed: tenant_id=%s environment=%s auth_mode=%s "
                "phase=%s status=%s error=%s body_excerpt=%s",
                tenant_id,
                environment,
                auth_mode,
                probe_phase,
                exc.status_code,
                exc,
                safe_upstream_body_excerpt(exc.body),
            )
            return jsonify({"success": False, "error": "FreeWheel rejected the credentials"}), 200

        logger.info(
            "FreeWheel connection test succeeded: tenant_id=%s environment=%s auth_mode=%s phase=%s",
            tenant_id,
            environment,
            auth_mode,
            probe_phase,
        )
        return jsonify(
            {
                "success": True,
                "environment": environment,
                "expires_in": expires_in,
                "auth_mode": auth_mode,
            }
        )

    except Exception as e:
        logger.error(
            "FreeWheel connection test crashed: tenant_id=%s environment=%s auth_mode=%s error=%s",
            tenant_id,
            locals().get("environment", "unknown"),
            locals().get("auth_mode", "unknown"),
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "Connection test failed (see server logs)"}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/inventory", methods=["GET"])
@require_tenant_access()
def list_freewheel_inventory(tenant_id, **kwargs):
    """Return locally-cached FreeWheel inventory entries for the product setup UI.

    Filterable by ``entity_type`` (site, site_section, site_group, series,
    video_group, ad_unit_package, ad_unit, ad_unit_node, standard_attribute).
    Optional ``parent_id`` narrows to children of a specific parent. Optional
    ``q`` substring-matches the ``name`` field.

    Returns a flat list (no pagination — the cache is small enough that
    sending the whole filtered set is fine for now).
    """
    from src.core.database.repositories.freewheel_inventory import FreeWheelInventoryRepository

    entity_type = request.args.get("entity_type")
    parent_id = request.args.get("parent_id")
    q = request.args.get("q")

    if not entity_type:
        return jsonify({"success": False, "error": "entity_type query param is required"}), 400

    with get_db_session() as session:
        repo = FreeWheelInventoryRepository(session, tenant_id)
        rows = repo.list_by_type(entity_type, parent_id=parent_id)

    items = [
        {"entity_id": row.entity_id, "name": row.name, "parent_id": row.parent_id}
        for row in rows
        if not q or (row.name and q.lower() in row.name.lower())
    ]
    return jsonify({"success": True, "entity_type": entity_type, "count": len(items), "items": items})


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/sync-inventory", methods=["POST"])
@require_tenant_access(role=("admin",))
def sync_freewheel_inventory(tenant_id, **kwargs):
    """Walk the FreeWheel inventory taxonomy and refresh the local cache.

    Reads the stored connection config, instantiates a FreeWheel client,
    and runs :class:`FreeWheelInventorySync` against the tenant's adapter
    config. Returns per-entity-type counts + any partial-failure errors.

    The cache feeds the FreeWheel adapter's product setup UI; it's not
    exposed to AdCP buyers (property discovery goes through AAO lookup).
    """
    try:
        result = _execute_freewheel_sync(tenant_id, sync_kind="inventory", triggered_by="admin_button")
        if result is None:
            return jsonify({"success": False, "error": "FreeWheel adapter is not configured for this tenant"}), 400
        return jsonify(
            {
                "success": result.succeeded,
                "sync_id": result.sync_id,
                "counts": result.counts,
                "errors": result.errors,
                "total_synced": sum(result.counts.values()),
                "started_at": result.started_at.isoformat() if result.started_at else None,
                "finished_at": result.finished_at.isoformat() if result.finished_at else None,
            }
        )
    except ValidationError as exc:
        return jsonify({"success": False, "error": f"Stored config is invalid: {exc}"}), 400
    except Exception as e:
        logger.error(f"FreeWheel inventory sync failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Sync failed (see server logs)"}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/sync-reporting", methods=["POST"])
@require_tenant_access(role=("admin",))
def sync_freewheel_reporting(tenant_id, **kwargs):
    """Pull a fresh delivery report from FreeWheel and upsert into the
    placement-stats cache feeding ``get_packages_snapshot`` /
    ``get_media_buy_delivery``.

    Submits one Query Reporting job covering all placements (or the
    placements named in ``placement_ids``) for today (or the date window
    in ``start_date`` / ``end_date``). Polls the job, fetches results,
    parses each row, bulk-upserts into ``freewheel_placement_stats``.

    Returns 503 when the upstream scope is still pending — the
    permission-check endpoint surfaces the exact denied paths.
    """
    try:
        body = request.get_json(silent=True) or {}
        from datetime import date as _date

        run_kwargs: dict = {}
        if body.get("placement_ids"):
            run_kwargs["placement_ids"] = body["placement_ids"]
        if body.get("start_date"):
            run_kwargs["start_date"] = _date.fromisoformat(body["start_date"])
        if body.get("end_date"):
            run_kwargs["end_date"] = _date.fromisoformat(body["end_date"])

        result = _execute_freewheel_sync(
            tenant_id, sync_kind="reporting", triggered_by="admin_button", run_kwargs=run_kwargs
        )
        if result is None:
            return jsonify({"success": False, "error": "FreeWheel adapter is not configured for this tenant"}), 400

        if result.scope_pending:
            return (
                jsonify(
                    {
                        "success": False,
                        "scope_pending": True,
                        "sync_id": result.sync_id,
                        "error": result.errors.get("scope", "Scope grant pending"),
                    }
                ),
                503,
            )
        return jsonify(
            {
                "success": result.succeeded,
                "sync_id": result.sync_id,
                "placements_updated": result.counts.get("placements", 0),
                "job_id": result.metadata.get("job_id"),
                "error": next(iter(result.errors.values()), None) if result.errors else None,
            }
        )
    except ValidationError as exc:
        return jsonify({"success": False, "error": f"Stored config is invalid: {exc}"}), 400
    except Exception as e:
        logger.error(f"FreeWheel reporting sync failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Sync failed (see server logs)"}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/cache-freshness", methods=["GET"])
@require_tenant_access()
def freewheel_cache_freshness(tenant_id, **kwargs):
    """Return ``last_synced_at`` for both FW caches so the settings page
    can surface a "data is stale" banner.

    Stale thresholds are policy choices, returned alongside the timestamps
    so the UI doesn't have to embed them:

      - inventory: stale after 24h (taxonomy changes are infrequent;
        publishers re-sync on a daily-ish cadence).
      - reporting: stale after 2h (delivery pacing matters in near-
        real-time; webhook scheduler runs hourly so 2h is one missed
        cycle).

    ``never_synced=true`` is a distinct signal from "stale by N hours" —
    a never-run cache is an onboarding gap, not a freshness issue.
    """
    from datetime import UTC, datetime, timedelta

    from src.core.database.repositories.freewheel_inventory import FreeWheelInventoryRepository
    from src.core.database.repositories.freewheel_placement_stats import FreeWheelPlacementStatsRepository

    INVENTORY_STALE_THRESHOLD = timedelta(hours=24)
    REPORTING_STALE_THRESHOLD = timedelta(hours=2)

    with get_db_session() as session:
        inventory_at = FreeWheelInventoryRepository(session, tenant_id).latest_sync_at()
        reporting_at = FreeWheelPlacementStatsRepository(session, tenant_id).latest_sync_at()

    now = datetime.now(UTC)

    def _stale_info(last_at, threshold):
        if last_at is None:
            return {"last_synced_at": None, "age_seconds": None, "stale": True, "never_synced": True}
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        age = (now - last_at).total_seconds()
        return {
            "last_synced_at": last_at.isoformat(),
            "age_seconds": int(age),
            "stale": age > threshold.total_seconds(),
            "never_synced": False,
        }

    return jsonify(
        {
            "success": True,
            "inventory": {
                **_stale_info(inventory_at, INVENTORY_STALE_THRESHOLD),
                "threshold_seconds": int(INVENTORY_STALE_THRESHOLD.total_seconds()),
            },
            "reporting": {
                **_stale_info(reporting_at, REPORTING_STALE_THRESHOLD),
                "threshold_seconds": int(REPORTING_STALE_THRESHOLD.total_seconds()),
            },
        }
    )


# Triton test-connection endpoint removed — adapter parked while their APIs
# aren't production-ready. Source remains under src/adapters/triton/ so
# restoring the endpoint is a single revert; templates/adapters/triton/ also
# preserved.


# Broadstreet-specific endpoints


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",), allow_embedded_writes=True)
def test_broadstreet_connection(tenant_id, **kwargs):
    """Test Broadstreet API connection with provided credentials.

    Read-only probe — validates credentials against the upstream provider and
    never writes to AdapterConfig — so it opts into the embedded-write gate.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        network_id = data.get("network_id")
        api_key = data.get("api_key")

        if not network_id or not api_key:
            return jsonify({"success": False, "error": "network_id and api_key are required"}), 400

        # Test connection by fetching network info
        from src.adapters.broadstreet import BroadstreetClient

        client = BroadstreetClient(access_token=api_key, network_id=network_id)
        network_info = client.get_network()

        if network_info:
            return jsonify(
                {
                    "success": True,
                    "network_name": network_info.get("name", "Unknown"),
                    "network_id": network_id,
                }
            )
        else:
            return jsonify({"success": False, "error": "Could not retrieve network information"})

    except Exception as e:
        logger.error(f"Broadstreet connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/zones", methods=["GET"])
@require_tenant_access()
def list_broadstreet_zones(tenant_id, **kwargs):
    """List available zones from Broadstreet for the tenant's configured account."""
    try:
        # Get adapter config for this tenant
        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config:
                return jsonify({"zones": [], "error": "No adapter configured"}), 200

            # Get Broadstreet credentials from config_json or legacy columns
            config = adapter_config.config_json or {}
            network_id = config.get("network_id") or getattr(adapter_config, "broadstreet_network_id", None)
            api_key = config.get("api_key") or getattr(adapter_config, "broadstreet_api_key", None)

            if not network_id or not api_key:
                return jsonify({"zones": [], "error": "Broadstreet not configured"}), 200

            # Fetch zones from Broadstreet
            from src.adapters.broadstreet import BroadstreetClient

            client = BroadstreetClient(access_token=api_key, network_id=network_id)
            zones = client.get_zones()

            return jsonify(
                {
                    "zones": [
                        {"id": str(zone.get("id")), "name": zone.get("name", f"Zone {zone.get('id')}")}
                        for zone in zones
                    ]
                }
            )

    except Exception as e:
        logger.error(f"Error fetching Broadstreet zones: {e}", exc_info=True)
        return jsonify({"zones": [], "error": str(e)}), 500


# SpringServe-specific endpoints


def _execute_springserve_sync(
    tenant_id: str,
    *,
    sync_kind: str,
    triggered_by: str,
    run_kwargs: dict | None = None,
):
    """Thin wrapper around the shared sync orchestration for the SpringServe
    per-adapter buttons. Returns ``None`` when SpringServe isn't configured
    for this tenant; otherwise returns the
    :class:`SyncExecutionResult` from the orchestration."""
    from src.services.adapter_sync_orchestration import execute_adapter_sync

    return execute_adapter_sync(
        tenant_id=tenant_id,
        adapter_type="springserve",
        sync_kind=sync_kind,
        triggered_by=triggered_by,
        run_kwargs=run_kwargs,
    )


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/springserve/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",), allow_embedded_writes=True)
def test_springserve_connection(tenant_id, **kwargs):
    """Verify SpringServe credentials by minting a token (email+password) or
    probing /campaigns with a pre-minted token.

    Submitted ciphertext on secret fields is rejected to prevent
    cross-tenant replay; missing fields fall back to the encrypted values
    already on AdapterConfig.config_json.
    """
    from src.core.utils.encryption import is_encrypted

    try:
        data = request.get_json() or {}
        email = data.get("email")
        password = data.get("password")
        api_token = data.get("api_token")

        for field_name, field_value in [("password", password), ("api_token", api_token)]:
            if field_value and is_encrypted(field_value):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"{field_name} must be plaintext (encrypted-token replay rejected)",
                        }
                    ),
                    400,
                )

        if not (email and password) and not api_token:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            with get_db_session() as session:
                existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if existing and existing.config_json:
                    from src.adapters.springserve import SpringServeConnectionConfig

                    try:
                        rehydrated = SpringServeConnectionConfig.model_validate(existing.config_json)
                        email = email or rehydrated.email
                        password = password or rehydrated.password
                        api_token = api_token or rehydrated.api_token
                    except ValidationError:
                        pass

        if not (email and password) and not api_token:
            return (
                jsonify({"success": False, "error": "Connection test requires either (email + password) or api_token"}),
                400,
            )

        from src.adapters.springserve import SpringServeClient, SpringServeError

        client = SpringServeClient(email=email, password=password, api_token=api_token)
        try:
            status, _body = client.probe("GET", "/campaigns?per_page=1")
        except SpringServeError as exc:
            logger.warning("SpringServe credential probe failed: %s body=%s", exc, exc.body)
            return jsonify({"success": False, "error": "SpringServe rejected the credentials"}), 200

        if status >= 400:
            return jsonify({"success": False, "error": f"SpringServe responded HTTP {status}"}), 200

        return jsonify(
            {
                "success": True,
                "auth_mode": "password_grant" if (email and password) else "pre_minted_token",
            }
        )
    except Exception as e:
        logger.error(f"SpringServe connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Connection test failed (see server logs)"}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/springserve/inventory", methods=["GET"])
@require_tenant_access()
def list_springserve_inventory(tenant_id, **kwargs):
    """Return locally-cached SpringServe inventory entries for the product setup UI.

    Filterable by ``entity_type`` (supply_partner, supply_router, supply_tag,
    key, value_list). Optional narrowing by the corresponding FK:

    * ``supply_partner_id`` -- list routers under a partner, or all tags
      (including orphans) under a partner
    * ``supply_router_id`` -- list tags inside a router
    * ``key_id`` -- list value_lists attached to a key
    """
    from src.core.database.repositories.springserve_inventory import (
        SpringServeInventoryRepository,
    )

    entity_type = request.args.get("entity_type")
    supply_partner_id = request.args.get("supply_partner_id")
    supply_router_id = request.args.get("supply_router_id")
    key_id = request.args.get("key_id")
    q = request.args.get("q")

    if not entity_type:
        return jsonify({"success": False, "error": "entity_type query param is required"}), 400

    with get_db_session() as session:
        repo = SpringServeInventoryRepository(session, tenant_id)
        rows = repo.list_by_type(
            entity_type,
            supply_partner_id=supply_partner_id,
            supply_router_id=supply_router_id,
            key_id=key_id,
        )

    items = [
        {
            "id": row.entity_id,
            "name": row.name,
            "supply_partner_id": row.supply_partner_id,
            "supply_router_id": row.supply_router_id,
            "key_id": row.key_id,
        }
        for row in rows
        if not q or (row.name and q.lower() in row.name.lower())
    ]
    return jsonify({"success": True, "entity_type": entity_type, "count": len(items), "items": items})


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/springserve/sync-inventory", methods=["POST"])
@require_tenant_access(role=("admin",))
def sync_springserve_inventory(tenant_id, **kwargs):
    """Walk SpringServe supply_partners + supply_tags and refresh the cache.

    Returns scope_pending=True when SpringServe denies supply-side reads
    so the UI can surface a clear scope-grant message.
    """
    result = _execute_springserve_sync(tenant_id, sync_kind="inventory", triggered_by="admin_button")
    if result is None:
        return jsonify({"success": False, "error": "SpringServe adapter is not configured for this tenant"}), 400
    metadata = getattr(result, "metadata", {}) or {}
    if not result.succeeded and metadata.get("scope_pending"):
        return (
            jsonify(
                {
                    "success": False,
                    "scope_pending": True,
                    "error": "Supply-side read scope not granted by SpringServe",
                    "errors": result.errors,
                }
            ),
            503,
        )
    return jsonify(
        {
            "success": result.succeeded,
            "sync_id": result.sync_id,
            "counts": result.counts,
            "errors": result.errors,
            "total_synced": sum(result.counts.values()),
        }
    )


# SpringServe permission probes use the generic
# ``/api/tenant/<tenant_id>/adapters/<adapter_type>/check-permissions``
# endpoint above. The generic path constructs a real typed ``Principal``
# stub (instead of a MagicMock) so the adapter's tenant-isolation invariants
# are honoured during the probe.
