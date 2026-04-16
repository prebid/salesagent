"""Unit tests for safe_next_url and login_url_with_next.

Critical coverage: open-redirect prevention, protocol-relative URL rejection,
encoded path traversal rejection, length limits, prefix allowlist.
"""

from __future__ import annotations

from src.admin.utils.urls import login_url_with_next, safe_next_url


class TestSafeNextURL:
    def test_allows_admin_path(self):
        assert safe_next_url("/admin/accounts/1") == "/admin/accounts/1"

    def test_allows_tenant_path(self):
        assert safe_next_url("/tenant/my-pub/products/5/edit") == "/tenant/my-pub/products/5/edit"

    def test_allows_query_string(self):
        assert safe_next_url("/admin/accounts?page=2") == "/admin/accounts?page=2"

    def test_rejects_absolute_https(self):
        assert safe_next_url("https://evil.com") is None

    def test_rejects_absolute_http(self):
        assert safe_next_url("http://evil.com/admin/") is None

    def test_rejects_protocol_relative_with_path(self):
        assert safe_next_url("//evil.com/phish") is None

    def test_rejects_protocol_relative_no_path(self):
        assert safe_next_url("//evil.com") is None

    def test_rejects_backslash_protocol_relative(self):
        assert safe_next_url("\\\\evil.com\\phish") is None

    def test_rejects_embedded_backslash(self):
        assert safe_next_url("/admin/\\evil") is None

    def test_rejects_api_prefix(self):
        assert safe_next_url("/api/v1/something") is None

    def test_rejects_mcp_prefix(self):
        assert safe_next_url("/mcp/tools") is None

    def test_rejects_a2a_prefix(self):
        assert safe_next_url("/a2a") is None

    def test_rejects_internal_prefix(self):
        assert safe_next_url("/_internal/health") is None

    def test_rejects_root(self):
        assert safe_next_url("/") is None

    def test_rejects_encoded_path_traversal(self):
        assert safe_next_url("/admin/%2e%2e%2f%2e%2e%2fetc/passwd") is None

    def test_rejects_unencoded_path_traversal(self):
        assert safe_next_url("/admin/../api/v1") is None

    def test_rejects_encoded_protocol_relative(self):
        # %2f%2fevil.com decodes to //evil.com
        assert safe_next_url("%2f%2fevil.com/phish") is None

    def test_rejects_length_over_limit(self):
        assert safe_next_url("/admin/" + "x" * 3000) is None

    def test_rejects_none(self):
        assert safe_next_url(None) is None

    def test_rejects_empty(self):
        assert safe_next_url("") is None

    def test_rejects_no_leading_slash(self):
        assert safe_next_url("admin/accounts") is None


class TestLoginURLWithNext:
    def test_builds_correctly(self):
        assert (
            login_url_with_next("/admin/auth/login", "/admin/accounts/1")
            == "/admin/auth/login?next=%2Fadmin%2Faccounts%2F1"
        )

    def test_drops_invalid_next(self):
        assert login_url_with_next("/admin/auth/login", "https://evil.com") == "/admin/auth/login"

    def test_handles_none_next(self):
        assert login_url_with_next("/admin/auth/login", None) == "/admin/auth/login"

    def test_handles_empty_next(self):
        assert login_url_with_next("/admin/auth/login", "") == "/admin/auth/login"
