"""Factory for creating Pydantic AI models with tenant-aware configuration."""

import logging
import pkgutil
from functools import lru_cache
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic_ai.exceptions import AgentRunError, UserError

from src.core.logging_config import log_safe
from src.core.security.outbound_http import is_transport_failure
from src.services.ai.config import (
    UNUSABLE_AI_CONFIG,
    SellerAIConfig,
    TenantAIConfig,
    canonicalize_google_provider,
    get_platform_defaults,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent, AgentRunResult

    from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


class ResolvedAIConfig(NamedTuple):
    """What one AI call runs on, after platform defaults are applied.

    The three fields are resolved TOGETHER by `AIServiceFactory.resolve` because they
    are not independent: a model name and an API key both belong to a specific vendor.
    Resolving them from independent expressions is what sent the platform's Google
    credential to api.anthropic.com and paired a "claude-*" model name with a Google
    client. `provider` is canonical (`canonicalize_google_provider`).
    """

    provider: str
    model: str
    api_key: str


#: The providers this factory builds, by canonical name: the pydantic-ai model class and
#: the provider class that carries the API key, as `pkgutil.resolve_name` paths so each
#: SDK is imported only when its provider is built. This table is the one statement of
#: what can be built: `resolve` refuses every provider not in it, and `build` builds from
#: it. A provider outside it has nowhere to put an explicit key; pydantic-ai would
#: authenticate it with whatever else is in the environment.
PROVIDER_MODELS: dict[str, tuple[str, str]] = {
    "google": ("pydantic_ai.models.google:GoogleModel", "pydantic_ai.providers.google:GoogleProvider"),
    "anthropic": ("pydantic_ai.models.anthropic:AnthropicModel", "pydantic_ai.providers.anthropic:AnthropicProvider"),
    "openai": ("pydantic_ai.models.openai:OpenAIChatModel", "pydantic_ai.providers.openai:OpenAIProvider"),
    "groq": ("pydantic_ai.models.groq:GroqModel", "pydantic_ai.providers.groq:GroqProvider"),
    "mistral": ("pydantic_ai.models.mistral:MistralModel", "pydantic_ai.providers.mistral:MistralProvider"),
    "cohere": ("pydantic_ai.models.cohere:CohereModel", "pydantic_ai.providers.cohere:CohereProvider"),
}

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
    """Builds the Pydantic AI model a seller's AI configuration runs on.

    Usage:
        model = AIServiceFactory().build(tenant.ai_config)
        if model is None:
            ...  # AI is not available for this seller: skip the AI step
    """

    def __init__(self):
        """Initialize the factory with platform defaults."""
        self._platform_defaults = get_platform_defaults()
        self._platform_provider = canonicalize_google_provider(self._platform_defaults["provider"])

        # Try to configure logfire on factory creation
        configure_logfire(self._platform_defaults.get("logfire_token"))

    def resolve(self, seller_ai: SellerAIConfig = None, *, platform_fallback: bool = True) -> ResolvedAIConfig | None:
        """Provider, model and key for `seller_ai`, or None when no model can be built.

        `seller_ai` is what `TenantAIConfig.from_tenant` answered. `UNUSABLE_AI_CONFIG`
        resolves to None: the seller configured AI that cannot run, and the platform's
        credential is not a substitute for the seller's choice. `None` (configured
        nothing) resolves on the platform defaults alone, unless `platform_fallback` is
        False: the policy check runs only on the seller's own credential, because a
        blocking verdict is a rejection the seller did not configure.

        The platform's model and API key belong to the PLATFORM's provider
        (PYDANTIC_AI_PROVIDER, whose key `get_platform_defaults` reads from that
        provider's own environment variable). They fill in only for a configuration that
        resolves to that same provider. Filling them in regardless sent the operator's
        Google secret to another vendor: a tenant naming "anthropic" got an
        AnthropicModel whose client carried the platform's GEMINI_API_KEY.

        None also when the model or the key is missing, or the provider is not in
        `PROVIDER_MODELS`; `build` builds exactly what this resolves, so a caller never
        holds a resolution the build then refuses.
        """
        if seller_ai is UNUSABLE_AI_CONFIG or (seller_ai is None and not platform_fallback):
            return None
        config = seller_ai or TenantAIConfig()

        provider = canonicalize_google_provider(config.provider or self._platform_defaults["provider"])
        platform_is_same_vendor = provider == self._platform_provider
        model = config.model or (self._platform_defaults["model"] if platform_is_same_vendor else None)
        api_key = config.api_key or (self._platform_defaults.get("api_key") if platform_is_same_vendor else None)

        if not (model and api_key) or provider not in PROVIDER_MODELS:
            return None
        return ResolvedAIConfig(provider=provider, model=model, api_key=api_key)

    def build(self, seller_ai: SellerAIConfig = None, *, platform_fallback: bool = True) -> Any | None:
        """The model AI runs on for `seller_ai`, or None when AI is not available for it.

        The one call every consumer makes: None means "skip the AI step", and a model
        means it can run. `platform_fallback` is `resolve`'s. The API key goes to the
        provider object, never to a global environment variable.

        A model pydantic-ai refuses to construct (``UserError``, or ``ImportError`` for a
        provider whose SDK is not installed) is also None: the configuration cannot run,
        which is the same answer as one `resolve` refuses, and the caller skips the AI step.
        Any other exception is a defect and propagates.
        """
        resolved = self.resolve(seller_ai, platform_fallback=platform_fallback)
        if resolved is None:
            return None
        if isinstance(seller_ai, TenantAIConfig) and seller_ai.logfire_token:
            configure_logfire(seller_ai.logfire_token)

        logger.debug("Creating Pydantic AI model: %s:%s", resolved.provider, resolved.model)
        model_path, provider_path = PROVIDER_MODELS[resolved.provider]
        try:
            provider = pkgutil.resolve_name(provider_path)(api_key=resolved.api_key)
            return pkgutil.resolve_name(model_path)(resolved.model, provider=provider)
        except (UserError, ImportError) as e:
            logger.warning("Cannot build the %s model; AI is unavailable: %s", resolved.provider, log_safe(e))
            return None


class AIProviderError(Exception):
    """The AI provider failed the call: unreachable, refused it, or answered unusably.

    Raised by :func:`run_agent` so a consumer that degrades when the model is unavailable
    (ranking, the policy check, order naming) catches this one type, while a defect in its
    own code still propagates.
    """


async def run_agent[OutputT](agent: "Agent[None, OutputT]", prompt: str) -> "AgentRunResult[OutputT]":
    """Run *agent* on *prompt*, raising :class:`AIProviderError` when the provider fails.

    The one place a provider failure is told apart from everything else. pydantic-ai wraps
    most provider failures in ``AgentRunError`` (``ModelHTTPError``, ``ModelAPIError``,
    ``UnexpectedModelBehavior``, ``UsageLimitExceeded``, ...) and reports a model it cannot
    use as ``UserError``. It does not wrap every SDK's transport: google-genai raises
    ``httpx.ConnectError`` unwrapped for an unreachable endpoint and
    ``httpx.RemoteProtocolError`` for a dropped connection, which ``is_transport_failure``
    recognises without importing httpx outside the egress seam. Anything else is not the
    provider and propagates unchanged.
    """
    try:
        return await agent.run(prompt)
    except Exception as e:
        if isinstance(e, AgentRunError | UserError) or is_transport_failure(e):
            raise AIProviderError(f"{type(e).__name__}: {e}") from e
        raise


def tenant_ai_enabled(tenant: "Tenant", *, platform_fallback: bool = True) -> bool:
    """Can an AI call run for this tenant row? The admin UI's one answer to that question.

    The same resolution every AI step acts on: what the seller configured
    (`TenantAIConfig.from_tenant`), with the platform fallback for a seller who configured
    nothing unless `platform_fallback` is False (the policy check's rule). Reading
    `tenant.gemini_api_key` directly answered a different question: the settings page and
    the creative-review page told a seller who had configured AI in the `ai_config` column
    that it needed a Gemini key, while the AI steps ran for it.
    """
    return (
        AIServiceFactory().resolve(TenantAIConfig.from_tenant(tenant), platform_fallback=platform_fallback) is not None
    )


@lru_cache(maxsize=1)
def get_factory() -> AIServiceFactory:
    """Get the singleton factory instance.

    Returns:
        AIServiceFactory instance
    """
    return AIServiceFactory()
