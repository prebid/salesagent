"""Basic smoke tests that don't require running servers."""

import importlib.util
from pathlib import Path

import pytest

_REPO_INVARIANTS_HOOK = Path(__file__).resolve().parents[2] / ".pre-commit-hooks" / "check_repo_invariants.py"
_spec = importlib.util.spec_from_file_location("check_repo_invariants", _REPO_INVARIANTS_HOOK)
assert _spec and _spec.loader
_check_repo_invariants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_repo_invariants)
_is_forbidden_skip_line = _check_repo_invariants.is_forbidden_skip_line


class TestCriticalImports:
    """Test that critical modules can be imported."""

    @pytest.mark.smoke
    def test_main_module_imports(self):
        """Test that main.py can be imported."""
        from src.core import main

        assert hasattr(main, "mcp")

    @pytest.mark.smoke
    def test_schemas_import(self):
        """Test that schemas module imports correctly."""
        from src.core import schemas

        assert hasattr(schemas, "CreateMediaBuyRequest")
        assert hasattr(schemas, "Product")

    @pytest.mark.smoke
    def test_database_module_imports(self):
        """Test that database modules import."""
        from src.core.database import database_session, models

        assert hasattr(database_session, "get_db_session")
        assert hasattr(models, "MediaBuy")

    @pytest.mark.smoke
    def test_adapter_imports(self):
        """Test that adapters can be imported."""
        from src.adapters.base import AdServerAdapter
        from src.adapters.mock_ad_server import MockAdServer

        assert issubclass(MockAdServer, AdServerAdapter)


class TestDatabaseSchema:
    """Test database schema is correct."""

    @pytest.mark.smoke
    def test_models_have_required_fields(self):
        """Test that models have required fields."""
        from src.core.database.models import MediaBuy, Principal, Product, Tenant

        # Test MediaBuy has critical fields
        assert hasattr(MediaBuy, "media_buy_id")
        assert hasattr(MediaBuy, "tenant_id")
        assert hasattr(MediaBuy, "status")
        assert hasattr(MediaBuy, "budget")

        # Test Tenant has critical fields
        assert hasattr(Tenant, "tenant_id")
        assert hasattr(Tenant, "name")

        # Test Principal has auth fields
        assert hasattr(Principal, "principal_id")
        assert hasattr(Principal, "access_token")

        # Test Product has required fields
        assert hasattr(Product, "product_id")
        assert hasattr(Product, "name")


class TestConfiguration:
    """Test configuration can be loaded."""

    @pytest.mark.smoke
    def test_config_loader_imports(self):
        """Test that config loader can be imported."""
        from src.core.config_loader import load_config, set_current_tenant

        # Functions should exist and be callable
        assert callable(load_config)
        assert callable(set_current_tenant)


class TestCriticalPaths:
    """Test critical code paths work."""

    @pytest.mark.smoke
    def test_principal_auth_logic(self):
        """Test principal authentication logic exists."""
        from src.core.auth import get_principal_from_token

        # Function should exist and be callable
        assert callable(get_principal_from_token)

    @pytest.mark.smoke
    def test_adapter_factory_pattern(self):
        """Test adapter factory pattern works."""
        from src.adapters.mock_ad_server import MockAdServer
        from src.core.schemas import Principal

        # Create a test principal
        principal = Principal(
            principal_id="test",
            name="Test",
            platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
        )

        # Should be able to create adapter
        config = {"enabled": True}
        adapter = MockAdServer(config=config, principal=principal, dry_run=False, tenant_id="test_tenant")
        assert adapter is not None

    @pytest.mark.smoke
    def test_audit_logger_exists(self):
        """Test audit logger can be imported."""
        from src.core.audit_logger import get_audit_logger

        logger = get_audit_logger("mock", "test_tenant")
        assert logger is not None
        assert hasattr(logger, "log_operation")


class TestProjectStructure:
    """Test project structure is correct."""

    @pytest.mark.smoke
    def test_critical_files_exist(self):
        """Test that critical files exist."""
        base_dir = Path(__file__).parent.parent.parent

        critical_files = [
            "src/core/main.py",
            "src/core/schemas/__init__.py",
            "src/core/database/models.py",
            "src/core/database/database_session.py",
            "src/core/config_loader.py",
            "src/core/audit_logger.py",
            "src/adapters/base.py",
            "src/adapters/mock_ad_server.py",
            "pytest.ini",
            ".pre-commit-config.yaml",
        ]

        for file_path in critical_files:
            full_path = base_dir / file_path
            assert full_path.exists(), f"Critical file missing: {file_path}"

    @pytest.mark.smoke
    def test_migrations_directory_exists(self):
        """Test that migrations directory exists."""
        migrations_dir = Path(__file__).parent.parent.parent / "alembic"
        assert migrations_dir.exists(), "Migrations directory missing"

        versions_dir = migrations_dir / "versions"
        assert versions_dir.exists(), "Migration versions directory missing"


class TestNoSkippedTests:
    """Ensure no tests are being skipped."""

    @pytest.mark.smoke
    def test_no_skip_decorators(self):
        """Test that no test files contain forbidden bare skip decorators."""
        tests_dir = Path(__file__).parent.parent
        bad_lines: list[str] = []
        for path in sorted(tests_dir.rglob("test_*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(tests_dir.parent)
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _is_forbidden_skip_line(line):
                    bad_lines.append(f"{rel}:{lineno}:{line.strip()}")
        assert not bad_lines, f"Found forbidden skip decorators:\n{chr(10).join(bad_lines[:5])}"


class TestCodeQuality:
    """Test code quality standards."""

    @pytest.mark.smoke
    def test_no_hardcoded_credentials(self):
        """Test that no hardcoded credentials exist in code."""
        import subprocess

        # Check for hardcoded credential patterns (not dynamic config access)
        patterns = [
            "password\\s*=\\s*[\"'][a-zA-Z0-9_!@#$%^&*-]{8,}[\"']",  # Direct assignment only
            "secret\\s*=\\s*[\"'][a-zA-Z0-9_!@#$%^&*-]{8,}[\"']",
            "api_key\\s*=\\s*[\"'][a-zA-Z0-9_-]{20,}[\"']",  # API keys usually longer
            "token\\s*=\\s*[\"'][a-zA-Z0-9_-]{16,}[\"']",  # Tokens usually longer
        ]

        test_dir = Path(__file__).parent.parent.parent
        for pattern in patterns:
            result = subprocess.run(
                ["grep", "-r", "-E", pattern, "--include=*.py", "."],
                cwd=str(test_dir),
                capture_output=True,
                text=True,
            )

            # Filter out test files, comments, and legitimate config access
            if result.returncode == 0:
                lines = result.stdout.split("\n")
                non_test_lines = [
                    line
                    for line in lines
                    if line
                    and "test" not in line.lower()
                    and "#" not in line
                    and ".venv/" not in line  # Exclude virtual environment
                    and "site-packages/" not in line  # Exclude installed packages
                    and not line.startswith("./.")  # Exclude hidden directories
                    # Exclude legitimate config access patterns
                    and "config.get(" not in line
                    and "config[" not in line
                    and "request.form.get(" not in line
                    and "getenv(" not in line
                    and "environ.get(" not in line
                ]
                assert len(non_test_lines) == 0, f"Found hardcoded credentials:\n{chr(10).join(non_test_lines[:5])}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "smoke"])
