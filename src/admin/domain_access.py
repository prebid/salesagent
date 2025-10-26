"""
Domain-based tenant access control functions.

Simple email domain extraction approach - no complex OAuth hd claims needed.
"""

import json
import logging

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User

logger = logging.getLogger(__name__)


def extract_email_domain(email: str) -> str:
    """Extract domain from email address."""
    if not email or "@" not in email:
        return ""
    return email.split("@")[1].lower()


def find_tenant_by_authorized_domain(domain: str) -> Tenant | None:
    """
    Find tenant that has this domain in their authorized_domains list.

    Args:
        domain: Domain to look up (e.g., "scribd.com")

    Returns:
        Tenant object if found, None otherwise
    """
    if not domain:
        return None

    with get_db_session() as session:
        tenants = session.scalars(select(Tenant).where(Tenant.is_active)).all()

        for tenant in tenants:
            if tenant.authorized_domains:
                try:
                    # Parse JSON field
                    if isinstance(tenant.authorized_domains, str):
                        domains = json.loads(tenant.authorized_domains)
                    else:
                        domains = tenant.authorized_domains

                    if domain in domains:
                        logger.info(f"Found tenant {tenant.tenant_id} for domain {domain}")
                        return tenant

                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Invalid authorized_domains JSON for tenant {tenant.tenant_id}: {e}")
                    continue

        return None


def find_tenants_by_authorized_email(email: str) -> list[Tenant]:
    """
    Find tenants that have this email in their authorized_emails list.

    Args:
        email: Email address to look up

    Returns:
        List of Tenant objects that explicitly authorize this email
    """
    if not email:
        return []

    email_lower = email.lower()
    matching_tenants = []

    with get_db_session() as session:
        tenants = session.scalars(select(Tenant).where(Tenant.is_active)).all()

        for tenant in tenants:
            if tenant.authorized_emails:
                try:
                    # Parse JSON field
                    if isinstance(tenant.authorized_emails, str):
                        emails = json.loads(tenant.authorized_emails)
                    else:
                        emails = tenant.authorized_emails

                    if email_lower in [e.lower() for e in emails]:
                        logger.info(f"Found tenant {tenant.tenant_id} for email {email}")
                        matching_tenants.append(tenant)

                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Invalid authorized_emails JSON for tenant {tenant.tenant_id}: {e}")
                    continue

        return matching_tenants


def ensure_user_in_tenant(email: str, tenant_id: str, role: str = "admin", name: str = None) -> User:
    """
    Create or update user record in tenant.

    Args:
        email: User's email address
        tenant_id: Tenant to add user to
        role: User role (admin, manager, viewer)
        name: User's display name (optional)

    Returns:
        User object (created or existing)
    """
    import uuid
    from datetime import UTC, datetime

    email_lower = email.lower()

    with get_db_session() as session:
        # Check if user already exists
        stmt = select(User).filter_by(email=email_lower, tenant_id=tenant_id)
        user = session.scalars(stmt).first()

        if user:
            # Update existing user
            if not user.is_active:
                user.is_active = True
                logger.info(f"Reactivated user {email} in tenant {tenant_id}")
            user.last_login = datetime.now(UTC)  # type: ignore[assignment]
            session.commit()
            return user

        # Create new user
        user = User(
            user_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email=email_lower,
            name=name or email.split("@")[0].title(),  # Default name from email
            role=role,
            is_active=True,
            created_at=datetime.now(UTC),
            last_login=datetime.now(UTC),
        )

        session.add(user)
        session.commit()

        logger.info(f"Created new user {email} in tenant {tenant_id} with role {role}")
        return user


def get_user_tenant_access(email: str) -> dict:
    """
    Get all tenant access for a user based on email domain and explicit authorization.

    Args:
        email: User's email address

    Returns:
        Dict with access information:
        {
            "domain_tenant": Tenant object or None,
            "email_tenants": List of Tenant objects,
            "is_super_admin": bool,
            "total_access": int
        }
    """
    email_domain = extract_email_domain(email)

    result: dict[str, Tenant | list[Tenant] | bool | int | None] = {
        "domain_tenant": None,
        "email_tenants": [],
        "is_super_admin": email_domain == "scope3.com",
        "total_access": 0,
    }

    # Check domain-based access
    total_access = 0
    if email_domain and email_domain != "scope3.com":
        domain_tenant = find_tenant_by_authorized_domain(email_domain)
        if domain_tenant:
            result["domain_tenant"] = domain_tenant
            total_access += 1

    # Check email-based access
    email_tenants = find_tenants_by_authorized_email(email)
    result["email_tenants"] = email_tenants
    total_access += len(email_tenants)
    result["total_access"] = total_access

    return result


def add_authorized_domain(tenant_id: str, domain: str) -> bool:
    """
    Add domain to tenant's authorized_domains list.

    Args:
        tenant_id: Tenant ID
        domain: Domain to add (e.g., "scribd.com")

    Returns:
        True if successful, False otherwise
    """
    domain_lower = domain.lower()

    # Security check - prevent scope3.com hijacking
    if domain_lower == "scope3.com":
        logger.error(f"Attempted to add scope3.com domain to tenant {tenant_id}")
        return False

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()
        if not tenant:
            return False

        try:
            # Get current domains
            if tenant.authorized_domains:
                if isinstance(tenant.authorized_domains, str):
                    domains = json.loads(tenant.authorized_domains)
                else:
                    domains = list(tenant.authorized_domains)
            else:
                domains = []

            # Add new domain if not already present
            if domain_lower not in domains:
                domains.append(domain_lower)
                tenant.authorized_domains = domains
                session.commit()
                logger.info(f"Added domain {domain} to tenant {tenant_id}")

            return True

        except Exception as e:
            logger.error(f"Error adding domain {domain} to tenant {tenant_id}: {e}")
            return False


def remove_authorized_domain(tenant_id: str, domain: str) -> bool:
    """
    Remove domain from tenant's authorized_domains list.

    Args:
        tenant_id: Tenant ID
        domain: Domain to remove

    Returns:
        True if successful, False otherwise
    """
    domain_lower = domain.lower()

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()
        if not tenant:
            return False

        try:
            # Get current domains
            if tenant.authorized_domains:
                if isinstance(tenant.authorized_domains, str):
                    domains = json.loads(tenant.authorized_domains)
                else:
                    domains = list(tenant.authorized_domains)
            else:
                return True  # Nothing to remove

            # Remove domain if present
            if domain_lower in domains:
                domains.remove(domain_lower)
                tenant.authorized_domains = domains
                session.commit()
                logger.info(f"Removed domain {domain} from tenant {tenant_id}")

            return True

        except Exception as e:
            logger.error(f"Error removing domain {domain} from tenant {tenant_id}: {e}")
            return False


def add_authorized_email(tenant_id: str, email: str) -> bool:
    """
    Add email to tenant's authorized_emails list.

    Args:
        tenant_id: Tenant ID
        email: Email to add

    Returns:
        True if successful, False otherwise
    """
    email_lower = email.lower()

    # Security check - prevent scope3.com email hijacking
    if email_lower.endswith("@scope3.com"):
        logger.error(f"Attempted to add scope3.com email {email} to tenant {tenant_id}")
        return False

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()
        if not tenant:
            return False

        try:
            # Get current emails
            if tenant.authorized_emails:
                if isinstance(tenant.authorized_emails, str):
                    emails = json.loads(tenant.authorized_emails)
                else:
                    emails = list(tenant.authorized_emails)
            else:
                emails = []

            # Add new email if not already present
            if email_lower not in [e.lower() for e in emails]:
                emails.append(email_lower)
                tenant.authorized_emails = emails
                session.commit()
                logger.info(f"Added email {email} to tenant {tenant_id}")

            return True

        except Exception as e:
            logger.error(f"Error adding email {email} to tenant {tenant_id}: {e}")
            return False


def remove_authorized_email(tenant_id: str, email: str) -> bool:
    """
    Remove email from tenant's authorized_emails list.

    Args:
        tenant_id: Tenant ID
        email: Email to remove

    Returns:
        True if successful, False otherwise
    """
    email_lower = email.lower()

    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = session.scalars(stmt).first()
        if not tenant:
            return False

        try:
            # Get current emails
            if tenant.authorized_emails:
                if isinstance(tenant.authorized_emails, str):
                    emails = json.loads(tenant.authorized_emails)
                else:
                    emails = list(tenant.authorized_emails)
            else:
                return True  # Nothing to remove

            # Remove email if present (case-insensitive)
            emails = [e for e in emails if e.lower() != email_lower]
            tenant.authorized_emails = emails
            session.commit()
            logger.info(f"Removed email {email} from tenant {tenant_id}")

            return True

        except Exception as e:
            logger.error(f"Error removing email {email} from tenant {tenant_id}: {e}")
            return False
