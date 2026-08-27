"""Structural guard: every advisory ``Error(code=...)`` construction in
``src/core/tools/`` must be routed through ``normalize_advisory_errors``
somewhere in the same module.

The advisory lane (``errors[]`` inside a success response) serializes
verbatim -- unlike a raised ``AdCPError``, nothing at the transport boundary
re-codes it. A module that builds a marked advisory ``Error(code=...)`` (the
same ``# structural-guard:`` marker ``test_architecture_no_error_construction_
in_impl.py`` uses to recognize a legitimate per-item advisory) but never calls
``normalize_advisory_errors`` can leak a non-standard code and omit
``recovery`` entirely -- exactly the regression this guard would have caught
in accounts.py and media_buy_list.py (#1721 M1).

Coarse by design: module-level "does this file call the normalizer at all",
not a data-flow proof that every specific site is wrapped. That is enough to
catch "the whole file never routes through the shared lane" -- the actual
disease found here -- without the false-positive risk of tracing every
intermediate helper call.

Recognizes calls through a module-level alias (e.g.
``_normalize_advisory_errors = normalize_advisory_errors``,
``media_buy_delivery.py``'s existing pattern) as well as the literal name.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import (
    collect_error_aliases,
    iter_call_expressions,
    rel,
    safe_parse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src" / "core" / "tools"]

_SKIP_MARKER = "# structural-guard:"
_NORMALIZER_NAME = "normalize_advisory_errors"


def _has_marked_advisory_site(tree: ast.Module, source_lines: list[str]) -> bool:
    """True if the module constructs at least one marked advisory ``Error(code=...)``."""
    aliases = collect_error_aliases(tree)
    for node in iter_call_expressions(tree):
        func = node.func
        matched = (isinstance(func, ast.Name) and func.id in aliases) or (
            isinstance(func, ast.Attribute) and func.attr == "Error"
        )
        if not matched or not any(kw.arg == "code" for kw in node.keywords):
            continue
        start = node.lineno - 1
        end = (getattr(node, "end_lineno", None) or node.lineno) - 1
        if any(_SKIP_MARKER in source_lines[i] for i in range(start, min(end + 1, len(source_lines)))):
            return True
    return False


def _normalizer_aliases(tree: ast.Module) -> set[str]:
    """``{_NORMALIZER_NAME}`` plus any module-level ``alias = normalize_advisory_errors``."""
    aliases = {_NORMALIZER_NAME}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Name) and node.value.id == _NORMALIZER_NAME):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _calls_normalizer(tree: ast.Module) -> bool:
    """True if the module calls ``normalize_advisory_errors`` (or a module-level alias of it)."""
    aliases = _normalizer_aliases(tree)
    for node in iter_call_expressions(tree):
        func = node.func
        if isinstance(func, ast.Name) and func.id in aliases:
            return True
        if isinstance(func, ast.Attribute) and func.attr in aliases:
            return True
    return False


def _unrouted_modules() -> list[str]:
    """Files with a marked advisory site that never call the normalizer."""
    unrouted: list[str] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            tree = safe_parse(py_file)
            if tree is None:
                continue
            source_lines = py_file.read_text().splitlines()
            if not _has_marked_advisory_site(tree, source_lines):
                continue
            if not _calls_normalizer(tree):
                unrouted.append(rel(py_file))
    return unrouted


def test_advisory_errors_route_through_normalizer():
    """Every module building a marked advisory ``Error(code=...)`` calls ``normalize_advisory_errors``."""
    unrouted = _unrouted_modules()
    assert not unrouted, (
        "Found module(s) constructing a marked advisory Error(code=...) "
        "(# structural-guard: ...) that never call normalize_advisory_errors -- the "
        "advisory would reach the wire with an unguaranteed code and no recovery "
        "classification. Route the errors=[...] list through normalize_advisory_errors() "
        "before it reaches the response:\n" + "\n".join(f"  {m}" for m in unrouted)
    )


# ---------------------------------------------------------------------------
# Meta-tests: the guard's own detection logic, on synthetic source.
# ---------------------------------------------------------------------------


def _parse(source: str) -> tuple[ast.Module, list[str]]:
    return ast.parse(source), source.splitlines()


def test_meta_flags_marked_site_with_no_normalizer_call():
    """Positive: a marked advisory site with zero normalize_advisory_errors calls is unrouted.

    This is the exact shape accounts.py and media_buy_list.py had before the fix.
    """
    tree, lines = _parse(
        "from adcp.types import Error\n"
        "\n"
        "def build():\n"
        "    return [\n"
        "        Error(  # structural-guard: advisory per-account result\n"
        "            code='VALIDATION_ERROR', message='bad',\n"
        "        )\n"
        "    ]\n"
    )
    assert _has_marked_advisory_site(tree, lines)
    assert not _calls_normalizer(tree)


def test_meta_does_not_flag_module_that_routes_through_normalizer_by_name():
    """Negative: the same marked site, but the module calls normalize_advisory_errors directly."""
    tree, lines = _parse(
        "from adcp.types import Error\n"
        "from src.core.exceptions import normalize_advisory_errors\n"
        "\n"
        "def build():\n"
        "    errors = [\n"
        "        Error(  # structural-guard: advisory per-account result\n"
        "            code='VALIDATION_ERROR', message='bad',\n"
        "        )\n"
        "    ]\n"
        "    return normalize_advisory_errors(errors)\n"
    )
    assert _has_marked_advisory_site(tree, lines)
    assert _calls_normalizer(tree)


def test_meta_recognizes_module_level_alias_call_not_just_the_literal_name():
    """Would-be-missed case: a module that calls the normalizer through a
    module-level ALIAS (``_normalize_advisory_errors = normalize_advisory_errors``
    then ``_normalize_advisory_errors(...)``) rather than the literal name at
    the call site. media_buy_delivery.py used exactly this pattern until the
    alias was inlined away (PR #1721 review round 2, F7); kept as a synthetic
    regex-slip meta-test so a guard that only pattern-matches the literal
    ``normalize_advisory_errors(`` call can't quietly regress on the next
    module that reaches for an alias -- the alias-tracking in
    ``_normalizer_aliases`` is what prevents that.
    """
    tree, lines = _parse(
        "from adcp.types import Error\n"
        "from src.core.exceptions import normalize_advisory_errors\n"
        "\n"
        "_normalize_advisory_errors = normalize_advisory_errors\n"
        "\n"
        "def build():\n"
        "    errors = [\n"
        "        Error(  # structural-guard: advisory per-buy result\n"
        "            code='VALIDATION_ERROR', message='bad',\n"
        "        )\n"
        "    ]\n"
        "    return _normalize_advisory_errors(errors)\n"
    )
    assert _has_marked_advisory_site(tree, lines)
    assert _calls_normalizer(tree)


def test_meta_unmarked_advisory_site_is_not_flagged():
    """An Error(code=...) WITHOUT the # structural-guard: marker is out of this
    guard's scope entirely (it is either not an advisory, or it is caught by the
    separate Pattern-A cap guard) -- this guard must not false-positive on it.
    """
    tree, lines = _parse(
        "from adcp.types import Error\n\ndef build():\n    return [Error(code='VALIDATION_ERROR', message='bad')]\n"
    )
    assert not _has_marked_advisory_site(tree, lines)
