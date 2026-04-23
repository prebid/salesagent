"""Service for discovering and caching properties from publisher adagents.json files.

This service fetches properties and tags from publishers' adagents.json files
and caches them in the database for use in inventory profiles and products.
"""

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any, cast

from adcp import (
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
    fetch_adagents,
    get_all_properties,
    get_all_tags,
)
from adcp.adagents import get_properties_by_agent, normalize_url
from sqlalchemy import select
from sqlalchemy.sql import Select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty, PropertyTag

logger = logging.getLogger(__name__)


_SUBDOMAIN_PREFIXES = ("www.", "m.", "mobile.")


def _normalize_domain(domain: str) -> str:
    """Strip common subdomain prefixes and lowercase for comparison.

    Handles variants like ``www.example.com``, ``m.example.com``, and
    ``mobile.example.com`` so they all compare equal to ``example.com``.
    """
    domain = domain.strip().lower()
    for prefix in _SUBDOMAIN_PREFIXES:
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]
            break  # Only strip one prefix layer
    return domain


def _domains_match(publisher_domain: str, domain_identifiers: list[str]) -> bool:
    """Check whether *publisher_domain* matches any value in *domain_identifiers*.

    Comparison is done on normalized domains so that ``www.example.com``
    matches ``example.com`` and vice-versa.
    """
    normalized_publisher = _normalize_domain(publisher_domain)
    return any(_normalize_domain(d) == normalized_publisher for d in domain_identifiers)


def _make_stats(dry_run: bool) -> dict[str, Any]:
    """Create an empty stats dict for sync operations."""
    return {
        "domains_synced": 0,
        "properties_found": 0,
        "tags_found": 0,
        "properties_created": 0,
        "properties_updated": 0,
        "tags_created": 0,
        "errors": [],
        "dry_run": dry_run,
    }


def _log_fetch_error(domain: str, error: Exception, stats: dict[str, Any]) -> None:
    """Log a fetch error and append it to stats."""
    if isinstance(error, AdagentsNotFoundError):
        msg = f"{domain}: adagents.json not found (404)"
        stats["errors"].append(msg)
        logger.warning(f"\u26a0\ufe0f {msg}")
    elif isinstance(error, AdagentsTimeoutError):
        msg = f"{domain}: Request timeout"
        stats["errors"].append(msg)
        logger.warning(f"\u26a0\ufe0f {msg}")
    elif isinstance(error, AdagentsValidationError):
        msg = f"{domain}: Invalid adagents.json - {error!s}"
        stats["errors"].append(msg)
        logger.error(f"\u274c {msg}")
    else:
        msg = f"{domain}: {error!s}"
        stats["errors"].append(msg)
        logger.error(f"\u274c Error syncing {domain}: {error}", exc_info=True)


def _extract_properties(
    adagents_data: dict[str, Any],
    domain: str,
    agent_url: str | None,
) -> list[dict[str, Any]]:
    """Extract properties from adagents.json data.

    Checks for unrestricted agent access and uses the appropriate adcp extraction
    method based on whether an agent_url is provided.
    """
    all_properties_from_file = adagents_data.get("properties", [])

    # Check if the relevant agent has no property restrictions
    authorized_agents = adagents_data.get("authorized_agents", [])
    has_unrestricted_agent = False
    for agent in authorized_agents:
        if not isinstance(agent, dict):
            continue
        if agent_url and normalize_url(agent.get("url", "")) != normalize_url(agent_url):
            continue
        has_property_ids = bool(agent.get("property_ids"))
        has_property_tags = bool(agent.get("property_tags"))
        has_properties = bool(agent.get("properties"))
        has_publisher_properties = bool(agent.get("publisher_properties"))

        if not (has_property_ids or has_property_tags or has_properties or has_publisher_properties):
            has_unrestricted_agent = True
            logger.info(f"Found unrestricted agent {agent.get('url')} - authorized for ALL properties from {domain}")
            break

    if agent_url:
        properties_from_agents = get_properties_by_agent(adagents_data, agent_url)
        properties_from_agents = [p for p in properties_from_agents if p.get("property_type")]
    else:
        properties_from_agents = get_all_properties(adagents_data)

    if has_unrestricted_agent and all_properties_from_file:
        logger.info(
            f"Syncing all {len(all_properties_from_file)} top-level properties (unrestricted agent has access to all)"
        )
        return all_properties_from_file
    elif properties_from_agents:
        return properties_from_agents
    else:
        return []


def _filter_properties_by_domain(
    properties: list[dict[str, Any]],
    domain: str,
) -> list[dict[str, Any]]:
    """Filter properties to only those belonging to the given publisher domain.

    An adagents.json may list properties for many domains. Properties without
    a domain identifier (e.g., mobile apps) are kept. Domain comparison is
    normalized (www/m/mobile subdomain prefixes are stripped).
    """
    if not properties:
        return properties

    filtered = []
    for prop in properties:
        identifiers = prop.get("identifiers", [])
        domain_identifiers = [ident.get("value", "") for ident in identifiers if ident.get("type") == "domain"]
        if not domain_identifiers:
            filtered.append(prop)
        elif _domains_match(domain, domain_identifiers):
            filtered.append(prop)
        else:
            logger.debug(
                f"Skipping property {prop.get('name', 'unknown')} - "
                f"domain {domain_identifiers} doesn't match publisher {domain}"
            )
    if len(filtered) != len(properties):
        logger.info(f"Filtered {len(properties)} properties to {len(filtered)} matching publisher domain {domain}")
    return filtered


def _finalize_session(session: Any, dry_run: bool, stats: dict[str, Any]) -> None:
    """Commit or rollback the session based on dry_run flag."""
    if dry_run:
        session.rollback()
        logger.info("\U0001f50d DRY RUN - No changes committed to database")
    else:
        session.commit()
        logger.info(
            f"\u2705 Sync complete: {stats['domains_synced']} domains, "
            f"{stats['properties_created']} properties created, "
            f"{stats['properties_updated']} updated, "
            f"{stats['tags_created']} tags created"
        )


class PropertyDiscoveryService:
    """Service for discovering properties from publisher adagents.json files.

    This service:
    - Fetches adagents.json from publisher domains
    - Extracts properties and tags using adcp library
    - Caches them in database for inventory profiles and products
    - Auto-verifies properties (since they come from adagents.json)
    """

    async def sync_properties_from_adagents(
        self,
        tenant_id: str,
        publisher_domains: list[str] | None = None,
        dry_run: bool = False,
        agent_url: str | None = None,
    ) -> dict[str, Any]:
        """Fetch properties and tags from publisher adagents.json files.

        Args:
            tenant_id: Tenant ID
            publisher_domains: List of domains to sync. If None, syncs all unique domains
                              from existing AuthorizedProperty records.
            dry_run: If True, fetch and process but don't commit to database
            agent_url: Our agent's URL. When provided, uses get_properties_by_agent()
                      which handles all authorization types (property_ids, property_tags,
                      inline properties). Without this, only inline properties are found.

        Returns:
            Dict with sync stats
        """
        stats = _make_stats(dry_run)

        with get_db_session() as session:
            if not publisher_domains:
                stmt = (
                    select(AuthorizedProperty.publisher_domain)
                    .where(AuthorizedProperty.tenant_id == tenant_id)
                    .distinct()
                )
                result = session.execute(stmt).all()
                publisher_domains_list: list[str] = [row[0] for row in result if row[0]]
                publisher_domains = publisher_domains_list

            if not publisher_domains:
                logger.warning(f"No publisher domains found for tenant {tenant_id}")
                stats["errors"].append("No publisher domains found to sync")
                return stats

            logger.info(f"Syncing properties from {len(publisher_domains)} publisher domains")

            async def fetch_domain_data(domain: str, delay: float) -> tuple[str, dict | Exception]:
                """Fetch adagents.json from a domain with rate limiting delay."""
                try:
                    await asyncio.sleep(delay)
                    logger.info(f"Fetching adagents.json from {domain}")
                    adagents_data = await fetch_adagents(domain)
                    return (domain, adagents_data)
                except Exception as e:
                    return (domain, e)

            fetch_tasks = [fetch_domain_data(domain, i * 0.5) for i, domain in enumerate(publisher_domains)]
            fetch_results_raw = await asyncio.gather(*fetch_tasks, return_exceptions=False)
            fetch_results_list = cast(list[tuple[str, dict[str, Any] | Exception]], list(fetch_results_raw))

            for domain, result in fetch_results_list:  # type: ignore[assignment]
                try:
                    if isinstance(result, Exception):
                        _log_fetch_error(domain, result, stats)
                        continue

                    adagents_data: dict[str, Any] = result  # type: ignore[assignment]

                    properties = _extract_properties(adagents_data, domain, agent_url)
                    properties = _filter_properties_by_domain(properties, domain)

                    stats["properties_found"] += len(properties)
                    logger.info(f"Found {len(properties)} properties from {domain}")

                    tags = get_all_tags(adagents_data)
                    stats["tags_found"] += len(tags)
                    logger.info(f"Found {len(tags)} unique tags from {domain}")

                    self._batch_sync_properties(session, tenant_id, domain, properties, stats)
                    self._batch_sync_tags(session, tenant_id, tags, stats)

                    stats["domains_synced"] += 1
                    logger.info(f"\u2705 Synced {len(properties)} properties and {len(tags)} tags from {domain}")

                except Exception as e:
                    error = f"{domain}: {e!s}"
                    stats["errors"].append(error)
                    logger.error(f"\u274c Error processing {domain}: {e}", exc_info=True)

            _finalize_session(session, dry_run, stats)

        return stats

    def _batch_sync_properties(
        self,
        session: Any,
        tenant_id: str,
        domain: str,
        properties: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        """Batch-check and create/update property records."""
        property_ids_to_check = []
        properties_data = []
        for prop in properties:
            property_id = self._generate_property_id(tenant_id, domain, prop)
            if property_id:
                property_ids_to_check.append(property_id)
                properties_data.append((property_id, prop))

        stmt_props: Select[tuple[AuthorizedProperty]] = select(AuthorizedProperty).where(
            AuthorizedProperty.tenant_id == tenant_id,
            AuthorizedProperty.property_id.in_(property_ids_to_check),
        )
        existing_properties_objs = list(session.scalars(stmt_props).all())
        existing_properties: dict[str, AuthorizedProperty] = {p.property_id: p for p in existing_properties_objs}

        for property_id, prop in properties_data:
            was_created = self._create_or_update_property(
                session, tenant_id, domain, prop, property_id, existing_properties
            )
            if was_created:
                stats["properties_created"] += 1
            else:
                stats["properties_updated"] += 1

    def _batch_sync_tags(
        self,
        session: Any,
        tenant_id: str,
        tags: set[str] | list[str],
        stats: dict[str, Any],
    ) -> None:
        """Batch-check and create tag records."""
        stmt_tags: Select[tuple[PropertyTag]] = select(PropertyTag).where(
            PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(tags)
        )
        existing_tags_objs = list(session.scalars(stmt_tags).all())
        existing_tags: dict[str, PropertyTag] = {t.tag_id: t for t in existing_tags_objs}

        for tag in tags:
            was_created = self._create_or_update_tag(session, tenant_id, tag, existing_tags)
            if was_created:
                stats["tags_created"] += 1

    def _generate_property_id(self, tenant_id: str, publisher_domain: str, prop_data: dict[str, Any]) -> str | None:
        """Generate property_id from property data.

        Returns None if property is invalid (missing required fields).
        """
        property_type = prop_data.get("property_type")
        if not property_type:
            logger.warning(f"Property missing property_type: {prop_data}")
            return None

        identifiers = prop_data.get("identifiers", [])
        if not identifiers:
            logger.warning(f"Property missing identifiers: {prop_data}")
            return None

        first_ident_value = identifiers[0].get("value", "unknown")
        identifier_str = "|".join(f"{ident.get('type', '')}={ident.get('value', '')}" for ident in identifiers)
        full_key = f"{property_type}:{publisher_domain}:{identifier_str}"
        hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]

        safe_value = re.sub(r"[^a-z0-9]+", "_", first_ident_value.lower())[:30]
        return f"{property_type}_{safe_value}_{hash_suffix}".lower()

    def _create_or_update_property(
        self,
        session: Any,
        tenant_id: str,
        publisher_domain: str,
        prop_data: dict[str, Any],
        property_id: str,
        existing_properties: dict[str, AuthorizedProperty],
    ) -> bool:
        """Create or update a property record using pre-fetched existing properties.

        Returns:
            True if created, False if updated
        """
        property_type = prop_data.get("property_type")
        identifiers = prop_data.get("identifiers", [])
        property_name = prop_data.get("name", property_id.replace("_", " ").title())
        property_tags = prop_data.get("tags", [])

        existing = existing_properties.get(property_id)

        if existing:
            existing.name = property_name
            existing.identifiers = identifiers
            existing.tags = property_tags
            existing.updated_at = datetime.now(UTC)
            logger.debug(f"Updated property: {property_id}")
            return False
        else:
            new_property = AuthorizedProperty(
                tenant_id=tenant_id,
                property_id=property_id,
                name=property_name,
                property_type=property_type,
                publisher_domain=publisher_domain,
                identifiers=identifiers,
                tags=property_tags,
                verification_status="verified",
                verification_checked_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(new_property)
            logger.debug(f"Created property: {property_id}")
            return True

    def _create_or_update_tag(
        self, session: Any, tenant_id: str, tag_id: str, existing_tags: dict[str, PropertyTag]
    ) -> bool:
        """Create or update a property tag using pre-fetched existing tags.

        Returns:
            True if created, False if already exists
        """
        existing = existing_tags.get(tag_id)

        if existing:
            return False

        tag_name = tag_id.replace("_", " ").replace("-", " ").title()

        new_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id=tag_id,
            name=tag_name,
            description="Tag discovered from publisher adagents.json",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(new_tag)
        logger.debug(f"Created tag: {tag_id}")
        return True

    def sync_properties_from_adagents_sync(
        self,
        tenant_id: str,
        publisher_domains: list[str] | None = None,
        dry_run: bool = False,
        agent_url: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous wrapper for async sync_properties_from_adagents."""
        return asyncio.run(
            self.sync_properties_from_adagents(tenant_id, publisher_domains, dry_run, agent_url=agent_url)
        )


def get_property_discovery_service() -> PropertyDiscoveryService:
    """Get property discovery service instance."""
    return PropertyDiscoveryService()
