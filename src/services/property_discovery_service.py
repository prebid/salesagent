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
            Dict with sync stats: {
                "domains_synced": int,
                "properties_found": int,
                "tags_found": int,
                "properties_created": int,
                "properties_updated": int,
                "tags_created": int,
                "errors": list[str],
                "dry_run": bool
            }
        """
        stats = self._make_stats(dry_run=dry_run)

        with get_db_session() as session:
            # Get publisher domains to sync
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

            # Fetch all domains in parallel with rate limiting
            async def fetch_domain_data(domain: str, delay: float) -> tuple[str, dict | Exception]:
                """Fetch adagents.json from a domain with rate limiting delay."""
                try:
                    await asyncio.sleep(delay)  # Stagger requests
                    logger.info(f"Fetching adagents.json from {domain}")
                    adagents_data = await fetch_adagents(domain)
                    return (domain, adagents_data)
                except Exception as e:
                    return (domain, e)

            # Create fetch tasks with staggered delays (500ms apart)
            fetch_tasks = [fetch_domain_data(domain, i * 0.5) for i, domain in enumerate(publisher_domains)]

            # Fetch all domains in parallel
            fetch_results_raw = await asyncio.gather(*fetch_tasks, return_exceptions=False)
            # mypy doesn't understand that gather returns the right type here
            fetch_results_list = cast(list[tuple[str, dict[str, Any] | Exception]], list(fetch_results_raw))

            # Process results
            for domain, result in fetch_results_list:  # type: ignore[assignment]
                try:
                    # Check if fetch succeeded
                    if isinstance(result, Exception):
                        self._log_fetch_error(domain, result, stats)
                        continue

                    # At this point, result is guaranteed to be dict[str, Any], not Exception
                    adagents_data: dict[str, Any] = result  # type: ignore[assignment]

                    properties = self._extract_properties(adagents_data, domain, agent_url)
                    properties = self._filter_properties_by_domain(properties, domain)

                    stats["properties_found"] += len(properties)
                    logger.info(f"Found {len(properties)} properties from {domain}")

                    tags = get_all_tags(adagents_data)
                    stats["tags_found"] += len(tags)
                    logger.info(f"Found {len(tags)} unique tags from {domain}")

                    self._batch_sync_properties(session, tenant_id, domain, properties, stats)
                    self._batch_sync_tags(session, tenant_id, set(tags), stats)

                    stats["domains_synced"] += 1
                    logger.info(f"✅ Synced {len(properties)} properties and {len(tags)} tags from {domain}")

                except Exception as e:
                    error = f"{domain}: {str(e)}"
                    stats["errors"].append(error)
                    logger.error(f"❌ Error processing {domain}: {e}", exc_info=True)

            self._finalize_session(session, dry_run, stats)

        return stats

    async def sync_properties_from_registry(
        self,
        tenant_id: str,
        publisher_domains: list[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Resolve properties via the AAO registry API.

        The registry handles all authorization models (property_ids, property_tags,
        inline properties, publisher_properties) and follows authoritative_location
        delegation. This is more reliable than parsing adagents.json directly.

        Falls back to direct adagents.json fetch if the registry is unreachable.

        Args:
            tenant_id: Tenant ID
            publisher_domains: List of domains to resolve
            dry_run: If True, don't commit to database

        Returns:
            Sync stats dictionary
        """
        from src.services.registry_client import get_registry_client

        stats = self._make_stats(dry_run=dry_run, source="registry")

        if not publisher_domains:
            stats["errors"].append("No publisher domains to resolve")
            return stats

        # Bulk-resolve all domains via registry
        registry = get_registry_client()
        try:
            results = await registry.resolve_properties_bulk(publisher_domains)
        except Exception as e:
            logger.warning(f"Registry bulk resolve failed, falling back to direct fetch: {e}")
            stats["source"] = "direct_fallback"
            return await self.sync_properties_from_adagents(tenant_id, publisher_domains, dry_run)

        if not results:
            logger.warning("Registry returned empty results, falling back to direct fetch")
            stats["source"] = "direct_fallback"
            return await self.sync_properties_from_adagents(tenant_id, publisher_domains, dry_run)

        with get_db_session() as session:
            for domain in publisher_domains:
                resolved = results.get(domain)
                if not resolved:
                    stats["errors"].append(f"{domain}: not found in registry")
                    logger.warning(f"⚠️ {domain}: not found in registry")
                    continue

                try:
                    properties = resolved.get("properties", [])
                    if not properties:
                        stats["errors"].append(f"{domain}: no properties in registry response")
                        logger.warning(f"⚠️ {domain}: no properties in registry response")
                        continue

                    normalized_properties = self._normalize_registry_properties(properties)

                    stats["properties_found"] += len(normalized_properties)
                    logger.info(f"Registry resolved {len(normalized_properties)} properties from {domain}")

                    self._batch_sync_properties(session, tenant_id, domain, normalized_properties, stats)

                    # Extract tags from resolved properties
                    all_tags: set[str] = set()
                    for prop in normalized_properties:
                        for tag in prop.get("tags", []):
                            if isinstance(tag, str):
                                all_tags.add(tag)

                    stats["tags_found"] += len(all_tags)
                    self._batch_sync_tags(session, tenant_id, all_tags, stats)

                    stats["domains_synced"] += 1
                    logger.info(f"✅ Registry sync: {len(normalized_properties)} properties from {domain}")

                except Exception as e:
                    error = f"{domain}: {str(e)}"
                    stats["errors"].append(error)
                    logger.error(
                        f"❌ Error processing registry result for {domain}: {e}",
                        exc_info=True,
                    )

            self._finalize_session(session, dry_run, stats)

        return stats

    def sync_properties_from_registry_sync(
        self,
        tenant_id: str,
        publisher_domains: list[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Synchronous wrapper for sync_properties_from_registry."""
        return asyncio.run(self.sync_properties_from_registry(tenant_id, publisher_domains, dry_run))

    def sync_properties_from_adagents_sync(
        self,
        tenant_id: str,
        publisher_domains: list[str] | None = None,
        dry_run: bool = False,
        agent_url: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous wrapper for async sync_properties_from_adagents.

        Args:
            tenant_id: Tenant ID
            publisher_domains: List of domains to sync (optional)
            dry_run: If True, fetch and process but don't commit
            agent_url: Our agent's URL for property resolution (optional)

        Returns:
            Sync stats dictionary
        """
        return asyncio.run(
            self.sync_properties_from_adagents(tenant_id, publisher_domains, dry_run, agent_url=agent_url)
        )

    # ── Shared helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_stats(dry_run: bool = False, **extra: Any) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "domains_synced": 0,
            "properties_found": 0,
            "tags_found": 0,
            "properties_created": 0,
            "properties_updated": 0,
            "tags_created": 0,
            "errors": [],
            "dry_run": dry_run,
        }
        stats.update(extra)
        return stats

    @staticmethod
    def _log_fetch_error(domain: str, error: Exception, stats: dict[str, Any]) -> None:
        if isinstance(error, AdagentsNotFoundError):
            msg = f"{domain}: adagents.json not found (404)"
            stats["errors"].append(msg)
            logger.warning(f"⚠️ {msg}")
        elif isinstance(error, AdagentsTimeoutError):
            msg = f"{domain}: Request timeout"
            stats["errors"].append(msg)
            logger.warning(f"⚠️ {msg}")
        elif isinstance(error, AdagentsValidationError):
            msg = f"{domain}: Invalid adagents.json - {str(error)}"
            stats["errors"].append(msg)
            logger.error(f"❌ {msg}")
        else:
            msg = f"{domain}: {str(error)}"
            stats["errors"].append(msg)
            logger.error(f"❌ Error syncing {domain}: {error}", exc_info=True)

    @staticmethod
    def _extract_properties(adagents_data: dict[str, Any], domain: str, agent_url: str | None) -> list[dict[str, Any]]:
        """Extract properties from adagents.json data."""
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
                logger.info(
                    f"Found unrestricted agent {agent.get('url')} - authorized for ALL properties from {domain}"
                )
                break

        if agent_url:
            properties_from_agents = get_properties_by_agent(adagents_data, agent_url)
            properties_from_agents = [p for p in properties_from_agents if p.get("property_type")]
        else:
            properties_from_agents = get_all_properties(adagents_data)

        if has_unrestricted_agent and all_properties_from_file:
            logger.info(
                f"Syncing all {len(all_properties_from_file)} top-level properties "
                f"(unrestricted agent has access to all)"
            )
            return all_properties_from_file
        elif properties_from_agents:
            return properties_from_agents
        return []

    @staticmethod
    def _filter_properties_by_domain(properties: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
        """Filter properties to only those belonging to a specific publisher domain."""
        if not properties:
            return properties

        filtered = []
        for prop in properties:
            identifiers = prop.get("identifiers", [])
            domain_identifiers = [ident.get("value", "") for ident in identifiers if ident.get("type") == "domain"]
            if not domain_identifiers:
                filtered.append(prop)
            elif domain in domain_identifiers:
                filtered.append(prop)
            else:
                logger.debug(
                    f"Skipping property {prop.get('name', 'unknown')} - "
                    f"domain {domain_identifiers} doesn't match publisher {domain}"
                )
        if len(filtered) != len(properties):
            logger.info(f"Filtered {len(properties)} properties to {len(filtered)} matching publisher domain {domain}")
        return filtered

    @staticmethod
    def _normalize_registry_properties(
        properties: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert registry format to adagents.json format for reuse.

        Registry returns: {id, type, name, identifiers, tags}
        Discovery expects: {property_type, name, identifiers, tags}
        """
        normalized = []
        for prop in properties:
            entry: dict[str, Any] = {
                "property_type": prop.get("type", "website"),
                "name": prop.get("name", prop.get("id", "Unknown")),
                "identifiers": prop.get("identifiers", []),
                "tags": prop.get("tags", []),
            }
            if prop.get("id"):
                entry["property_id"] = prop["id"]
            normalized.append(entry)
        return normalized

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
        existing_objs = list(session.scalars(stmt_props).all())
        existing: dict[str, AuthorizedProperty] = {p.property_id: p for p in existing_objs}

        for property_id, prop in properties_data:
            was_created = self._create_or_update_property(session, tenant_id, domain, prop, property_id, existing)
            if was_created:
                stats["properties_created"] += 1
            else:
                stats["properties_updated"] += 1

    def _batch_sync_tags(
        self,
        session: Any,
        tenant_id: str,
        tags: set[str],
        stats: dict[str, Any],
    ) -> None:
        """Batch-check and create tag records."""
        if not tags:
            return

        stmt_tags: Select[tuple[PropertyTag]] = select(PropertyTag).where(
            PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(tags)
        )
        existing_objs = list(session.scalars(stmt_tags).all())
        existing: dict[str, PropertyTag] = {t.tag_id: t for t in existing_objs}

        for tag in tags:
            was_created = self._create_or_update_tag(session, tenant_id, tag, existing)
            if was_created:
                stats["tags_created"] += 1

    @staticmethod
    def _finalize_session(session: Any, dry_run: bool, stats: dict[str, Any]) -> None:
        if dry_run:
            session.rollback()
            logger.info("🔍 DRY RUN - No changes committed to database")
        else:
            session.commit()
            source = stats.get("source", "adagents")
            logger.info(
                f"✅ Sync complete (source={source}): {stats['domains_synced']} domains, "
                f"{stats['properties_created']} properties created, "
                f"{stats['properties_updated']} updated, "
                f"{stats['tags_created']} tags created"
            )

    @staticmethod
    def _generate_property_id(tenant_id: str, publisher_domain: str, prop_data: dict[str, Any]) -> str | None:
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

        # Create deterministic hash from all identifiers for uniqueness
        identifier_str = "|".join(f"{ident.get('type', '')}={ident.get('value', '')}" for ident in identifiers)
        full_key = f"{property_type}:{publisher_domain}:{identifier_str}"
        hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]

        # Use readable prefix + hash for both readability and uniqueness
        safe_value = re.sub(r"[^a-z0-9]+", "_", first_ident_value.lower())[:30]
        return f"{property_type}_{safe_value}_{hash_suffix}".lower()

    @staticmethod
    def _create_or_update_property(
        session: Any,
        tenant_id: str,
        publisher_domain: str,
        prop_data: dict[str, Any],
        property_id: str,
        existing_properties: dict[str, Any],
    ) -> bool:
        """Create or update a property record.

        Returns True if created, False if updated.
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

    @staticmethod
    def _create_or_update_tag(session: Any, tenant_id: str, tag_id: str, existing_tags: dict[str, Any]) -> bool:
        """Create a tag record if it doesn't exist.

        Returns True if created, False if already exists.
        """
        if existing_tags.get(tag_id):
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


def get_property_discovery_service() -> PropertyDiscoveryService:
    """Get property discovery service instance."""
    return PropertyDiscoveryService()
