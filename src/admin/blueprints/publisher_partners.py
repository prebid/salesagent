"""Blueprint for managing publisher partnerships."""

import asyncio
import logging
from datetime import UTC, datetime

from adcp.adagents import (
    AuthorizationContext,
    fetch_adagents,
    get_properties_by_agent,
    verify_agent_authorization,
)
from adcp.exceptions import AdagentsNotFoundError, AdagentsTimeoutError, AdagentsValidationError
from flask import Blueprint, Response, jsonify, request
from sqlalchemy import select

from src.core.config import get_config
from src.core.database.database_session import get_db_session
from src.core.database.models import PublisherPartner, Tenant
from src.core.domain_config import get_tenant_url

logger = logging.getLogger(__name__)

publisher_partners_bp = Blueprint("publisher_partners", __name__)


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["GET"])
def list_publisher_partners(tenant_id: str) -> Response | tuple[Response, int]:
    """List all publisher partners for a tenant."""
    try:
        with get_db_session() as session:
            # Get tenant
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get all publisher partners
            stmt_partners = (
                select(PublisherPartner).filter_by(tenant_id=tenant_id).order_by(PublisherPartner.publisher_domain)
            )
            partners = session.scalars(stmt_partners).all()

            # Get property counts per publisher domain
            from sqlalchemy import func

            from src.core.database.models import AuthorizedProperty

            property_counts_stmt = (
                select(AuthorizedProperty.publisher_domain, func.count(AuthorizedProperty.property_id))
                .filter(AuthorizedProperty.tenant_id == tenant_id)
                .group_by(AuthorizedProperty.publisher_domain)
            )
            property_counts = {row[0]: row[1] for row in session.execute(property_counts_stmt).all()}

            # Convert to dict
            partners_list = []
            for partner in partners:
                partners_list.append(
                    {
                        "id": partner.id,
                        "publisher_domain": partner.publisher_domain,
                        "display_name": partner.display_name,
                        "is_verified": partner.is_verified,
                        "last_synced_at": partner.last_synced_at.isoformat() if partner.last_synced_at else None,
                        "sync_status": partner.sync_status,
                        "sync_error": partner.sync_error,
                        "created_at": partner.created_at.isoformat(),
                        "property_count": property_counts.get(partner.publisher_domain, 0),
                    }
                )

            return jsonify(
                {
                    "partners": partners_list,
                    "total": len(partners_list),
                    "verified": sum(1 for p in partners_list if p["is_verified"]),
                    "pending": sum(1 for p in partners_list if p["sync_status"] == "pending"),
                }
            )

    except Exception as e:
        logger.error(f"Error listing publisher partners: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["POST"])
def add_publisher_partner(tenant_id: str) -> Response | tuple[Response, int]:
    """Add a new publisher partner."""
    try:
        data = request.get_json()
        publisher_domain = data.get("publisher_domain", "").strip().lower()
        display_name = data.get("display_name", "").strip()

        if not publisher_domain:
            return jsonify({"error": "Publisher domain is required"}), 400

        # Remove http:// or https:// if present
        publisher_domain = publisher_domain.replace("https://", "").replace("http://", "")
        # Remove trailing slash
        publisher_domain = publisher_domain.rstrip("/")

        with get_db_session() as session:
            # Check tenant adapter type
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # For mock adapters OR development environment, auto-verify publishers (no adagents.json to check)
            # Development: Local dev servers won't be in any publisher's adagents.json
            # Mock: Testing tenants use fake domains
            config = get_config()
            is_dev = config.environment == "development"
            is_mock = tenant.adapter_config and tenant.adapter_config.adapter_type == "mock"
            should_auto_verify = is_dev or is_mock

            # Check if already exists
            stmt = select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain=publisher_domain)
            existing = session.scalars(stmt).first()
            if existing:
                return jsonify({"error": "Publisher already exists"}), 409

            # Create new partner
            # Auto-verify for dev environment or mock adapters (no real adagents.json to check)
            partner = PublisherPartner(
                tenant_id=tenant_id,
                publisher_domain=publisher_domain,
                display_name=display_name or publisher_domain,
                sync_status="success" if should_auto_verify else "pending",
                is_verified=should_auto_verify,
                last_synced_at=datetime.now(UTC) if should_auto_verify else None,
            )
            session.add(partner)
            session.commit()

            # Build message
            message = "Publisher added successfully"
            if should_auto_verify:
                reasons = []
                if is_dev:
                    reasons.append("development environment")
                if is_mock:
                    reasons.append("mock tenant")
                message += f" (auto-verified for {' and '.join(reasons)})"

            return (
                jsonify(
                    {
                        "id": partner.id,
                        "publisher_domain": partner.publisher_domain,
                        "display_name": partner.display_name,
                        "sync_status": partner.sync_status,
                        "is_verified": partner.is_verified,
                        "message": message,
                    }
                ),
                201,
            )

    except Exception as e:
        logger.error(f"Error adding publisher partner: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<int:partner_id>", methods=["DELETE"])
def delete_publisher_partner(tenant_id: str, partner_id: int) -> Response | tuple[Response, int]:
    """Delete a publisher partner."""
    try:
        with get_db_session() as session:
            stmt = select(PublisherPartner).filter_by(id=partner_id, tenant_id=tenant_id)
            partner = session.scalars(stmt).first()

            if not partner:
                return jsonify({"error": "Publisher not found"}), 404

            session.delete(partner)
            session.commit()

            return jsonify({"message": "Publisher deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting publisher partner: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/sync", methods=["POST"])
def sync_publisher_partners(tenant_id: str) -> Response | tuple[Response, int]:
    """Sync verification status for all publisher partners."""
    try:
        with get_db_session() as session:
            # Get tenant
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get all publisher partners
            stmt_partners = select(PublisherPartner).filter_by(tenant_id=tenant_id)
            partners = session.scalars(stmt_partners).all()

            if not partners:
                return jsonify({"message": "No publishers to sync"}), 200

            # For development environment or mock adapters, auto-verify publishers
            # (don't require our agent to be in their adagents.json)
            # BUT still fetch real properties from adagents.json if available
            config = get_config()
            is_dev = config.environment == "development"
            is_mock = tenant.adapter_config and tenant.adapter_config.adapter_type == "mock"
            should_auto_verify = is_dev or is_mock

            if should_auto_verify:
                reasons = []
                if is_dev:
                    reasons.append("development environment")
                if is_mock:
                    reasons.append("mock tenant")
                reason_str = " and ".join(reasons)

                logger.info(f"{reason_str} detected - auto-verifying {len(partners)} publishers")
                verified_domains = []
                for partner in partners:
                    partner.sync_status = "success"
                    partner.sync_error = None
                    partner.is_verified = True
                    partner.last_synced_at = datetime.now(UTC)
                    verified_domains.append(partner.publisher_domain)

                session.commit()

                # Try to fetch real properties from adagents.json for each publisher
                # This allows mock tenants to test with real publisher inventory
                properties_created = 0
                properties_updated = 0
                tags_created = 0
                fallback_properties = 0

                if verified_domains:
                    from src.services.property_discovery_service import get_property_discovery_service

                    discovery_service = get_property_discovery_service()

                    for domain in verified_domains:
                        # Try to fetch real properties from adagents.json
                        property_stats = discovery_service.sync_properties_from_adagents_sync(
                            tenant_id, publisher_domains=[domain], dry_run=False
                        )
                        domain_properties_created = property_stats.get("properties_created", 0)
                        properties_created += domain_properties_created
                        properties_updated += property_stats.get("properties_updated", 0)
                        tags_created += property_stats.get("tags_created", 0)

                        # Check if sync failed or created no properties
                        # (errors list or 0 properties means adagents.json unavailable/empty)
                        has_errors = bool(property_stats.get("errors", []))
                        if has_errors or domain_properties_created == 0:
                            # Create a fallback mock property for this domain
                            logger.info(
                                f"No properties from {domain} adagents.json "
                                f"(errors: {has_errors}, created: {domain_properties_created}) - "
                                f"creating fallback mock property"
                            )

                            from src.core.database.models import AuthorizedProperty, PropertyTag

                            # Ensure 'all_inventory' tag exists
                            tag_stmt = select(PropertyTag).where(
                                PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == "all_inventory"
                            )
                            all_inventory_tag = session.scalars(tag_stmt).first()
                            if not all_inventory_tag:
                                all_inventory_tag = PropertyTag(
                                    tag_id="all_inventory",
                                    tenant_id=tenant_id,
                                    name="All Inventory",
                                    description="Default tag that applies to all properties.",
                                    created_at=datetime.now(UTC),
                                    updated_at=datetime.now(UTC),
                                )
                                session.add(all_inventory_tag)
                                tags_created += 1

                            # Create fallback property
                            property_id = f"website_{domain.replace('.', '_').replace('-', '_')}"
                            prop_stmt = select(AuthorizedProperty).where(
                                AuthorizedProperty.tenant_id == tenant_id,
                                AuthorizedProperty.property_id == property_id,
                            )
                            existing = session.scalars(prop_stmt).first()
                            if not existing:
                                fallback_property = AuthorizedProperty(
                                    tenant_id=tenant_id,
                                    property_id=property_id,
                                    property_type="website",
                                    name=domain,
                                    publisher_domain=domain,
                                    identifiers=[{"type": "domain", "value": domain}],
                                    tags=["all_inventory"],
                                    verification_status="verified",
                                    verification_checked_at=datetime.now(UTC),
                                    created_at=datetime.now(UTC),
                                    updated_at=datetime.now(UTC),
                                )
                                session.add(fallback_property)
                                fallback_properties += 1

                            session.commit()
                        else:
                            logger.info(f"Fetched real properties from {domain}: {domain_properties_created} created")

                return jsonify(
                    {
                        "message": f"Sync completed ({reason_str} - auto-verified)",
                        "synced": len(partners),
                        "verified": len(partners),
                        "errors": 0,
                        "total": len(partners),
                        "properties_created": properties_created + fallback_properties,
                        "properties_updated": properties_updated,
                        "tags_created": tags_created,
                    }
                )

            # Get our agent URL - use virtual_host if configured, otherwise construct from subdomain
            if tenant.virtual_host:
                agent_url: str = f"https://{tenant.virtual_host}"
            else:
                maybe_url = get_tenant_url(tenant.subdomain)
                if not maybe_url:
                    return jsonify({"error": "Agent URL not configured (SALES_AGENT_DOMAIN not set)"}), 500
                agent_url = maybe_url

            # Fetch authorization for each publisher (real verification for non-mock tenants)
            logger.info(f"Fetching authorizations for {len(partners)} publishers")

            synced = 0
            verified = 0
            errors = 0

            async def check_publisher(domain: str) -> tuple[str, dict]:
                """Check a single publisher and return status."""
                try:
                    # Fetch adagents.json
                    adagents_data = await fetch_adagents(domain, timeout=10.0)

                    # Check if agent is authorized
                    is_authorized = verify_agent_authorization(adagents_data, agent_url)

                    if is_authorized:
                        # Get properties for this agent
                        properties = get_properties_by_agent(adagents_data, agent_url)
                        ctx = AuthorizationContext(properties)

                        return (domain, {"status": "success", "is_verified": True, "error": None, "context": ctx})
                    else:
                        # Agent not authorized
                        return (
                            domain,
                            {
                                "status": "error",
                                "is_verified": False,
                                "error": f"Agent {agent_url} is not authorized by this publisher",
                                "context": None,
                            },
                        )

                except AdagentsNotFoundError:
                    return (
                        domain,
                        {
                            "status": "error",
                            "is_verified": False,
                            "error": "Publisher adagents.json not found (404)",
                            "context": None,
                        },
                    )
                except AdagentsTimeoutError:
                    return (
                        domain,
                        {"status": "error", "is_verified": False, "error": "Request timed out", "context": None},
                    )
                except AdagentsValidationError as e:
                    return (
                        domain,
                        {
                            "status": "error",
                            "is_verified": False,
                            "error": f"Invalid adagents.json: {str(e)}",
                            "context": None,
                        },
                    )
                except Exception as e:
                    return (
                        domain,
                        {
                            "status": "error",
                            "is_verified": False,
                            "error": f"Unexpected error: {str(e)}",
                            "context": None,
                        },
                    )

            # Run async checks with overall timeout
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                tasks = [check_publisher(p.publisher_domain) for p in partners]
                # Add 30s overall timeout to prevent infinite hangs (individual checks have 10s timeout)
                results = loop.run_until_complete(asyncio.wait_for(asyncio.gather(*tasks), timeout=30.0))
                results_dict = dict(results)
            finally:
                loop.close()

            # Update each partner with results
            verified_domains = []
            for partner in partners:
                result = results_dict.get(partner.publisher_domain)

                if result and result["status"] == "success":
                    partner.sync_status = "success"
                    partner.sync_error = None
                    partner.is_verified = result["is_verified"]
                    partner.last_synced_at = datetime.now(UTC)
                    synced += 1
                    if result["is_verified"]:
                        verified += 1
                        verified_domains.append(partner.publisher_domain)
                elif result:
                    partner.sync_status = "error"
                    partner.sync_error = result["error"]
                    partner.is_verified = False
                    errors += 1

            session.commit()

            # CRITICAL: Now sync properties from verified publishers
            # This populates AuthorizedProperty table which is used by Admin UI for building
            # inventory profiles and products (requires full property details)
            if verified_domains:
                logger.info(f"Syncing properties from {len(verified_domains)} verified publishers")
                from src.services.property_discovery_service import get_property_discovery_service

                discovery_service = get_property_discovery_service()
                property_stats = discovery_service.sync_properties_from_adagents_sync(
                    tenant_id, publisher_domains=verified_domains, dry_run=False
                )
                logger.info(
                    f"Property sync completed: {property_stats['properties_created']} created, "
                    f"{property_stats['properties_updated']} updated, "
                    f"{property_stats['tags_created']} tags created"
                )

            return jsonify(
                {
                    "message": "Sync completed",
                    "synced": synced,
                    "verified": verified,
                    "errors": errors,
                    "total": len(partners),
                }
            )

    except Exception as e:
        logger.error(f"Error syncing publisher partners: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<int:partner_id>/properties", methods=["GET"])
def get_publisher_properties(tenant_id: str, partner_id: int) -> Response | tuple[Response, int]:
    """Get properties for a specific publisher (fetched fresh from adagents.json)."""
    try:
        with get_db_session() as session:
            # Get tenant
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get publisher partner
            stmt_partner = select(PublisherPartner).filter_by(id=partner_id, tenant_id=tenant_id)
            partner = session.scalars(stmt_partner).first()

            if not partner:
                return jsonify({"error": "Publisher not found"}), 404

            # Get our agent URL - use virtual_host if configured, otherwise construct from subdomain
            if tenant.virtual_host:
                agent_url: str = f"https://{tenant.virtual_host}"
            else:
                maybe_url = get_tenant_url(tenant.subdomain)
                if not maybe_url:
                    return jsonify({"error": "Agent URL not configured (SALES_AGENT_DOMAIN not set)"}), 500
                agent_url = maybe_url

            # Fetch fresh authorization context
            logger.info(f"Fetching properties for {partner.publisher_domain}")

            try:
                # Fetch adagents.json
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    adagents_data = loop.run_until_complete(fetch_adagents(partner.publisher_domain, timeout=10.0))
                finally:
                    loop.close()

                # Check if agent is authorized
                is_authorized = verify_agent_authorization(adagents_data, agent_url)

                if not is_authorized:
                    return (
                        jsonify(
                            {"error": f"Agent {agent_url} is not authorized by this publisher", "is_authorized": False}
                        ),
                        200,
                    )

                # Get properties for this agent
                properties = get_properties_by_agent(adagents_data, agent_url)
                ctx = AuthorizationContext(properties)

                # Return authorization context
                return jsonify(
                    {
                        "domain": partner.publisher_domain,
                        "is_authorized": True,
                        "property_ids": ctx.property_ids,
                        "property_tags": ctx.property_tags,
                        "properties": ctx.raw_properties,
                    }
                )

            except AdagentsNotFoundError:
                return jsonify({"error": "Publisher adagents.json not found (404)", "is_authorized": False}), 200
            except AdagentsTimeoutError:
                return jsonify({"error": "Request timed out", "is_authorized": False}), 200
            except AdagentsValidationError as e:
                return jsonify({"error": f"Invalid adagents.json: {str(e)}", "is_authorized": False}), 200

    except Exception as e:
        logger.error(f"Error fetching publisher properties: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
