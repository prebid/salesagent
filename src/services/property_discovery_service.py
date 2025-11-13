"""Service for discovering and caching properties from publisher adagents.json files.

This service fetches properties and tags from publishers' adagents.json files
and caches them in the database for use in inventory profiles and products.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from adcp import (
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
    fetch_adagents,
    get_all_properties,
    get_all_tags,
)
from sqlalchemy import select

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
        self, tenant_id: str, publisher_domains: list[str] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Fetch properties and tags from publisher adagents.json files.

        Args:
            tenant_id: Tenant ID
            publisher_domains: List of domains to sync. If None, syncs all unique domains
                              from existing AuthorizedProperty records.
            dry_run: If True, fetch and process but don't commit to database

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

        with get_db_session() as session:
            # Get publisher domains to sync
            if not publisher_domains:
                # Get unique domains from existing properties
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
            fetch_results_list = await asyncio.gather(  # type: ignore[var-annotated]
                *fetch_tasks, return_exceptions=False
            )

            # Process results
            for domain, result in fetch_results_list:  # type: ignore[assignment]
                try:
                    # Check if fetch succeeded
                    if isinstance(result, Exception):
                        if isinstance(result, AdagentsNotFoundError):
                            error = f"{domain}: adagents.json not found (404)"
                            stats["errors"].append(error)
                            logger.warning(f"⚠️ {error}")
                        elif isinstance(result, AdagentsTimeoutError):
                            error = f"{domain}: Request timeout"
                            stats["errors"].append(error)
                            logger.warning(f"⚠️ {error}")
                        elif isinstance(result, AdagentsValidationError):
                            error = f"{domain}: Invalid adagents.json - {str(result)}"
                            stats["errors"].append(error)
                            logger.error(f"❌ {error}")
                        else:
                            error = f"{domain}: {str(result)}"
                            stats["errors"].append(error)
                            logger.error(f"❌ Error syncing {domain}: {result}", exc_info=True)
                        continue

                    adagents_data = result

                    # Extract all properties
                    properties = get_all_properties(adagents_data)
                    stats["properties_found"] += len(properties)
                    logger.info(f"Found {len(properties)} properties from {domain}")

                    # Extract all tags
                    tags = get_all_tags(adagents_data)
                    stats["tags_found"] += len(tags)
                    logger.info(f"Found {len(tags)} unique tags from {domain}")

                    # Batch-check existing properties for this tenant (performance optimization)
                    property_ids_to_check = []
                    properties_data = []
                    for prop in properties:
                        property_id = self._generate_property_id(tenant_id, domain, prop)
                        if property_id:
                            property_ids_to_check.append(property_id)
                            properties_data.append((property_id, prop))

                    # Batch fetch existing properties
                    stmt = select(AuthorizedProperty).where(  # type: ignore[assignment]
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id.in_(property_ids_to_check),
                    )
                    existing_properties_objs = session.scalars(stmt).all()  # type: ignore[assignment]
                    existing_properties: dict[str, AuthorizedProperty] = {
                        p.property_id: p for p in existing_properties_objs  # type: ignore[attr-defined,misc]
                    }

                    # Create/update property records (using batched existence check)
                    for property_id, prop in properties_data:
                        was_created = self._create_or_update_property_batched(
                            session, tenant_id, domain, prop, property_id, existing_properties
                        )
                        if was_created:
                            stats["properties_created"] += 1
                        else:
                            stats["properties_updated"] += 1

                    # Batch-check existing tags
                    stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(tags))  # type: ignore[assignment]
                    existing_tags_objs = session.scalars(stmt).all()  # type: ignore[assignment]
                    existing_tags: dict[str, PropertyTag] = {t.tag_id: t for t in existing_tags_objs}  # type: ignore[attr-defined,misc]

                    # Create tag records (using batched existence check)
                    for tag in tags:
                        was_created = self._create_or_update_tag_batched(session, tenant_id, tag, existing_tags)
                        if was_created:
                            stats["tags_created"] += 1

                    stats["domains_synced"] += 1
                    logger.info(f"✅ Synced {len(properties)} properties and {len(tags)} tags from {domain}")

                except Exception as e:
                    error = f"{domain}: {str(e)}"
                    stats["errors"].append(error)
                    logger.error(f"❌ Error processing {domain}: {e}", exc_info=True)

            # Commit all changes (unless dry-run)
            if dry_run:
                session.rollback()
                logger.info("🔍 DRY RUN - No changes committed to database")
            else:
                session.commit()
                logger.info(
                    f"✅ Sync complete: {stats['domains_synced']} domains, "
                    f"{stats['properties_created']} properties created, "
                    f"{stats['properties_updated']} updated, "
                    f"{stats['tags_created']} tags created"
                )

        return stats

    def _generate_property_id(self, tenant_id: str, publisher_domain: str, prop_data: dict[str, Any]) -> str | None:
        """Generate property_id from property data.

        Returns None if property is invalid (missing required fields).
        """
        import hashlib
        import re

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

    def _create_or_update_property_batched(
        self,
        session,
        tenant_id: str,
        publisher_domain: str,
        prop_data: dict[str, Any],
        property_id: str,
        existing_properties: dict[str, Any],
    ) -> bool:
        """Create or update a property record (batched version).

        Args:
            session: Database session
            tenant_id: Tenant ID
            publisher_domain: Publisher domain
            prop_data: Property data from adagents.json
            property_id: Pre-generated property ID
            existing_properties: Dict of existing properties keyed by property_id

        Returns:
            True if created, False if updated
        """
        property_type = prop_data.get("property_type")
        identifiers = prop_data.get("identifiers", [])
        property_name = prop_data.get("name", property_id.replace("_", " ").title())
        property_tags = prop_data.get("tags", [])

        existing = existing_properties.get(property_id)

        if existing:
            # Update existing property
            existing.name = property_name
            existing.identifiers = identifiers
            existing.tags = property_tags
            existing.updated_at = datetime.now(UTC)  # type: ignore[assignment]
            logger.debug(f"Updated property: {property_id}")
            return False
        else:
            # Create new property
            new_property = AuthorizedProperty(
                tenant_id=tenant_id,
                property_id=property_id,
                name=property_name,
                property_type=property_type,
                publisher_domain=publisher_domain,
                identifiers=identifiers,
                tags=property_tags,
                verification_status="verified",  # From adagents.json = auto-verified
                verification_checked_at=datetime.now(UTC),  # type: ignore[assignment]
                created_at=datetime.now(UTC),  # type: ignore[assignment]
                updated_at=datetime.now(UTC),  # type: ignore[assignment]
            )
            session.add(new_property)
            logger.debug(f"Created property: {property_id}")
            return True

    def _create_or_update_tag_batched(
        self, session, tenant_id: str, tag_id: str, existing_tags: dict[str, Any]
    ) -> bool:
        """Create or update a property tag (batched version).

        Args:
            session: Database session
            tenant_id: Tenant ID
            tag_id: Tag identifier
            existing_tags: Dict of existing tags keyed by tag_id

        Returns:
            True if created, False if already exists
        """
        existing = existing_tags.get(tag_id)

        if existing:
            # Tag already exists, nothing to update
            return False

        # Create new tag
        tag_name = tag_id.replace("_", " ").replace("-", " ").title()

        new_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id=tag_id,
            name=tag_name,
            description="Tag discovered from publisher adagents.json",
            created_at=datetime.now(UTC),  # type: ignore[assignment]
            updated_at=datetime.now(UTC),  # type: ignore[assignment]
        )
        session.add(new_tag)
        logger.debug(f"Created tag: {tag_id}")
        return True

    def _create_or_update_property(
        self, session, tenant_id: str, publisher_domain: str, prop_data: dict[str, Any]
    ) -> bool:
        """Create or update a property record from adagents.json data.

        Args:
            session: Database session
            tenant_id: Tenant ID
            publisher_domain: Publisher domain this property belongs to
            prop_data: Property data from adagents.json (with agent_url added by adcp library)

        Returns:
            True if created, False if updated
        """
        # Extract property info
        property_type = prop_data.get("property_type")
        if not property_type:
            logger.warning(f"Property missing property_type: {prop_data}")
            return False

        identifiers = prop_data.get("identifiers", [])
        if not identifiers:
            logger.warning(f"Property missing identifiers: {prop_data}")
            return False

        # Generate property_id from property data
        # Include publisher_domain to prevent collisions across different publishers
        import hashlib
        import re

        first_ident_value = identifiers[0].get("value", "unknown")

        # Create deterministic hash from all identifiers for uniqueness
        identifier_str = "|".join(f"{ident.get('type', '')}={ident.get('value', '')}" for ident in identifiers)
        full_key = f"{property_type}:{publisher_domain}:{identifier_str}"
        hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]

        # Use readable prefix + hash for both readability and uniqueness
        safe_value = re.sub(r"[^a-z0-9]+", "_", first_ident_value.lower())[:30]
        property_id = f"{property_type}_{safe_value}_{hash_suffix}".lower()

        # Get property name (from adagents.json or generate from ID)
        property_name = prop_data.get("name", property_id.replace("_", " ").title())

        # Get tags
        property_tags = prop_data.get("tags", [])

        # Check if exists
        stmt = select(AuthorizedProperty).where(
            AuthorizedProperty.tenant_id == tenant_id, AuthorizedProperty.property_id == property_id
        )
        existing = session.scalars(stmt).first()

        if existing:
            # Update existing property
            existing.name = property_name
            existing.identifiers = identifiers
            existing.tags = property_tags
            existing.updated_at = datetime.now(UTC)  # type: ignore[assignment]
            logger.debug(f"Updated property: {property_id}")
            return False
        else:
            # Create new property
            new_property = AuthorizedProperty(
                tenant_id=tenant_id,
                property_id=property_id,
                name=property_name,
                property_type=property_type,
                publisher_domain=publisher_domain,
                identifiers=identifiers,
                tags=property_tags,
                verification_status="verified",  # From adagents.json = auto-verified
                verification_checked_at=datetime.now(UTC),  # type: ignore[assignment]
                created_at=datetime.now(UTC),  # type: ignore[assignment]
                updated_at=datetime.now(UTC),  # type: ignore[assignment]
            )
            session.add(new_property)
            logger.debug(f"Created property: {property_id}")
            return True

    def _create_or_update_tag(self, session, tenant_id: str, tag_id: str) -> bool:
        """Create or update a property tag.

        Args:
            session: Database session
            tenant_id: Tenant ID
            tag_id: Tag identifier

        Returns:
            True if created, False if already exists
        """
        # Check if exists
        stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == tag_id)
        existing = session.scalars(stmt).first()

        if existing:
            # Tag already exists, nothing to update
            return False

        # Create new tag
        # Generate human-readable name from tag_id
        tag_name = tag_id.replace("_", " ").replace("-", " ").title()

        new_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id=tag_id,
            name=tag_name,
            description="Tag discovered from publisher adagents.json",
            created_at=datetime.now(UTC),  # type: ignore[assignment]
            updated_at=datetime.now(UTC),  # type: ignore[assignment]
        )
        session.add(new_tag)
        logger.debug(f"Created tag: {tag_id}")
        return True

    def sync_properties_from_adagents_sync(
        self, tenant_id: str, publisher_domains: list[str] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Synchronous wrapper for async sync_properties_from_adagents.

        Args:
            tenant_id: Tenant ID
            publisher_domains: List of domains to sync (optional)
            dry_run: If True, fetch and process but don't commit

        Returns:
            Sync stats dictionary
        """
        return asyncio.run(self.sync_properties_from_adagents(tenant_id, publisher_domains, dry_run))


def get_property_discovery_service() -> PropertyDiscoveryService:
    """Get property discovery service instance."""
    return PropertyDiscoveryService()
