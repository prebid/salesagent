"""Regression test for issue #154.

`url_for("tenants.settings", ...)` was used at seven sites on error/success
branches of admin flows (GAM OAuth, Slack settings save/test, GAM disconnect).
The view function is `tenant_settings`, so the registered endpoint is
`tenants.tenant_settings` — the literal `tenants.settings` raised BuildError
and surfaced as HTTP 500 to operators whenever those branches ran.
"""

from pathlib import Path

import pytest
from flask import url_for

from src.admin.app import create_app


class TestTenantSettingsEndpoint:
    @pytest.fixture
    def app(self):
        return create_app()

    def test_tenant_settings_endpoint_resolves(self, app):
        with app.test_request_context():
            url = url_for("tenants.tenant_settings", tenant_id="t1")
            assert url.endswith("/t1/settings")

    def test_tenant_settings_endpoint_resolves_with_section(self, app):
        with app.test_request_context():
            url = url_for("tenants.tenant_settings", tenant_id="t1", section="slack")
            assert url.endswith("/t1/settings/slack")

    def test_dead_endpoint_name_not_referenced_in_source(self):
        repo_root = Path(__file__).resolve().parents[2]
        src_dir = repo_root / "src"
        offenders = []
        for path in src_dir.rglob("*.py"):
            text = path.read_text()
            if '"tenants.settings"' in text or "'tenants.settings'" in text:
                offenders.append(str(path.relative_to(repo_root)))
        assert not offenders, (
            "Found references to non-existent endpoint 'tenants.settings'. "
            f"Use 'tenants.tenant_settings'. Offenders: {offenders}"
        )
