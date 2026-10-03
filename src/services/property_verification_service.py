"""Service for verifying authorized properties via adagents.json files.

This service wraps the adcp library's adagents functionality and adds
database status tracking for property verification.
"""

import asyncio
import logging
from datetime import UTC, datetime

from adcp import (
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
    fetch_adagents,
    verify_agent_authorization,
)
from sqlalchemy import select

from src.core.agent_identity import adagents_scoped_to_agent
from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty
from src.services.adagents_error_messages import describe_adagents_error

logger = logging.getLogger(__name__)


class PropertyVerificationService:
    """Service for verifying authorized properties against adagents.json files.

    This service wraps the adcp library's adagents functionality (added in v1.6.0)
    and adds database status tracking for property verification results.

    The actual adagents.json fetching, parsing, and validation logic is handled
    by the adcp library to ensure consistent validation across all AdCP implementations.
    """

    def verify_property(self, tenant_id: str, property_id: str, agent_url: str) -> tuple[bool, str | None]:
        """Verify a single property against its publisher domain's adagents.json.

        Args:
            tenant_id: Tenant ID
            property_id: Property ID to verify
            agent_url: URL of this sales agent for verification

        Returns:
            Tuple of (is_verified, error_message)
        """
        # Run async verification in sync context
        return asyncio.run(self._verify_property_async(tenant_id, property_id, agent_url))

    async def _verify_property_async(self, tenant_id: str, property_id: str, agent_url: str) -> tuple[bool, str | None]:
        """Async implementation of property verification.

        Args:
            tenant_id: Tenant ID
            property_id: Property ID to verify
            agent_url: URL of this sales agent for verification

        Returns:
            Tuple of (is_verified, error_message)
        """
        try:
            logger.info(f"🔍 Starting verification - tenant: {tenant_id}, property: {property_id}, agent: {agent_url}")

            with get_db_session() as session:
                stmt = select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == tenant_id,
                    AuthorizedProperty.property_id == property_id,
                )
                property_obj = session.scalars(stmt).first()

                if not property_obj:
                    logger.error(f"❌ Property not found: {property_id} in tenant {tenant_id}")
                    return False, "Property not found"

                logger.info(f"✅ Found property: {property_obj.name} on domain {property_obj.publisher_domain}")

                # Use adcp library to fetch and validate adagents.json
                try:
                    logger.info(f"🌐 Fetching adagents.json from: {property_obj.publisher_domain}")
                    # Sanctioned self-pinning dialer, deliberately outside the
                    # egress seam -- see src/core/security/outbound_http.py's
                    # module docstring.
                    adagents_data = await fetch_adagents(property_obj.publisher_domain)
                    logger.info("✅ Successfully fetched and validated adagents.json")

                except AdagentsNotFoundError as e:
                    logger.error(f"❌ adagents.json not found (404): {e}")
                    error_msg = describe_adagents_error(e)
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                except AdagentsTimeoutError as e:
                    logger.error(f"❌ Timeout fetching adagents.json: {e}")
                    error_msg = describe_adagents_error(e)
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                except AdagentsValidationError as e:
                    logger.error(f"❌ Invalid adagents.json: {e}")
                    error_msg = describe_adagents_error(e)
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                # Use adcp library to verify authorization
                logger.info(f"🔍 Checking if agent {agent_url} is authorized...")

                # Convert property identifiers to format expected by adcp library
                property_identifiers = property_obj.identifiers or []

                is_authorized = verify_agent_authorization(
                    adagents_data=adagents_scoped_to_agent(adagents_data, agent_url),
                    agent_url=agent_url,
                    property_type=property_obj.property_type,
                    property_identifiers=property_identifiers,
                )

                if is_authorized:
                    logger.info("✅ Agent verification successful!")
                    self._update_verification_status(session, property_obj, "verified", None)
                    return True, None
                else:
                    error_msg = f"Agent {agent_url} not authorized for this property"
                    logger.error(f"❌ {error_msg}")
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

        except Exception as e:
            logger.error(f"Error verifying property {property_id}: {e}")
            return False, f"Verification error: {str(e)}"

    def _update_verification_status(
        self, session, property_obj: AuthorizedProperty, status: str, error: str | None
    ) -> None:
        """Update the verification status of a property in the database.

        Args:
            session: Database session
            property_obj: Property object to update
            status: New verification status
            error: Error message (if any)
        """
        property_obj.verification_status = status
        property_obj.verification_checked_at = datetime.now(UTC)
        property_obj.verification_error = error
        property_obj.updated_at = datetime.now(UTC)
        session.commit()

    def verify_all_properties(self, tenant_id: str, agent_url: str) -> dict[str, int | list[str]]:
        """Verify all pending properties for a tenant.

        Args:
            tenant_id: Tenant ID
            agent_url: URL of this sales agent

        Returns:
            Dictionary with verification results
        """
        # Use separate counters for type safety
        total_checked = 0
        verified = 0
        failed = 0
        errors: list[str] = []

        try:
            with get_db_session() as session:
                # Get all pending properties
                stmt = select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == tenant_id, AuthorizedProperty.verification_status == "pending"
                )
                pending_properties = session.scalars(stmt).all()

                total_checked = len(pending_properties)

                for property_obj in pending_properties:
                    try:
                        is_verified, error = self.verify_property(tenant_id, property_obj.property_id, agent_url)

                        if is_verified:
                            verified += 1
                        else:
                            failed += 1
                            if error:
                                errors.append(f"{property_obj.name}: {error}")

                    except Exception as e:
                        failed += 1
                        errors.append(f"{property_obj.name}: {str(e)}")
                        logger.error(f"Error verifying property {property_obj.property_id}: {e}")

        except Exception as e:
            logger.error(f"Error in bulk verification: {e}")
            errors.append(f"Bulk verification error: {str(e)}")

        results: dict[str, int | list[str]] = {
            "total_checked": total_checked,
            "verified": verified,
            "failed": failed,
            "errors": errors,
        }

        return results


def get_property_verification_service() -> PropertyVerificationService:
    """Get a property verification service instance."""
    return PropertyVerificationService()
