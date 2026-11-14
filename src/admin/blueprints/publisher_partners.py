"""Admin routes for the publishers a tenant sells for.

The operator adds a publisher's domain; everything else comes from the publisher's own
adagents.json, which ``src/services/publisher_ingest.py`` fetches and stores. Adding a
publisher ingests it at once, "Sync All" ingests every publisher (the scheduled refresh
does the same every six hours), and the properties view reads the stored document. No
route here reads a publisher's file except through the ingest service.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request

from src.admin.utils.operator_errors import safe_error_message
from src.core.database.models import Publisher
from src.core.database.repositories.uow import PublisherUoW
from src.core.http_utils import canonical_publisher_domain
from src.core.publisher_format_catalog import authorized_properties
from src.services.publisher_ingest import IngestOutcome, ingest_sync

logger = logging.getLogger(__name__)

publisher_partners_bp = Blueprint("publisher_partners", __name__)


def _outcome_json(outcome: IngestOutcome) -> dict[str, Any]:
    return {
        "publisher_domain": outcome.domain,
        "fetch_status": outcome.fetch_status,
        "error": outcome.error,
        "property_count": outcome.property_count,
        "format_option_count": outcome.format_option_count,
    }


def _publisher_json(publisher: Publisher, now: datetime) -> dict[str, Any]:
    authorized = authorized_properties(publisher, now)
    return {
        "publisher_domain": publisher.domain,
        "display_name": publisher.display_name,
        "authorizes": bool(authorized),
        "fetch_status": publisher.fetch_status,
        "fetch_error": publisher.fetch_error,
        "fetched_at": publisher.fetched_at.isoformat() if publisher.fetched_at else None,
        "fetch_attempted_at": publisher.fetch_attempted_at.isoformat() if publisher.fetch_attempted_at else None,
        "property_count": len(authorized),
        "format_option_count": len(publisher.format_options),
    }


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["GET"])
def list_publisher_partners(tenant_id: str) -> Response | tuple[Response, int]:
    """The tenant's publishers, with what each one's stored adagents.json authorizes now."""
    now = datetime.now(UTC)
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None
        partners = [_publisher_json(publisher, now) for publisher in uow.publishers.list_all()]
    return jsonify(
        {
            "partners": partners,
            "total": len(partners),
            "authorizing": sum(1 for p in partners if p["authorizes"]),
            "never_fetched": sum(1 for p in partners if p["fetch_status"] == "never_fetched"),
        }
    )


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["POST"])
def add_publisher_partner(tenant_id: str) -> Response | tuple[Response, int]:
    """Add a publisher by its domain, and ingest its adagents.json."""
    data = request.get_json(silent=True) or {}
    domain = canonical_publisher_domain(str(data.get("publisher_domain") or ""))
    if not domain:
        return jsonify({"error": "Publisher domain is required"}), 400
    try:
        with PublisherUoW(tenant_id) as uow:
            assert uow.publishers is not None and uow.tenant_config is not None
            if uow.tenant_config.get_tenant() is None:
                return jsonify({"error": "Tenant not found"}), 404
            if not uow.publishers.add_publisher(domain, str(data.get("display_name") or "").strip() or None):
                return jsonify({"error": "Publisher already exists"}), 409
    except Exception as e:
        # Anything but the duplicate the repository resolves: the operator is told the write
        # failed, never the driver's statement or parameters.
        logger.error(f"Error adding publisher: {e}", exc_info=True)
        return jsonify({"error": safe_error_message(e)}), 500
    (outcome,) = ingest_sync(tenant_id, [domain])
    return jsonify({**_outcome_json(outcome), "message": "Publisher added"}), 201


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<path:domain>", methods=["DELETE"])
def delete_publisher_partner(tenant_id: str, domain: str) -> Response | tuple[Response, int]:
    """Remove a publisher; its derived properties and every selection of it go with it."""
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None
        if not uow.publishers.delete(domain):
            return jsonify({"error": "Publisher not found"}), 404
    return jsonify({"message": "Publisher deleted successfully"})


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/sync", methods=["POST"])
def sync_publisher_partners(tenant_id: str) -> Response | tuple[Response, int]:
    """Ingest every publisher's adagents.json now."""
    try:
        with PublisherUoW(tenant_id) as uow:
            assert uow.publishers is not None
            domains = [publisher.domain for publisher in uow.publishers.list_all()]
        outcomes = ingest_sync(tenant_id, domains)
    except Exception as e:
        logger.error(f"Error syncing publishers: {e}", exc_info=True)
        return jsonify({"error": safe_error_message(e)}), 500
    return jsonify(
        {
            "message": "Sync completed",
            "total": len(outcomes),
            "ok": sum(1 for outcome in outcomes if outcome.fetch_status == "ok"),
            "errors": sum(1 for outcome in outcomes if outcome.fetch_status != "ok"),
            "outcomes": [_outcome_json(outcome) for outcome in outcomes],
        }
    )


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<path:domain>/properties", methods=["GET"])
def get_publisher_properties(tenant_id: str, domain: str) -> Response | tuple[Response, int]:
    """What the publisher's stored adagents.json authorizes this agent for now. Fetches nothing."""
    now = datetime.now(UTC)
    with PublisherUoW(tenant_id) as uow:
        assert uow.publishers is not None and uow.tenant_config is not None
        publisher = uow.publishers.get(domain)
        tenant = uow.tenant_config.get_tenant()
        if publisher is None or tenant is None:
            return jsonify({"error": "Publisher not found"}), 404
        if publisher.adagents is None:
            return jsonify({"error": "No adagents.json has been fetched from this publisher", "is_authorized": False})
        properties = authorized_properties(publisher, now)
        if not properties:
            return jsonify(
                {"error": f"Agent {tenant.agent_url} is not authorized by this publisher", "is_authorized": False}
            )
        return jsonify(
            {
                "domain": publisher.domain,
                "is_authorized": True,
                "property_ids": list(dict.fromkeys(p.property_id for p in properties if p.property_id)),
                "property_tags": sorted({tag for p in properties for tag in p.tags or ()}),
                "format_option_ids": sorted(option.format_option_id for option in publisher.format_options),
                "fetched_at": publisher.fetched_at.isoformat() if publisher.fetched_at else None,
            }
        )
