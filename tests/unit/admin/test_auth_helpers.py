"""Unit tests for shared admin-auth primitives.

Covers ``src/admin/auth/__init__.py``: the ``Role`` literal, ``normalize_email``
canonicalization, and ``coerce_role`` parsing. These helpers are the
single source of truth across ``principal.py``, ``deps/auth.py``, and
``unified_auth.py``. CLAUDE.md DRY policy treats three copies as a defect —
this test file pins the shared contract so future divergence is caught.
"""

from __future__ import annotations

from src.admin.auth import Role, coerce_role, normalize_email


class TestNormalizeEmail:
    def test_none_returns_empty(self) -> None:
        assert normalize_email(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert normalize_email("") == ""

    def test_mixed_case_lowered(self) -> None:
        assert normalize_email("ALICE@X.COM") == "alice@x.com"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert normalize_email("  alice@x.com  ") == "alice@x.com"

    def test_combined_strip_and_lower(self) -> None:
        assert normalize_email("  ALICE@X.Com\t") == "alice@x.com"

    def test_preserves_plus_addressing(self) -> None:
        assert normalize_email("Alice+Work@x.com") == "alice+work@x.com"

    def test_preserves_non_ascii_local_part(self) -> None:
        # Unicode in the local part survives canonicalization; .lower()
        # performs unicode case folding per Python string semantics.
        assert normalize_email("Alicé@example.com") == "alicé@example.com"

    def test_idempotent(self) -> None:
        once = normalize_email("  ALICE@X.COM  ")
        assert normalize_email(once) == once


class TestCoerceRole:
    def test_super_admin_is_valid(self) -> None:
        assert coerce_role("super_admin") == "super_admin"

    def test_tenant_admin_is_valid(self) -> None:
        assert coerce_role("tenant_admin") == "tenant_admin"

    def test_tenant_user_is_valid(self) -> None:
        assert coerce_role("tenant_user") == "tenant_user"

    def test_test_role_is_valid(self) -> None:
        assert coerce_role("test") == "test"

    def test_unknown_string_returns_none(self) -> None:
        # Returning None (not a default) is load-bearing: callers MUST
        # choose their own default so malformed session data doesn't
        # silently promote to tenant_user via an embedded helper default.
        assert coerce_role("admin") is None
        assert coerce_role("guest") is None
        assert coerce_role("SUPER_ADMIN") is None  # case-sensitive

    def test_empty_string_returns_none(self) -> None:
        assert coerce_role("") is None

    def test_whitespace_string_returns_none(self) -> None:
        # Does NOT strip — a "tenant_admin " (trailing space) session value
        # is invalid, not something to silently accept.
        assert coerce_role(" tenant_admin ") is None
        assert coerce_role("tenant_admin\n") is None

    def test_none_returns_none(self) -> None:
        assert coerce_role(None) is None

    def test_non_string_returns_none(self) -> None:
        assert coerce_role(42) is None
        assert coerce_role(["tenant_admin"]) is None
        assert coerce_role({"role": "tenant_admin"}) is None
        assert coerce_role(True) is None

    def test_all_valid_roles_returned_verbatim(self) -> None:
        # Every literal in the Role type is parseable.
        expected: tuple[Role, ...] = ("super_admin", "tenant_admin", "tenant_user", "test")
        for role in expected:
            assert coerce_role(role) == role
