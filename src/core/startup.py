"""Startup configuration and validation for Prebid Sales Agent."""

import logging

from src.core.config import validate_configuration
from src.core.logging_config import setup_oauth_logging, setup_structured_logging

logger = logging.getLogger(__name__)


def initialize_application() -> None:
    """Initialize the application with configuration validation and setup.

    This should be called at the start of both the MCP server and Admin UI.

    Raises:
        SystemExit: If configuration validation fails
    """
    try:
        # Setup structured logging FIRST (before any logging calls)
        # This ensures production environments get JSON logs
        setup_structured_logging()

        logger.info("Initializing Prebid Sales Agent...")

        # Setup OAuth-specific logging
        setup_oauth_logging()
        logger.info("Structured logging initialized")

        # Validate all configuration
        validate_configuration()
        logger.info("Configuration validation passed")

        logger.info("Application initialization completed successfully")

    except Exception as e:
        logger.error(f"Application initialization failed: {str(e)}")
        raise SystemExit(1) from e


def validate_startup_requirements() -> None:
    """Validate startup requirements without full initialization.

    This is useful for health checks and lightweight validation.
    """
    try:
        from src.core.config import load_settings

        # The composition root for a server started through run_server.py: the environment
        # is read here, once, and a bad value fails here.
        load_settings()

        # Note: SUPER_ADMIN_EMAILS is no longer required at startup.
        # Per-tenant OIDC with Setup Mode is the default authentication flow.
        # New tenants start with auth_setup_mode=true, so a super admin can administer
        # one before it has configured its own SSO. The password login this used to
        # name is gone (#2253): a session comes from Google, or not at all.

        logger.info("Startup requirements validation passed")

    except Exception as e:
        logger.error(f"Startup requirements validation failed: {str(e)}")
        raise
