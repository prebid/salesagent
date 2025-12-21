"""Configuration models for Pydantic AI service."""

import os

from pydantic import BaseModel, Field


class ModelSettings(BaseModel):
    """Settings for model behavior."""

    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout: int = Field(default=30, gt=0)


class TenantAIConfig(BaseModel):
    """Per-tenant AI configuration stored in database.

    This model defines the structure of the `ai_config` JSON column on the Tenant table.
    All fields are optional - tenants inherit platform defaults for any unset values.
    """

    model_config = {"extra": "ignore"}  # Forward compatible with future fields

    # Model selection - accepts any Pydantic AI provider string
    # e.g., "google-gla", "anthropic", "openai", "gateway/anthropic", etc.
    provider: str | None = None
    model: str | None = None  # e.g., "gemini-2.0-flash", "claude-sonnet-4-20250514"

    # API key (encrypted in database, decrypted when loaded)
    api_key: str | None = None

    # Observability
    logfire_token: str | None = None

    # Model behavior settings
    settings: ModelSettings = Field(default_factory=ModelSettings)


def get_platform_defaults() -> dict:
    """Get platform-level AI configuration from environment variables.

    Returns:
        dict with platform default settings
    """
    return {
        "provider": os.getenv("PYDANTIC_AI_PROVIDER", "gemini"),
        "model": os.getenv("PYDANTIC_AI_MODEL", "gemini-2.0-flash"),
        "api_key": _get_provider_api_key(os.getenv("PYDANTIC_AI_PROVIDER", "gemini")),
        "logfire_token": os.getenv("LOGFIRE_TOKEN"),
    }


def _get_provider_api_key(provider: str) -> str | None:
    """Get the API key for a specific provider from environment.

    Args:
        provider: The provider name (gemini, openai, anthropic, etc.)

    Returns:
        API key if found, None otherwise
    """
    provider_env_vars = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "groq": "GROQ_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",  # Bedrock uses AWS credentials
    }
    env_var = provider_env_vars.get(provider)
    if env_var:
        return os.getenv(env_var)
    return None


def build_model_string(provider: str, model: str) -> str:
    """Build the Pydantic AI model string.

    Pydantic AI uses format: "provider:model" (e.g., "google-gla:gemini-2.0-flash")

    Args:
        provider: Provider name (e.g., "google-gla", "anthropic", "gateway/openai")
        model: Model name

    Returns:
        Pydantic AI model string
    """
    # Handle legacy "gemini" provider name
    if provider == "gemini":
        provider = "google-gla"

    # Provider string is already in Pydantic AI format (e.g., "google-gla", "gateway/anthropic")
    return f"{provider}:{model}"
