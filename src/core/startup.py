"""Startup configuration and validation for Prebid Sales Agent."""

import logging

from src.core.config import validate_configuration
from src.core.logging_config import setup_oauth_logging, setup_structured_logging
from src.core.telemetry import init_telemetry

logger = logging.getLogger(__name__)


def instrument_sqlalchemy() -> None:
    """Instrument SQLAlchemy engines for OTEL tracing.

    No-op when tracing is disabled.
    """
    from src.core.telemetry import is_tracing_enabled

    if not is_tracing_enabled():
        return

    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument()


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

        init_telemetry()
        instrument_sqlalchemy()

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
        from src.core.config import get_config

        # Just check that config can be loaded
        get_config()

        # Note: SUPER_ADMIN_EMAILS is no longer required at startup.
        # Per-tenant OIDC with Setup Mode is the default authentication flow.
        # New tenants start with auth_setup_mode=true, allowing test credentials
        # to log in and configure SSO via the Admin UI.

        logger.info("Startup requirements validation passed")

    except Exception as e:
        logger.error(f"Startup requirements validation failed: {str(e)}")
        raise
