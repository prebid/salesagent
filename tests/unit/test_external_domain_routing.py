"""Test external domain routing via Approximated."""

from unittest.mock import Mock, patch

from src.admin.blueprints.core import get_tenant_from_hostname


class TestExternalDomainRouting:
    """Test that external domains (via Approximated) route to tenant home page instead of signup."""

    def test_get_tenant_from_hostname_with_approximated_header(self):
        """Test tenant lookup via Apx-Incoming-Host header."""
        from src.admin.app import create_app

        app, _ = create_app()

        with app.test_request_context(
            "/",
            headers={
                "Host": "backend.example.com",
                "Apx-Incoming-Host": "sales-agent.accuweather.com",
            },
        ):
            with patch("src.admin.blueprints.core.get_db_session") as mock_db:
                # Mock the database session
                mock_session = Mock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Mock tenant object
                mock_tenant = Mock()
                mock_tenant.tenant_id = "accuweather"
                mock_tenant.name = "AccuWeather"
                mock_tenant.subdomain = "accuweather"
                mock_tenant.virtual_host = "sales-agent.accuweather.com"

                # Mock the database query
                mock_scalars = Mock()
                mock_scalars.first.return_value = mock_tenant
                mock_session.scalars.return_value = mock_scalars

                # Call the function
                result = get_tenant_from_hostname()

                # Verify tenant was returned
                assert result is not None
                assert result.tenant_id == "accuweather"
                assert result.virtual_host == "sales-agent.accuweather.com"

    def test_get_tenant_from_hostname_no_tenant_configured(self):
        """Test that None is returned when no tenant is configured for external domain."""
        from src.admin.app import create_app

        app, _ = create_app()

        with app.test_request_context(
            "/",
            headers={
                "Host": "backend.example.com",
                "Apx-Incoming-Host": "unknown-domain.com",
            },
        ):
            with patch("src.admin.blueprints.core.get_db_session") as mock_db:
                # Mock the database session
                mock_session = Mock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Mock the database query - no tenant found
                mock_scalars = Mock()
                mock_scalars.first.return_value = None
                mock_session.scalars.return_value = mock_scalars

                # Call the function
                result = get_tenant_from_hostname()

                # Verify None is returned
                assert result is None

    def test_index_route_external_domain_with_tenant(self):
        """Test that external domain with configured tenant shows agent landing page."""
        from src.admin.app import create_app

        app, _ = create_app()

        with app.test_client() as client:
            # Mock single-tenant mode to return False (we're testing multi-tenant routing)
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=False):
                # Mock the centralized routing function
                with patch("src.core.domain_routing.route_landing_page") as mock_route:
                    with patch("src.landing.landing_page.generate_tenant_landing_page") as mock_landing:
                        # Mock routing result with tenant
                        from src.core.domain_routing import RoutingResult

                        tenant_dict = {
                            "tenant_id": "accuweather",
                            "name": "AccuWeather",
                            "subdomain": "accuweather",
                            "virtual_host": "sales-agent.accuweather.com",
                        }
                        mock_route.return_value = RoutingResult(
                            "custom_domain", tenant_dict, "sales-agent.accuweather.com"
                        )

                        # Mock landing page generation
                        mock_landing.return_value = "<html><body>Agent Landing Page</body></html>"

                        # Make request with Approximated headers
                        response = client.get(
                            "/",
                            headers={
                                "Host": "backend.example.com",
                                "Apx-Incoming-Host": "sales-agent.accuweather.com",
                            },
                        )

                        # Should show agent landing page (200) with MCP/A2A endpoints
                        assert response.status_code == 200
                        assert b"Agent Landing Page" in response.data
                        # Verify landing page was called with correct parameters
                        mock_landing.assert_called_once()
                        call_args = mock_landing.call_args
                        assert call_args[0][0]["tenant_id"] == "accuweather"
                        assert call_args[0][1] == "sales-agent.accuweather.com"

    def test_index_route_external_domain_no_tenant(self):
        """Test that external domain without configured tenant shows signup landing page."""
        from src.admin.app import create_app

        app, _ = create_app()

        with app.test_client() as client:
            # Mock single-tenant mode to return False (we're testing multi-tenant routing)
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=False):
                # Mock the centralized routing function
                with patch("src.core.domain_routing.route_landing_page") as mock_route:
                    from src.core.domain_routing import RoutingResult

                    # Mock routing result with no tenant
                    mock_route.return_value = RoutingResult("custom_domain", None, "unknown-domain.com")

                    # Make request with Approximated headers
                    response = client.get(
                        "/",
                        headers={
                            "Host": "backend.example.com",
                            "Apx-Incoming-Host": "unknown-domain.com",
                        },
                    )

                    # Should redirect to signup landing page (302)
                    assert response.status_code == 302
                    assert "landing" in response.location or "signup" in response.location

    def test_index_route_subdomain_with_tenant(self):
        """Test that subdomain (*.sales-agent.example.com) with tenant shows agent landing page."""
        from src.admin.app import create_app

        app, _ = create_app()

        with app.test_client() as client:
            # Mock single-tenant mode to return False (we're testing multi-tenant routing)
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=False):
                # Mock the centralized routing function
                with patch("src.core.domain_routing.route_landing_page") as mock_route:
                    with patch("src.landing.landing_page.generate_tenant_landing_page") as mock_landing:
                        from src.core.domain_routing import RoutingResult

                        # Mock routing result with tenant
                        tenant_dict = {
                            "tenant_id": "accuweather",
                            "name": "AccuWeather",
                            "subdomain": "accuweather",
                            "virtual_host": None,
                        }
                        mock_route.return_value = RoutingResult(
                            "subdomain", tenant_dict, "accuweather.sales-agent.example.com"
                        )

                        # Mock landing page generation
                        mock_landing.return_value = "<html><body>Agent Landing Page</body></html>"

                        # Make request with subdomain
                        response = client.get(
                            "/",
                            headers={
                                "Host": "accuweather.sales-agent.example.com",
                            },
                        )

                        # Should show agent landing page (200)
                        assert response.status_code == 200
                        assert b"Agent Landing Page" in response.data
