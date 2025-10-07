"""Service for verifying authorized properties via adagents.json files."""

import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests
from requests.exceptions import RequestException
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty

logger = logging.getLogger(__name__)


class PropertyVerificationService:
    """Service for verifying authorized properties against adagents.json files."""

    # Timeout for HTTP requests (in seconds)
    REQUEST_TIMEOUT = 10

    # User agent for requests
    USER_AGENT = "AdCP-Sales-Agent/1.0 (Property Verification)"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})

    def verify_property(self, tenant_id: str, property_id: str, agent_url: str) -> tuple[bool, str | None]:
        """Verify a single property against its publisher domain's adagents.json.

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

                # Fetch adagents.json from publisher domain
                adagents_url = f"https://{property_obj.publisher_domain}/.well-known/adagents.json"
                logger.info(f"🌐 Fetching adagents.json from: {adagents_url}")

                try:
                    response = self.session.get(adagents_url, timeout=self.REQUEST_TIMEOUT)
                    response.raise_for_status()
                    adagents_data = response.json()
                    logger.info(f"✅ Successfully fetched adagents.json (status: {response.status_code})")
                except RequestException as e:
                    error_msg = f"Failed to fetch adagents.json: {str(e)}"
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg
                except json.JSONDecodeError as e:
                    error_msg = f"Invalid JSON in adagents.json: {str(e)}"
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                # Validate adagents.json structure
                logger.info("📋 Validating adagents.json structure...")
                if not isinstance(adagents_data, dict) or "authorized_agents" not in adagents_data:
                    error_msg = "Invalid adagents.json format: missing 'authorized_agents' field"
                    logger.error(f"❌ {error_msg}")
                    logger.info(
                        f"📊 adagents.json keys: {list(adagents_data.keys()) if isinstance(adagents_data, dict) else 'not a dict'}"
                    )
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                agents = adagents_data.get("authorized_agents", [])
                if not isinstance(agents, list):
                    error_msg = "Invalid adagents.json format: 'authorized_agents' must be an array"
                    logger.error(f"❌ {error_msg}")
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

                logger.info(f"👥 Found {len(agents)} authorized agents in adagents.json")
                for i, agent in enumerate(agents):
                    agent_url_in_file = agent.get("url", "NO_URL")
                    logger.info(f"  Agent {i}: {agent_url_in_file}")

                # Check if our agent is authorized
                logger.info(f"🔍 Checking if agent {agent_url} is authorized...")
                is_authorized = self._check_agent_authorization(agents, agent_url, property_obj)

                if is_authorized:
                    logger.info("✅ Agent verification successful!")
                    self._update_verification_status(session, property_obj, "verified", None)
                    return True, None
                else:
                    error_msg = f"Agent {agent_url} not found in authorized agents list"
                    logger.error(f"❌ {error_msg}")
                    self._update_verification_status(session, property_obj, "failed", error_msg)
                    return False, error_msg

        except Exception as e:
            logger.error(f"Error verifying property {property_id}: {e}")
            return False, f"Verification error: {str(e)}"

    def _check_agent_authorization(
        self, agents: list[dict[str, Any]], agent_url: str, property_obj: AuthorizedProperty
    ) -> bool:
        """Check if the agent is authorized for this property.

        Args:
            agents: List of agents from adagents.json
            agent_url: URL of this sales agent
            property_obj: The property being verified

        Returns:
            True if agent is authorized, False otherwise
        """
        for agent in agents:
            if not isinstance(agent, dict):
                continue

            # Check agent URL match
            agent_agent_url = agent.get("url", "")
            if not self._urls_match(agent_agent_url, agent_url):
                continue

            # Check if agent covers this property
            agent_properties = agent.get("properties", [])
            if self._property_matches(property_obj, agent_properties):
                return True

        return False

    def _property_matches(self, property_obj: AuthorizedProperty, agent_properties: list[dict[str, Any]]) -> bool:
        """Check if a property matches any of the agent's authorized properties.

        Args:
            property_obj: The property to check
            agent_properties: List of properties from adagents.json

        Returns:
            True if property matches, False otherwise
        """
        # If no properties specified, agent is authorized for all properties on this domain
        if not agent_properties:
            return True

        for agent_prop in agent_properties:
            if not isinstance(agent_prop, dict):
                continue

            # Check property type match
            if agent_prop.get("property_type") != property_obj.property_type:
                continue

            # Check identifier matches
            agent_identifiers = agent_prop.get("identifiers", [])
            property_identifiers = property_obj.identifiers or []

            if self._identifiers_match(property_identifiers, agent_identifiers):
                return True

        return False

    def _identifiers_match(
        self, property_identifiers: list[dict[str, str]], agent_identifiers: list[dict[str, str]]
    ) -> bool:
        """Check if property identifiers match agent's authorized identifiers.

        Args:
            property_identifiers: Identifiers from the property
            agent_identifiers: Identifiers from adagents.json

        Returns:
            True if any identifier matches, False otherwise
        """
        for prop_ident in property_identifiers:
            prop_type = prop_ident.get("type")
            prop_value = prop_ident.get("value")

            for agent_ident in agent_identifiers:
                agent_type = agent_ident.get("type")
                agent_value = agent_ident.get("value")

                if prop_type == agent_type and self._identifier_values_match(prop_value, agent_value, prop_type):
                    return True

        return False

    def _identifier_values_match(self, property_value: str, agent_value: str, identifier_type: str) -> bool:
        """Check if identifier values match according to AdCP rules.

        Args:
            property_value: Value from property
            agent_value: Value from adagents.json
            identifier_type: Type of identifier (domain, bundle_id, etc.)

        Returns:
            True if values match according to AdCP rules
        """
        if identifier_type == "domain":
            return self._domain_matches(property_value, agent_value)
        else:
            # For non-domain identifiers, require exact match
            return property_value == agent_value

    def _domain_matches(self, property_domain: str, agent_domain: str) -> bool:
        """Check if domains match according to AdCP domain matching rules.

        Rules:
        - 'example.com' matches www.example.com and m.example.com only
        - 'subdomain.example.com' matches that specific subdomain
        - '*.example.com' matches all subdomains

        Args:
            property_domain: Domain from property
            agent_domain: Domain pattern from adagents.json

        Returns:
            True if domains match
        """
        # Normalize domains (remove protocol, convert to lowercase)
        property_domain = self._normalize_domain(property_domain)
        agent_domain = self._normalize_domain(agent_domain)

        # Handle wildcard patterns
        if agent_domain.startswith("*."):
            # Wildcard pattern: *.example.com matches any.example.com
            base_domain = agent_domain[2:]  # Remove *.
            return property_domain.endswith("." + base_domain) or property_domain == base_domain

        # Handle base domain patterns
        if "." not in agent_domain or agent_domain.count(".") == 1:
            # Base domain: example.com matches www.example.com, m.example.com, etc.
            if property_domain == agent_domain:
                return True
            # Check if property is a common subdomain of agent domain
            common_subdomains = ["www", "m", "mobile", "amp"]
            for subdomain in common_subdomains:
                if property_domain == f"{subdomain}.{agent_domain}":
                    return True
            return False

        # Exact subdomain match
        return property_domain == agent_domain

    def _normalize_domain(self, domain: str) -> str:
        """Normalize a domain string by removing protocol and converting to lowercase.

        Args:
            domain: Domain string to normalize

        Returns:
            Normalized domain string
        """
        # Remove protocol if present
        if "://" in domain:
            domain = urlparse(domain).netloc

        # Convert to lowercase and strip whitespace
        return domain.lower().strip()

    def _urls_match(self, url1: str, url2: str) -> bool:
        """Check if two URLs match (ignoring protocol differences).

        Args:
            url1: First URL
            url2: Second URL

        Returns:
            True if URLs match
        """
        try:
            parsed1 = urlparse(url1.lower())
            parsed2 = urlparse(url2.lower())

            # Compare netloc and path (ignore protocol)
            return parsed1.netloc == parsed2.netloc and parsed1.path.rstrip("/") == parsed2.path.rstrip("/")
        except Exception:
            return url1.lower().strip() == url2.lower().strip()

    def _update_verification_status(self, session, property_obj: AuthorizedProperty, status: str, error: str | None):
        """Update the verification status of a property in the database.

        Args:
            session: Database session
            property_obj: Property object to update
            status: New verification status
            error: Error message (if any)
        """
        property_obj.verification_status = status
        property_obj.verification_checked_at = datetime.utcnow()
        property_obj.verification_error = error
        property_obj.updated_at = datetime.utcnow()
        session.commit()

    def verify_all_properties(self, tenant_id: str, agent_url: str) -> dict[str, Any]:
        """Verify all pending properties for a tenant.

        Args:
            tenant_id: Tenant ID
            agent_url: URL of this sales agent

        Returns:
            Dictionary with verification results
        """
        results = {"total_checked": 0, "verified": 0, "failed": 0, "errors": []}

        try:
            with get_db_session() as session:
                # Get all pending properties
                stmt = select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == tenant_id, AuthorizedProperty.verification_status == "pending"
                )
                pending_properties = session.scalars(stmt).all()

                results["total_checked"] = len(pending_properties)

                for property_obj in pending_properties:
                    try:
                        is_verified, error = self.verify_property(tenant_id, property_obj.property_id, agent_url)

                        if is_verified:
                            results["verified"] += 1
                        else:
                            results["failed"] += 1
                            if error:
                                results["errors"].append(f"{property_obj.name}: {error}")

                    except Exception as e:
                        results["failed"] += 1
                        results["errors"].append(f"{property_obj.name}: {str(e)}")
                        logger.error(f"Error verifying property {property_obj.property_id}: {e}")

        except Exception as e:
            logger.error(f"Error in bulk verification: {e}")
            results["errors"].append(f"Bulk verification error: {str(e)}")

        return results


def get_property_verification_service() -> PropertyVerificationService:
    """Get a property verification service instance."""
    return PropertyVerificationService()
