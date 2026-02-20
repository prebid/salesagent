"""Unit tests for property discovery service.

Tests the property discovery service that fetches and caches properties/tags
from publisher adagents.json files using the adcp library.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from adcp import AdagentsNotFoundError, AdagentsTimeoutError, AdagentsValidationError

from src.services.property_discovery_service import PropertyDiscoveryService


class MockSetup:
    """Centralized mock setup to reduce duplicate mocking."""

    @staticmethod
    def create_mock_db_session():
        """Create mock database session (SQLAlchemy 2.0 compatible)."""
        mock_session = Mock()
        mock_db_session_patcher = patch("src.services.property_discovery_service.get_db_session")
        mock_db_session = mock_db_session_patcher.start()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock SQLAlchemy 2.0 pattern: session.scalars(stmt).all()
        # Must return empty list (iterable) not Mock object
        mock_scalars = Mock()
        mock_scalars.all.return_value = []
        mock_scalars.first.return_value = None
        mock_session.scalars.return_value = mock_scalars
        mock_session.execute.return_value.all.return_value = []

        return mock_db_session_patcher, mock_session


class TestPropertyDiscoveryService:
    """Test PropertyDiscoveryService functionality.

    These tests focus on the service logic. The adcp library's
    adagents.json fetching and parsing are tested in the adcp library.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.service = PropertyDiscoveryService()

    @pytest.mark.asyncio
    async def test_sync_properties_success(self):
        """Test successful property and tag sync from adagents.json."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists (no existing properties/tags)
        # This mock needs to handle both .first() and .all() calls
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Mock adagents.json data
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "name": "Example Site",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                            "tags": ["premium", "news"],
                        }
                    ],
                }
            ]
        }

        # Mock adcp library functions
        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_props.return_value = [
                        {
                            "property_type": "website",
                            "name": "Example Site",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                            "tags": ["premium", "news"],
                        }
                    ]
                    mock_tags.return_value = ["premium", "news"]

                    # Test sync
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    # Verify results
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1
                    assert stats["tags_found"] == 2
                    assert stats["properties_created"] == 1
                    assert stats["tags_created"] == 2
                    assert len(stats["errors"]) == 0

                    # Verify adcp library called
                    mock_fetch.assert_called_once_with("example.com")
                    mock_props.assert_called_once_with(mock_adagents_data)
                    mock_tags.assert_called_once_with(mock_adagents_data)

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_adagents_not_found(self):
        """Test handling of missing adagents.json (404)."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock fetch to raise not found error
        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsNotFoundError("404 Not Found")

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert stats["properties_found"] == 0
            assert len(stats["errors"]) == 1
            assert "adagents.json not found (404)" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_timeout(self):
        """Test handling of timeout when fetching adagents.json."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsTimeoutError("https://example.com/.well-known/adagents.json", 5.0)

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert len(stats["errors"]) == 1
            assert "timeout" in stats["errors"][0].lower()

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_invalid_json(self):
        """Test handling of invalid adagents.json format."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = AdagentsValidationError("Missing authorized_agents field")

            stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

            assert stats["domains_synced"] == 0
            assert len(stats["errors"]) == 1
            assert "Invalid adagents.json" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_no_domains(self):
        """Test handling when no publisher domains found."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock empty result from database query
        mock_session.execute.return_value.all.return_value = []

        stats = await self.service.sync_properties_from_adagents("tenant1", None)

        assert stats["domains_synced"] == 0
        assert len(stats["errors"]) == 1
        assert "No publisher domains found" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_multiple_domains(self):
        """Test syncing properties from multiple domains.

        Each domain's adagents.json returns a property matching that domain.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Each domain has its own adagents.json with a matching property
        adagents_com = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ],
                }
            ]
        }
        adagents_org = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.org"}],
                        }
                    ],
                }
            ]
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    # Return different adagents data per domain
                    mock_fetch.side_effect = [adagents_com, adagents_org]
                    mock_props.side_effect = [
                        [
                            {
                                "property_type": "website",
                                "identifiers": [{"type": "domain", "value": "example.com"}],
                            }
                        ],
                        [
                            {
                                "property_type": "website",
                                "identifiers": [{"type": "domain", "value": "example.org"}],
                            }
                        ],
                    ]
                    mock_tags.return_value = []

                    # Sync from multiple domains
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com", "example.org"])

                    assert stats["domains_synced"] == 2
                    assert stats["properties_found"] == 2
                    assert mock_fetch.call_count == 2

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_partial_failure(self):
        """Test syncing when some domains fail but others succeed."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ],
                }
            ]
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    # First domain succeeds, second fails
                    mock_fetch.side_effect = [
                        mock_adagents_data,
                        AdagentsNotFoundError("404 Not Found"),
                    ]
                    mock_props.return_value = [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com", "example.org"])

                    # One success, one failure
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1
                    assert len(stats["errors"]) == 1
                    assert "example.org" in stats["errors"][0]

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_unrestricted_agent_all_properties(self):
        """Test syncing when agent has no property restrictions (access to all properties).

        Per AdCP spec: if property_ids/property_tags/properties/publisher_properties
        are all missing/empty, agent has access to ALL properties from that publisher.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        # Mock database queries to return empty lists
        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Mock adagents.json with unrestricted agent (no property_ids field)
        # AND top-level properties array
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://wonderstruck.sales-agent.example.com",
                    "authorized_for": "Authorized for display banners",
                    # Note: No property_ids, property_tags, properties, or publisher_properties fields
                    # This means access to ALL properties from this publisher
                }
            ],
            "properties": [
                {
                    "property_id": "main_site",
                    "property_type": "website",
                    "name": "Main site",
                    "identifiers": [{"type": "domain", "value": "wonderstruck.org"}],
                    "tags": ["sites"],
                },
                {
                    "property_id": "mobile_app",
                    "property_type": "mobile_app",
                    "name": "Mobile App",
                    "identifiers": [{"type": "bundle_id", "value": "com.wonderstruck.app"}],
                    "tags": ["apps"],
                },
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    # get_all_properties returns empty list (no per-agent properties)
                    mock_props.return_value = []
                    mock_tags.return_value = ["sites", "apps"]

                    # Test sync
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["wonderstruck.org"])

                    # Verify results - should sync ALL top-level properties
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 2, "Should sync both top-level properties"
                    assert stats["tags_found"] == 2
                    assert stats["properties_created"] == 2
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_unrestricted_agent_no_top_level_properties(self):
        """Test unrestricted agent when no top-level properties exist (edge case)."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Unrestricted agent but no top-level properties
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://sales-agent.example.com",
                    "authorized_for": "All properties",
                    # No property restrictions
                }
            ],
            # No top-level properties array
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_props.return_value = []
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    # Should handle gracefully - no properties to sync
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 0
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_mixed_restricted_unrestricted(self):
        """Test adagents.json with both restricted and unrestricted agents.

        If ANY agent is unrestricted, we should sync all top-level properties.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://restricted-agent.example.com",
                    "authorized_for": "Only main site",
                    "property_ids": ["main_site"],  # Restricted to specific property
                },
                {
                    "url": "https://unrestricted-agent.example.com",
                    "authorized_for": "All properties",
                    # No restrictions - access to all
                },
            ],
            "properties": [
                {
                    "property_id": "main_site",
                    "property_type": "website",
                    "identifiers": [{"type": "domain", "value": "example.com"}],
                },
                {
                    "property_id": "mobile_app",
                    "property_type": "mobile_app",
                    "identifiers": [{"type": "bundle_id", "value": "com.example.app"}],
                },
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_props.return_value = [
                        {
                            "property_id": "main_site",
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    # Should sync ALL properties (because of unrestricted agent)
                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 2, "Should sync all properties due to unrestricted agent"

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_unrestricted_check_scoped_to_our_agent(self):
        """Test that unrestricted agent check is scoped to our agent when agent_url is provided.

        Scenario: adagents.json has two agents:
        - A media agency with no restrictions (unrestricted)
        - Our agent restricted to property_ids: ["capital"]

        When agent_url identifies our restricted agent, we should only get "capital",
        NOT all top-level properties. The media agency's unrestricted status should
        not override our agent's restrictions.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://media-agency.example.com",
                    "authorized_for": "Full portfolio management",
                    # No restrictions — unrestricted agent
                },
                {
                    "url": "https://our-agent.example.com",
                    "authorization_type": "property_ids",
                    "property_ids": ["capital"],
                },
            ],
            "properties": [
                {
                    "property_id": "capital",
                    "property_type": "website",
                    "name": "Capital",
                    "identifiers": [{"type": "domain", "value": "capital.fr"}],
                },
                {
                    "property_id": "geo",
                    "property_type": "website",
                    "name": "Geo",
                    "identifiers": [{"type": "domain", "value": "geo.fr"}],
                },
                {
                    "property_id": "voici",
                    "property_type": "website",
                    "name": "Voici",
                    "identifiers": [{"type": "domain", "value": "voici.fr"}],
                },
            ],
        }

        # get_properties_by_agent correctly returns only "capital" for our agent
        resolved_properties = [
            {
                "property_id": "capital",
                "property_type": "website",
                "name": "Capital",
                "identifiers": [{"type": "domain", "value": "capital.fr"}],
            },
        ]

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = resolved_properties
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1",
                        ["capital.fr"],
                        agent_url="https://our-agent.example.com",
                    )

                    assert stats["domains_synced"] == 1
                    # Must be 1 (only capital), NOT 3 (all top-level properties)
                    assert stats["properties_found"] == 1, (
                        "Our restricted agent should only get 'capital', not all properties. "
                        "The media agency's unrestricted status must not override our restrictions."
                    )
                    assert stats["properties_created"] == 1
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_property_ids_authorization_with_agent_url(self):
        """Test property_ids authorization resolves top-level properties when agent_url is provided.

        This is the key bug fix: when publishers use authorization_type: "property_ids"
        (properties defined at top level, agents reference by ID), the old code using
        get_all_properties() returned empty. With agent_url, we use get_properties_by_agent()
        which correctly resolves property_ids references.

        Also verifies domain filtering: only properties matching the publisher domain
        (or without domain identifiers, like mobile apps) are synced.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Simulates Prisma Media-style adagents.json: one file with properties for many domains
        # Fetched from capital.fr, but contains properties for geo.fr, cotemaison.fr, etc.
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://our-agent.example.com",
                    "authorized_for": "Display advertising",
                    "authorization_type": "property_ids",
                    "property_ids": ["capital", "geo", "app_ios"],
                }
            ],
            "properties": [
                {
                    "property_id": "capital",
                    "property_type": "website",
                    "name": "Capital",
                    "identifiers": [{"type": "domain", "value": "capital.fr"}],
                    "tags": ["news", "finance"],
                },
                {
                    "property_id": "geo",
                    "property_type": "website",
                    "name": "Geo",
                    "identifiers": [{"type": "domain", "value": "geo.fr"}],
                    "tags": ["news"],
                },
                {
                    "property_id": "app_ios",
                    "property_type": "mobile_app",
                    "name": "Capital iOS",
                    "identifiers": [{"type": "bundle_id", "value": "com.capital.ios"}],
                    "tags": ["apps"],
                },
            ],
        }

        # get_properties_by_agent returns ALL 3 properties the agent is authorized for
        resolved_properties = [
            {
                "property_id": "capital",
                "property_type": "website",
                "name": "Capital",
                "identifiers": [{"type": "domain", "value": "capital.fr"}],
                "tags": ["news", "finance"],
            },
            {
                "property_id": "geo",
                "property_type": "website",
                "name": "Geo",
                "identifiers": [{"type": "domain", "value": "geo.fr"}],
                "tags": ["news"],
            },
            {
                "property_id": "app_ios",
                "property_type": "mobile_app",
                "name": "Capital iOS",
                "identifiers": [{"type": "bundle_id", "value": "com.capital.ios"}],
                "tags": ["apps"],
            },
        ]

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = resolved_properties
                    mock_tags.return_value = ["news", "finance", "apps"]

                    # Sync for capital.fr only - should filter out geo.fr property
                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1",
                        ["capital.fr"],
                        agent_url="https://our-agent.example.com",
                    )

                    assert stats["domains_synced"] == 1
                    # Only 2: capital.fr website + mobile app (no domain identifier = kept)
                    # geo.fr property is filtered out because it doesn't match capital.fr
                    assert stats["properties_found"] == 2, (
                        "Should only sync properties matching publisher domain "
                        "(capital.fr website + mobile app without domain)"
                    )
                    assert stats["properties_created"] == 2
                    assert len(stats["errors"]) == 0

                    mock_by_agent.assert_called_once_with(mock_adagents_data, "https://our-agent.example.com")

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_multi_domain_adagents_each_gets_own_property(self):
        """Test that a multi-domain adagents.json correctly links each property to its publisher.

        Real-world case: Prisma Media hosts one adagents.json at creas.prismamediadigital.com
        with properties for capital.fr, geo.fr, cotemaison.fr, etc. When syncing multiple
        publisher domains, each should only get its own property.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # All domains serve the same adagents.json (Prisma Media pattern)
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://our-agent.example.com",
                    "authorization_type": "property_ids",
                    "property_ids": ["capital", "geo"],
                }
            ],
            "properties": [
                {
                    "property_id": "capital",
                    "property_type": "website",
                    "name": "Capital",
                    "identifiers": [{"type": "domain", "value": "capital.fr"}],
                },
                {
                    "property_id": "geo",
                    "property_type": "website",
                    "name": "Geo",
                    "identifiers": [{"type": "domain", "value": "geo.fr"}],
                },
            ],
        }

        all_resolved = [
            {
                "property_id": "capital",
                "property_type": "website",
                "name": "Capital",
                "identifiers": [{"type": "domain", "value": "capital.fr"}],
            },
            {
                "property_id": "geo",
                "property_type": "website",
                "name": "Geo",
                "identifiers": [{"type": "domain", "value": "geo.fr"}],
            },
        ]

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = all_resolved
                    mock_tags.return_value = []

                    # Sync for both domains
                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1",
                        ["capital.fr", "geo.fr"],
                        agent_url="https://our-agent.example.com",
                    )

                    assert stats["domains_synced"] == 2
                    # Each domain gets exactly 1 property (its own)
                    assert stats["properties_found"] == 2
                    assert stats["properties_created"] == 2
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_property_tags_authorization_with_agent_url(self):
        """Test property_tags authorization resolves top-level properties when agent_url is provided."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://our-agent.example.com",
                    "authorized_for": "Premium inventory",
                    "property_tags": ["premium"],
                }
            ],
            "properties": [
                {
                    "property_id": "site_premium",
                    "property_type": "website",
                    "name": "Premium Site",
                    "identifiers": [{"type": "domain", "value": "premium.example.com"}],
                    "tags": ["premium"],
                },
                {
                    "property_id": "site_basic",
                    "property_type": "website",
                    "name": "Basic Site",
                    "identifiers": [{"type": "domain", "value": "basic.example.com"}],
                    "tags": ["basic"],
                },
            ],
        }

        # get_properties_by_agent filters by tag, only returns the premium one
        resolved_properties = [
            {
                "property_id": "site_premium",
                "property_type": "website",
                "name": "Premium Site",
                "identifiers": [{"type": "domain", "value": "premium.example.com"}],
                "tags": ["premium"],
            },
        ]

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = resolved_properties
                    mock_tags.return_value = ["premium"]

                    # Publisher domain matches the property's domain identifier
                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1",
                        ["premium.example.com"],
                        agent_url="https://our-agent.example.com",
                    )

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1, "Should only resolve tag-matched properties"
                    assert stats["properties_created"] == 1
                    assert len(stats["errors"]) == 0

                    mock_by_agent.assert_called_once_with(mock_adagents_data, "https://our-agent.example.com")

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_publisher_properties_filtered_out(self):
        """Test that publisher_properties selectors (no property_type) are filtered out."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://our-agent.example.com",
                    "publisher_properties": [{"publisher_domain": "other-publisher.com"}],
                }
            ],
        }

        # get_properties_by_agent returns publisher_properties selectors (no property_type)
        resolved_properties = [
            {"publisher_domain": "other-publisher.com"},  # No property_type = cross-domain ref
        ]

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = resolved_properties
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1",
                        ["example.com"],
                        agent_url="https://our-agent.example.com",
                    )

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 0, "publisher_properties selectors should be filtered out"
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_property_ids_without_agent_url_finds_zero(self):
        """Regression test: property_ids authorization without agent_url returns 0 properties.

        This demonstrates the original bug: get_all_properties() only reads inline
        agent.properties arrays. When publishers use authorization_type: "property_ids"
        (properties at top level, agents reference by ID), get_all_properties() returns
        nothing because there are no inline properties.

        Without this test, the agent_url plumbing could be removed and all other tests
        would still pass.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        # Prisma Media-style adagents.json: properties at top level, agent references by ID
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://our-agent.example.com",
                    "authorization_type": "property_ids",
                    "property_ids": ["capital"],
                }
            ],
            "properties": [
                {
                    "property_id": "capital",
                    "property_type": "website",
                    "name": "Capital",
                    "identifiers": [{"type": "domain", "value": "capital.fr"}],
                },
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_all_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    # get_all_properties returns [] because there are no inline agent.properties
                    mock_all_props.return_value = []
                    mock_tags.return_value = []

                    # Without agent_url, the old code path is used
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["capital.fr"])

                    # Bug: 0 properties found because get_all_properties can't resolve property_ids
                    assert stats["properties_found"] == 0, (
                        "Without agent_url, property_ids authorization returns 0 properties "
                        "(this is the original bug — use agent_url to fix)"
                    )

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_without_agent_url_uses_get_all_properties(self):
        """Test backward compatibility: without agent_url, still uses get_all_properties."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": "https://agent.example.com",
                    "properties": [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ],
                }
            ]
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_all_properties") as mock_all_props:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_all_props.return_value = [
                        {
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    # No agent_url = backward compatible path
                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    assert stats["properties_found"] == 1
                    # Verify get_all_properties was used (not get_properties_by_agent)
                    mock_all_props.assert_called_once_with(mock_adagents_data)

        mock_db_patcher.stop()

    def test_sync_properties_sync_wrapper(self):
        """Test that sync wrapper calls async implementation with all parameters."""
        with patch.object(self.service, "sync_properties_from_adagents", new_callable=AsyncMock) as mock_async:
            mock_async.return_value = {
                "domains_synced": 1,
                "properties_found": 1,
                "tags_found": 0,
                "properties_created": 1,
                "properties_updated": 0,
                "tags_created": 0,
                "errors": [],
                "dry_run": False,
            }

            result = self.service.sync_properties_from_adagents_sync("tenant1", ["example.com"])

            assert result["domains_synced"] == 1
            mock_async.assert_called_once_with("tenant1", ["example.com"], False, agent_url=None)

    def test_sync_properties_sync_wrapper_passes_agent_url(self):
        """Test that sync wrapper passes agent_url through to async implementation."""
        with patch.object(self.service, "sync_properties_from_adagents", new_callable=AsyncMock) as mock_async:
            mock_async.return_value = {
                "domains_synced": 1,
                "properties_found": 3,
                "tags_found": 0,
                "properties_created": 3,
                "properties_updated": 0,
                "tags_created": 0,
                "errors": [],
                "dry_run": False,
            }

            result = self.service.sync_properties_from_adagents_sync(
                "tenant1", ["example.com"], agent_url="https://our-agent.example.com"
            )

            assert result["properties_found"] == 3
            mock_async.assert_called_once_with(
                "tenant1", ["example.com"], False, agent_url="https://our-agent.example.com"
            )
