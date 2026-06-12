"""Meta-tests for the transport-aware realization seam (tests/harness/_realize.py).

Tests the dispatch primitive in isolation: ``realize_e2e`` selects the e2e
branch iff ``env.is_e2e``, the in-process branch is byte-identical to the
undecorated method, ``e2e_unsupported`` raises ``E2EUnsupportedSetup`` naming
the method, and ``functools.wraps`` metadata is preserved. No DB needed —
these are pure-logic tests on a stub env.
"""

from __future__ import annotations

import pytest

from tests.harness._realize import E2EUnsupportedSetup, e2e_unsupported, realize_e2e


class _StubEnv:
    """Minimal env exposing the single dispatch signal."""

    def __init__(self, is_e2e: bool) -> None:
        self.is_e2e = is_e2e
        self.calls: list[tuple[str, tuple, dict]] = []


class TestRealizeE2EDispatch:
    """realize_e2e dispatches to the correct branch based on env.is_e2e."""

    def test_in_process_branch_runs_when_not_e2e(self):
        def _e2e(self, *a, **kw):
            self.calls.append(("e2e", a, kw))
            return "e2e"

        class Env(_StubEnv):
            @realize_e2e(_e2e)
            def setup(self, value):
                self.calls.append(("in_process", (value,), {}))
                return "in_process"

        env = Env(is_e2e=False)
        result = env.setup(42)

        assert result == "in_process"
        assert env.calls == [("in_process", (42,), {})]

    def test_e2e_branch_runs_when_e2e(self):
        def _e2e(self, value):
            self.calls.append(("e2e", (value,), {}))
            return "e2e"

        class Env(_StubEnv):
            @realize_e2e(_e2e)
            def setup(self, value):
                self.calls.append(("in_process", (value,), {}))
                return "in_process"

        env = Env(is_e2e=True)
        result = env.setup(42)

        assert result == "e2e"
        assert env.calls == [("e2e", (42,), {})]

    def test_both_branches_receive_identical_arguments(self):
        """The same normalized args reach both branches — no copy-paste divergence."""
        seen: dict[str, tuple] = {}

        def _e2e(self, a, b, *, c):
            seen["e2e"] = (a, b, c)

        class Env(_StubEnv):
            @realize_e2e(_e2e)
            def setup(self, a, b, *, c):
                seen["in_process"] = (a, b, c)

        Env(is_e2e=False).setup(1, 2, c=3)
        Env(is_e2e=True).setup(1, 2, c=3)

        assert seen["in_process"] == (1, 2, 3)
        assert seen["e2e"] == (1, 2, 3)

    def test_wraps_metadata_preserved(self):
        def _e2e(self):
            pass

        class Env(_StubEnv):
            @realize_e2e(_e2e)
            def my_setup(self):
                """The original docstring."""

        assert Env.my_setup.__name__ == "my_setup"
        assert Env.my_setup.__doc__ == "The original docstring."


class TestE2EUnsupported:
    """e2e_unsupported declares an intent unrealizable over e2e."""

    def test_raises_e2e_unsupported_with_reason(self):
        class Env(_StubEnv):
            @realize_e2e(e2e_unsupported("no server fault-injection surface"))
            def set_adapter_error(self, exc):
                self.calls.append(("in_process", (exc,), {}))

        with pytest.raises(E2EUnsupportedSetup) as exc_info:
            Env(is_e2e=True).set_adapter_error(ValueError("boom"))

        assert "no server fault-injection surface" in str(exc_info.value)
        assert exc_info.value.reason == "no server fault-injection surface"

    def test_names_the_decorated_method(self):
        class Env(_StubEnv):
            @realize_e2e(e2e_unsupported("unrealizable"))
            def set_adapter_error(self, exc):
                pass

        with pytest.raises(E2EUnsupportedSetup) as exc_info:
            Env(is_e2e=True).set_adapter_error(ValueError())

        assert exc_info.value.method_name == "set_adapter_error"

    def test_in_process_branch_unaffected_when_not_e2e(self):
        class Env(_StubEnv):
            @realize_e2e(e2e_unsupported("unrealizable"))
            def set_adapter_error(self, exc):
                self.calls.append(("in_process", (exc,), {}))

        env = Env(is_e2e=False)
        env.set_adapter_error(ValueError("boom"))  # must NOT raise

        assert env.calls[0][0] == "in_process"
