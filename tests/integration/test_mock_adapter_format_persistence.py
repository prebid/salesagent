"""Regression test for H2: mock adapter writes .formats instead of .format_ids.

The mock adapter admin form handler sets `product_obj.formats = formats` (line 1403
of mock_ad_server.py), but the ORM column is `format_ids`. SQLAlchemy silently sets
`.formats` as a transient Python attribute that is never persisted.

This test verifies via ProductRepository that:
1. `update_fields(format_ids=...)` persists correctly (correct path)
2. `update_fields(formats=...)` raises ValueError (what the adapter SHOULD hit if it used the repo)

GH #1078 H2.
"""

import pytest

from src.core.database.repositories.product import ProductRepository
from tests.factories import TenantFactory
from tests.factories.product import ProductFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration]


class _BareEnv(IntegrationEnv):
    """Minimal integration env — just session + factory binding, no patches."""

    EXTERNAL_PATCHES = {}


class TestProductFormatFieldPersistence:
    """ProductRepository rejects the wrong attribute name and accepts the right one."""

    def test_update_format_ids_persists_via_repository(self, integration_db):
        """Updating format_ids through the repository persists to DB."""
        with _BareEnv() as env:
            tenant = TenantFactory(tenant_id="t-fmt")
            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            )

            repo = ProductRepository(env._session, tenant_id="t-fmt")
            new_formats = [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_16x9"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ]
            updated = repo.update_fields(product.product_id, format_ids=new_formats)
            assert updated is not None
            env._session.commit()

            # Re-read to confirm persistence
            reloaded = repo.get_by_id(product.product_id)
            assert reloaded is not None
            assert len(reloaded.format_ids) == 2
            assert reloaded.format_ids[0]["id"] == "video_16x9"
            assert reloaded.format_ids[1]["id"] == "display_728x90"

    def test_update_formats_raises_via_repository(self, integration_db):
        """Using 'formats' (wrong column name) through the repository raises ValueError.

        This is the bug: the mock adapter writes to .formats directly on the ORM
        object, bypassing the repository. If it used the repository, this would
        fail immediately instead of silently losing data.
        """
        with _BareEnv() as env:
            tenant = TenantFactory(tenant_id="t-fmt2")
            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            )

            repo = ProductRepository(env._session, tenant_id="t-fmt2")

            with pytest.raises(ValueError, match="Product has no attribute 'formats'"):
                repo.update_fields(product.product_id, formats=["video_16x9"])
