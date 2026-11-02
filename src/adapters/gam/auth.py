"""
Google Ad Manager Authentication Manager

Handles OAuth credentials, service account authentication, and credential management
for Google Ad Manager API access.
"""

import logging
from typing import Any

import google.oauth2.service_account
from googleads import oauth2

from src.core.exceptions import AdCPConfigurationError, AdCPSalesAgentError

logger = logging.getLogger(__name__)


class GAMAuthManager:
    """Manages authentication credentials for Google Ad Manager API."""

    def __init__(self, config: dict[str, Any]):
        """Initialize authentication manager with configuration.

        Args:
            config: Dictionary containing authentication configuration:
                - refresh_token: OAuth refresh token
                - service_account_json: Service account credentials as JSON string
                - service_account_key_file: Path to service account JSON file (legacy)
        """
        self.config = config
        self.refresh_token = config.get("refresh_token")
        self.service_account_json = config.get("service_account_json")
        self.key_file = config.get("service_account_key_file")

        # Validate that we have at least one authentication method
        if not self.refresh_token and not self.service_account_json and not self.key_file:
            raise AdCPConfigurationError()

    def get_credentials(self):
        """Get authenticated credentials for GAM API.

        Returns:
            Authenticated credentials object for use with GAM client.

        Raises:
            ValueError: If authentication configuration is invalid
            Exception: If credential creation fails
        """
        try:
            if self.refresh_token:
                return self._get_oauth_credentials()
            elif self.service_account_json or self.key_file:
                return self._get_service_account_credentials()
            else:
                raise AdCPConfigurationError()
        except Exception as e:
            logger.error(f"Error creating GAM credentials: {e}")
            raise

    def _get_oauth_credentials(self):
        """Get OAuth credentials using refresh token and Pydantic configuration."""
        try:
            from src.core.config import get_settings

            auth = get_settings().auth
            client_id = auth.gam_oauth_client_id
            client_secret = auth.gam_oauth_client_secret

        except AdCPSalesAgentError:
            # A typed error already names its own fault; flattening it here would
            # cost the buyer the code it earned.
            raise
        except Exception as e:
            raise AdCPConfigurationError(internal_detail=e) from e

        # Create GoogleAds OAuth2 client
        oauth2_client = oauth2.GoogleRefreshTokenClient(
            client_id=client_id, client_secret=client_secret, refresh_token=self.refresh_token
        )

        return oauth2_client

    def _get_service_account_credentials(self):
        """Get service account credentials from JSON string or file.

        Supports both direct JSON string (preferred for cloud deployments)
        and file path (legacy).

        Returns:
            GoogleCredentialsClient: Wrapped credentials for use with AdManagerClient
        """
        import json

        if self.service_account_json:
            # Parse JSON string directly
            try:
                key_data = json.loads(self.service_account_json)
                credentials = google.oauth2.service_account.Credentials.from_service_account_info(
                    key_data, scopes=["https://www.googleapis.com/auth/dfp"]
                )
                # Wrap in GoogleCredentialsClient for AdManagerClient compatibility
                oauth2_client = oauth2.GoogleCredentialsClient(credentials)
                logger.info("Using service account credentials from JSON string")
                return oauth2_client
            except json.JSONDecodeError as e:
                raise AdCPConfigurationError(internal_detail=e) from e
        elif self.key_file:
            # Legacy: Load from file
            credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                self.key_file, scopes=["https://www.googleapis.com/auth/dfp"]
            )
            # Wrap in GoogleCredentialsClient for AdManagerClient compatibility
            oauth2_client = oauth2.GoogleCredentialsClient(credentials)
            logger.info(f"Using service account credentials from file: {self.key_file}")
            return oauth2_client
        else:
            raise AdCPConfigurationError()

    def is_oauth_configured(self) -> bool:
        """Check if OAuth authentication is configured."""
        return self.refresh_token is not None

    def is_service_account_configured(self) -> bool:
        """Check if service account authentication is configured."""
        return self.service_account_json is not None or self.key_file is not None

    def get_auth_method(self) -> str:
        """Get the current authentication method name."""
        if self.is_oauth_configured():
            return "oauth"
        elif self.is_service_account_configured():
            return "service_account"
        else:
            return "none"
