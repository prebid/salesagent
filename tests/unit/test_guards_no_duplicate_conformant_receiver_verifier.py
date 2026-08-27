"""Guard: no tests/*.py module reimplements the webhook-conformant-receiver verifier.

``tests/helpers/signing.py::verify_as_conformant_receiver`` is the ONE shared home for
the pattern -- constructing an ``adcp.signing.verifier.StaticJwksResolver`` and calling
``verify_request_signature`` with the ``expected_adcp_use="request-signing"`` SDK-divergence
substitution -- that proves a captured webhook verifies against a served JWKS. Before this
guard, that exact shape lived as a private function duplicated inside a single integration
test module (a codebase-scan MIGRATE finding, RFC 9421 webhook-signing epic #1291); this
bans a future reintroduction of a second copy.

A function that constructs ``StaticJwksResolver`` and calls ``verify_request_signature``
with an ``expected_adcp_use=`` keyword -- the SDK-divergence substitution that only the
webhook-conformant-receiver shape needs -- in the SAME body is the disease shape. Other
tests/*.py modules construct ``StaticJwksResolver`` or call ``verify_request_signature``
independently for genuinely different verification concerns (revocation, conformance
vectors, replay-store, raw-wire gaps); ``tests/integration/test_replay_store.py::_verify``
is exactly this -- it builds a ``StaticJwksResolver`` too, but passes
``capability``/``operation``/``replay_store``, never ``expected_adcp_use``, because it is
grading replay detection, not the conformant-receiver substitution. A codebase-wide
cross-check confirmed it is not a duplicate; requiring the ``expected_adcp_use=`` marker is
what keeps this guard from flagging it.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT, iter_module_trees

_TESTS_DIR = REPO_ROOT / "tests"
_SHARED_HOME = "tests/helpers/signing.py"


def _calls_name(node: ast.AST, name: str) -> bool:
    """True if *node* is a Call whose callee is named or attributed *name*."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == name
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return False


def _verify_call_with_adcp_use_substitution(node: ast.AST) -> bool:
    """True if *node* is a verify_request_signature(...) call carrying the
    expected_adcp_use= keyword -- the SDK-divergence substitution unique to the
    conformant-receiver shape (tests/helpers/signing.py:790 verify_as_conformant_receiver)."""
    return _calls_name(node, "verify_request_signature") and any(
        kw.arg == "expected_adcp_use" for kw in getattr(node, "keywords", [])
    )


def _function_reimplements_verifier(func_node: ast.AST) -> bool:
    """True if *func_node*'s body constructs StaticJwksResolver AND calls
    verify_request_signature(..., expected_adcp_use=...) -- the conformant-receiver
    verification shape, not merely any StaticJwksResolver + verify_request_signature
    co-occurrence (which also matches genuinely different verification concerns like
    replay-store testing)."""
    has_resolver = False
    has_substituted_verify_call = False
    for node in ast.walk(func_node):
        if _calls_name(node, "StaticJwksResolver"):
            has_resolver = True
        if _verify_call_with_adcp_use_substitution(node):
            has_substituted_verify_call = True
    return has_resolver and has_substituted_verify_call


def _scan() -> set[tuple[str, int, str]]:
    violations: set[tuple[str, int, str]] = set()
    for tree, rel in iter_module_trees([_TESTS_DIR]):
        if rel == _SHARED_HOME:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _function_reimplements_verifier(node):
                violations.add((rel, node.lineno, node.name))
    return violations


class TestNoDuplicateConformantReceiverVerifier:
    """Only tests/helpers/signing.py may combine StaticJwksResolver + verify_request_signature."""

    def test_no_new_reimplementations(self):
        violations = _scan()
        assert not violations, (
            "tests/*.py functions (outside tests/helpers/signing.py) that construct "
            "StaticJwksResolver AND call verify_request_signature -- reimplementing "
            "verify_as_conformant_receiver instead of importing it:\n"
            + "\n".join(f"  - {p}:{n} ({fn})" for p, n, fn in sorted(violations))
        )

    def test_positive_meta_detects_reimplementation(self):
        tree = ast.parse(
            "def _verify(signed, jwks):\n"
            "    resolver = StaticJwksResolver(jwks)\n"
            "    return verify_request_signature(method='POST', url='x', headers={}, body=b'', "
            "options=VerifyOptions(jwks_resolver=resolver), expected_adcp_use='request-signing')\n"
        )
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert _function_reimplements_verifier(func_node), "detector failed to flag a reimplemented verifier"

    def test_positive_meta_detects_attribute_call_form(self):
        """The disease shape still counts when reached via module-qualified calls
        (``mw.verify_request_signature(...)`` / ``verifier.StaticJwksResolver(...)``)."""
        tree = ast.parse(
            "def _verify(signed, jwks):\n"
            "    resolver = verifier.StaticJwksResolver(jwks)\n"
            "    return mw.verify_request_signature(method='POST', url='x', headers={}, body=b'', "
            "expected_adcp_use='request-signing')\n"
        )
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert _function_reimplements_verifier(func_node), "detector missed the attribute-call form"

    def test_negative_meta_ignores_resolver_alone(self):
        """Constructing StaticJwksResolver for an unrelated purpose (e.g. a
        revocation or conformance-vector test) is not the disease shape by itself."""
        tree = ast.parse("def _build(jwks):\n    return StaticJwksResolver(jwks)\n")
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert not _function_reimplements_verifier(func_node)

    def test_negative_meta_ignores_replay_store_shape(self):
        """tests/integration/test_replay_store.py::_verify's actual shape: builds a
        StaticJwksResolver AND calls verify_request_signature, but never passes
        expected_adcp_use -- confirmed a different verification concern (replay
        detection), not a duplicate, by the codebase-scan cross-check."""
        tree = ast.parse(
            "def _verify(headers, jwk, store):\n"
            "    verify_request_signature(method='POST', url='x', headers=headers, body=b'', "
            "options=VerifyOptions(capability=VerifierCapability(), operation='get_products', "
            "jwks_resolver=StaticJwksResolver({'keys': [jwk]}), replay_store=store))\n"
        )
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert not _function_reimplements_verifier(func_node), (
            "detector false-positived on the replay-store verification shape"
        )

    def test_negative_meta_ignores_verify_call_alone(self):
        """Calling verify_request_signature (even with expected_adcp_use=) without
        also building a StaticJwksResolver is not the full disease shape."""
        tree = ast.parse(
            "def _check(**kwargs):\n    return verify_request_signature(expected_adcp_use='request-signing', "
            "**kwargs)\n",
        )
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert not _function_reimplements_verifier(func_node)

    def test_negative_meta_ignores_unrelated_calls(self):
        tree = ast.parse("def _noop():\n    return some_other_call(1, 2)\n")
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        assert not _function_reimplements_verifier(func_node)
