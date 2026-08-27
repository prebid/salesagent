"""Guard: no second inline e2e admin-auth block outside the shared helper.

**The disease** (verbatim from the salesagent-mp53.6 codebase scan, #1291): an
e2e admin-route driver POSTs ``{_ADMIN_PREFIX}/test/auth`` inline — inside a
``requests.Session``, asserting the 200/302 auth response — instead of routing
through ``tests/e2e/_signing_e2e.py``'s shared driver helper.

Pre-mp53.6 there was exactly ONE such block, inside
``provision_signing_key_via_admin``, with no second caller to prove the shared
shape was correct. mp53.6 needed a SECOND admin-route driver
(``revoke_signing_key_via_admin``) and extracted the full auth + POST + assert +
flash-regex shape (architect review HIGH-2 / MEDIUM-6 — extracting only the auth
block and leaving the rest duplicated per driver would be the same defect one
layer down) rather than copying the block a second time. This guard pins that:
any FUTURE e2e admin-route driver must route through the shared helper too.

The exempt name is now ``_admin_post_rendered_page``, the auth+POST+render half.
It moved down one layer (salesagent-n78j0.1.4) because a THIRD driver
(``trigger_delivery_webhook_via_admin``) reads a route that reports a VERDICT —
sent / declined / broken — and so has to match more than one flash;
``_admin_post_expecting_flash`` is now itself a caller of the exempt helper, for
the routes that have exactly one acceptable outcome. The invariant is unchanged
and the exemption is no wider: still exactly ONE function may POST the auth path.

Method: SYNTACTIC/narrow disease (one exact literal, ``/test/auth``, POSTed via
``requests``) — an AST-node-precision guard, not a whole-file regex, so it
finds every POST call site by shape rather than by grepping the literal string
(which would also match this module's own diagnostic error messages).
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import (
    format_failure,
    iter_call_expressions,
    parse_module,
    repo_root,
)

_ALLOWED_FUNCTION = "_admin_post_rendered_page"
_AUTH_PATH_LITERAL = "/test/auth"
_SCOPE = ("tests/e2e/_signing_e2e.py",)


def _string_literals(node: ast.expr) -> list[str]:
    """Every string literal fragment inside *node* — handles both a bare Constant and an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            value.value for value in node.values if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
    return []


def find_inline_admin_auth_violations(tree: ast.Module) -> list[int]:
    """Line numbers of ``.post(...)`` calls that POST the admin-auth path outside the shared helper."""
    violations: list[int] = []
    enclosing: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                enclosing[id(child)] = node.name

    for call in iter_call_expressions(tree, name="post"):
        if not call.args:
            continue
        literals = _string_literals(call.args[0])
        if not any(_AUTH_PATH_LITERAL in literal for literal in literals):
            continue
        if enclosing.get(id(call)) == _ALLOWED_FUNCTION:
            continue
        violations.append(call.lineno)
    return violations


class TestNoDuplicateAdminAuthBlock:
    """The class-level pin: the admin-auth POST lives in exactly one place."""

    def test_every_scoped_module_exists(self):
        repo = repo_root()
        missing = [rel for rel in _SCOPE if not (repo / rel).exists()]
        assert not missing, format_failure(
            summary="Scoped module is gone — update _SCOPE, do not let the guard scan nothing:",
            violations=missing,
        )

    def test_no_inline_admin_auth_block_outside_the_shared_helper(self):
        repo = repo_root()
        violations = [
            f"{rel}:{lineno}: POSTs {_AUTH_PATH_LITERAL!r} outside {_ALLOWED_FUNCTION}()"
            for rel in _SCOPE
            for lineno in find_inline_admin_auth_violations(parse_module(repo / rel))
        ]
        assert not violations, format_failure(
            summary=f"An e2e admin-route driver POSTs the auth path inline instead of through {_ALLOWED_FUNCTION}():",
            violations=violations,
            fix_hint=(
                f"Route the new driver through {_ALLOWED_FUNCTION}(base_url, tenant_id=..., path=..., data=...) — "
                "or through _admin_post_expecting_flash(..., pattern=...) if the route has exactly one "
                "acceptable outcome. Copying the auth block (or the POST/assert/extract shape around it) is "
                "the duplication mp53.6 extracted this helper to stop."
            ),
        )


class TestDetectorCatchesTheDisease:
    """Meta-tests. A guard whose detector finds nothing is worthless."""

    def test_detector_flags_an_inline_auth_block_in_a_new_driver(self):
        tree = ast.parse(
            "def new_driver(base_url, *, tenant_id):\n"
            "    with requests.Session() as session:\n"
            "        session.post(f'{base_url}{_ADMIN_PREFIX}/test/auth', data={}, timeout=30)\n"
        )
        assert find_inline_admin_auth_violations(tree) == [3]

    def test_detector_flags_a_bare_string_literal_too(self):
        tree = ast.parse("def new_driver():\n    session.post('/admin/test/auth', data={})\n")
        assert find_inline_admin_auth_violations(tree) == [2]


class TestDetectorDoesNotOverfire:
    """Negative meta-tests — the detector must stay silent on what is NOT the disease."""

    def test_the_shared_helper_itself_is_exempt(self):
        tree = ast.parse(
            "def _admin_post_rendered_page(base_url, *, tenant_id, path, data):\n"
            "    with requests.Session() as session:\n"
            "        session.post(f'{base_url}{_ADMIN_PREFIX}/test/auth', data={}, timeout=30)\n"
        )
        assert find_inline_admin_auth_violations(tree) == []

    def test_the_flash_wrapper_is_not_exempt_by_name(self):
        """The exemption is ONE name, and it is the auth+POST helper — not its callers.

        ``_admin_post_expecting_flash`` is now a wrapper that owns no session; if it ever
        re-acquires an inline auth POST, that is the duplication returning and the guard
        must say so rather than excusing it on its old reputation.
        """
        tree = ast.parse(
            "def _admin_post_expecting_flash(base_url, *, tenant_id, path, data, pattern):\n"
            "    with requests.Session() as session:\n"
            "        session.post(f'{base_url}{_ADMIN_PREFIX}/test/auth', data={}, timeout=30)\n"
        )
        assert find_inline_admin_auth_violations(tree) == [3]

    def test_a_post_to_an_unrelated_path_is_clean(self):
        tree = ast.parse(
            "def other_driver(base_url):\n    session.post(f'{base_url}/admin/tenant/x/signing-keys/create', data={})\n"
        )
        assert find_inline_admin_auth_violations(tree) == []

    def test_a_get_call_is_not_scanned(self):
        """Only .post(...) calls are in scope — a GET can't drive the admin auth POST."""
        tree = ast.parse("def other():\n    session.get(f'{base_url}/test/auth')\n")
        assert find_inline_admin_auth_violations(tree) == []
