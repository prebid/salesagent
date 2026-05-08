"""Regression tests: auth infrastructure hardening.

Core invariant: Auth infrastructure must be defensively robust — immutable
state, shared constants, portable test paths, and consistent middleware style.

beads: salesagent-5p7g
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

        assert hasattr(
            auth_context, "AUTH_CONTEXT_STATE_KEY"
        ), "AUTH_CONTEXT_STATE_KEY constant must be defined in src.core.auth_context"


class TestNoRelativePathOpens:
    """Test files must not use relative open('src/...') paths."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "tests/unit/test_unified_auth_middleware.py",
            "tests/unit/test_shared_header_util.py",
            "tests/unit/test_media_buy_tenant_context.py",
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
