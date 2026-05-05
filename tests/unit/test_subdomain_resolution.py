"""Unit tests for ``core.main._resolve_tenant`` host suffix stripping.

Verifies that the configured ``SALES_AGENT_DOMAIN`` environment variable
participates in the subdomain-strategy lookup so production tenants
(stored with ``subdomain='foo'``) resolve correctly via
``foo.${SALES_AGENT_DOMAIN}`` requests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
class TestResolveTenantSuffixStripping:
    """Suffix list expands with ``SALES_AGENT_DOMAIN`` so prod hosts resolve."""

    def _patched_session(self, expected_filter_kwargs: dict) -> MagicMock:
        """Build a mocked ``get_db_session`` ctx manager that asserts the
        expected ``filter_by`` kwargs were used in the lookup query.
        """
        session = MagicMock()
        scalars_result = MagicMock()
        scalars_result.first.return_value = None  # tenant lookup miss is fine
        session.scalars.return_value = scalars_result

        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = None

        get_db_session = MagicMock(return_value=ctx)

        captured: dict = {"filter_by_kwargs": None}

        original_filter_by = MagicMock

        def fake_select(*_args, **_kwargs):
            stmt = MagicMock()

            def fake_filter_by(**kwargs):
                captured["filter_by_kwargs"] = kwargs
                return stmt

            stmt.filter_by = fake_filter_by
            return stmt

        return get_db_session, captured, fake_select

    async def test_prod_subdomain_resolves_via_sales_agent_domain_env(self):
        from core import main as core_main

        get_db_session, captured, fake_select = self._patched_session({"subdomain": "acme"})

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            with patch("core.main.get_db_session", get_db_session):
                with patch("core.main.select", fake_select):
                    result = await core_main._resolve_tenant("acme.sales-agent.scope3.com")

        assert result is None  # tenant lookup miss (mock returns None)
        # The critical assertion: the lookup used subdomain="acme",
        # NOT virtual_host="acme.sales-agent.scope3.com". That proves the
        # SALES_AGENT_DOMAIN suffix was stripped.
        assert captured["filter_by_kwargs"] == {"subdomain": "acme", "is_active": True}

    async def test_unknown_suffix_falls_through_to_virtual_host_lookup(self):
        from core import main as core_main

        get_db_session, captured, fake_select = self._patched_session({})

        # SALES_AGENT_DOMAIN unset, custom host doesn't match any known
        # suffix → strategy 3 (virtual_host lookup).
        with patch.dict("os.environ", {}, clear=True):
            with patch("core.main.get_db_session", get_db_session):
                with patch("core.main.select", fake_select):
                    result = await core_main._resolve_tenant("custom.publisher.com")

        assert result is None
        assert captured["filter_by_kwargs"] == {
            "virtual_host": "custom.publisher.com",
            "is_active": True,
        }

    async def test_dev_subdomain_still_works_when_sales_agent_domain_set(self):
        """Setting SALES_AGENT_DOMAIN must not break the dev suffix list."""
        from core import main as core_main

        get_db_session, captured, fake_select = self._patched_session({"subdomain": "default"})

        with patch.dict("os.environ", {"SALES_AGENT_DOMAIN": "sales-agent.scope3.com"}):
            with patch("core.main.get_db_session", get_db_session):
                with patch("core.main.select", fake_select):
                    await core_main._resolve_tenant("default.localhost")

        assert captured["filter_by_kwargs"] == {"subdomain": "default", "is_active": True}
