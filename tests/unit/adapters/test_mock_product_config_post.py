"""Unit tests for the MockProductConfig POST form handler.

Covers the three paths in the mock_product_config route:
  - Valid form data → config saved to DB via MockProductConfig.model_dump()
  - Out-of-range value → ValidationError caught, error rendered to template
  - Non-numeric input → ValueError caught, error rendered to template
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

VALID_FORM_DATA = {
    "daily_impressions": "50000",
    "fill_rate": "75.0",
    "ctr": "2.0",
    "viewability_rate": "60.0",
    "latency_ms": "100",
    "error_rate": "5.0",
    "test_mode": "stress",
    "price_variance": "5.0",
    "seasonal_factor": "1.2",
}


def _make_test_app():
    from flask import Flask

    from src.adapters.mock_ad_server import MockAdServer

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    MockAdServer(config=MagicMock(), principal=MagicMock(), tenant_id="tenant1").register_ui_routes(app)
    return app


def _mock_db(product_obj):
    session = MagicMock()
    session.scalars.return_value.first.return_value = product_obj

    @contextmanager
    def _get_db_session():
        yield session

    return session, _get_db_session


def _make_product():
    p = MagicMock()
    p.implementation_config = {}
    p.format_ids = []
    p.name = "Test Product"
    return p


class TestMockProductConfigPost:
    """POST path: save, ValidationError, ValueError."""

    def test_valid_post_saves_config_to_db(self):
        """Valid form data is validated by MockProductConfig and saved to DB."""
        app = _make_test_app()
        product = _make_product()
        session, mock_get_db = _mock_db(product)

        with patch("src.admin.utils.require_auth", return_value=lambda f: f), \
             patch("src.core.database.database_session.get_db_session", mock_get_db), \
             patch("src.admin.blueprints.products.get_creative_formats", return_value=[]), \
             patch("flask.render_template", return_value="ok"):
            with app.test_client() as client:
                resp = client.post(
                    "/adapters/mock/config/tenant1/prod1",
                    data=VALID_FORM_DATA,
                )

        assert resp.status_code == 200
        saved = product.implementation_config
        assert saved["fill_rate"] == 75.0
        assert saved["test_mode"] == "stress"
        assert saved["daily_impressions"] == 50000
        session.commit.assert_called_once_with()

    def test_out_of_range_value_returns_error(self):
        """fill_rate > 100 triggers ValidationError; template rendered with error, DB not touched."""
        app = _make_test_app()
        product = _make_product()
        session, mock_get_db = _mock_db(product)
        render_kwargs: dict = {}

        def capture(template, **kwargs):
            render_kwargs.update(kwargs)
            return "rendered"

        with patch("src.admin.utils.require_auth", return_value=lambda f: f), \
             patch("src.core.database.database_session.get_db_session", mock_get_db), \
             patch("src.admin.blueprints.products.get_creative_formats", return_value=[]), \
             patch("flask.render_template", side_effect=capture):
            with app.test_client() as client:
                client.post(
                    "/adapters/mock/config/tenant1/prod1",
                    data={**VALID_FORM_DATA, "fill_rate": "200"},
                )

        assert render_kwargs.get("error") is not None
        session.commit.assert_not_called()

    def test_non_numeric_input_returns_error(self):
        """Non-numeric fill_rate triggers ValueError; template rendered with error, DB not touched."""
        app = _make_test_app()
        product = _make_product()
        session, mock_get_db = _mock_db(product)
        render_kwargs: dict = {}

        def capture(template, **kwargs):
            render_kwargs.update(kwargs)
            return "rendered"

        with patch("src.admin.utils.require_auth", return_value=lambda f: f), \
             patch("src.core.database.database_session.get_db_session", mock_get_db), \
             patch("src.admin.blueprints.products.get_creative_formats", return_value=[]), \
             patch("flask.render_template", side_effect=capture):
            with app.test_client() as client:
                client.post(
                    "/adapters/mock/config/tenant1/prod1",
                    data={**VALID_FORM_DATA, "fill_rate": "not_a_number"},
                )

        assert render_kwargs.get("error") is not None
        session.commit.assert_not_called()
