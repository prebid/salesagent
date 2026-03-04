"""Test that health routes work in the refactored structure."""

import json
import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.mark.requires_db
def test_health_routes_in_refactored_app(integration_db):
    """Test that both health routes work in the refactored app."""
    from src.admin.app import create_app
    from src.core.database.database_session import reset_engine

    # Reset engine to ensure it uses the test database (integration_db fixture sets DATABASE_URL)
    reset_engine()

    app = create_app()
    with app.test_client() as client:
        # Test simple health endpoint
        response = client.get("/health")
        assert response.status_code == 200
        assert response.data == b"OK"

        # Test API health endpoint
        response = client.get("/api/health")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "healthy"

    print("✅ Both health routes work in refactored app!")


@pytest.mark.requires_db
def test_health_routes_in_original_app(integration_db):
    """Test that health routes still work in original app for comparison."""
    from src.admin.app import create_app
    from src.core.database.database_session import reset_engine

    # Reset engine to ensure it uses the test database (integration_db fixture sets DATABASE_URL)
    reset_engine()

    app = create_app()
    with app.test_client() as client:
        # Test simple health endpoint
        response = client.get("/health")
        assert response.status_code == 200
        assert response.data == b"OK"

        # Test API health endpoint
        response = client.get("/api/health")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "healthy"

    print("✅ Both health routes work in original app!")


if __name__ == "__main__":
    print("Testing health routes in refactored app...")
    test_health_routes_in_refactored_app(None)

    print("\nTesting health routes in original app...")
    test_health_routes_in_original_app(None)

    print("\n🎉 All health route tests passed!")
