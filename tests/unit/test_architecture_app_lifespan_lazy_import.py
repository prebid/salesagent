"""Guard: ``src/app.py`` shutdown is service-agnostic — it delegates to the
``src.core.lifecycle`` shutdown-callback registry and never reaches into any
service's internals.

History:
- PR #1264 fix #3 wired ``await _webhook_service.close()`` into ``app_lifespan``
  via a *lazy* private import — an encapsulation violation and a tripwire.
- GH #1264 follow-up (salesagent-x2h.7) replaced the trick with the public
  accessor ``get_webhook_service_or_none()`` resolved at call time.
- salesagent-x2h.6 inverted the dependency entirely: services self-register
  their async ``close`` via ``src.core.lifecycle.register_shutdown`` at first
  construction, and ``app_lifespan`` only calls ``run_all_shutdown_callbacks()``.
  ``src/app.py`` no longer references the webhook service at all.

This guard now pins the x2h.6 contract:

1. ``src/app.py`` must NOT import the private ``_webhook_service`` (the
   private global must never leak — unchanged from x2h.7).
2. ``app_lifespan`` must reference ``run_all_shutdown_callbacks`` and
   ``src/app.py`` must import it from ``src.core.lifecycle``.
3. ``app_lifespan`` must contain NO ``from src.services...`` import and must
   not name any ``src.services`` webhook accessor — the lifespan is
   transport-only; service lifecycle belongs to the service.

The behavioral regression tests in
``tests/integration/test_app_lifespan_shutdown.py`` exercise the real ASGI
lifespan; this AST guard fails *fast* at ``make quality``.

beads: salesagent-x2h.6 (supersedes the x2h.7 accessor contract)
GH #1264
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP_PY = Path(__file__).resolve().parents[2] / "src" / "app.py"

_LIFESPAN_FUNC = "app_lifespan"
_WEBHOOK_MODULE = "src.services.protocol_webhook_service"
_PRIVATE_NAME = "_webhook_service"
_LIFECYCLE_MODULE = "src.core.lifecycle"
_RUN_ALL = "run_all_shutdown_callbacks"
# Service accessors that must NOT appear inside app_lifespan anymore.
_FORBIDDEN_SERVICE_NAMES = {"get_webhook_service_or_none", "get_protocol_webhook_service"}


def _find_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    return None


def _imports_private_singleton(node: ast.ImportFrom) -> bool:
    """True if ``node`` is ``from <webhook module> import _webhook_service``."""
    return node.module == _WEBHOOK_MODULE and any(alias.name == _PRIVATE_NAME for alias in node.names)


class TestAppLifespanIsServiceAgnostic:
    """``src/app.py`` shutdown must delegate to the lifecycle registry."""

    def _module_tree(self) -> ast.Module:
        assert _APP_PY.exists(), f"Expected {_APP_PY} to exist"
        return ast.parse(_APP_PY.read_text(encoding="utf-8"), filename=str(_APP_PY))

    def test_no_private_webhook_singleton_import_anywhere_in_app(self):
        """``src/app.py`` must NOT import the private ``_webhook_service`` at all.

        Reaching into another module's private global is the encapsulation
        violation GH #1264 follow-up removed; x2h.6 keeps the prohibition.
        """
        tree = self._module_tree()

        violations = [
            node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and _imports_private_singleton(node)
        ]
        assert not violations, (
            f"`from {_WEBHOOK_MODULE} import {_PRIVATE_NAME}` must NOT appear in "
            f"src/app.py (found at line {[n.lineno for n in violations]}). The "
            f"private module global must not leak — services self-register "
            f"shutdown via {_LIFECYCLE_MODULE}.register_shutdown (x2h.6)."
        )

    def test_app_lifespan_uses_shutdown_callback_registry(self):
        """``app_lifespan`` must call ``run_all_shutdown_callbacks``.

        Pins the shutdown path to the service-agnostic registry so the
        connection-pool release (PR #1264 fix #3) cannot silently disappear.
        """
        tree = self._module_tree()

        func = _find_function(tree, _LIFESPAN_FUNC)
        assert func is not None, (
            f"{_LIFESPAN_FUNC}() not found in src/app.py — the FastAPI lifespan "
            f"hook was renamed or removed. The shutdown registry call lives here."
        )

        used_names = {node.id for node in ast.walk(func) if isinstance(node, ast.Name)}
        assert _RUN_ALL in used_names, (
            f"{_LIFESPAN_FUNC}() must call `{_RUN_ALL}()` so every service's "
            f"self-registered shutdown callback fires. Without it the "
            f"ProtocolWebhookService requests.Session pool is never released "
            f"(PR #1264 fix #3 regression)."
        )

    def test_run_all_shutdown_callbacks_is_imported(self):
        """``src/app.py`` must import ``run_all_shutdown_callbacks`` from lifecycle."""
        tree = self._module_tree()

        imports_it = any(
            isinstance(node, ast.ImportFrom)
            and node.module == _LIFECYCLE_MODULE
            and any(alias.name == _RUN_ALL for alias in node.names)
            for node in ast.walk(tree)
        )
        assert imports_it, (
            f"src/app.py must import `{_RUN_ALL}` from {_LIFECYCLE_MODULE}. It is "
            f"the service-agnostic shutdown entry point (salesagent-x2h.6)."
        )

    def test_no_service_imports_in_app_lifespan(self):
        """``app_lifespan`` must contain NO ``from src.services...`` import and
        must not name any service webhook accessor.

        Service lifecycle belongs to the service (self-registration). The
        lifespan is transport-only — an inverted dependency on a concrete
        service is exactly what x2h.6 removed.
        """
        tree = self._module_tree()
        func = _find_function(tree, _LIFESPAN_FUNC)
        assert func is not None, f"{_LIFESPAN_FUNC}() not found in src/app.py"

        service_imports = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.services")
        ]
        assert not service_imports, (
            f"{_LIFESPAN_FUNC}() must not import from src.services "
            f"(found at lines {[n.lineno for n in service_imports]}). Services "
            f"self-register shutdown via {_LIFECYCLE_MODULE}.register_shutdown."
        )

        named = {node.id for node in ast.walk(func) if isinstance(node, ast.Name)}
        leaked = named & _FORBIDDEN_SERVICE_NAMES
        assert not leaked, (
            f"{_LIFESPAN_FUNC}() must not reference service accessors {sorted(leaked)} "
            f"— the lifespan is service-agnostic (salesagent-x2h.6)."
        )
