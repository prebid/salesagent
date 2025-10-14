"""Configuration management for AdCP Sales Agent.

Provides Pydantic-based configuration classes for type-safe, validated configuration
management using environment variables.
"""

import os

from pydantic import ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings


class GAMOAuthConfig(BaseSettings):
    """Google Ad Manager OAuth configuration."""

    client_id: str = Field(default="", description="GAM OAuth Client ID from Google Cloud Console")
    client_secret: str = Field(default="", description="GAM OAuth Client Secret from Google Cloud Console")

    model_config = ConfigDict(env_prefix="GAM_OAUTH_", case_sensitive=False)

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, v):
        """Validate GAM OAuth Client ID format (only if provided)."""
        if not v:
            return v  # Allow empty - validation happens when GAM adapter is used
        if not v.endswith(".apps.googleusercontent.com"):
            raise ValueError("GAM OAuth Client ID must end with '.apps.googleusercontent.com'")
        return v

    @field_validator("client_secret")
    @classmethod
    def validate_client_secret(cls, v):
        """Validate GAM OAuth Client Secret format (only if provided)."""
        if not v:
            return v  # Allow empty - validation happens when GAM adapter is used
        if not v.startswith("GOCSPX-"):
            raise ValueError("GAM OAuth Client Secret must start with 'GOCSPX-'")
        return v


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str | None = Field(default=None, description="Database connection URL")
    type: str = Field(default="postgresql", description="Database type")

    model_config = ConfigDict(env_prefix="DATABASE_", case_sensitive=False)


class ServerConfig(BaseSettings):
    """Server configuration."""

    adcp_sales_port: int = Field(default=8080, description="MCP server port")
    admin_ui_port: int = Field(default=8001, description="Admin UI port")
    a2a_port: int = Field(default=8091, description="A2A server port")

    model_config = ConfigDict(env_prefix="", case_sensitive=False)


class GoogleOAuthConfig(BaseSettings):
    """Google OAuth configuration for admin UI."""

    client_id: str | None = Field(default=None, description="Google OAuth Client ID")
    client_secret: str | None = Field(default=None, description="Google OAuth Client Secret")
    credentials_file: str | None = Field(default=None, description="Path to Google OAuth credentials file")

    model_config = ConfigDict(env_prefix="GOOGLE_", case_sensitive=False)


class SuperAdminConfig(BaseSettings):
    """Super admin configuration."""

    emails: str = Field(..., description="Comma-separated list of super admin emails")
    domains: str | None = Field(default=None, description="Comma-separated list of super admin domains")

    model_config = ConfigDict(env_prefix="SUPER_ADMIN_", case_sensitive=False)

    @property
    def email_list(self) -> list[str]:
        """Get super admin emails as a list."""
        return [email.strip() for email in self.emails.split(",") if email.strip()]

    @property
    def domain_list(self) -> list[str]:
        """Get super admin domains as a list."""
        if not self.domains:
            return []
        return [domain.strip() for domain in self.domains.split(",") if domain.strip()]


class AppConfig(BaseSettings):
    """Main application configuration."""

    gemini_api_key: str = Field(..., description="Gemini API key for AI features")
    flask_secret_key: str = Field(default="dev-secret-key-change-in-production", description="Flask secret key")
    debug: bool = Field(default=False, description="Enable debug mode")
    environment: str = Field(default="development", description="Environment: production, staging, or development")

    # Configuration objects
    gam_oauth: GAMOAuthConfig = Field(default_factory=GAMOAuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    google_oauth: GoogleOAuthConfig = Field(default_factory=GoogleOAuthConfig)
    superadmin: SuperAdminConfig = Field(default_factory=SuperAdminConfig)

    model_config = ConfigDict(env_prefix="", case_sensitive=False)


# Global configuration instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def validate_configuration() -> None:
    """Validate all configuration at startup.

    Raises:
        ValueError: If required configuration is missing or invalid
        RuntimeError: If configuration validation fails
    """
    try:
        config = get_config()

        # Validate GAM OAuth configuration
        if config.gam_oauth:
            # Configuration validation happens automatically via Pydantic
            pass

        # Validate critical configuration
        required_configs = [
            ("GEMINI_API_KEY", config.gemini_api_key),
            ("SUPER_ADMIN_EMAILS", config.superadmin.emails),
        ]

        missing = []
        for name, value in required_configs:
            if not value:
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        print("✅ Configuration validation passed")
        print(f"   GAM OAuth: {'✅ Configured' if config.gam_oauth.client_id else '❌ Not configured'}")
        print(f"   Database: {'✅ Configured' if config.database.url else '❌ Not configured'}")
        print(f"   Gemini API: {'✅ Configured' if config.gemini_api_key else '❌ Not configured'}")
        print(f"   Super Admin: {'✅ Configured' if config.superadmin.emails else '❌ Not configured'}")

    except Exception as e:
        raise RuntimeError(f"Configuration validation failed: {str(e)}") from e


def get_gam_oauth_config() -> GAMOAuthConfig:
    """Get GAM OAuth configuration."""
    return get_config().gam_oauth


def is_production() -> bool:
    """Check if running in production environment.

    Returns:
        bool: True if ENVIRONMENT=production, False otherwise
    """
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def get_pydantic_extra_mode() -> str:
    """Get Pydantic extra field handling mode based on environment.

    Production: "ignore" - Accept extra fields for forward compatibility
    Non-production: "forbid" - Reject extra fields to catch bugs early

    Returns:
        str: "ignore" or "forbid"
    """
    return "ignore" if is_production() else "forbid"
