"""
Test that all URLs used in Slack notifications actually exist as routes.

This test prevents the 404 bug where Slack notifications linked to /operations
which didn't exist (should have been /workflows).

The test extracts all URL patterns from slack_notifier.py and verifies each
one corresponds to an actual Flask route.
"""

import re
from pathlib import Path

import pytest

from src.admin.app import create_app


class TestNotificationUrlsExist:
    """Verify all notification URLs correspond to actual routes."""

    @pytest.fixture
    def app(self):
        """Create Flask app for route inspection."""
        app, _ = create_app()
        return app

    @pytest.fixture
    def slack_notifier_urls(self):
        """Extract all URL patterns from slack_notifier.py."""
        slack_notifier_path = Path(__file__).parent.parent.parent / "src" / "services" / "slack_notifier.py"

        # We control this file - it should always exist
        assert slack_notifier_path.exists(), f"slack_notifier.py must exist at {slack_notifier_path}"

        content = slack_notifier_path.read_text()

        # Extract URL patterns like: f"{admin_url}/tenant/{tenant_id}/workflows"
        # Pattern matches: /tenant/{var}/something or /something
        url_pattern = r'["\'](/[a-z_\-{}/<>]+)["\']'
        urls = re.findall(url_pattern, content)

        # Filter to only URLs that look like routes (not full URLs with http://)
        route_patterns = []
        for url in urls:
            # Skip if it's part of a full URL
            if content[content.find(url) - 10 : content.find(url)].find("http") != -1:
                continue
            # Skip static files and external paths
            if url.startswith("/static") or url.startswith("/admin/admin"):
                continue
            route_patterns.append(url)

        # Deduplicate
        return list(set(route_patterns))

    @pytest.fixture
    def app_routes(self, app):
        """Get all registered routes in the Flask app."""
        routes = set()
        for rule in app.url_map.iter_rules():
            # Convert Flask route format to our format
            # <tenant_id> -> {tenant_id}
            route = str(rule.rule)
            routes.add(route)
        return routes

    def test_all_slack_notification_urls_are_valid_routes(self, app_routes, slack_notifier_urls):
        """
        Verify every URL pattern in slack_notifier.py exists as a Flask route.

        This test prevents bugs where we hardcode URLs that don't exist.
        Example: /tenant/{tenant_id}/operations didn't exist (should be /workflows).
        """
        # We control this file - it should have URLs
        assert slack_notifier_urls, "slack_notifier.py should contain notification URLs"

        missing_routes = []

        for notification_url in slack_notifier_urls:
            # Convert our format {tenant_id} to Flask format <tenant_id>
            flask_route = notification_url.replace("{", "<").replace("}", ">")

            # Check if route exists (exact match or as a prefix)
            route_exists = any(
                route == flask_route or route.startswith(flask_route + "/") or
                # Handle both /tenant/<tenant_id>/workflows and /tenant/<string:tenant_id>/workflows
                route.replace("<string:", "<") == flask_route
                for route in app_routes
            )

            if not route_exists:
                missing_routes.append((notification_url, flask_route))

        if missing_routes:
            error_msg = "Found notification URLs that don't exist as Flask routes:\n"
            for notif_url, flask_route in missing_routes:
                error_msg += f"  - Notification uses: {notif_url}\n"
                error_msg += f"    Expected Flask route: {flask_route}\n"
                error_msg += "    Available similar routes:\n"
                for route in sorted(app_routes):
                    if "/tenant/" in route and any(
                        keyword in route for keyword in ["workflow", "operation", "creative"]
                    ):
                        error_msg += f"      - {route}\n"

            pytest.fail(error_msg)

    def test_known_notification_urls_exist(self, app_routes):
        """
        Test specific known notification URLs to prevent regression.

        This is a safety net in case the regex pattern extraction fails.
        """
        # Known notification URLs that should exist
        required_routes = [
            "/tenant/<tenant_id>/workflows",  # Fixed in this PR (was /operations)
            "/tenant/<tenant_id>/creatives/review",  # Creative review page
        ]

        missing = []
        for route in required_routes:
            # Check with both regular and string: type hints
            route_exists = any(
                app_route == route or app_route.replace("<string:", "<") == route for app_route in app_routes
            )
            if not route_exists:
                missing.append(route)

        if missing:
            error_msg = "Required notification routes don't exist:\n"
            for route in missing:
                error_msg += f"  - {route}\n"
            error_msg += "\nAvailable routes:\n"
            for route in sorted(app_routes):
                if "/tenant/" in route:
                    error_msg += f"  - {route}\n"
            pytest.fail(error_msg)

    def test_deprecated_operations_route_not_used(self, slack_notifier_urls):
        """
        Ensure we don't use the old /operations route that caused the 404 bug.

        This is a regression test for the specific bug we just fixed.
        """
        deprecated_patterns = [
            "/operations",  # Global operations (doesn't exist)
            "/tenant/{tenant_id}/operations",  # Tenant operations (doesn't exist)
        ]

        found_deprecated = [pattern for pattern in deprecated_patterns if pattern in slack_notifier_urls]

        if found_deprecated:
            pytest.fail(
                f"Found deprecated /operations routes in slack_notifier.py: {found_deprecated}\n"
                "These routes don't exist. Use /workflows instead."
            )
