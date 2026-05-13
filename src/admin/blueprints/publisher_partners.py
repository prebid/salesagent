"""Blueprint for managing publisher partnerships."""

import asyncio
import ipaddress
import logging
import re
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

from src.admin.utils import require_tenant_access
from src.core.config import get_config
from src.core.database.database_session import get_db_session
from src.core.database.models import PublisherPartner, Tenant
from src.core.domain_config import get_tenant_url
from src.core.security.url_validator import BLOCKED_HOSTNAMES, check_url_ssrf
from src.services.aao_lookup_service import (
    PublisherPartnerStatus,
    get_publisher_partner_status,
)
from src.services.agent_url_resolver import resolve_agent_url as _resolve_agent_url

logger = logging.getLogger(__name__)

publisher_partners_bp = Blueprint("publisher_partners", __name__)


def _reject_if_embedded(tenant: Tenant) -> tuple[Response, int] | None:
    """Mutating publisher-partner endpoints reject embedded tenants —
    Scope3 owns the partner roster on managed-mode tenants. The UI hides
    the buttons; this guard catches direct API hits."""
    if tenant.is_embedded:
        return (
            jsonify({"error": "Embedded tenants are platform-managed; publisher partners are configured by Scope3."}),
            403,
        )
    return None


def _persist_status(partner: PublisherPartner, status: PublisherPartnerStatus) -> None:
    """Copy a fresh AAO status snapshot onto a PublisherPartner row.

    Single source of truth for translating the in-memory status object into
    persistence — used by both the per-row refresh endpoint and the bulk
    Verify-All path so they can't drift."""
    partner.total_properties = status.total_properties
    partner.authorized_properties = status.authorized_properties
    partner.last_refreshed_at = datetime.now(UTC)
    partner.aao_status_kind = status.status
    # last_fetch_error is reserved for the "fetch failed" path so the
    # legacy derivation in _partner_to_dict (used when aao_status_kind is
    # NULL, e.g. after a column rollback) doesn't mis-render unbound or
    # no_properties rows as "unreachable". Diagnostic hints for the
    # post-fetch states live in sync_error instead, which the UI surfaces
    # alongside the chip.
    if status.status == "unreachable":
        partner.last_fetch_error = status.error
        partner.sync_status = "error"
        partner.sync_error = status.error
        partner.is_verified = False
    elif status.status in ("authorized", "unbound"):
        # Operational states — products can bind. "unbound" is non-conformant
        # (no authorization_type on the publisher's entry) but the salesagent
        # resolves permissively against top-level properties[] so the row is
        # usable today. The chip + sync_error hint nudge the publisher to add
        # a typed binding for spec conformance.
        partner.last_fetch_error = None
        partner.sync_status = "success"
        partner.sync_error = status.error  # hint copy for unbound, None for authorized
        partner.is_verified = True
        partner.last_synced_at = datetime.now(UTC)
    elif status.status == "no_properties":
        # File fetched cleanly but exposes zero usable inventory. Counted
        # as an error by the bulk sync handler (no inventory = nothing to
        # do), so sync_status mirrors that for consistency with the
        # response payload's `errors` count.
        partner.last_fetch_error = None
        partner.sync_status = "error"
        partner.sync_error = status.error
        partner.is_verified = False
        partner.last_synced_at = datetime.now(UTC)
    else:  # pending — file fetched cleanly, publisher just hasn't authorized us
        partner.last_fetch_error = None
        partner.sync_status = "success"
        partner.sync_error = status.error  # may be None or a typed-binding-empty hint
        partner.is_verified = False
        partner.last_synced_at = datetime.now(UTC)


def _partner_to_dict(partner: PublisherPartner, *, fallback_property_count: int = 0) -> dict:
    """Serialize a PublisherPartner row for the JSON list endpoint.

    ``fallback_property_count`` is the legacy count from AuthorizedProperty,
    used only when the new AAO ``total_properties`` column is NULL (pre-AAO
    rows that haven't been refreshed yet)."""
    aao_url = f"https://agenticadvertising.org/publisher/{partner.publisher_domain}"
    if partner.total_properties is None and partner.aao_status_kind is None:
        # Pre-AAO row — legacy count from AuthorizedProperty as a stopgap so
        # the UI shows something until the next sync runs.
        total = fallback_property_count
        authorized = fallback_property_count if partner.is_verified else 0
        ui_status = "stale"
    elif partner.aao_status_kind is not None:
        # Persisted kind from aao_lookup_service is the source of truth —
        # distinguishes "invalid" (schema-broken file) from "unreachable"
        # (fetch failed), which legacy derivation collapsed together.
        total = partner.total_properties or 0
        authorized = partner.authorized_properties or 0
        ui_status = partner.aao_status_kind
    elif partner.last_fetch_error:
        total = partner.total_properties or 0
        authorized = partner.authorized_properties or 0
        ui_status = "unreachable"
    elif (partner.authorized_properties or 0) > 0:
        total = partner.total_properties or 0
        authorized = partner.authorized_properties or 0
        ui_status = "authorized"
    else:
        total = partner.total_properties or 0
        authorized = partner.authorized_properties or 0
        ui_status = "pending"

    return {
        "id": partner.id,
        "publisher_domain": partner.publisher_domain,
        "display_name": partner.display_name,
        "is_verified": partner.is_verified,
        "last_synced_at": partner.last_synced_at.isoformat() if partner.last_synced_at else None,
        "last_refreshed_at": partner.last_refreshed_at.isoformat() if partner.last_refreshed_at else None,
        "sync_status": partner.sync_status,
        "sync_error": partner.sync_error,
        "last_fetch_error": partner.last_fetch_error,
        "total_properties": total,
        "authorized_properties": authorized,
        "aao_status": ui_status,
        "aao_onboarding_url": aao_url,
        "created_at": partner.created_at.isoformat(),
        # Legacy alias for unmigrated callers.
        "property_count": total,
    }


# RFC 1035-ish — each label up to 63 chars, total up to 253. Conservative
# subset of the spec (ASCII, no underscores, no leading/trailing hyphens).
# Real publisher domains all match this; the regex doubles as an SSRF gate
# by rejecting non-hostname strings before they reach any outbound call.
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def _validate_publisher_domain(domain: str) -> tuple[bool, str]:
    """Format-only validation for publisher_domain at create time.

    Rejects IP literals, localhost-likes, Docker-internal hostnames, and
    structurally-malformed strings. Does NOT do DNS resolution — a brand-
    new publisher domain may not resolve yet but should still be acceptable
    to register; the DNS-time SSRF check fires inside ``check_publisher``
    before the actual outbound HTTP call.

    Returns ``(is_safe, error_message)``.
    """
    if not domain or len(domain) > 253:
        return False, "Publisher domain must be 1-253 characters"
    if domain in BLOCKED_HOSTNAMES:
        return False, f"Publisher domain '{domain}' is blocked (internal/private)"
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        return False, "Publisher domain must be a hostname, not an IP address"
    if not _DOMAIN_RE.match(domain):
        return False, "Publisher domain has invalid format"
    return True, ""


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["GET"])
@require_tenant_access(api_mode=True)
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
            partners_list = [
                _partner_to_dict(
                    partner,
                    fallback_property_count=property_counts.get(partner.publisher_domain, 0),
                )
                for partner in partners
            ]

            total_properties = sum(p["total_properties"] or 0 for p in partners_list)
            authorized_properties = sum(p["authorized_properties"] or 0 for p in partners_list)

            return jsonify(
                {
                    "partners": partners_list,
                    "total": len(partners_list),
                    "verified": sum(1 for p in partners_list if p["is_verified"]),
                    "pending": sum(1 for p in partners_list if p["sync_status"] == "pending"),
                    "total_properties": total_properties,
                    "authorized_properties": authorized_properties,
                }
            )

    except Exception as e:
        logger.error(f"Error listing publisher partners: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners", methods=["POST"])
@require_tenant_access(api_mode=True, role=("admin", "member"))
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

        # SSRF gate at the boundary — IP literals, localhost-likes, malformed
        # strings can't be persisted, so downstream callers (sync, property
        # discovery) never see them. See ``_validate_publisher_domain``.
        ok, err = _validate_publisher_domain(publisher_domain)
        if not ok:
            return jsonify({"error": err}), 400

        with get_db_session() as session:
            # Check tenant adapter type
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            embedded_reject = _reject_if_embedded(tenant)
            if embedded_reject is not None:
                return embedded_reject

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

            # Create new partner. Auto-verify for dev environment or mock
            # adapters (no real adagents.json to check). For auto-verified
            # rows we also stamp ``last_refreshed_at`` so the UI renders
            # them as "authorized" instead of the "refresh needed" placeholder
            # — mock tenants never round-trip through the real AAO path.
            now = datetime.now(UTC)
            partner = PublisherPartner(
                tenant_id=tenant_id,
                publisher_domain=publisher_domain,
                display_name=display_name or publisher_domain,
                sync_status="success" if should_auto_verify else "pending",
                is_verified=should_auto_verify,
                last_synced_at=now if should_auto_verify else None,
                last_refreshed_at=now if should_auto_verify else None,
                total_properties=0 if should_auto_verify else None,
                authorized_properties=0 if should_auto_verify else None,
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
@require_tenant_access(api_mode=True, role=("admin", "member"))
def delete_publisher_partner(tenant_id: str, partner_id: int) -> Response | tuple[Response, int]:
    """Delete a publisher partner."""
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404
            embedded_reject = _reject_if_embedded(tenant)
            if embedded_reject is not None:
                return embedded_reject

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
@require_tenant_access(api_mode=True, role=("admin", "member"))
def sync_publisher_partners(tenant_id: str) -> Response | tuple[Response, int]:
    """Sync verification status for all publisher partners."""
    try:
        with get_db_session() as session:
            # Get tenant
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt_tenant).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404
            embedded_reject = _reject_if_embedded(tenant)
            if embedded_reject is not None:
                return embedded_reject

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
                now = datetime.now(UTC)
                for partner in partners:
                    partner.sync_status = "success"
                    partner.sync_error = None
                    partner.is_verified = True
                    partner.last_synced_at = now
                    # Stamp the AAO columns so _partner_to_dict renders these
                    # as authorized rather than perpetual "refresh needed".
                    # Mock/dev partners can't be probed against a real AAO.
                    partner.last_refreshed_at = now
                    partner.last_fetch_error = None
                    if partner.total_properties is None:
                        partner.total_properties = 0
                    if partner.authorized_properties is None:
                        partner.authorized_properties = 0
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

                    # Compute agent_url for property resolution (handles property_ids, property_tags)
                    if tenant.virtual_host:
                        agent_url_for_sync: str | None = f"https://{tenant.virtual_host}"
                    else:
                        agent_url_for_sync = get_tenant_url(tenant.subdomain)

                    for domain in verified_domains:
                        # Try to fetch real properties from adagents.json
                        property_stats = discovery_service.sync_properties_from_adagents_sync(
                            tenant_id, publisher_domains=[domain], dry_run=False, agent_url=agent_url_for_sync
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

            agent_url = _resolve_agent_url(tenant)
            if not agent_url:
                return (
                    jsonify(
                        {
                            "error": "Agent URL not configured (set public_agent_url, virtual_host, or SALES_AGENT_DOMAIN)"
                        }
                    ),
                    500,
                )

            logger.info(f"Fetching AAO status for {len(partners)} publishers (agent_url={agent_url})")

            # DNS-time SSRF check — defense in depth on top of the create-time
            # format gate (_validate_publisher_domain at row insert). Catches
            # a domain whose resolution flips to a private/loopback/metadata
            # IP after it was registered. Failed partners are persisted as
            # errors and skipped from the AAO fetch batch.
            now = datetime.now(UTC)
            safe_partners: list[PublisherPartner] = []
            ssrf_errors = 0
            for partner in partners:
                ssrf_ok, ssrf_err = check_url_ssrf(f"https://{partner.publisher_domain}")
                if not ssrf_ok:
                    partner.sync_status = "error"
                    partner.sync_error = f"Refused: {ssrf_err}"
                    partner.last_fetch_error = ssrf_err
                    partner.last_refreshed_at = now
                    partner.is_verified = False
                    ssrf_errors += 1
                    continue
                safe_partners.append(partner)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                tasks = [
                    get_publisher_partner_status(p.publisher_domain, agent_url, force_refresh=True)
                    for p in safe_partners
                ]
                statuses = loop.run_until_complete(asyncio.wait_for(asyncio.gather(*tasks), timeout=30.0))
            finally:
                loop.close()

            statuses_by_domain = {s.publisher_domain: s for s in statuses}

            verified_domains = []
            synced = 0
            verified = 0
            errors = ssrf_errors
            for partner in safe_partners:
                status = statuses_by_domain.get(partner.publisher_domain)
                if status is None:
                    continue
                _persist_status(partner, status)
                if status.status in ("authorized", "unbound"):
                    # Both states are operational — products can bind. Run
                    # property discovery so AuthorizedProperty rows populate
                    # for the products page (unbound publishers like
                    # wonderstruck.org depend on this).
                    verified += 1
                    verified_domains.append(partner.publisher_domain)
                    synced += 1
                elif status.status == "pending":
                    synced += 1
                else:  # no_properties or unreachable
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
                    tenant_id, publisher_domains=verified_domains, dry_run=False, agent_url=agent_url
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


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<int:partner_id>/refresh", methods=["POST"])
@require_tenant_access(api_mode=True, role=("admin", "member"))
def refresh_publisher_partner(tenant_id: str, partner_id: int) -> Response | tuple[Response, int]:
    """Force-refresh AAO status for a single publisher partner.

    Bypasses the 6h adagents.json cache and re-queries the publisher's
    adagents.json. Persists ``total_properties`` / ``authorized_properties``
    so the UI renders the fresh "47 / 200 authorized" picture immediately.
    """
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404
            embedded_reject = _reject_if_embedded(tenant)
            if embedded_reject is not None:
                return embedded_reject

            partner = session.scalars(select(PublisherPartner).filter_by(id=partner_id, tenant_id=tenant_id)).first()
            if not partner:
                return jsonify({"error": "Publisher not found"}), 404

            agent_url = _resolve_agent_url(tenant)
            if not agent_url:
                return jsonify({"error": "Agent URL not configured"}), 500

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                status = loop.run_until_complete(
                    asyncio.wait_for(
                        get_publisher_partner_status(partner.publisher_domain, agent_url, force_refresh=True),
                        timeout=15.0,
                    )
                )
            finally:
                loop.close()

            _persist_status(partner, status)
            session.commit()

            return jsonify(_partner_to_dict(partner))

    except Exception as e:
        logger.error(f"Error refreshing publisher partner: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@publisher_partners_bp.route("/<tenant_id>/publisher-partners/<int:partner_id>/properties", methods=["GET"])
@require_tenant_access(api_mode=True)
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
