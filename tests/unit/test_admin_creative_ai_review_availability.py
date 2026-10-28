"""The creative-management page and the AI review impl must answer the same question.

``review_creatives`` renders a ``has_ai_review`` flag that decides whether the page even
offers AI review, and ``_perform_ai_review`` decides whether one runs. They read the same
tenant one click apart, so they cannot be allowed to disagree — and they did: the page
tested ``tenant.gemini_api_key`` (the legacy column) while the impl resolved through
``TenantAIConfig.from_tenant`` (``ai_config`` first, the legacy column second). A seller
who configured AI in the Admin UI — which writes ``ai_config``, not that column — was
shown "AI review unavailable" by a page whose impl would happily have run one.

Both now go through ``_tenant_ai_enabled``, the single owner of "can an AI call actually
run for this tenant".

``TenantFactory.build()`` makes a real ORM ``Tenant`` without touching the database, so
``ai_config`` / ``gemini_api_key`` / ``creative_review_criteria`` are read through the
same attributes production reads.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.admin.blueprints.creatives import _tenant_ai_enabled, review_creatives
from tests.factories import TenantFactory

# A tenant configured the way the Admin UI writes it: provider, model and key on
# `ai_config`, nothing in the legacy `gemini_api_key` column.
_ADMIN_UI_AI_CONFIG = {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant-tenant"}

# What `src/admin/blueprints/settings.py` stores when the seller leaves the key field
# blank — and tells them "AI features will be disabled" as it does.
_KEYLESS_AI_CONFIG = {"provider": "google", "model": "gemini-2.0-flash"}


@pytest.fixture(autouse=True)
def _tenant_credentials_only(monkeypatch):
    """Remove the platform credentials, and supply the key the tenant column needs.

    ``is_ai_enabled`` counts the platform key, so with ``GEMINI_API_KEY`` left in place —
    ``tests/conftest.py`` sets it for every test — every tenant looks AI-enabled and the
    keyless-row case below would pass no matter what production resolved.

    ``Tenant.gemini_api_key`` encrypts on write and decrypts on read, so the legacy-column
    case needs an ``ENCRYPTION_KEY``. A per-run Fernet key round-trips through the real
    encrypt/decrypt path rather than stubbing it out.
    """
    from cryptography.fernet import Fernet

    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "PYDANTIC_AI_PROVIDER", "PYDANTIC_AI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


def _render_page(tenant):
    """Call ``review_creatives`` with its access decorator off and capture the template kwargs.

    ``__wrapped__`` is the undecorated view — ``require_tenant_access`` needs a Flask
    request context and grades nothing this test is about. The UoW is a stub with no
    creatives, so the only thing the page computes is the tenant flag set.
    """
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.tenant_config.get_tenant.return_value = tenant
    uow.creatives.admin_list_all.return_value = []

    with (
        patch("src.admin.blueprints.creatives.AdminCreativeUoW", return_value=uow),
        patch("src.admin.blueprints.creatives.render_template") as render,
    ):
        review_creatives.__wrapped__(tenant_id=tenant.tenant_id)

    return render.call_args.kwargs


class TestPageOffersAIReviewExactlyWhenTheImplWouldRunOne:
    def test_ai_config_only_tenant_is_offered_ai_review(self):
        """The regression: configured through the Admin UI, told it was unavailable."""
        tenant = TenantFactory.build(
            tenant_id="t-ai-config", ai_config=_ADMIN_UI_AI_CONFIG, creative_review_criteria="no nudity"
        )

        assert _render_page(tenant)["has_ai_review"] is True
        # ...and the impl agrees, which is the whole point of sharing the owner.
        assert _tenant_ai_enabled(tenant) is True

    def test_legacy_gemini_key_tenant_is_still_offered_ai_review(self):
        """The pre-existing behaviour the page had, preserved through the new owner."""
        tenant = TenantFactory.build(
            tenant_id="t-legacy", gemini_api_key="AIza-legacy-key", creative_review_criteria="no nudity"
        )

        assert _render_page(tenant)["has_ai_review"] is True

    def test_key_left_blank_in_settings_is_not_offered_ai_review(self):
        """A provider/model row with no key is not a configuration, so the page says so."""
        tenant = TenantFactory.build(
            tenant_id="t-keyless", ai_config=_KEYLESS_AI_CONFIG, creative_review_criteria="no nudity"
        )

        assert _render_page(tenant)["has_ai_review"] is False
        assert _tenant_ai_enabled(tenant) is False

    def test_configured_ai_without_review_criteria_is_not_offered_ai_review(self):
        """The second condition still stands: the impl refuses without criteria."""
        tenant = TenantFactory.build(
            tenant_id="t-no-criteria", ai_config=_ADMIN_UI_AI_CONFIG, creative_review_criteria=None
        )

        assert _render_page(tenant)["has_ai_review"] is False
