"""Regression tests: auth infrastructure hardening.

Core invariant: Auth infrastructure must be defensively robust — immutable
state, shared constants, portable test paths, and consistent middleware style.

"""

import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestAuthContextImmutableHeaders:
    """AuthContext.headers must be truly immutable (not just frozen dataclass)."""

    def test_headers_not_mutatable(self):
        """Mutating AuthContext.headers must raise TypeError."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext(auth_token="tok", headers={"host": "example.com"})
        with pytest.raises(TypeError):
            ctx.headers["injected"] = "value"


class TestAuthContextStateKey:
    """'auth_context' state key must be a shared constant, not repeated string literals."""

    def test_constant_defined(self):
        """AUTH_CONTEXT_STATE_KEY must be defined in auth_context module."""
        from src.core import auth_context

        assert hasattr(auth_context, "AUTH_CONTEXT_STATE_KEY"), (
            "AUTH_CONTEXT_STATE_KEY constant must be defined in src.core.auth_context"
        )

    def test_context_builder_uses_constant(self):
        """context_builder.py must import and use AUTH_CONTEXT_STATE_KEY."""
        source = (PROJECT_ROOT / "src" / "a2a_server" / "context_builder.py").read_text()
        assert "AUTH_CONTEXT_STATE_KEY" in source, "context_builder.py must use AUTH_CONTEXT_STATE_KEY constant"

    def test_handler_uses_constant(self):
        """adcp_a2a_server.py must import and use AUTH_CONTEXT_STATE_KEY."""
        source = (PROJECT_ROOT / "src" / "a2a_server" / "adcp_a2a_server.py").read_text()
        assert "AUTH_CONTEXT_STATE_KEY" in source, "adcp_a2a_server.py must use AUTH_CONTEXT_STATE_KEY constant"

    def test_helpers_use_constant(self):
        """a2a_helpers.py must import and use AUTH_CONTEXT_STATE_KEY."""
        source = (PROJECT_ROOT / "tests" / "a2a_helpers.py").read_text()
        assert "AUTH_CONTEXT_STATE_KEY" in source, "tests/a2a_helpers.py must use AUTH_CONTEXT_STATE_KEY constant"


class TestNoRelativePathOpens:
    """Test files must not use relative open('src/...') paths."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "tests/unit/test_unified_auth_middleware.py",
            "tests/unit/test_shared_header_util.py",
            "tests/unit/test_media_buy_tenant_context.py",
            "tests/unit/test_a2a_call_context_builder.py",
            "tests/unit/test_no_duplicate_auth_functions.py",
            "tests/unit/test_lazy_tenant_no_contextvar_mutation.py",
        ],
    )
    def test_no_relative_open(self, rel_path):
        """Test files must not open files with relative paths like open('src/...')."""
        source = (PROJECT_ROOT / rel_path).read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            if 'open("src/' in line or "open('src/" in line:
                pytest.fail(f"{rel_path}:{lineno} uses relative open() path: {line.strip()}")


class TestCredentialCompareSurvivesNonAsciiHeaders:
    """A malformed credential header must be a non-match, never an exception.

    ``hmac.compare_digest`` raises ``TypeError`` on ``str`` operands carrying a
    non-ASCII character, and every credential compare in this codebase reads its
    presented value from a request header — which Starlette/Werkzeug decode as
    latin-1, so any byte > 0x7F arrives as exactly such a character. Before
    ``credentials_equal`` that raised out of the authentication check with no
    handler above it, so one malformed ``Authorization`` header returned 500
    instead of 401 on public endpoints (#1197 review).
    """

    def test_non_ascii_presented_credential_is_a_non_match(self):
        """A non-ASCII presented credential returns False rather than raising."""
        from src.core.auth_utils import credentials_equal

        assert credentials_equal("kéy-from-header", "stored-admin-token") is False

    def test_non_ascii_stored_credential_is_a_non_match(self):
        """The same holds when the *stored* side carries the non-ASCII character."""
        from src.core.auth_utils import credentials_equal

        assert credentials_equal("token-from-header", "stored-tökén") is False

    def test_identical_non_ascii_credentials_still_match(self):
        """Non-ASCII is not rejected wholesale — an exact match is still a match."""
        from src.core.auth_utils import credentials_equal

        assert credentials_equal("tökén", "tökén") is True

    def test_equal_and_unequal_ascii_credentials_are_unchanged(self):
        """The ASCII behaviour compare_digest provided is preserved exactly."""
        from src.core.auth_utils import credentials_equal

        assert credentials_equal("same-token", "same-token") is True
        assert credentials_equal("some-token", "other-token") is False

    def test_no_raw_string_compare_digest_remains_on_a_credential_path(self):
        """No call site may re-introduce the raising ``str`` compare.

        Both header-fed credential compares (``get_principal_from_token``'s
        admin-token branch and ``admin.auth_helpers``' API-key decorator) route
        through the one helper; a third copy would carry the 500 back.
        """
        for module in ("src/core/auth_utils.py", "src/admin/auth_helpers.py"):
            source = (PROJECT_ROOT / module).read_text()
            compares = [line for line in source.splitlines() if "compare_digest(" in line and "def " not in line]
            assert all("encode(" in line for line in compares), f"{module}: raw str compare_digest on a credential"
