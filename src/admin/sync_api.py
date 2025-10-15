"""
Sync API endpoints for background inventory/targeting synchronization.

Provides tenant management API endpoints for:
- Triggering sync jobs
- Checking sync status
- Getting sync history
"""

import json
import logging
import secrets
from datetime import UTC, datetime
from functools import wraps

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, SyncJob, Tenant, TenantManagementConfig
from src.services.gam_inventory_service import db_session as gam_db_session

logger = logging.getLogger(__name__)

# Create Blueprint
sync_api = Blueprint("sync_api", __name__, url_prefix="/api/v1/sync")

# Get database session
db_session = gam_db_session


def get_tenant_management_api_key() -> str | None:
    """Get tenant management API key from database."""
    with get_db_session() as db_session:
        stmt = select(TenantManagementConfig).filter_by(config_key="api_key")
        config = db_session.scalars(stmt).first()

    if config:
        return config.config_value
    return None


def require_tenant_management_api_key(f):
    """Decorator to require tenant management API key authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")

        if not api_key:
            return jsonify({"error": "API key required"}), 401

        valid_key = get_tenant_management_api_key()
        if not valid_key or api_key != valid_key:
            return jsonify({"error": "Invalid API key"}), 401

        return f(*args, **kwargs)

    return decorated_function


@sync_api.route("/trigger/<tenant_id>", methods=["POST"])
@require_tenant_management_api_key
def trigger_sync(tenant_id: str):
    """
    Trigger an inventory sync for a tenant.

    Request body:
    {
        "sync_type": "full" | "inventory" | "targeting",
        "force": true/false  # Force sync even if recent sync exists
    }
    """
    try:
        # Validate tenant exists and has GAM configured
        db_session.remove()  # Start fresh

        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return jsonify({"error": "Tenant not found"}), 404

        if tenant.ad_server != "google_ad_manager":
            return jsonify({"error": "Only Google Ad Manager sync is currently supported"}), 400

        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter_config = db_session.scalars(stmt).first()

        if not adapter_config:
            return jsonify({"error": "Adapter not configured"}), 400

        # Get request parameters
        data = request.get_json() or {}
        sync_type = data.get("sync_type", "full")
        force = data.get("force", False)

        # Check for recent sync if not forcing
        if not force:
            stmt = select(SyncJob).where(
                SyncJob.tenant_id == tenant_id,
                SyncJob.status.in_(["running", "completed"]),
                SyncJob.started_at >= datetime.now(UTC).replace(hour=0, minute=0, second=0),
            )
            recent_sync = db_session.scalars(stmt).first()

            if recent_sync:
                if recent_sync.status == "running":
                    return jsonify({"message": "Sync already in progress", "sync_id": recent_sync.sync_id}), 409
                else:
                    return (
                        jsonify(
                            {
                                "message": "Recent sync exists",
                                "sync_id": recent_sync.sync_id,
                                "completed_at": recent_sync.completed_at.isoformat(),
                            }
                        ),
                        200,
                    )

        # Create sync job
        sync_id = f"sync_{tenant_id}_{int(datetime.now().timestamp())}"
        sync_job = SyncJob(
            sync_id=sync_id,
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            sync_type=sync_type,
            status="pending",
            started_at=datetime.now(UTC),
            triggered_by="api",
            triggered_by_id="tenant_management_api",
        )

        db_session.add(sync_job)
        db_session.commit()

        # Trigger sync using GAM adapter with sync manager
        try:
            # Initialize GAM adapter with sync manager
            from src.core.schemas import Principal

            # Create dummy principal for sync (no advertiser needed for inventory sync)
            principal = Principal(
                principal_id="system",
                name="System",
                access_token="system_sync_token",  # Required field for sync operations
                platform_mappings={},  # No advertiser_id needed for inventory sync
            )

            # Build GAM config
            gam_config = {
                "enabled": True,
                "network_code": adapter_config.gam_network_code,
                "refresh_token": adapter_config.gam_refresh_token,
                "trafficker_id": adapter_config.gam_trafficker_id,
                "manual_approval_required": adapter_config.gam_manual_approval_required,
            }

            # Create GAM adapter with new modular architecture
            # NOTE: advertiser_id=None is fine for inventory sync operations
            adapter = GoogleAdManager(
                config=gam_config,
                principal=principal,
                network_code=adapter_config.gam_network_code,
                advertiser_id=None,  # Not needed for inventory sync
                trafficker_id=adapter_config.gam_trafficker_id or None,
                dry_run=False,
                audit_logger=None,
                tenant_id=tenant_id,
            )

            # Use the sync manager to perform the sync
            if sync_type == "full":
                result = adapter.sync_full(db_session, force=force)
            elif sync_type == "inventory":
                result = adapter.sync_inventory(db_session, force=force)
            elif sync_type == "targeting":
                # Targeting sync can be mapped to inventory sync for now
                result = adapter.sync_inventory(db_session, force=force)
            else:
                raise ValueError(f"Unsupported sync type: {sync_type}")

            return jsonify(result), 200

        except Exception as e:
            logger.error(f"Sync failed for tenant {tenant_id}: {e}", exc_info=True)

            # Update sync job with error (if it was created)
            try:
                sync_job.status = "failed"
                sync_job.completed_at = datetime.now(UTC)
                sync_job.error_message = str(e)
                db_session.commit()
            except:
                pass  # Ignore secondary errors in error handling

            return jsonify({"sync_id": sync_id, "status": "failed", "error": str(e)}), 500

    except Exception as e:
        logger.error(f"Failed to trigger sync: {e}", exc_info=True)
        db_session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/status/<sync_id>", methods=["GET"])
@require_tenant_management_api_key
def get_sync_status(sync_id: str):
    """Get status of a specific sync job."""
    try:
        db_session.remove()  # Start fresh

        stmt = select(SyncJob).filter_by(sync_id=sync_id)
        sync_job = db_session.scalars(stmt).first()
        if not sync_job:
            return jsonify({"error": "Sync job not found"}), 404

        response = {
            "sync_id": sync_job.sync_id,
            "tenant_id": sync_job.tenant_id,
            "adapter_type": sync_job.adapter_type,
            "sync_type": sync_job.sync_type,
            "status": sync_job.status,
            "started_at": sync_job.started_at.isoformat(),
            "triggered_by": sync_job.triggered_by,
            "triggered_by_id": sync_job.triggered_by_id,
        }

        if sync_job.completed_at:
            response["completed_at"] = sync_job.completed_at.isoformat()
            response["duration_seconds"] = (sync_job.completed_at - sync_job.started_at).total_seconds()

        if sync_job.summary:
            response["summary"] = json.loads(sync_job.summary)

        if sync_job.error_message:
            response["error"] = sync_job.error_message

        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Failed to get sync status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/history/<tenant_id>", methods=["GET"])
@require_tenant_management_api_key
def get_sync_history(tenant_id: str):
    """
    Get sync history for a tenant.

    Query parameters:
    - limit: Number of records to return (default: 10)
    - offset: Offset for pagination (default: 0)
    - status: Filter by status (optional)
    """
    try:
        db_session.remove()  # Start fresh

        # Get query parameters
        limit = int(request.args.get("limit", 10))
        offset = int(request.args.get("offset", 0))
        status_filter = request.args.get("status")

        # Build query
        stmt = select(SyncJob).filter_by(tenant_id=tenant_id)

        if status_filter:
            stmt = stmt.filter_by(status=status_filter)

        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db_session.scalar(count_stmt)

        # Get results
        stmt = stmt.order_by(SyncJob.started_at.desc()).limit(limit).offset(offset)
        sync_jobs = db_session.scalars(stmt).all()

        results = []
        for job in sync_jobs:
            result = {
                "sync_id": job.sync_id,
                "sync_type": job.sync_type,
                "status": job.status,
                "started_at": job.started_at.isoformat(),
                "triggered_by": job.triggered_by,
                "triggered_by_id": job.triggered_by_id,
            }

            if job.completed_at:
                result["completed_at"] = job.completed_at.isoformat()
                result["duration_seconds"] = (job.completed_at - job.started_at).total_seconds()

            if job.summary:
                result["summary"] = json.loads(job.summary)

            if job.error_message:
                result["error"] = job.error_message

            results.append(result)

        return jsonify({"total": total, "limit": limit, "offset": offset, "results": results}), 200

    except Exception as e:
        logger.error(f"Failed to get sync history: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/tenants", methods=["GET"])
@require_tenant_management_api_key
def list_tenants():
    """List all GAM-enabled tenants."""
    try:
        db_session.remove()  # Start fresh

        # Get all GAM tenants with their adapter configs
        stmt = select(Tenant).filter_by(ad_server="google_ad_manager")
        tenants = db_session.scalars(stmt).all()

        results = []
        for tenant in tenants:
            # Get adapter config
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)
            adapter_config = db_session.scalars(stmt).first()

            # Get last sync info
            stmt = (
                select(SyncJob)
                .where(SyncJob.tenant_id == tenant.tenant_id, SyncJob.status == "completed")
                .order_by(SyncJob.completed_at.desc())
            )
            last_sync = db_session.scalars(stmt).first()

            tenant_info = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "has_adapter_config": adapter_config is not None,
                "last_sync": (
                    {
                        "sync_id": last_sync.sync_id,
                        "completed_at": last_sync.completed_at.isoformat(),
                        "summary": json.loads(last_sync.summary) if last_sync.summary else None,
                    }
                    if last_sync
                    else None
                ),
            }

            if adapter_config:
                tenant_info["gam_network_code"] = adapter_config.gam_network_code
                # NOTE: gam_company_id removed - advertiser_id is per-principal

            results.append(tenant_info)

        return jsonify({"total": len(results), "tenants": results}), 200

    except Exception as e:
        logger.error(f"Failed to list tenants: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/stats", methods=["GET"])
@require_tenant_management_api_key
def get_sync_stats():
    """Get overall sync statistics across all tenants."""
    try:
        db_session.remove()  # Start fresh

        # Get stats for the last 24 hours
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0)

        # Count by status
        status_counts = {}
        for status in ["pending", "running", "completed", "failed"]:
            stmt = (
                select(func.count()).select_from(SyncJob).where(SyncJob.status == status, SyncJob.started_at >= since)
            )
            count = db_session.scalar(stmt)
            status_counts[status] = count

        # Get recent failures
        stmt = (
            select(SyncJob)
            .where(SyncJob.status == "failed", SyncJob.started_at >= since)
            .order_by(SyncJob.started_at.desc())
            .limit(5)
        )
        recent_failures = db_session.scalars(stmt).all()

        failures = []
        for job in recent_failures:
            failures.append(
                {
                    "sync_id": job.sync_id,
                    "tenant_id": job.tenant_id,
                    "started_at": job.started_at.isoformat(),
                    "error": job.error_message,
                }
            )

        # Get tenants that haven't synced recently
        stmt = select(Tenant).filter_by(ad_server="google_ad_manager")
        all_gam_tenants = db_session.scalars(stmt).all()

        stale_tenants = []
        for tenant in all_gam_tenants:
            stmt = (
                select(SyncJob)
                .where(SyncJob.tenant_id == tenant.tenant_id, SyncJob.status == "completed")
                .order_by(SyncJob.completed_at.desc())
            )
            last_sync = db_session.scalars(stmt).first()

            if not last_sync or (datetime.now(UTC) - last_sync.completed_at).days > 1:
                stale_tenants.append(
                    {
                        "tenant_id": tenant.tenant_id,
                        "tenant_name": tenant.name,
                        "last_sync": last_sync.completed_at.isoformat() if last_sync else None,
                    }
                )

        return (
            jsonify(
                {
                    "status_counts": status_counts,
                    "recent_failures": failures,
                    "stale_tenants": stale_tenants,
                    "since": since.isoformat(),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Failed to get sync stats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/tenant/<tenant_id>/orders/sync", methods=["POST"])
@require_tenant_management_api_key
def sync_tenant_orders(tenant_id):
    """Trigger orders and line items sync for a tenant."""
    db_session.remove()  # Clean start
    try:

        # Get tenant and adapter config
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return jsonify({"error": "Tenant not found"}), 404

        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
        adapter_config = db_session.scalars(stmt).first()

        if not adapter_config or not adapter_config.gam_network_code:
            return (
                jsonify({"error": "Please connect your GAM account first. Go to Ad Server settings to configure GAM."}),
                400,
            )

        # Create sync job
        sync_id = f"orders_sync_{tenant_id}_{int(datetime.now().timestamp())}"
        sync_job = SyncJob(
            sync_id=sync_id,
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            sync_type="orders",
            status="running",
            started_at=datetime.now(UTC),
            triggered_by="api",
            triggered_by_id="tenant_management_api",
        )

        db_session.add(sync_job)
        db_session.commit()

        try:
            # Initialize GAM client
            from gam_orders_service import GAMOrdersService

            from src.adapters.google_ad_manager import GoogleAdManager
            from src.core.schemas import Principal

            # Create dummy principal for sync (no advertiser needed for order discovery)
            principal = Principal(
                principal_id="system",
                name="System",
                access_token="system_sync_token",  # Required field for sync operations
                platform_mappings={},  # No advertiser_id needed for order discovery
            )

            # Build GAM config
            gam_config = {
                "enabled": True,
                "network_code": adapter_config.gam_network_code,
                "refresh_token": adapter_config.gam_refresh_token,
                "trafficker_id": adapter_config.gam_trafficker_id,
                "manual_approval_required": adapter_config.gam_manual_approval_required,
            }

            adapter = GoogleAdManager(
                gam_config,
                principal,
                network_code=adapter_config.gam_network_code,
                advertiser_id=None,  # Not needed for order discovery
                trafficker_id=adapter_config.gam_trafficker_id or None,
                tenant_id=tenant_id,
            )

            # Perform sync
            service = GAMOrdersService(db_session)
            summary = service.sync_tenant_orders(tenant_id, adapter.client)

            # Update sync job with results
            sync_job.status = "completed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.summary = json.dumps(summary)
            db_session.commit()

            return jsonify({"sync_id": sync_id, "status": "completed", "summary": summary}), 200

        except Exception as e:
            logger.error(f"Orders sync failed for tenant {tenant_id}: {e}", exc_info=True)

            # Update sync job with error
            sync_job.status = "failed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.error_message = str(e)
            db_session.commit()

            return jsonify({"sync_id": sync_id, "status": "failed", "error": str(e)}), 500

    except Exception as e:
        logger.error(f"Failed to trigger orders sync: {e}", exc_info=True)
        db_session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/tenant/<tenant_id>/orders", methods=["GET"])
@require_tenant_management_api_key
def get_tenant_orders(tenant_id):
    """Get orders for a tenant."""
    try:
        db_session.remove()  # Start fresh

        from gam_orders_service import GAMOrdersService

        # Validate tenant_id
        if not tenant_id or len(tenant_id) > 50:
            return jsonify({"error": "Invalid tenant_id"}), 400

        # Parse and validate filters from query params
        filters = {}

        status = request.args.get("status")
        if status:
            # Validate status is one of allowed values
            valid_statuses = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "PAUSED", "CANCELED", "DELETED"]
            if status not in valid_statuses:
                return jsonify({"error": f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}), 400
            filters["status"] = status

        advertiser_id = request.args.get("advertiser_id")
        if advertiser_id:
            # Validate advertiser_id format (alphanumeric)
            if not advertiser_id.isalnum() or len(advertiser_id) > 50:
                return jsonify({"error": "Invalid advertiser_id"}), 400
            filters["advertiser_id"] = advertiser_id

        search = request.args.get("search")
        if search:
            # Limit search string length for safety
            if len(search) > 200:
                return jsonify({"error": "Search string too long (max 200 characters)"}), 400
            filters["search"] = search

        has_line_items = request.args.get("has_line_items")
        if has_line_items:
            # Validate boolean string
            if has_line_items not in ["true", "false"]:
                return jsonify({"error": 'has_line_items must be "true" or "false"'}), 400
            filters["has_line_items"] = has_line_items

        service = GAMOrdersService(db_session)
        orders = service.get_orders(tenant_id, filters)

        return jsonify({"total": len(orders), "orders": orders}), 200

    except Exception as e:
        logger.error(f"Failed to get orders: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/tenant/<tenant_id>/orders/<order_id>", methods=["GET"])
@require_tenant_management_api_key
def get_order_details(tenant_id, order_id):
    """Get detailed information about an order including line items."""
    try:
        db_session.remove()  # Start fresh

        # Validate inputs
        if not tenant_id or len(tenant_id) > 50:
            return jsonify({"error": "Invalid tenant_id"}), 400
        if not order_id or len(order_id) > 50:
            return jsonify({"error": "Invalid order_id"}), 400

        from gam_orders_service import GAMOrdersService

        service = GAMOrdersService(db_session)
        order_details = service.get_order_details(tenant_id, order_id)

        if not order_details:
            return jsonify({"error": "Order not found"}), 404

        return jsonify(order_details), 200

    except Exception as e:
        logger.error(f"Failed to get order details: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


@sync_api.route("/tenant/<tenant_id>/line-items", methods=["GET"])
@require_tenant_management_api_key
def get_tenant_line_items(tenant_id):
    """Get line items for a tenant."""
    try:
        db_session.remove()  # Start fresh

        from gam_orders_service import GAMOrdersService

        # Parse filters from query params
        filters = {}
        if request.args.get("status"):
            filters["status"] = request.args.get("status")
        if request.args.get("line_item_type"):
            filters["line_item_type"] = request.args.get("line_item_type")
        if request.args.get("search"):
            filters["search"] = request.args.get("search")

        order_id = request.args.get("order_id")

        service = GAMOrdersService(db_session)
        line_items = service.get_line_items(tenant_id, order_id, filters)

        return jsonify({"total": len(line_items), "line_items": line_items}), 200

    except Exception as e:
        logger.error(f"Failed to get line items: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        db_session.remove()


def initialize_tenant_management_api_key() -> str:
    """Initialize tenant management API key if not exists."""
    with get_db_session() as db_session:
        # Check if API key exists
        stmt = select(TenantManagementConfig).filter_by(config_key="api_key")
        config = db_session.scalars(stmt).first()

        if config:
            return config.config_value

        # Generate new API key
        api_key = f"sk_{secrets.token_urlsafe(32)}"

        # Store in database
        new_config = TenantManagementConfig(
            config_key="api_key",
            config_value=api_key,
            description="Tenant management API key for programmatic access",
            updated_by="system",
            updated_at=datetime.now(UTC),
        )
        db_session.add(new_config)
        db_session.commit()

        logger.info(f"Generated new tenant management API key: {api_key[:10]}...")
        return api_key
