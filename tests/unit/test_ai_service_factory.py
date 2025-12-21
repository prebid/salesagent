"""Tests for AI service factory and configuration."""

import os
from unittest.mock import patch

import pytest

from src.services.ai import (
    AIServiceFactory,
    ModelSettings,
    TenantAIConfig,
    build_model_string,
    get_platform_defaults,
)


class TestTenantAIConfig:
    """Tests for TenantAIConfig model."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = TenantAIConfig()
        assert config.provider is None
        assert config.model is None
        assert config.api_key is None
        assert config.logfire_token is None
        assert config.settings.temperature == 0.3
        assert config.settings.timeout == 30

    def test_parse_from_dict(self):
        """Config can be parsed from dict (database JSON)."""
        config = TenantAIConfig.model_validate(
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "settings": {"temperature": 0.5}}
        )
        assert config.provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"
        assert config.settings.temperature == 0.5

    def test_extra_fields_ignored(self):
        """Unknown fields are ignored for forward compatibility."""
        config = TenantAIConfig.model_validate(
            {
                "provider": "gemini",
                "future_field": "some value",
            }
        )
        assert config.provider == "gemini"
        assert not hasattr(config, "future_field")


class TestModelSettings:
    """Tests for ModelSettings model."""

    def test_temperature_bounds(self):
        """Temperature must be between 0 and 2."""
        with pytest.raises(ValueError):
            ModelSettings(temperature=-0.1)
        with pytest.raises(ValueError):
            ModelSettings(temperature=2.1)

        # Valid values
        ModelSettings(temperature=0)
        ModelSettings(temperature=2)
        ModelSettings(temperature=1.5)

    def test_timeout_positive(self):
        """Timeout must be positive."""
        with pytest.raises(ValueError):
            ModelSettings(timeout=0)
        with pytest.raises(ValueError):
            ModelSettings(timeout=-1)


class TestBuildModelString:
    """Tests for build_model_string function."""

    def test_gemini_provider(self):
        """Gemini uses google-gla prefix."""
        result = build_model_string("gemini", "gemini-2.0-flash")
        assert result == "google-gla:gemini-2.0-flash"

    def test_openai_provider(self):
        """OpenAI uses openai prefix."""
        result = build_model_string("openai", "gpt-4o")
        assert result == "openai:gpt-4o"

    def test_anthropic_provider(self):
        """Anthropic uses anthropic prefix."""
        result = build_model_string("anthropic", "claude-sonnet-4-20250514")
        assert result == "anthropic:claude-sonnet-4-20250514"


class TestGetPlatformDefaults:
    """Tests for get_platform_defaults function."""

    def test_defaults_from_env(self):
        """Platform defaults come from environment variables."""
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "openai",
                "PYDANTIC_AI_MODEL": "gpt-4o",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        ):
            defaults = get_platform_defaults()
            assert defaults["provider"] == "openai"
            assert defaults["model"] == "gpt-4o"
            assert defaults["api_key"] == "test-key"

    def test_defaults_fallback(self):
        """Defaults fall back to gemini when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            defaults = get_platform_defaults()
            assert defaults["provider"] == "gemini"
            assert defaults["model"] == "gemini-2.0-flash"


class TestAIServiceFactory:
    """Tests for AIServiceFactory class."""

    def test_create_model_with_platform_defaults(self):
        """Factory creates model using platform defaults."""
        from pydantic_ai.models.google import GoogleModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            model = factory.create_model()
            # Now returns a Model instance instead of a string
            assert isinstance(model, GoogleModel)

    def test_create_model_with_tenant_config(self):
        """Factory uses tenant config over platform defaults."""
        from pydantic_ai.models.anthropic import AnthropicModel

        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "platform-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            tenant_config = {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": "tenant-key",
            }
            model = factory.create_model(tenant_ai_config=tenant_config)
            # Returns AnthropicModel for anthropic provider
            assert isinstance(model, AnthropicModel)

    def test_create_model_with_override(self):
        """Explicit overrides take highest priority."""
        from pydantic_ai.models.openai import OpenAIChatModel

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            tenant_config = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
            model = factory.create_model(
                tenant_ai_config=tenant_config,
                provider_override="openai",
                model_override="gpt-4o",
            )
            # Override to openai returns OpenAIChatModel
            assert isinstance(model, OpenAIChatModel)

    def test_get_effective_config_platform(self):
        """Effective config shows platform source when no tenant config."""
        with patch.dict(
            os.environ,
            {
                "PYDANTIC_AI_PROVIDER": "gemini",
                "PYDANTIC_AI_MODEL": "gemini-2.0-flash",
                "GEMINI_API_KEY": "test-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            effective = factory.get_effective_config()
            assert effective["provider"] == "gemini"
            assert effective["model"] == "gemini-2.0-flash"
            assert effective["has_api_key"] is True
            assert effective["source"] == "platform"

    def test_get_effective_config_tenant(self):
        """Effective config shows tenant source when tenant config provided."""
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "platform-key",
            },
            clear=False,
        ):
            factory = AIServiceFactory()
            tenant_config = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
            effective = factory.get_effective_config(tenant_ai_config=tenant_config)
            assert effective["provider"] == "anthropic"
            assert effective["model"] == "claude-sonnet-4-20250514"
            assert effective["source"] == "tenant"

    def test_model_receives_api_key_via_provider(self):
        """Factory passes API key directly via Provider, not environment variables."""
        from pydantic_ai.models.openai import OpenAIChatModel

        # Clear the environment to prove we're not relying on env vars
        with patch.dict(os.environ, {}, clear=True):
            factory = AIServiceFactory()
            tenant_config = {"provider": "openai", "model": "gpt-4o", "api_key": "tenant-openai-key"}
            model = factory.create_model(tenant_ai_config=tenant_config)
            # Model is created successfully
            assert isinstance(model, OpenAIChatModel)
            # API key is NOT set in environment (we pass it directly to Provider)
            assert os.environ.get("OPENAI_API_KEY") is None
