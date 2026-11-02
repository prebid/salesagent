"""Configuration models for Pydantic AI service."""

from pydantic import BaseModel, Field

from src.core.config import get_settings


class ModelSettings(BaseModel):
    """Settings for model behavior."""

    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout: int = Field(default=30, gt=0)


GOOGLE_PROVIDER_ALIASES: frozenset[str] = frozenset({"gemini", "google", "google-gla"})
CANONICAL_GOOGLE_PROVIDER = "google"


def canonicalize_google_provider(provider: str) -> str:
    """Map legacy Google provider names to pydantic-ai 1.99 canonical form."""
    if provider in GOOGLE_PROVIDER_ALIASES:
        return CANONICAL_GOOGLE_PROVIDER
    return provider


def uses_legacy_gemini_api_key(provider: str) -> bool:
    """True when admin AI settings should read tenant.gemini_api_key for this provider."""
    return canonicalize_google_provider(provider) == CANONICAL_GOOGLE_PROVIDER


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
    """Get platform-level AI configuration from the startup settings.

    Returns:
        dict with platform default settings
    """
    integrations = get_settings().integrations
    return {
        "provider": integrations.pydantic_ai_provider,
        "model": integrations.pydantic_ai_model,
        "api_key": _get_provider_api_key(integrations.pydantic_ai_provider),
        "logfire_token": integrations.logfire_token,
    }


def _get_provider_api_key(provider: str) -> str | None:
    """Get the API key for a specific provider from the startup settings.

    Args:
        provider: The provider name (gemini, openai, anthropic, etc.)

    Returns:
        API key if found, None otherwise
    """
    return get_settings().integrations.provider_api_key(canonicalize_google_provider(provider))


def build_model_string(provider: str, model: str) -> str:
    """Build the Pydantic AI model string.

    Pydantic AI uses format: "provider:model" (e.g., "google:gemini-2.0-flash")

    Args:
        provider: Provider name (e.g., "google-gla", "anthropic", "gateway/openai")
        model: Model name

    Returns:
        Pydantic AI model string
    """
    provider = canonicalize_google_provider(provider)
    return f"{provider}:{model}"
