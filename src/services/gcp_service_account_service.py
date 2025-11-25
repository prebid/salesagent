"""Google Cloud Platform Service Account Management.

This service handles automatic provisioning of service accounts for GAM integration.
Instead of partners sending us their service account JSON, we create the service account
for them, store it securely, and provide them the email to configure in their GAM.

Security Model - Two-Factor Control:
    ┌────────────────────────────────────────────────────────────────┐
    │ Service Account Authentication Requires BOTH:                   │
    ├────────────────────────────────────────────────────────────────┤
    │ 1. Private Key (we control - stored encrypted in database)     │
    │ 2. GAM User List Entry (partner controls - they add the email) │
    │                                                                 │
    │ Just knowing the service account email is NOT enough!          │
    │ API calls must be cryptographically signed with the private    │
    │ key to prove identity, AND the partner must explicitly grant   │
    │ permissions by adding the email to their GAM.                  │
    └────────────────────────────────────────────────────────────────┘

Why We Store the Service Account Key:
    We need the private key (stored as gam_service_account_json) to authenticate AS the
    service account when making GAM API calls on behalf of the tenant. Without it, we
    cannot access the partner's GAM even if they've added the email to their user list.

    Flow:
    1. We create: adcp-sales-tenant123@bok-playground.iam.gserviceaccount.com + private key
    2. We store: Private key encrypted in database (using ENCRYPTION_KEY)
    3. Partner adds: Service account email to their GAM with Trafficker role
    4. When we access GAM: We use stored key to sign API requests as that service account
    5. GAM validates: "Request signed correctly AND email in my user list" → Allow access

    Partner Security:
    - Partner can revoke access anytime by removing the email from their GAM
    - Partner controls what permissions to grant (Trafficker, Salesperson, etc.)
    - Partner can restrict access to specific advertisers via GAM teams
    - Service account activity appears in partner's GAM audit logs

Authentication for This Service:
    The IAMClient uses Application Default Credentials (ADC) to authenticate to GCP
    for creating service accounts (not for accessing partner GAM).

    In production (Fly.io), set the GOOGLE_APPLICATION_CREDENTIALS_JSON secret:
        fly secrets set GOOGLE_APPLICATION_CREDENTIALS_JSON='{"type":"service_account"...}' --app adcp-sales-agent

    The management service account must have these IAM roles in YOUR GCP project:
        - roles/iam.serviceAccountAdmin (to create service accounts)
        - roles/iam.serviceAccountKeyAdmin (to create service account keys)
"""

import logging
import os
import tempfile
from typing import Any

from google.cloud import iam_admin_v1
from google.cloud.iam_admin_v1 import types
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig

logger = logging.getLogger(__name__)


class GCPServiceAccountService:
    """Service for managing GCP service accounts for GAM integration."""

    def __init__(self, gcp_project_id: str):
        """Initialize service with GCP project ID.

        Args:
            gcp_project_id: The GCP project ID where service accounts will be created

        Note:
            Authentication uses Application Default Credentials (ADC).
            Set GOOGLE_APPLICATION_CREDENTIALS_JSON as a Fly secret or
            GOOGLE_APPLICATION_CREDENTIALS as a file path.
        """
        self.gcp_project_id = gcp_project_id
        self._temp_creds_file = None

        # Setup credentials if provided via environment variable (common in cloud deployments)
        self._setup_credentials()

        # Create IAM client (uses ADC)
        self.iam_client = iam_admin_v1.IAMClient()

    def _setup_credentials(self):
        """Setup GCP credentials from environment if provided.

        Handles GOOGLE_APPLICATION_CREDENTIALS_JSON secret for cloud deployments.
        """
        creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if creds_json:
            # Write credentials to temp file for GCP client library
            # This is needed because the client library expects a file path
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
                f.write(creds_json)
                self._temp_creds_file = f.name
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
            logger.info("GCP credentials loaded from GOOGLE_APPLICATION_CREDENTIALS_JSON")
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.info(f"Using GCP credentials from file: {os.environ['GOOGLE_APPLICATION_CREDENTIALS']}")
        else:
            logger.warning("No explicit GCP credentials provided - relying on Application Default Credentials")

    def cleanup(self):
        """Cleanup temporary credentials file.

        Called automatically on object destruction, but can be called manually if needed.
        """
        if self._temp_creds_file:
            try:
                os.unlink(self._temp_creds_file)
                logger.debug(f"Cleaned up temporary credentials file: {self._temp_creds_file}")
                self._temp_creds_file = None
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to cleanup temp credentials file: {e}")

    def __del__(self):
        """Destructor: cleanup temporary credentials file."""
        self.cleanup()

    def create_service_account_for_tenant(self, tenant_id: str, display_name: str | None = None) -> tuple[str, str]:
        """Create a service account for a tenant and store credentials.

        This creates a service account in GCP, generates a key for it,
        and stores the credentials encrypted in the database.

        Args:
            tenant_id: Tenant ID to create service account for
            display_name: Optional display name for the service account

        Returns:
            Tuple of (service_account_email, service_account_json)

        Raises:
            ValueError: If tenant not found or already has a service account
            Exception: If service account creation fails
        """
        with get_db_session() as session:
            # Get adapter config
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config:
                raise ValueError(f"Tenant {tenant_id} not found or has no adapter config")

            # Check if service account already exists
            if adapter_config.gam_service_account_email:
                raise ValueError(
                    f"Tenant {tenant_id} already has a service account: {adapter_config.gam_service_account_email}"
                )

            # Generate account ID from tenant ID (must be 6-30 chars, lowercase, numbers, hyphens)
            # Format: adcp-sales-{tenant_id}
            # GCP service account IDs must match: [a-zA-Z][a-zA-Z\d\-]*[a-zA-Z\d]
            # - No underscores allowed (replace with hyphens)
            # - Must start with letter, end with letter or digit
            sanitized_tenant_id = tenant_id.replace("_", "-")
            account_id = f"adcp-sales-{sanitized_tenant_id}".lower()
            
            # Ensure account_id doesn't end with hyphen (GCP requirement)
            account_id = account_id.rstrip("-")
            
            if len(account_id) > 30:
                # Truncate if too long, but ensure we don't end with hyphen after truncation
                account_id = account_id[:30].rstrip("-")

            # Expected service account email format
            expected_email = f"{account_id}@{self.gcp_project_id}.iam.gserviceaccount.com"

            # Check if service account already exists in GCP
            logger.info(f"Checking if service account {expected_email} already exists in GCP")
            existing_account = self._get_service_account_if_exists(expected_email)

            service_account_was_created = False  # Track if we created it (for cleanup on failure)

            if existing_account:
                logger.info(
                    f"Service account {expected_email} already exists in GCP - likely from previous failed attempt. Will reuse it."
                )
                service_account_email = existing_account.email
            else:
                # Create service account
                logger.info(f"Creating new service account with account_id='{account_id}' for tenant {tenant_id}")
                try:
                    service_account = self._create_service_account(
                        account_id=account_id, display_name=display_name or f"AdCP Sales Agent - {tenant_id}"
                    )
                    service_account_email = service_account.email
                    service_account_was_created = True
                    logger.info(f"Successfully created service account: {service_account_email}")
                except Exception as e:
                    logger.error(f"Failed to create service account for tenant {tenant_id}: {e}", exc_info=True)
                    raise

                # Verify service account was created (helps catch propagation issues)
                try:
                    self._verify_service_account_exists(service_account_email)
                except Exception as e:
                    logger.warning(f"Service account verification failed, but continuing: {e}")

            # Create key for service account (whether new or existing)
            try:
                logger.info(f"Creating key for service account: {service_account_email}")
                service_account_json = self._create_service_account_key(service_account_email)
                logger.info(f"Successfully created service account key for: {service_account_email}")

                # Store in database
                adapter_config.gam_service_account_email = service_account_email
                adapter_config.gam_service_account_json = service_account_json
                adapter_config.gam_auth_method = "service_account"
                session.commit()

                logger.info(f"Stored service account credentials for tenant {tenant_id}")

                return service_account_email, service_account_json

            except Exception as e:
                logger.error(f"Failed to create key or store credentials for tenant {tenant_id}: {e}", exc_info=True)

                # Cleanup: If we just created the service account and then failed to create the key,
                # delete the service account from GCP to avoid leaving orphaned accounts
                if service_account_was_created:
                    logger.warning(
                        f"Attempting to clean up newly created service account {service_account_email} after failure"
                    )
                    try:
                        self._delete_service_account_from_gcp(service_account_email)
                        logger.info(f"Successfully cleaned up service account {service_account_email}")
                    except Exception as cleanup_error:
                        logger.error(
                            f"Failed to cleanup service account {service_account_email}: {cleanup_error}",
                            exc_info=True,
                        )
                        # Don't fail the whole operation because cleanup failed - the original error is more important

                raise

    def _create_service_account(self, account_id: str, display_name: str) -> types.ServiceAccount:
        """Create a service account in GCP.

        Args:
            account_id: Unique ID for the service account (6-30 chars)
            display_name: Human-readable display name

        Returns:
            Created ServiceAccount object

        Raises:
            Exception: If creation fails
        """
        try:
            request = types.CreateServiceAccountRequest()
            request.account_id = account_id
            request.name = f"projects/{self.gcp_project_id}"

            service_account = types.ServiceAccount()
            service_account.display_name = display_name
            request.service_account = service_account

            logger.info(
                f"Calling IAM API to create service account: account_id={account_id}, project={self.gcp_project_id}"
            )
            account = self.iam_client.create_service_account(request=request)
            logger.info(f"IAM API returned service account with email: {account.email}")
            return account
        except Exception as e:
            logger.error(f"Failed to create service account with account_id={account_id}: {e}", exc_info=True)
            raise

    def _get_service_account_if_exists(self, service_account_email: str) -> types.ServiceAccount | None:
        """Check if a service account exists in GCP.

        Args:
            service_account_email: Email of the service account to check

        Returns:
            ServiceAccount object if exists, None if not found
        """
        try:
            request = iam_admin_v1.GetServiceAccountRequest()
            request.name = f"projects/{self.gcp_project_id}/serviceAccounts/{service_account_email}"

            account = self.iam_client.get_service_account(request=request)
            logger.info(f"Found existing service account: {account.email}")
            return account
        except Exception as e:
            # 404 means it doesn't exist, which is fine
            if "404" in str(e) or "NOT_FOUND" in str(e):
                logger.info(f"Service account {service_account_email} does not exist yet")
                return None
            else:
                # Some other error - log it but don't fail
                logger.warning(f"Error checking if service account exists: {e}")
                return None

    def _verify_service_account_exists(self, service_account_email: str) -> bool:
        """Verify that a service account exists in GCP.

        Args:
            service_account_email: Email of the service account to verify

        Returns:
            True if exists

        Raises:
            Exception: If service account doesn't exist or verification fails
        """
        try:
            request = iam_admin_v1.GetServiceAccountRequest()
            request.name = f"projects/{self.gcp_project_id}/serviceAccounts/{service_account_email}"

            account = self.iam_client.get_service_account(request=request)
            logger.info(f"Verified service account exists: {account.email}")
            return True
        except Exception as e:
            logger.error(f"Service account verification failed for {service_account_email}: {e}")
            raise

    def _create_service_account_key(self, service_account_email: str) -> str:
        """Create a key for a service account.

        Args:
            service_account_email: Email of the service account

        Returns:
            Service account JSON credentials as string

        Raises:
            Exception: If key creation fails
        """
        try:
            request = types.CreateServiceAccountKeyRequest()
            resource_name = f"projects/{self.gcp_project_id}/serviceAccounts/{service_account_email}"
            request.name = resource_name

            logger.info(f"Calling IAM API to create key for service account: {resource_name}")
            key = self.iam_client.create_service_account_key(request=request)

            # Extract private key data (this is the JSON credentials)
            # The private_key_data is bytes, need to decode to string
            service_account_json = key.private_key_data.decode("utf-8")
            logger.info(f"Successfully created and retrieved key for {service_account_email}")

            return service_account_json
        except Exception as e:
            logger.error(
                f"Failed to create key for service account {service_account_email} (resource: {resource_name}): {e}",
                exc_info=True,
            )
            raise

    def test_connection(self) -> dict[str, Any]:
        """Test GCP IAM API connectivity and permissions.

        Returns:
            Dict with test results including project, permissions, and any errors
        """
        result: dict[str, Any] = {
            "project_id": self.gcp_project_id,
            "can_list": False,
            "can_create": False,
            "errors": [],
        }

        try:
            # Test listing service accounts (requires serviceAccountViewer or higher)
            list_request = iam_admin_v1.ListServiceAccountsRequest()
            list_request.name = f"projects/{self.gcp_project_id}"

            response = self.iam_client.list_service_accounts(request=list_request)
            account_count = len(list(response))
            result["can_list"] = True
            result["service_account_count"] = account_count
            logger.info(f"Successfully listed {account_count} service accounts in project {self.gcp_project_id}")
        except Exception as e:
            result["errors"].append(f"Failed to list service accounts: {e}")
            logger.error(f"Failed to list service accounts: {e}", exc_info=True)

        # Note: We can't really test 'can_create' without actually creating one,
        # so we'll just check if we have the list permission as a basic sanity check
        result["can_create"] = result["can_list"]  # Approximation

        return result

    def get_service_account_email(self, tenant_id: str) -> str | None:
        """Get the service account email for a tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            Service account email or None if not created
        """
        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config:
                return None

            return adapter_config.gam_service_account_email

    def _delete_service_account_from_gcp(self, service_account_email: str) -> None:
        """Delete a service account from GCP (helper method).

        Args:
            service_account_email: Email of the service account to delete

        Raises:
            Exception: If deletion fails
        """
        delete_request = iam_admin_v1.DeleteServiceAccountRequest()
        delete_request.name = f"projects/{self.gcp_project_id}/serviceAccounts/{service_account_email}"
        self.iam_client.delete_service_account(request=delete_request)
        logger.info(f"Deleted service account from GCP: {service_account_email}")

    def delete_service_account(self, tenant_id: str) -> bool:
        """Delete a service account for a tenant.

        This removes the service account from GCP and clears the database.

        Args:
            tenant_id: Tenant ID

        Returns:
            True if deleted, False if no service account existed

        Raises:
            Exception: If deletion fails
        """
        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config or not adapter_config.gam_service_account_email:
                return False

            service_account_email = adapter_config.gam_service_account_email

            try:
                # Delete from GCP
                self._delete_service_account_from_gcp(service_account_email)

                # Clear from database
                adapter_config.gam_service_account_email = None
                adapter_config.gam_service_account_json = None
                session.commit()

                logger.info(f"Cleared service account for tenant {tenant_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to delete service account for tenant {tenant_id}: {e}", exc_info=True)
                raise
