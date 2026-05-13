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
    async def test_sync_properties_unbound_entry_with_agent_url_uses_top_level(self):
        """Wonderstruck-class file: our agent is listed with a bare entry
        (no ``authorization_type``, no selector) and the file has a top-level
        ``properties[]`` block. The SDK's strict resolver returns [] for the
        bare entry, but the salesagent's permissive fallback recognizes the
        intent and binds products to all top-level properties. See
        salesagent#377 for the ``unbound`` state rationale.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        agent_url = "https://wonderstruck.sales-agent.example.com"
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": agent_url,
                    "authorized_for": "Authorized for display banners",
                },
            ],
            "properties": [
                {
                    "property_id": "main_site",
                    "property_type": "website",
                    "name": "Main site",
                    "identifiers": [{"type": "domain", "value": "wonderstruck.org"}],
                    "tags": ["sites"],
                },
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    # SDK strict resolver returns [] for the bare entry —
                    # the permissive fallback in _extract_properties then
                    # reads adagents_data["properties"] directly.
                    mock_by_agent.return_value = []
                    mock_tags.return_value = ["sites"]

                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1", ["wonderstruck.org"], agent_url=agent_url
                    )

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1, (
                        "Unbound entry should resolve permissively to top-level properties"
                    )
                    assert stats["properties_created"] == 1
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_unbound_branch_drops_properties_without_matching_domain(self):
        """Security gate on the permissive unbound branch: top-level
        properties must carry a ``type=domain`` identifier matching the
        publisher we're talking to. Without the gate, a publisher whose
        adagents.json we've added could bare-list our agent and claim
        arbitrary app bundle IDs, podcast GUIDs, or DOOH venue identifiers —
        none of which we can verify. Strict typed bindings don't need the
        gate (the publisher's authorization_type is the attestation), but
        permissive resolution has no such attestation.

        Three top-level properties:
        - mobile_app (bundle_id only — no domain identifier; dropped)
        - foreign_site (domain=other.example; dropped — domain mismatch)
        - main_site (domain=wonderstruck.org; kept)

        Only ``main_site`` survives the gate.
        """
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        agent_url = "https://wonderstruck.sales-agent.example.com"
        mock_adagents_data = {
            "authorized_agents": [
                {"url": agent_url, "authorized_for": "Display banners"},
            ],
            "properties": [
                {
                    "property_id": "main_site",
                    "property_type": "website",
                    "name": "Main site",
                    "identifiers": [{"type": "domain", "value": "wonderstruck.org"}],
                },
                {
                    "property_id": "mobile_app",
                    "property_type": "mobile_app",
                    "name": "Companion app",
                    "identifiers": [{"type": "bundle_id", "value": "com.wonderstruck.app"}],
                },
                {
                    "property_id": "foreign_site",
                    "property_type": "website",
                    "name": "Foreign site",
                    "identifiers": [{"type": "domain", "value": "other.example"}],
                },
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = []
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1", ["wonderstruck.org"], agent_url=agent_url
                    )

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1, (
                        "Permissive unbound branch must drop properties without "
                        "a matching domain identifier (mobile_app, foreign_site); "
                        "only main_site should pass the gate."
                    )

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_unbound_entry_no_top_level_yields_zero(self):
        """Raptive-class file (in its blocked variant): bare entry for our
        agent but no top-level ``properties[]``. Permissive fallback has
        nothing to bind to, so we sync zero properties. The publisher must
        add a ``properties[]`` block; the chip surfaces that as
        ``no_properties`` at the aao_lookup_service layer."""
        mock_db_patcher, mock_session = MockSetup.create_mock_db_session()

        def create_mock_scalars():
            mock_scalars = Mock()
            mock_scalars.first.return_value = None
            mock_scalars.all.return_value = []
            return mock_scalars

        mock_session.scalars.side_effect = lambda *args: create_mock_scalars()

        agent_url = "https://sales-agent.example.com"
        mock_adagents_data = {
            "authorized_agents": [
                {
                    "url": agent_url,
                    "authorized_for": "All properties",
                }
            ],
        }

        with patch("src.services.property_discovery_service.fetch_adagents", new_callable=AsyncMock) as mock_fetch:
            with patch("src.services.property_discovery_service.get_properties_by_agent") as mock_by_agent:
                with patch("src.services.property_discovery_service.get_all_tags") as mock_tags:
                    mock_fetch.return_value = mock_adagents_data
                    mock_by_agent.return_value = []
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents(
                        "tenant1", ["example.com"], agent_url=agent_url
                    )

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 0
                    assert len(stats["errors"]) == 0

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_mixed_bare_and_typed_entries(self):
        """File with one bare entry and one ``authorization_type: property_ids``
        entry: the SDK resolves only the typed entry, so we sync only the
        properties it references. The bare entry contributes nothing — there
        is no "any-agent-unrestricted → all properties" semantics in the spec.
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
                    "authorization_type": "property_ids",
                    "property_ids": ["main_site"],
                },
                {
                    "url": "https://bare-agent.example.com",
                    "authorized_for": "All properties",
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
                    # SDK get_all_properties: union of typed-agent resolutions,
                    # bare entry contributes nothing.
                    mock_props.return_value = [
                        {
                            "property_id": "main_site",
                            "property_type": "website",
                            "identifiers": [{"type": "domain", "value": "example.com"}],
                        }
                    ]
                    mock_tags.return_value = []

                    stats = await self.service.sync_properties_from_adagents("tenant1", ["example.com"])

                    assert stats["domains_synced"] == 1
                    assert stats["properties_found"] == 1, (
                        "Only typed-entry property syncs; bare entry contributes nothing"
                    )

        mock_db_patcher.stop()

    @pytest.mark.asyncio
    async def test_sync_properties_typed_agent_restricted_to_one_property(self):
        """When ``agent_url`` is provided, the SDK's per-agent resolution
        scopes the result to that agent's ``authorization_type`` + selector.
        Co-listed agents on the same file can't widen our scope."""
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
                    # Bare entry — schema-invalid; SDK resolves it to []
                    # and per-agent resolution is unaffected by co-listed agents.
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
                    # Must be 1 (only capital), NOT 3 (all top-level properties).
                    # Per-agent SDK resolution scopes the result to our entry's
                    # property_ids selector regardless of what co-listed agents
                    # declare.
                    assert stats["properties_found"] == 1
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
