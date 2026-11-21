"""The Admin UI route reference lists the routes the admin application serves.

docs/development/admin-ui-routes.md is generated from the URL map of create_app(). This guard
regenerates the page in memory and fails when the committed page differs, naming each route
row that is missing or out of date. It never writes the page: run
scripts/generate_admin_route_reference.py to update it.
"""

import pytest

from scripts.generate_admin_route_reference import PAGE, render, stale_report


def test_admin_route_reference_matches_the_admin_app():
    report = stale_report(PAGE.read_text(), render())
    if report:
        pytest.fail(report, pytrace=False)
