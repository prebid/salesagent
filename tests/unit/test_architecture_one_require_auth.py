"""Guard: ``require_auth`` is defined once under src/, in ``src/admin/utils/helpers.py``.

prebid/salesagent#2204: ``src/adapters/gam_reporting_api.py`` carried its own ``require_auth``
and its own tenant check, ``get_tenant_access``, next to the canonical decorators in
``src/admin/utils/helpers.py``. A fix to the canonical decorator never reached the six routes
behind the private copy, which is how they came to answer 500 to every session whose user is a
plain email string (OIDC and ordinary Google login).

The rule: every binding of the NAME ``require_auth`` under src/ is the canonical one. A
``def``, ``async def``, ``class``, assignment, or import that binds the name to anything else
is a second authority. Importing the canonical decorator (``from src.admin.utils import
require_auth``, and the re-export in ``src/admin/utils/__init__.py``) is how every other module
reaches it, so those imports are not violations.

A renamed private copy is out of this guard's reach by construction. On a ``<tenant_id>`` route
it is caught by ``tests/unit/test_architecture_admin_tenant_scoping.py``, which requires
``require_tenant_access`` in the wrapper chain of every such route.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    format_failure,
    scan_src,
)

NAME = "require_auth"
CANONICAL_FILE = "src/admin/utils/helpers.py"
#: Modules an import may bind ``require_auth`` from: the defining module and its re-export.
CANONICAL_MODULES: frozenset[str] = frozenset({"src.admin.utils.helpers", "src.admin.utils"})


def find_require_auth_bindings(tree: ast.AST) -> list[int]:
    """Line of every node that binds the name ``require_auth`` to something other than the canonical one."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name == NAME:
            lines.append(node.lineno)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == NAME for t in targets):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            canonical = node.module in CANONICAL_MODULES and node.level == 0
            for alias in node.names:
                if (alias.asname or alias.name) == NAME and not (canonical and alias.name == NAME):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.asname == NAME for alias in node.names):
                lines.append(node.lineno)
    return sorted(lines)


@pytest.mark.arch_guard
def test_require_auth_is_defined_once_in_the_canonical_module() -> None:
    found = scan_src(find_require_auth_bindings)

    assert {path: len(lines) for path, lines in found.items()} == {CANONICAL_FILE: 1}, format_failure(
        summary=f"'{NAME}' must be bound exactly once under src/, by the def in {CANONICAL_FILE}",
        violations=[f"{path}:{line}" for path, lines in sorted(found.items()) for line in lines],
        fix_hint=(
            "Delete the local copy and import the canonical decorator. A route that takes <tenant_id> "
            "from the URL needs require_tenant_access (api_mode=True for JSON routes), not require_auth."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_flags_every_way_of_binding_the_name() -> None:
    assert_detector_catches_ast_snippets(
        find_require_auth_bindings,
        snippets={
            "bare decorator (the gam_reporting_api shape)": "def require_auth(f):\n    return f\n",
            "decorator factory (the helpers.py shape)": "def require_auth(admin_only=False):\n    pass\n",
            "async def": "async def require_auth(f):\n    return f\n",
            "nested def": "def outer():\n    def require_auth(f):\n        return f\n",
            "method": "class Guards:\n    def require_auth(self, f):\n        return f\n",
            "class": "class require_auth:\n    pass\n",
            "assignment": "require_auth = login_required\n",
            "annotated assignment": "require_auth: object = login_required\n",
            "inside try/except ImportError": "try:\n    import x\nexcept ImportError:\n    def require_auth(f):\n        return f\n",
            "import of another name as require_auth": "from flask_login import login_required as require_auth\n",
            "require_auth from another module": "from somewhere.auth import require_auth\n",
            "plain import aliased": "import auth_lib as require_auth\n",
        },
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "source",
    [
        "from src.admin.utils import require_auth\n",
        "from src.admin.utils.helpers import require_auth\n",
        "from src.admin.utils import execute_limited, require_auth, require_tenant_access\n",
        "@require_auth()\ndef view():\n    pass\n",
        'policy = "require_auth"\n',
        "def _require_auth_dep():\n    pass\n",
        "def test_require_auth_admin_only():\n    pass\n",
    ],
    ids=[
        "re-export import",
        "canonical import",
        "import among others",
        "decorator usage",
        "string literal",
        "longer name",
        "test-named function",
    ],
)
def test_detector_ignores_uses_of_the_canonical_decorator(source: str) -> None:
    assert find_require_auth_bindings(ast.parse(source)) == []
