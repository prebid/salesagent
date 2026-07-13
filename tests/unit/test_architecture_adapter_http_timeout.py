"""Guard: every adapter ``requests`` HTTP call must pass a bounded ``timeout``.

Bounded-execution contract (#1544): ``update_media_buy`` runs under a ``FOR UPDATE``
row lock, so an unbounded ad-server call would pin that lock (and its DB
connection) until the TCP stack gives up. A ``requests`` call without a
connect+read ``timeout`` defaults to blocking forever. This AST guard fails the
build on any ``requests.<method>(...)`` in ``src/adapters`` that omits ``timeout=``
— use :data:`src.adapters.constants.ADAPTER_HTTP_TIMEOUT`.

Non-``requests`` clients (googleads SOAP, broadstreet client) bound themselves at
construction and are out of scope for this text-level guard.
"""

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root, safe_parse

_REQUESTS_METHODS = {"post", "get", "put", "delete", "patch", "request", "head", "options"}

# Pre-existing unbounded calls: (relative_file_path, line). Empty — every adapter
# HTTP call is bounded. It may only shrink; a new entry means a new unbounded call.
ALLOWLIST: set[tuple[str, int]] = set()


def _unbounded_requests_calls(tree: ast.AST) -> list[int]:
    """Line numbers of ``requests.<method>(...)`` calls that omit a ``timeout`` kwarg."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _REQUESTS_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "requests"
            and not any(kw.arg == "timeout" for kw in node.keywords)
        ):
            hits.append(node.lineno)
    return hits


def test_adapter_requests_calls_are_bounded():
    repo = repo_root()
    found: set[tuple[str, int]] = set()
    for py_file in (repo / "src" / "adapters").rglob("*.py"):
        tree = safe_parse(py_file)
        if tree is None:
            continue
        rel = str(py_file.relative_to(repo))
        for line in _unbounded_requests_calls(tree):
            found.add((rel, line))

    assert_violations_match_allowlist(
        found,
        ALLOWLIST,
        fix_hint=(
            "Pass timeout=ADAPTER_HTTP_TIMEOUT (from src.adapters.constants) to every "
            "requests call so a hung ad server cannot pin an update_media_buy row lock. #1544."
        ),
    )
