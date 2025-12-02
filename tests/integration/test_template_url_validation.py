"""Comprehensive tests to validate all url_for calls in templates resolve correctly.

This test file prevents regressions by ensuring every url_for() call in templates
points to an actual route that exists in the application.
"""

import re
from pathlib import Path

import pytest
from flask import url_for
from werkzeug.routing.exceptions import BuildError

from src.admin.app import create_app

admin_app, _ = create_app()

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.ui]


class TestTemplateUrlValidation:
    """Validate all url_for calls in templates can be resolved."""

    def get_all_template_url_for_calls(self):
        """Extract all url_for calls from templates."""
        template_dir = Path(__file__).parent.parent.parent / "templates"
        url_for_calls = {}

        for template_file in template_dir.rglob("*.html"):
            with open(template_file) as f:
                content = f.read()

            # Skip commented out sections
            content = re.sub(r"{#.*?#}", "", content, flags=re.DOTALL)
            content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)

            # Find all url_for calls with their parameters
            # Matches url_for('endpoint') and url_for('endpoint', param=value)
            pattern = r"url_for\(['\"]([^'\"]+)['\"]([^)]*)\)"
            matches = re.findall(pattern, content)

            if matches:
                relative_path = template_file.relative_to(template_dir)
                url_for_calls[str(relative_path)] = [(endpoint, params.strip()) for endpoint, params in matches]

        return url_for_calls

    def test_all_template_url_for_calls_resolve(self, authenticated_admin_session, test_tenant_with_data):
        """Test that every url_for call in templates can be resolved."""
        url_for_calls = self.get_all_template_url_for_calls()
        errors = []

        with admin_app.test_request_context():
            for template_file, calls in url_for_calls.items():
                for endpoint, params in calls:
                    try:
                        # Try to resolve with common parameters
                        test_params = {}

                        # Add tenant_id if likely needed
                        if "tenant" in endpoint or params and "tenant_id" in params:
                            test_params["tenant_id"] = test_tenant_with_data["tenant_id"]

                        # Add other common IDs
                        if "product_id" in params:
                            test_params["product_id"] = "test_product"
                        if "creative_id" in params:
                            test_params["creative_id"] = "test_creative"
                        if "principal_id" in params:
                            test_params["principal_id"] = "test_principal"
                        if "media_buy_id" in params:
                            test_params["media_buy_id"] = "test_buy"
                        if "task_id" in params:
                            test_params["task_id"] = "test_task"
                        if "property_id" in params:
                            test_params["property_id"] = "test_property"
                        if "config_id" in params:
                            test_params["config_id"] = "test_config"
                        if "agent_id" in params:
                            test_params["agent_id"] = 1  # agent_id is an integer
                        if "profile_id" in params:
                            test_params["profile_id"] = 1  # profile_id is an integer
                        if "filename" in params:
                            test_params["filename"] = "test.js"

                        # Try to build the URL
                        url = url_for(endpoint, **test_params)

                    except BuildError as e:
                        errors.append(
                            {"template": template_file, "endpoint": endpoint, "params": params, "error": str(e)}
                        )

        if errors:
            error_msg = "Found unresolvable url_for calls in templates:\n\n"
            for error in errors:
                error_msg += f"Template: {error['template']}\n"
                error_msg += f"  url_for('{error['endpoint']}'{error['params']})\n"
                error_msg += f"  Error: {error['error']}\n\n"

            pytest.fail(error_msg)

    def test_critical_admin_routes_exist(self):
        """Test that critical admin routes exist and are registered."""
        critical_routes = [
            ("tenant_management_settings.tenant_management_settings", "/settings"),  # Tenant management settings
            ("core.index", "/"),  # Main dashboard
            ("auth.login", "/login"),  # Login page
            ("auth.logout", "/logout"),  # Logout
            ("tenants.dashboard", "/tenant/<tenant_id>"),  # Tenant dashboard
        ]

        with admin_app.test_request_context():
            missing_routes = []
            for endpoint, expected_path in critical_routes:
                try:
                    # Check if endpoint exists
                    if "tenant_id" in expected_path:
                        url = url_for(endpoint, tenant_id="test")
                    else:
                        url = url_for(endpoint)
                except BuildError:
                    missing_routes.append((endpoint, expected_path))

            if missing_routes:
                error_msg = "Critical routes are missing:\n"
                for endpoint, path in missing_routes:
                    error_msg += f"  {endpoint} -> {path}\n"
                pytest.fail(error_msg)

    def test_form_actions_point_to_valid_endpoints(self):
        """Test that all form actions in templates point to valid endpoints."""
        template_dir = Path(__file__).parent.parent.parent / "templates"
        form_errors = []

        for template_file in template_dir.rglob("*.html"):
            with open(template_file) as f:
                content = f.read()

            # Find all form actions with url_for
            pattern = r'<form[^>]*action=["\']{{[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)[^}]*}}["\']'
            matches = re.findall(pattern, content, re.IGNORECASE)

            if matches:
                relative_path = template_file.relative_to(template_dir)
                with admin_app.test_request_context():
                    for endpoint in matches:
                        try:
                            # Try to resolve the endpoint
                            test_params = {}
                            # Most product/tenant related endpoints need tenant_id
                            # Check the endpoint name and template location for hints
                            needs_tenant = (
                                "product" in endpoint
                                or "creative" in endpoint
                                or "inventory" in endpoint
                                or "bulk" in endpoint
                                or "template" in endpoint
                                or "analyze" in endpoint
                                or "sync" in endpoint
                                or "authorized_properties" in endpoint
                                or endpoint not in ["login", "logout", "index", "settings", "auth.login", "auth.logout"]
                            )
                            if needs_tenant:
                                test_params["tenant_id"] = "test"

                            # Add property_id for authorized_properties endpoints
                            if "authorized_properties" in endpoint and "property" in endpoint:
                                test_params["property_id"] = "test_property"

                            # Add principal_id and config_id for webhook endpoints
                            if "webhook" in endpoint:
                                test_params["principal_id"] = "test_principal"
                                if "delete" in endpoint or "toggle" in endpoint:
                                    test_params["config_id"] = "test_config"
                                # Delivery webhook endpoints need media_buy_id
                                if "delivery" in endpoint or "trigger" in endpoint:
                                    test_params["media_buy_id"] = "test_buy"

                            # Add media_buy_id for media buy endpoints
                            if "media_buy" in endpoint:
                                test_params["media_buy_id"] = "test_buy"

                            url_for(endpoint, **test_params)
                        except BuildError as e:
                            form_errors.append({"template": str(relative_path), "endpoint": endpoint, "error": str(e)})

        if form_errors:
            error_msg = "Found forms with invalid action endpoints:\n\n"
            for error in form_errors:
                error_msg += f"Template: {error['template']}\n"
                error_msg += f"  Form action: url_for('{error['endpoint']}')\n"
                error_msg += f"  Error: {error['error']}\n\n"
            pytest.fail(error_msg)

    def test_navigation_links_are_valid(self):
        """Test that navigation links in base templates are valid."""
        base_templates = ["base.html", "index.html", "tenant_dashboard.html"]
        template_dir = Path(__file__).parent.parent.parent / "templates"

        for template_name in base_templates:
            template_path = template_dir / template_name
            if not template_path.exists():
                continue

            with open(template_path) as f:
                content = f.read()

            # Find all href links with url_for
            pattern = r'href=["\']{{[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)[^}]*}}["\']'
            matches = re.findall(pattern, content, re.IGNORECASE)

            if matches:
                with admin_app.test_request_context():
                    for endpoint in matches:
                        try:
                            test_params = {}
                            if "tenant" in endpoint:
                                test_params["tenant_id"] = "test"
                            url_for(endpoint, **test_params)
                        except BuildError as e:
                            pytest.fail(
                                f"Navigation link in {template_name} is broken:\n  url_for('{endpoint}')\n  Error: {e}"
                            )

    def test_ajax_urls_are_valid(self):
        """Test that AJAX/JavaScript URLs in templates are valid."""
        template_dir = Path(__file__).parent.parent.parent / "templates"
        ajax_errors = []

        for template_file in template_dir.rglob("*.html"):
            with open(template_file) as f:
                content = f.read()

            # Find fetch/ajax calls with url_for
            patterns = [
                r'fetch\([\'"`]{{[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)[^}]*}}[\'"`]',
                r'\.ajax\({[^}]*url:[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)',
                r'\.post\([\'"`]{{[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)[^}]*}}[\'"`]',
                r'\.get\([\'"`]{{[^}]*url_for\([\'"]([^\'"]+)[\'"][^)]*\)[^}]*}}[\'"`]',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    relative_path = template_file.relative_to(template_dir)
                    with admin_app.test_request_context():
                        for endpoint in matches:
                            try:
                                test_params = {}
                                # Most product/tenant related endpoints need tenant_id
                                needs_tenant = (
                                    "product" in endpoint
                                    or "creative" in endpoint
                                    or "inventory" in endpoint
                                    or "bulk" in endpoint
                                    or "template" in endpoint
                                    or "analyze" in endpoint
                                    or "sync" in endpoint
                                    or "check" in endpoint
                                    or endpoint
                                    not in ["login", "logout", "index", "settings", "auth.login", "auth.logout"]
                                )
                                if needs_tenant:
                                    test_params["tenant_id"] = "test"

                                # Add principal_id and config_id for webhook endpoints
                                if "webhook" in endpoint:
                                    test_params["principal_id"] = "test_principal"
                                    if "delete" in endpoint or "toggle" in endpoint:
                                        test_params["config_id"] = "test_config"

                                url_for(endpoint, **test_params)
                            except BuildError as e:
                                ajax_errors.append(
                                    {"template": str(relative_path), "endpoint": endpoint, "error": str(e)}
                                )

        if ajax_errors:
            error_msg = "Found AJAX calls with invalid endpoints:\n\n"
            for error in ajax_errors:
                error_msg += f"Template: {error['template']}\n"
                error_msg += f"  AJAX endpoint: url_for('{error['endpoint']}')\n"
                error_msg += f"  Error: {error['error']}\n\n"
            pytest.fail(error_msg)
