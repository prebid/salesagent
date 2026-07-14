"""Guard: format-spec fetches route through format_resolver.fetch_format_spec.

Disease pattern (salesagent-mpo1): the registry format fetch was copy-pasted
three times with DIVERGENT error handling — one copy propagated typed
AdCPErrors, one rewrapped them into AdCPAdapterError (a creative-agent 429
became a terminal-looking creative failure), one had no handling at all. The
fix collapsed them into ONE helper (``format_resolver.fetch_format_spec``)
whose contract is: typed AdCPError propagates (recovery semantics preserved),
``None`` means unknown format, untyped errors log to ``None``.

This guard bans NEW direct ``<registry>.get_format(...)`` call sites outside
the shared helper's home module and the registry package itself, so the copies
cannot re-diverge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, parse_module

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"

# Modules allowed to call <x>.get_format directly:
# - format_resolver.py: home of the shared helper (the ONE fetch path)
# - creative_agent_registry.py: the registry's own internals
_ALLOWED_FILES = {
    "src/core/format_resolver.py",
    "src/core/creative_agent_registry.py",
}


def find_direct_get_format_calls(tree: ast.Module) -> list[int]:
    """Line numbers of SYNC ``<expr>.get_format(...)`` call sites in *tree*.

    ``await``-ed calls are excluded: the disease was the sync-bridge copies
    (each wrapping the coroutine + hand-rolling error handling); a native
    async caller has no bridge to diverge on and is covered by the
    transport-boundary error translation it already lives under.
    """
    awaited = {
        id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_format"
        and id(node) not in awaited
    ]


@pytest.mark.arch_guard
def test_no_direct_registry_get_format_outside_resolver():
    found: set[tuple[str, int]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _ALLOWED_FILES:
            continue
        for lineno in find_direct_get_format_calls(parse_module(path)):
            found.add((rel, lineno))
    assert_violations_match_allowlist(
        found,
        set(),
        fix_hint=(
            "Fetch format specs via format_resolver.fetch_format_spec (or get_format) — "
            "direct <registry>.get_format calls re-create the divergent error handling "
            "salesagent-mpo1 removed (typed transient errors must propagate uniformly)."
        ),
    )


class TestDetectorMetaTests:
    @pytest.mark.arch_guard
    def test_detector_catches_direct_call(self):
        tree = ast.parse(
            "def f(registry, url, fid):\n    return run_async_in_sync_context(registry.get_format(url, fid))\n"
        )
        assert find_direct_get_format_calls(tree) == [2]

    @pytest.mark.arch_guard
    def test_detector_ignores_helper_and_other_calls(self):
        tree = ast.parse(
            "def f(url, fid):\n"
            "    from src.core.format_resolver import fetch_format_spec\n"
            "    spec = fetch_format_spec(url, fid)\n"
            "    return spec\n"
        )
        assert find_direct_get_format_calls(tree) == []

    @pytest.mark.arch_guard
    def test_detector_ignores_native_async_callers(self):
        tree = ast.parse("async def f(registry, url, fid):\n    return await registry.get_format(url, fid)\n")
        assert find_direct_get_format_calls(tree) == []
