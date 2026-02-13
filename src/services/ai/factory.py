"""Factory for creating Pydantic AI models with tenant-aware configuration."""

import logging
from functools import lru_cache
from typing import Any

from src.services.ai.config import (
    TenantAIConfig,
    build_model_string,
    get_platform_defaults,
)

logger = logging.getLogger(__name__)

# Track if logfire has been configured
_logfire_configured = False


def configure_logfire(token: str | None = None) -> bool:
    """Configure Logfire for AI observability.

    Args:
        token: Optional Logfire token. If not provided, uses LOGFIRE_TOKEN env var
               or attempts to use default credentials from ~/.logfire/

    Returns:
        True if Logfire was successfully configured, False otherwise
    """
    global _logfire_configured

    if _logfire_configured:
        return True

    try:
        import logfire

        # Logfire will automatically use:
        # 1. Explicit token if provided
        # 2. LOGFIRE_TOKEN env var
        # 3. Default credentials from ~/.logfire/default.toml
        if token:
            logfire.configure(token=token)
        else:
            # Let logfire find credentials automatically
            logfire.configure()

        # Instrument Pydantic AI for automatic tracing
        logfire.instrument_pydantic_ai()

        _logfire_configured = True
        logger.info("Logfire configured for AI observability")
        return True

    except Exception as e:
        logger.debug(f"Logfire not configured: {e}")
        return False


class AIServiceFactory:
    """Factory for creating Pydantic AI models with tenant-aware configuration.

    Usage:
        factory = AIServiceFactory()

        # Using platform defaults
        model = factory.create_model()

        # Using tenant configuration
        model = factory.create_model(tenant_ai_config=tenant.ai_config)
    """

    def __init__(self):
        """Initialize the factory with platform defaults."""
        self._platform_defaults = get_platform_defaults()

        # Try to configure logfire on factory creation
        configure_logfire(self._platform_defaults.get("logfire_token"))

    def create_model(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> Any:
        """Create a Pydantic AI model with the appropriate configuration.

        Configuration priority:
        1. Explicit overrides (provider_override, model_override)
        2. Tenant-specific config (tenant_ai_config)
        3. Platform defaults (environment variables)

        Args:
            tenant_ai_config: Tenant's AI configuration (from database or dict)
            provider_override: Override the provider (for testing)
            model_override: Override the model (for testing)

        Returns:
            Pydantic AI Model instance with API key configured via Provider.
            This can be passed directly to Agent(model=...).

        Raises:
            ValueError: If no API key is available for the configured provider
        """
        # Parse tenant config if provided as dict
        if isinstance(tenant_ai_config, dict):
            config = TenantAIConfig.model_validate(tenant_ai_config)
        elif tenant_ai_config:
            config = tenant_ai_config
        else:
            config = TenantAIConfig()

        # Resolve configuration with priority
        provider = provider_override or config.provider or self._platform_defaults["provider"]
        model_name = model_override or config.model or self._platform_defaults["model"]
        api_key = config.api_key or self._platform_defaults.get("api_key")

        # Configure logfire with tenant token if provided
        if config.logfire_token:
            configure_logfire(config.logfire_token)

        # Normalize provider name
        if provider == "gemini":
            provider = "google-gla"

        logger.debug(f"Creating Pydantic AI model: {provider}:{model_name}")

        # Create model with Provider that has API key directly configured
        # This avoids setting global environment variables
        return self._create_provider_model(provider, model_name, api_key)

    def _create_provider_model(self, provider: str, model_name: str, api_key: str | None) -> Any:
        """Create a Pydantic AI model with explicit API key via Provider.

        This passes the API key directly to the Provider constructor,
        avoiding global environment variable mutation.

        Args:
            provider: Normalized provider name (e.g., "google-gla", "anthropic")
            model_name: Model name (e.g., "gemini-2.0-flash")
            api_key: API key for the provider

        Returns:
            Configured Model instance
        """
        # Import providers lazily to avoid import errors if not installed
        if provider == "google-gla":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            if api_key:
                return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
            return GoogleModel(model_name, provider="google-gla")

        elif provider == "anthropic":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            if api_key:
                return AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
            return AnthropicModel(model_name, provider="anthropic")

        elif provider == "openai":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            if api_key:
                return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=api_key))
            return OpenAIChatModel(model_name, provider="openai")

        elif provider == "groq":
            from pydantic_ai.models.groq import GroqModel
            from pydantic_ai.providers.groq import GroqProvider

            if api_key:
                return GroqModel(model_name, provider=GroqProvider(api_key=api_key))
            return GroqModel(model_name, provider="groq")

        elif provider == "mistral":
            from pydantic_ai.models.mistral import MistralModel
            from pydantic_ai.providers.mistral import MistralProvider

            if api_key:
                return MistralModel(model_name, provider=MistralProvider(api_key=api_key))
            return MistralModel(model_name, provider="mistral")

        elif provider == "cohere":
            from pydantic_ai.models.cohere import CohereModel
            from pydantic_ai.providers.cohere import CohereProvider

            if api_key:
                return CohereModel(model_name, provider=CohereProvider(api_key=api_key))
            return CohereModel(model_name, provider="cohere")

        else:
            # Fallback: use model string and let Pydantic AI resolve it
            # This handles gateway providers and any new providers
            model_string = build_model_string(provider, model_name)
            logger.warning(
                f"Provider '{provider}' not explicitly supported, "
                f"using model string '{model_string}' (API key must be in env var)"
            )
            return model_string

    def is_ai_enabled(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
    ) -> bool:
        """Check if AI is enabled for the given configuration.

        AI is enabled if there's an API key available (from tenant or platform).

        Args:
            tenant_ai_config: Tenant's AI configuration

        Returns:
            True if AI calls can be made, False otherwise
        """
        if isinstance(tenant_ai_config, dict):
            config = TenantAIConfig.model_validate(tenant_ai_config)
        elif tenant_ai_config:
            config = tenant_ai_config
        else:
            config = TenantAIConfig()

        # AI is enabled if we have an API key from either source
        return bool(config.api_key or self._platform_defaults.get("api_key"))

    def get_effective_config(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
    ) -> dict:
        """Get the effective configuration that would be used.

        Useful for debugging and displaying configuration in admin UI.

        Args:
            tenant_ai_config: Tenant's AI configuration

        Returns:
            dict with effective provider, model, and whether API key is set
        """
        if isinstance(tenant_ai_config, dict):
            config = TenantAIConfig.model_validate(tenant_ai_config)
        elif tenant_ai_config:
            config = tenant_ai_config
        else:
            config = TenantAIConfig()

        provider = config.provider or self._platform_defaults["provider"]
        model = config.model or self._platform_defaults["model"]
        has_api_key = bool(config.api_key or self._platform_defaults.get("api_key"))
        has_logfire = bool(config.logfire_token or self._platform_defaults.get("logfire_token"))

        return {
            "provider": provider,
            "model": model,
            "has_api_key": has_api_key,
            "has_logfire": has_logfire,
            "settings": config.settings,
            "source": "tenant" if config.provider else "platform",
        }


@lru_cache(maxsize=1)
def get_factory() -> AIServiceFactory:
    """Get the singleton factory instance.

    Returns:
        AIServiceFactory instance
    """
    return AIServiceFactory()
