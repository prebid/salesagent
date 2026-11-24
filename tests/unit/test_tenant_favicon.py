"""Test tenant favicon configuration functionality."""

import os
from pathlib import Path


class TestFaviconUpload:
    """Tests for favicon upload functionality."""

    def test_allowed_favicon_file_valid_extensions(self):
        """Test that valid favicon extensions are allowed."""
        from src.admin.blueprints.tenants import _allowed_favicon_file

        assert _allowed_favicon_file("icon.ico") is True
        assert _allowed_favicon_file("icon.png") is True
        assert _allowed_favicon_file("icon.jpg") is True
        assert _allowed_favicon_file("icon.jpeg") is True
        assert _allowed_favicon_file("ICON.ICO") is True
        assert _allowed_favicon_file("favicon.PNG") is True
        assert _allowed_favicon_file("favicon.JPG") is True

    def test_allowed_favicon_file_invalid_extensions(self):
        """Test that invalid favicon extensions are rejected."""
        from src.admin.blueprints.tenants import _allowed_favicon_file

        assert _allowed_favicon_file("icon.svg") is False  # a document that can carry script
        assert _allowed_favicon_file("icon.gif") is False
        assert _allowed_favicon_file("icon.bmp") is False
        assert _allowed_favicon_file("icon.webp") is False
        assert _allowed_favicon_file("icon") is False
        assert _allowed_favicon_file("") is False

    def test_favicon_content_must_match_its_extension(self):
        """The first bytes decide, not the file name: an SVG renamed .png is refused."""
        from src.admin.blueprints.tenants import _favicon_content_matches

        png = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])
        ico = bytes([0, 0, 1, 0, 1, 0, 16, 16])
        jpeg = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10]) + b"JF"
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'[:8]
        assert _favicon_content_matches("png", png)
        assert _favicon_content_matches("ico", ico)
        assert _favicon_content_matches("jpg", jpeg)
        assert _favicon_content_matches("jpeg", jpeg)
        assert not _favicon_content_matches("png", svg)
        assert not _favicon_content_matches("ico", png)
        assert not _favicon_content_matches("svg", svg)
        assert not _favicon_content_matches("png", b"")

    def test_favicon_upload_dir_path(self):
        """Test that favicon upload directory path is correctly constructed."""
        from src.admin.blueprints.tenants import _get_favicon_upload_dir

        upload_dir = _get_favicon_upload_dir()
        assert Path(upload_dir).as_posix().endswith("static/favicons")
        assert os.path.isabs(upload_dir)

    def test_safe_favicon_path_valid(self):
        """Test that valid tenant IDs pass path traversal check."""
        from src.admin.blueprints.tenants import _get_favicon_upload_dir, _is_safe_favicon_path

        base_dir = _get_favicon_upload_dir()
        assert _is_safe_favicon_path(base_dir, "tenant_123") is True
        assert _is_safe_favicon_path(base_dir, "my-tenant") is True
        assert _is_safe_favicon_path(base_dir, "default") is True

    def test_safe_favicon_path_traversal_blocked(self):
        """Test that path traversal attempts are blocked."""
        from src.admin.blueprints.tenants import _get_favicon_upload_dir, _is_safe_favicon_path

        base_dir = _get_favicon_upload_dir()
        assert _is_safe_favicon_path(base_dir, "../etc") is False
        assert _is_safe_favicon_path(base_dir, "../../passwd") is False
        assert _is_safe_favicon_path(base_dir, "tenant/../../../etc") is False

    def test_valid_favicon_url_http(self):
        """Test that HTTP/HTTPS URLs are valid."""
        from src.admin.blueprints.tenants import _is_valid_favicon_url

        assert _is_valid_favicon_url("https://example.com/favicon.ico") is True
        assert _is_valid_favicon_url("http://example.com/icon.png") is True
        assert _is_valid_favicon_url("") is True  # Empty is valid (clears favicon)

    def test_invalid_favicon_url_blocked(self):
        """Test that dangerous URL schemes are blocked."""
        from src.admin.blueprints.tenants import _is_valid_favicon_url

        assert _is_valid_favicon_url("javascript:alert(1)") is False
        assert _is_valid_favicon_url("data:image/png;base64,abc") is False
        assert _is_valid_favicon_url("file:///etc/passwd") is False
        assert _is_valid_favicon_url("/static/favicon.ico") is False  # Relative paths not allowed via URL input


class TestFaviconModel:
    """Tests for favicon_url field in Tenant model."""

    def test_tenant_has_favicon_url_field(self):
        """Test that Tenant model has favicon_url field."""
        from src.core.database.models import Tenant

        columns = [c.name for c in Tenant.__table__.columns]
        assert "favicon_url" in columns

    def test_favicon_url_is_nullable(self):
        """Test that favicon_url field is nullable."""
        from src.core.database.models import Tenant

        favicon_col = Tenant.__table__.columns["favicon_url"]
        assert favicon_col.nullable is True

    def test_favicon_url_max_length(self):
        """Test that favicon_url field has appropriate max length."""
        from src.core.database.models import Tenant

        favicon_col = Tenant.__table__.columns["favicon_url"]
        assert favicon_col.type.length == 500


class TestFaviconTemplateLogic:
    """Tests for favicon template rendering logic."""

    def test_base_template_has_favicon_link(self):
        """Test that base.html includes favicon link tag."""
        template_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "templates",
            "base.html",
        )
        with open(template_path, encoding="utf-8") as f:
            content = f.read()

        # Check for favicon link
        assert '<link rel="icon"' in content
        # Check for tenant favicon conditional
        assert "tenant.favicon_url" in content
        # Check for default favicon fallback
        assert "/static/favicons/default/favicon.jpg" in content

    def test_tenant_settings_has_favicon_ui(self):
        """Test that tenant_settings.html includes favicon configuration UI."""
        template_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "templates",
            "tenant_settings.html",
        )
        with open(template_path, encoding="utf-8") as f:
            content = f.read()

        # Check for favicon upload form
        assert "upload_favicon" in content
        assert 'enctype="multipart/form-data"' in content
        # Check for favicon URL form
        assert "update_favicon_url" in content
        # Check for favicon removal
        assert "remove_favicon" in content


class TestDefaultFavicon:
    """Tests for default favicon file."""

    def test_default_favicon_exists(self):
        """Test that default favicon file exists."""
        default_favicon_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "static",
            "favicons",
            "default",
            "favicon.jpg",
        )
        assert os.path.exists(default_favicon_path)

    def test_default_favicon_is_valid_jpg(self):
        """Test that default favicon is valid JPG."""
        default_favicon_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "static",
            "favicons",
            "default",
            "favicon.jpg",
        )
        with open(default_favicon_path, "rb") as f:
            content = f.read()

        # JPG files start with FF D8 FF magic bytes
        assert content[:3] == b"\xff\xd8\xff"
