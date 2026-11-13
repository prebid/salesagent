"""Pydantic AI service for multi-model LLM support.

This module provides:
- AIServiceFactory: Creates Pydantic AI models with tenant-aware configuration
- TenantAIConfig: Configuration schema for per-tenant AI settings
- configure_logfire: Optional Logfire observability setup

Usage:
    from src.services.ai import AIServiceFactory, TenantAIConfig

    # The model the seller's AI configuration runs on, or None when AI is unavailable
    model = AIServiceFactory().build(tenant.ai_config)

Configuration:
    Platform defaults come from environment variables:
    - PYDANTIC_AI_PROVIDER: Default provider (gemini, openai, anthropic, etc.)
    - PYDANTIC_AI_MODEL: Default model name
    - GEMINI_API_KEY, OPENAI_API_KEY, etc.: Provider API keys
    - LOGFIRE_TOKEN: Optional Logfire token for observability

    Per-tenant overrides are stored in the `ai_config` JSON column on Tenant.
"""

from src.services.ai.config import (
    ModelSettings,
    TenantAIConfig,
    get_platform_defaults,
)
from src.services.ai.factory import (
    AIServiceFactory,
    configure_logfire,
    get_factory,
)

__all__ = [
    "AIServiceFactory",
    "TenantAIConfig",
    "ModelSettings",
    "configure_logfire",
    "get_factory",
    "get_platform_defaults",
]
