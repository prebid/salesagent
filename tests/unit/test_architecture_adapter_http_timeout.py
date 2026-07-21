"""Guard: every adapter ``requests`` HTTP call must pass a bounded ``timeout``.

Bounded-execution contract (#1544): ``update_media_buy`` runs under a ``FOR UPDATE``
row lock, so an unbounded ad-server call would pin that lock (and its DB
connection) until the TCP stack gives up. A ``requests`` call without a
connect+read ``timeout`` defaults to blocking forever. This AST guard fails the
build on any ``requests.<method>(...)`` in ``src/adapters`` that omits ``timeout=``
— use :data:`src.adapters.constants.ADAPTER_HTTP_TIMEOUT`.

``requests.Session(...)`` construction is flagged too: calls made through a
session-bound variable (``session.post(...)``) are invisible to the per-call
matcher (it anchors on the ``requests`` module name), so a Session would be a
silent escape hatch from the timeout contract. No adapter uses one today; if one
ever must, it needs its own per-call timeout discipline plus an explicit
allowlist entry here.

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
    """Line numbers of unbounded ``requests`` usage.

    Flags ``requests.<method>(...)`` calls that omit a ``timeout`` kwarg, and any
    ``requests.Session(...)`` construction (calls through a session variable
    evade the per-call matcher, so a Session is an escape hatch — see module
    docstring).
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "requests"
        ):
            continue
        if node.func.attr == "Session":
            hits.append(node.lineno)
        elif node.func.attr in _REQUESTS_METHODS and not any(kw.arg == "timeout" for kw in node.keywords):
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


# ── Guard self-tests: synthetic snippets through the matcher ─────────────────
# Mirrors test_architecture_media_buy_status_writes.py — a guard whose matcher
# silently stops matching is a green build with no protection.


def _snippet_hits(snippet: str) -> list[int]:
    """Run the matcher over a synthetic source snippet."""
    return _unbounded_requests_calls(ast.parse(snippet))


def test_guard_detects_unbounded_requests_call():
    """Known-bad: a ``requests`` call without ``timeout=`` is flagged at its line."""
    assert _snippet_hits('import requests\n\n\ndef f(url):\n    return requests.post(url, json={"a": 1})\n') == [5]


def test_guard_detects_session_construction():
    """Known-bad: ``requests.Session()`` is flagged — the per-call matcher cannot
    see calls through the session variable, so construction itself is the seam."""
    assert _snippet_hits("import requests\n\n\ndef f():\n    s = requests.Session()\n    return s\n") == [5]


def test_guard_accepts_bounded_requests_call():
    """Known-good: a ``timeout=`` kwarg (any expression) satisfies the guard, and
    non-HTTP ``requests`` attributes (exceptions module) are out of scope."""
    assert (
        _snippet_hits(
            "import requests\n"
            "from src.adapters.constants import ADAPTER_HTTP_TIMEOUT\n"
            "\n"
            "\n"
            "def f(url):\n"
            "    try:\n"
            "        return requests.get(url, timeout=ADAPTER_HTTP_TIMEOUT)\n"
            "    except requests.exceptions.Timeout:\n"
            "        raise\n"
        )
        == []
    )
