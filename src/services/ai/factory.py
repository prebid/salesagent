"""Factory for creating Pydantic AI models with tenant-aware configuration."""

import logging
from functools import lru_cache
from typing import Any, NamedTuple

from src.core.exceptions import AdCPConfigurationError
from src.services.ai.config import (
    CANONICAL_GOOGLE_PROVIDER,
    TenantAIConfig,
    build_model_string,
    canonicalize_google_provider,
    get_platform_defaults,
)

logger = logging.getLogger(__name__)


class ResolvedAIConfig(NamedTuple):
    """What one AI call would actually run on, after platform defaults are applied.

    The three fields are resolved TOGETHER by `AIServiceFactory._resolve` because they
    are not independent: a model name and an API key both belong to a specific vendor.
    Resolving them from independent expressions is what sent the platform's Google
    credential to api.anthropic.com and paired a "claude-*" model name with a Google
    client.

    `model` and `api_key` are None when nothing coherent resolves for `provider` - that
    is "AI is not available for this request", not "use whatever the platform has".
    """

    provider: str
    model: str | None
    api_key: str | None


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
        self._platform_provider = canonicalize_google_provider(self._platform_defaults["provider"])

        # Try to configure logfire on factory creation
        configure_logfire(self._platform_defaults.get("logfire_token"))

    def _resolve(
        self,
        tenant_ai_config: dict | TenantAIConfig | None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> tuple[TenantAIConfig, ResolvedAIConfig]:
        """Resolve provider, model and key together - the one place platform defaults apply.

        The platform's model and API key belong to the PLATFORM's provider
        (PYDANTIC_AI_PROVIDER, whose key `get_platform_defaults` reads from that
        provider's own environment variable). They may therefore only fill in for a
        request that resolves to that same provider. Filling them in regardless sent the
        operator's Google secret to another vendor: a tenant naming "anthropic" got an
        AnthropicModel whose client carried the platform's GEMINI_API_KEY, and
        get_products is auth-OPTIONAL discovery, so an anonymous buyer's brief was
        enough to trigger it.

        When the providers differ and the tenant named no model/key of its own, the
        fields stay None: `is_ai_enabled` then reports False and callers skip AI,
        rather than building a model that cannot work or carries the wrong secret.

        Returns the parsed tenant config alongside the resolution, because callers need
        both (logfire token, settings, and "did the tenant name a provider at all").
        """
        config = TenantAIConfig.coerce(tenant_ai_config)

        # Display form, not canonicalized: get_effective_config reports this string to
        # the admin UI, and _create_provider_model canonicalizes for itself.
        provider = provider_override or config.provider or self._platform_defaults["provider"]
        platform_is_same_vendor = canonicalize_google_provider(provider) == self._platform_provider

        model = (
            model_override or config.model or (self._platform_defaults["model"] if platform_is_same_vendor else None)
        )
        api_key = config.api_key or (self._platform_defaults.get("api_key") if platform_is_same_vendor else None)

        return config, ResolvedAIConfig(provider=provider, model=model, api_key=api_key)

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
            AdCPConfigurationError: If no API key, or no model, resolves for the
                configured provider. Gate on `is_ai_enabled()` first to skip AI
                cleanly instead of raising into a request.
        """
        config, resolved = self._resolve(tenant_ai_config, provider_override, model_override)

        # Configure logfire with tenant token if provided
        if config.logfire_token:
            configure_logfire(config.logfire_token)

        if resolved.model is None:
            raise AdCPConfigurationError(
                f"No model configured for AI provider {resolved.provider!r}. The platform default model "
                f"belongs to provider {self._platform_defaults['provider']!r}, so pairing it with this one "
                "would send a model name to a vendor that does not have it. Set 'model' on the tenant's "
                "AI configuration."
            )

        provider = canonicalize_google_provider(resolved.provider)

        logger.debug(f"Creating Pydantic AI model: {provider}:{resolved.model}")

        # Create model with Provider that has API key directly configured
        # This avoids setting global environment variables
        return self._create_provider_model(provider, resolved.model, resolved.api_key)

    def _create_provider_model(self, provider: str, model_name: str, api_key: str | None) -> Any:
        """Create a Pydantic AI model with explicit API key via Provider.

        This passes the API key directly to the Provider constructor,
        avoiding global environment variable mutation.

        Args:
            provider: Normalized provider name (e.g., "google", "anthropic")
            model_name: Model name (e.g., "gemini-2.0-flash")
            api_key: API key for the provider

        Returns:
            Configured Model instance
        """
        # Import providers lazily to avoid import errors if not installed
        if provider == CANONICAL_GOOGLE_PROVIDER:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            # Google requires explicit api_key via GoogleProvider; other providers may
            # fall back to string-format and pydantic-ai env-var resolution.
            if not api_key:
                raise AdCPConfigurationError(
                    f"No API key available for provider '{provider}'. Set a tenant api_key or platform GEMINI_API_KEY."
                )
            return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))

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
            if api_key:
                # No quiet failures: this branch has nowhere to put an explicit key, so
                # returning the string would drop the configured credential on the floor
                # and let pydantic-ai authenticate with whatever ELSE is in the
                # environment - a different key than the one the seller configured.
                raise AdCPConfigurationError(
                    f"Provider '{provider}' has no explicit-API-key integration here, so the configured "
                    f"API key cannot be used for it; '{model_string}' would authenticate with whatever "
                    "credential pydantic-ai finds in the environment instead. Configure a supported "
                    "provider, or add an explicit branch for this one."
                )
            logger.warning(
                f"Provider '{provider}' not explicitly supported, "
                f"using model string '{model_string}' (API key must be in env var)"
            )
            return model_string

    def is_ai_enabled(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
    ) -> bool:
        """Check whether a COHERENT AI configuration resolves - can this call actually run?

        Enabled means a provider with both a model and an API key that belong to it.
        A key alone is not enough: the platform's key belongs to the platform's
        provider, so "there is a key somewhere" answered True for a tenant whose named
        provider had no credential at all, and the call it green-lit either carried the
        wrong vendor's secret or 404'd on a model name from another vendor.

        Args:
            tenant_ai_config: Tenant's AI configuration

        Returns:
            True if AI calls can be made, False otherwise
        """
        _, resolved = self._resolve(tenant_ai_config)
        return bool(resolved.api_key and resolved.model)

    def get_effective_config(
        self,
        tenant_ai_config: dict | TenantAIConfig | None = None,
    ) -> dict:
        """Get the effective configuration that would be used.

        Useful for debugging and displaying configuration in admin UI.

        Args:
            tenant_ai_config: Tenant's AI configuration

        Returns:
            dict with effective provider, model, and whether API key is set.
            `has_api_key` is provider-scoped: it reports whether a key is available
            FOR THE RESOLVED PROVIDER, not whether any key exists anywhere. Reporting
            the platform's Google key as this tenant's key is what told
            `PolicyCheckService` that AI was configured for a tenant that had no
            credential of its own.
        """
        config, resolved = self._resolve(tenant_ai_config)

        return {
            "provider": resolved.provider,
            "model": resolved.model,
            "has_api_key": bool(resolved.api_key),
            "has_logfire": bool(config.logfire_token or self._platform_defaults.get("logfire_token")),
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
