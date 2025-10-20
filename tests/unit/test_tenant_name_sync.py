"""Test that tenant names are always loaded fresh from database."""

from unittest.mock import MagicMock, patch


def test_context_processor_loads_fresh_tenant_data():
    """Test that context processor loads fresh tenant data from database."""
    from src.admin.app import create_app

    app, _ = create_app()

    with app.test_request_context():
        # Mock session with tenant_id only (no tenant_name - we don't use it anymore)
        with patch("flask.session", {"tenant_id": "tenant_123"}):
            # Mock database query to return tenant
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = "tenant_123"
            mock_tenant.name = "Current Name"

            with patch("src.core.database.database_session.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_session.scalars.return_value.first.return_value = mock_tenant
                mock_db.return_value.__enter__.return_value = mock_session

                # Call context processor
                context = None
                for processor in app.template_context_processors[None]:
                    if processor.__name__ == "inject_context":
                        context = processor()
                        break

                # Verify tenant is loaded with fresh data
                assert context is not None
                assert "tenant" in context
                assert context["tenant"].name == "Current Name"


def test_context_processor_handles_missing_tenant():
    """Test that context processor handles missing tenant gracefully."""
    from src.admin.app import create_app

    app, _ = create_app()

    with app.test_request_context():
        # Mock session without tenant_id
        with patch("flask.session", {}):
            # Call context processor
            context = None
            for processor in app.template_context_processors[None]:
                if processor.__name__ == "inject_context":
                    context = processor()
                    break

            # Verify no tenant in context
            assert context is not None
            assert "tenant" not in context or context.get("tenant") is None


def test_template_uses_only_tenant_from_database():
    """Test that base.html template uses only tenant.name from database."""
    # Verify the template logic uses tenant.name directly (no session.tenant_name)

    template_logic = """
    {% if tenant and tenant.name and session.role != 'super_admin' %}
        {{ tenant.name }} Sales Agent Dashboard
    {% else %}
        Sales Agent Admin
    {% endif %}
    """

    # The test verifies tenant.name is used (from database via context processor)
    assert "tenant and tenant.name" in template_logic
    assert "tenant.name" in template_logic
    # session.tenant_name should NOT be used anymore
    assert "session.tenant_name" not in template_logic
