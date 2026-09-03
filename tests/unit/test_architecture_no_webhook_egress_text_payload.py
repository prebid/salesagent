"""Structural guard: webhook_egress.py's public payload parameter stays dict-only.

GH #1802: the signer-side duplicate-object-key MUST (AdCP 3.1.1
L1/security.mdx §Duplicate object keys) is satisfied *by construction* today
-- ``prepare_signed_request``'s ``payload: dict[str, Any]`` cannot hold a
duplicate key, so there is no runtime check to write. mypy already enforces
this (a caller passing ``bytes``/``str`` is a type error in ``make quality``),
but nothing stops a FUTURE PR from silently widening the annotation to admit
text -- at which point the only thing left is a docstring, and docstrings do
not fail builds. This guard AST-scans the three public functions in
``src/core/security/webhook_egress.py`` and fails if any of them ever accepts
``bytes``/``str`` for the payload it hands to the signer, so a future
text-accepting entry point is forced to go through
``src.core.security.webhook_strict_json.loads_rejecting_duplicate_keys``
first rather than reopening the gap silently.

Mirrors this file's existing sibling guards at this density:
``test_architecture_no_signed_webhook_json_send.py`` and
``test_architecture_no_hardcoded_unsigned_webhook.py``.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import repo_root

_MODULE_PATH = "src/core/security/webhook_egress.py"
# Repointed in Epic D lane C4 alongside the private rename, and EXTENDED to the two
# public entry points — leaving them out would let a future ``payload: bytes``
# on the seam itself walk straight past this MUST. Repointed again in
# GH #1802, which deleted the two private payload-taking helpers and
# folded their bodies into ``deliver_webhook`` / ``adeliver_webhook``. Every name here MUST resolve in the
# module: see test_every_guarded_function_exists below, without which a deleted name
# just stops being scanned and this guard degrades one silent notch at a time.
_GUARDED_FUNCTIONS = frozenset(
    {
        "prepare_signed_request",
        "deliver_webhook",
        "adeliver_webhook",
    }
)
_PAYLOAD_PARAM = "payload"

# Names whose presence anywhere in the payload parameter's annotation means
# "this can carry parsed-from-text JSON, unrepresentable as a duplicate-key-free
# dict" -- the exact thing the signer-side MUST relies on being impossible.
_FORBIDDEN_ANNOTATION_NAMES = frozenset({"bytes", "str", "Any"})


def _annotation_names(node: ast.expr | None) -> set[str]:
    """Every ``Name``/``Attribute`` identifier appearing anywhere in an annotation."""
    if node is None:
        return set()
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def find_payload_annotation_violations(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Return (lineno, func_name, reason) for a payload param whose annotation
    admits ``bytes``/``str``/bare ``Any`` instead of exactly ``dict[str, Any]``.

    A bare top-level ``dict[str, Any]`` is safe (``Any`` is the VALUE type
    parameter there, not the payload's own type) -- only a payload annotated
    directly as ``bytes``, ``str``, a union including either, or bare ``Any``
    (accepting literally anything, including text) is a violation.
    """
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in _GUARDED_FUNCTIONS:
            continue
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.arg != _PAYLOAD_PARAM:
                continue
            annotation = arg.annotation
            # The safe, expected shape: a Subscript whose base is exactly `dict`.
            if (
                isinstance(annotation, ast.Subscript)
                and isinstance(annotation.value, ast.Name)
                and annotation.value.id == "dict"
            ):
                continue
            names = _annotation_names(annotation)
            bad = names & _FORBIDDEN_ANNOTATION_NAMES
            if bad or annotation is None:
                reason = f"admits {sorted(bad)}" if bad else "has no annotation at all"
                violations.append((node.lineno, node.name, reason))
    return violations


class TestWebhookEgressPayloadStaysDictOnly:
    def test_no_text_accepting_payload_parameter(self) -> None:
        path = repo_root() / _MODULE_PATH
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations = find_payload_annotation_violations(tree)
        assert not violations, (
            "webhook_egress.py's payload parameter must stay dict[str, Any]-only -- a text-accepting "
            "signature reopens the signer-side duplicate-key gap (GH #1802): "
            f"{violations}"
        )


class TestDetectorCatchesKnownBadSnippets:
    def test_catches_a_bytes_union(self) -> None:
        snippet = """
def prepare_signed_request(payload: dict[str, Any] | bytes, secret, headers):
    pass
"""
        tree = ast.parse(snippet)
        violations = find_payload_annotation_violations(tree)
        assert len(violations) == 1
        assert violations[0][1] == "prepare_signed_request"

    def test_catches_a_bare_str_payload(self) -> None:
        snippet = """
def deliver_webhook(url, payload: str, *, scheme=None, credentials=None):
    pass
"""
        tree = ast.parse(snippet)
        violations = find_payload_annotation_violations(tree)
        assert len(violations) == 1

    def test_catches_a_bare_any_payload(self) -> None:
        snippet = """
async def adeliver_webhook(url, payload: Any, *, scheme=None, credentials=None):
    pass
"""
        tree = ast.parse(snippet)
        violations = find_payload_annotation_violations(tree)
        assert len(violations) == 1


class TestDetectorAllowsSafeSnippets:
    def test_allows_dict_str_any_payload(self) -> None:
        snippet = """
def prepare_signed_request(payload: dict[str, Any], secret, headers, *, timestamp=None):
    pass
"""
        tree = ast.parse(snippet)
        assert find_payload_annotation_violations(tree) == []

    def test_ignores_unrelated_functions(self) -> None:
        snippet = """
def some_other_function(payload: bytes):
    pass
"""
        tree = ast.parse(snippet)
        assert find_payload_annotation_violations(tree) == []


class TestTheGuardHasSubjects:
    """Every guarded name must still be defined in the scanned module.

    Added in GH #1802. That change deleted two of the five names this set
    then held, and nothing failed: a name that no longer resolves is simply never
    matched by the AST walk, so the guard quietly shrank its own population while
    still reporting green. This is the assertion that would have caught it.
    """

    def test_every_guarded_function_exists(self) -> None:
        path = repo_root() / _MODULE_PATH
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = sorted(_GUARDED_FUNCTIONS - defined)
        assert not missing, (
            f"{missing} is guarded but no longer defined in {_MODULE_PATH} -- the scan silently "
            "skips names that do not exist, so a stale entry is coverage this guard does not have"
        )
