"""Test how the admin plane resolves a request's host to a tenant.

The ``Host`` is the one host input here as on the buyer-facing plane, and these assert the
lookup ARGUMENT rather than only the returned tenant. A mocked session that answers ANY
query with a tenant cannot tell WHICH host was looked up, which is the only thing that
distinguishes "the Host was used" from "some host was used".
"""

from unittest.mock import Mock, patch

from src.admin.blueprints.core import get_tenant_from_hostname


class TestGetTenantFromHostname:
    """``core.get_tenant_from_hostname`` is the admin plane's one host -> tenant lookup."""

    def test_the_tenant_is_looked_up_by_the_host(self):
        """The ``Host`` is the lookup key, and the vendor header does not displace it."""
        from src.admin.app import create_app

        app = create_app()

        with app.test_request_context("/", headers={"Host": "sales-agent.accuweather.com"}):
            with patch("src.admin.blueprints.core.get_db_session"):
                with patch("src.admin.blueprints.core.TenantLookupRepository") as mock_repo_cls:
                    mock_tenant = Mock()
                    mock_tenant.tenant_id = "accuweather"
                    mock_tenant.virtual_host = "sales-agent.accuweather.com"
                    lookup = mock_repo_cls.return_value.find_active_by_virtual_host
                    lookup.return_value = mock_tenant

                    result = get_tenant_from_hostname()

                    lookup.assert_called_once_with("sales-agent.accuweather.com")
                    assert result.tenant_id == "accuweather"

    def test_a_host_no_tenant_claims_resolves_to_none(self):
        from src.admin.app import create_app

        app = create_app()

        with app.test_request_context("/", headers={"Host": "unknown-domain.com"}):
            with patch("src.admin.blueprints.core.get_db_session"):
                with patch("src.admin.blueprints.core.TenantLookupRepository") as mock_repo_cls:
                    lookup = mock_repo_cls.return_value.find_active_by_virtual_host
                    lookup.return_value = None

                    assert get_tenant_from_hostname() is None
                    lookup.assert_called_once_with("unknown-domain.com")

    def test_the_admin_domain_is_not_a_tenant_and_is_never_looked_up(self):
        """A host under ``admin.`` names the admin domain itself."""
        from src.admin.app import create_app

        app = create_app()

        with app.test_request_context("/", headers={"Host": "admin.sales-agent.example.com"}):
            with patch("src.admin.blueprints.core.TenantLookupRepository") as mock_repo_cls:
                assert get_tenant_from_hostname() is None
                mock_repo_cls.return_value.find_active_by_virtual_host.assert_not_called()


class TestExternalDomainRouting:
    """Test that external domains route to tenant home page instead of signup."""

    def test_index_route_external_domain_with_tenant(self):
        """Test that external domain with configured tenant shows agent landing page."""
        from src.admin.app import create_app

        app = create_app()

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
                        response = client.get("/", headers={"Host": "sales-agent.accuweather.com"})

                        # Should show agent landing page (200) with MCP/A2A endpoints
                        assert response.status_code == 200
                        assert b"Agent Landing Page" in response.data
                        # The page is generated for the tenant the host resolved, and from
                        # that tenant's row alone — the request's host is not passed on.
                        mock_landing.assert_called_once_with(tenant_dict)

    def test_index_route_external_domain_no_tenant(self):
        """Test that external domain without configured tenant shows signup landing page."""
        from src.admin.app import create_app

        app = create_app()

        with app.test_client() as client:
            # Mock single-tenant mode to return False (we're testing multi-tenant routing)
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=False):
                # Mock the centralized routing function
                with patch("src.core.domain_routing.route_landing_page") as mock_route:
                    from src.core.domain_routing import RoutingResult

                    # Mock routing result with no tenant
                    mock_route.return_value = RoutingResult("custom_domain", None, "unknown-domain.com")

                    # Make request with Approximated headers
                    response = client.get("/", headers={"Host": "unknown-domain.com"})

                    # Should redirect to signup landing page (302)
                    assert response.status_code == 302
                    assert "landing" in response.location or "signup" in response.location

    def test_index_route_subdomain_with_tenant(self):
        """Test that subdomain (*.sales-agent.example.com) with tenant shows agent landing page."""
        from src.admin.app import create_app

        app = create_app()

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
                            "virtual_host": "accuweather.sales-agent.example.com",
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


class TestSingleTenantAdminEntry:
    """``/admin/`` in single-tenant mode names the deployment's tenant, whatever its id (#2260).

    Nothing creates a ``default`` tenant any more, so the entry must not assume one: a
    redirect to ``/tenant/default`` reached a sign-in page that 404s for a missing tenant.
    """

    def test_the_host_naming_no_tenant_falls_back_to_the_one_active_tenant(self):
        from src.admin.app import create_app
        from src.admin.blueprints.core import single_tenant_admin_tenant

        app = create_app()

        with app.test_request_context("/", headers={"Host": "localhost:8000"}):
            with patch("src.admin.blueprints.core.get_db_session"):
                with patch("src.admin.blueprints.core.TenantLookupRepository") as mock_repo_cls:
                    lookup = mock_repo_cls.return_value
                    lookup.find_active_by_virtual_host.return_value = None
                    lookup.find_sole_active.return_value = Mock(tenant_id="demo")

                    assert single_tenant_admin_tenant().tenant_id == "demo"
                    lookup.find_active_by_virtual_host.assert_called_once_with("localhost:8000")

    def test_the_entry_redirects_to_the_resolved_tenant(self):
        from src.admin.app import create_app

        app = create_app()

        with app.test_client() as client:
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=True):
                with patch("src.admin.blueprints.core.single_tenant_admin_tenant", return_value=Mock(tenant_id="demo")):
                    response = client.get("/admin/")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/tenant/demo")

    def test_with_no_tenant_to_name_the_entry_sends_the_visitor_to_sign_in(self):
        from src.admin.app import create_app

        app = create_app()

        with app.test_client() as client:
            with patch("src.core.config_loader.is_single_tenant_mode", return_value=True):
                with patch("src.admin.blueprints.core.single_tenant_admin_tenant", return_value=None):
                    with patch("src.admin.blueprints.core.get_tenant_from_hostname", return_value=None):
                        response = client.get("/admin/")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")
