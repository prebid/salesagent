"""The checklist's optional "AI Features" task is done exactly when the seller's own AI can run.

The task used to read ``tenant.gemini_api_key``. The Admin UI's AI Services form writes
``tenant.ai_config``, so a seller who configured AI there saw the task incomplete while
the AI features ran for it (#2224). The task now asks ``tenant_ai_enabled`` with no
platform fallback: the seller's own configuration, resolved through
``TenantAIConfig.from_tenant``.

``_ai_features_task`` is the one builder both enumeration paths (per-tenant and bulk) call.
``TenantFactory.build()`` makes a real ORM ``Tenant`` without touching the database, so
``ai_config`` and ``gemini_api_key`` are read through the attributes production reads.
"""

from __future__ import annotations

import pytest

from src.services.setup_checklist_service import _ai_features_task
from tests.factories import TenantFactory
from tests.helpers.settings_injection import PlatformAICredential

_ANTHROPIC_AI_CONFIG = {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant-tenant"}

# What `src/admin/blueprints/settings.py` stores when the key field is left blank.
_KEYLESS_AI_CONFIG = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """``Tenant.gemini_api_key`` encrypts on write and decrypts on read."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


def _task(platform_key: str | None = None, **tenant_fields):
    tenant = TenantFactory.build(**tenant_fields)
    with PlatformAICredential(platform_key):
        return _ai_features_task(tenant.tenant_id, tenant)


def test_ai_config_only_tenant_completes_the_task():
    task = _task(ai_config=_ANTHROPIC_AI_CONFIG)

    assert task.is_complete
    assert task.key == "ai_provider_key"
    assert task.name == "AI Features"
    assert task.details == "AI features enabled"


def test_legacy_gemini_column_still_completes_the_task():
    assert _task(ai_config=None, gemini_api_key="legacy-tenant-key").is_complete


@pytest.mark.parametrize(
    ("ai_config", "platform_key"),
    [
        (None, None),
        (None, "platform-key"),
        (_KEYLESS_AI_CONFIG, None),
        (_KEYLESS_AI_CONFIG, "platform-key"),
    ],
    ids=[
        "nothing-configured",
        "nothing-configured-platform-key-is-not-the-sellers",
        "keyless-row",
        "keyless-row-platform-key-is-not-the-sellers",
    ],
)
def test_task_is_incomplete_without_a_usable_seller_configuration(ai_config, platform_key):
    task = _task(platform_key, ai_config=ai_config)

    assert not task.is_complete
    assert task.details == "Optional: Configure an AI provider API key for AI features"
