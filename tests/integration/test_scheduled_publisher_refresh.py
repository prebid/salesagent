"""The scheduled refresh ingests every publisher of every tenant.

``scripts/ops/refresh_publishers.py`` is what the crontab runs every six hours. It walks
every publisher row across tenants and ingests each in a unit of work of its own; a failed
fetch is recorded on that publisher and does not stop the others (managed-networks.mdx
:623-626, the stored document is kept). The publisher's file is served where the ingest
service binds ``fetch_adagents``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from adcp import AdagentsNotFoundError

from src.core.database.repositories.publisher import PublisherRepository
from src.services.publisher_ingest import website_document, website_property_id


@pytest.mark.requires_db
def test_the_scheduled_refresh_ingests_every_publisher_of_every_tenant(factory_session):
    from scripts.ops.refresh_publishers import main
    from tests.factories import TenantFactory
    from tests.factories.publisher import PublisherFactory

    first, second = TenantFactory(), TenantFactory()
    owners = {"news-psatest.com": first, "sports-psatest.com": first, "radio-psatest.com": second}
    for domain, tenant in owners.items():
        PublisherFactory(tenant=tenant, domain=domain)
    factory_session.commit()

    fetched: list[str] = []

    async def serve(publisher_domain: str, **_options: Any) -> dict[str, Any]:
        """Each publisher authorizes its own tenant for one website; sports has no file."""
        fetched.append(publisher_domain)
        if publisher_domain == "sports-psatest.com":
            raise AdagentsNotFoundError(publisher_domain)
        return website_document(
            owners[publisher_domain].agent_url,
            domain=publisher_domain,
            property_id=website_property_id(publisher_domain),
            name=publisher_domain,
        )

    with patch("src.services.publisher_ingest.fetch_adagents", new=serve):
        main()

    assert sorted(fetched) == sorted(owners)
    factory_session.expire_all()
    stored = {
        domain: PublisherRepository(factory_session, tenant.tenant_id).get(domain) for domain, tenant in owners.items()
    }
    assert {domain: publisher.fetch_status for domain, publisher in stored.items() if publisher} == {
        "news-psatest.com": "ok",
        "sports-psatest.com": "not_found",
        "radio-psatest.com": "ok",
    }
    assert {domain: len(publisher.properties) for domain, publisher in stored.items() if publisher} == {
        "news-psatest.com": 1,
        "sports-psatest.com": 0,
        "radio-psatest.com": 1,
    }
