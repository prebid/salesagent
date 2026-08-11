"""Guard: _impl functions must not call .model_dump() or .model_dump_internal().

Serialization is the transport wrapper's responsibility, not business logic.
_impl functions should return Pydantic model objects and let the transport
boundary decide how to serialize them.

Legitimate uses (NOT violations):
- Schema classes defining model_dump() overrides (Pattern #4 nested serialization)
- Transport wrappers calling model_dump() before returning to the client
- Model -> model transforms, where the dump is an internal round-trip used to drop a
  nested write-only field before re-validating (the function returns models, not dicts).
  ``_scrub_notification_credentials`` and ``_scrub_business_entity`` in
  src/core/tools/accounts.py are the examples: they keep authentication.credentials and
  billing_entity.bank off the wire. Detected structurally by the return annotation, not
  by name and not by allowlist -- see ``_returns_dicts``.

Current violations are serializing for DB storage (raw_request, workflow step
response_data). These should be replaced with typed repository methods that
accept model objects directly, eliminating the manual serialization.

SCOPE: the rule is about what runs INSIDE the _impl call graph, not about what a
function is named. Matching only ``node.name.endswith("_impl")`` let the offending
call escape by being moved one frame out, into a same-module private helper the
_impl still calls -- which is exactly how six serializing helpers in accounts.py
sat invisible to this guard (#1721 review F5). The scan therefore starts at each
_impl function and follows module-local calls transitively.

beads: salesagent-hr8n
"""

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions

TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "tools"

BANNED_METHODS = {"model_dump", "model_dump_internal"}

# Known violations — allowlist shrinks as violations are fixed.
# Each entry is (relative_path_from_tools_dir, line_number).
# Now EMPTY: _update_media_buy_impl moved workflow-step serialization into
# ContextManager.audit_workflow_step_result; _get_products_impl logs the model
# directly (no model_dump); _list_creatives_impl's internal filter merge moved
# into the module-level _merge_structured_filters helper.
KNOWN_VIOLATIONS: set[tuple[str, int]] = set()


def _module_local_callees(node: ast.FunctionDef | ast.AsyncFunctionDef, defined: dict[str, ast.AST]) -> set[str]:
    """Names *node* calls that are functions defined in the same module.

    Only BARE ``helper(...)`` calls count. Matching ``obj.helper(...)`` by attribute
    name would conflate a method call with a module-level function that happens to
    share its name -- ``adapter.create_media_buy(...)`` inside an _impl would drag in
    the module's own ``create_media_buy`` transport wrapper, whose model_dump() call
    is legitimate. Anything not defined in this module is out of scope: following
    imports would turn the guard into a whole-program analysis for no extra signal,
    since the evasion shape it exists to catch is a helper extracted next to its caller.
    """
    return {
        call.func.id
        for call in iter_call_expressions(node)
        if isinstance(call.func, ast.Name) and call.func.id in defined
    }


def _returns_dicts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *node*'s return annotation mentions ``dict`` -- a serialization boundary.

    This is what separates the two things a ``model_dump()`` call can mean inside the
    _impl call graph:

    * ``-> list[dict[str, object]] | None`` -- the function's PURPOSE is to hand back
      plain data. That is serialization, and the guard's rule is that it belongs at the
      transport or data layer, not in business logic.
    * ``-> list[NotificationConfig] | None`` / ``-> BusinessEntity | None`` -- the
      function takes a model and hands back a model; the dump is an internal round-trip
      used to drop a nested write-only field before re-validating. Nothing is serialized
      across a boundary, so the rule was never about it.

    Widening the scan to the call graph without this distinction would force the
    write-only-field scrubbers (``_scrub_notification_credentials``,
    ``_scrub_business_entity``) into a contorted rewrite to satisfy a rule aimed at
    something else -- or, worse, onto the allowlist, where a legitimate construct is
    indistinguishable from debt (#1721 review F5).

    An UNANNOTATED return is treated as a serialization boundary: the guard fails closed.
    """
    if node.returns is None:
        return True
    return any(isinstance(n, ast.Name) and n.id == "dict" for n in ast.walk(node.returns))


def _banned_calls_in(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """(lineno, method) for each banned serialization call directly inside *node*."""
    return [
        (call.lineno, call.func.attr)
        for call in iter_call_expressions(node)
        if isinstance(call.func, ast.Attribute) and call.func.attr in BANNED_METHODS
    ]


def _find_model_dump_in_impl() -> list[tuple[str, int, str, str]]:
    """Find banned serialization calls reachable from any _impl function.

    Walks each module's _impl entrypoints and follows module-local calls transitively,
    so a helper that does the serializing on an _impl's behalf is a violation of the
    same rule regardless of what it is called.

    Returns list of (relative_path, lineno, func_name, method_name).
    """
    violations = []
    seen: set[tuple[str, int]] = set()

    for py_file in sorted(TOOLS_DIR.rglob("*.py")):
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        defined: dict[str, ast.AST] = {
            n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        entrypoints = [name for name in defined if name.endswith("_impl")]

        reached: set[str] = set()
        queue = list(entrypoints)
        while queue:
            name = queue.pop()
            if name in reached:
                continue
            reached.add(name)
            queue.extend(_module_local_callees(defined[name], defined) - reached)

        rel_path = str(py_file.relative_to(TOOLS_DIR))
        for name in sorted(reached):
            if not _returns_dicts(defined[name]):
                continue  # model -> model transform, not a serialization boundary
            for lineno, method in _banned_calls_in(defined[name]):
                key = (rel_path, lineno)
                if key in seen:
                    continue
                seen.add(key)
                violations.append((rel_path, lineno, name, method))

    return violations


class TestNoModelDumpInImpl:
    """_impl functions must not call .model_dump() or .model_dump_internal()."""

    @pytest.mark.arch_guard
    def test_no_new_model_dump_violations(self):
        """No NEW .model_dump() calls in _impl functions beyond the known allowlist."""
        all_violations = _find_model_dump_in_impl()

        new_violations = []
        for rel_path, lineno, func_name, method in all_violations:
            if (rel_path, lineno) not in KNOWN_VIOLATIONS:
                new_violations.append(f"  {rel_path}:{lineno} in {func_name}() — .{method}()")

        assert not new_violations, (
            f"Found {len(new_violations)} NEW .model_dump() call(s) in _impl functions.\n"
            f"Serialization belongs in the transport wrapper, not business logic.\n" + "\n".join(new_violations)
        )

    @pytest.mark.arch_guard
    def test_known_violations_not_stale(self):
        """Every entry in KNOWN_VIOLATIONS must still exist in the source.

        When a violation is fixed, remove it from the allowlist.
        Stale entries mean the allowlist is not being maintained.
        """
        all_violations = _find_model_dump_in_impl()
        actual_sites = {(v[0], v[1]) for v in all_violations}
        assert_violations_match_allowlist(
            actual_sites,
            KNOWN_VIOLATIONS,
            fix_hint="Remove fixed entries from KNOWN_VIOLATIONS.",
        )

    @pytest.mark.arch_guard
    def test_violation_count_documented(self):
        """Track the total violation count — should only decrease over time."""
        all_violations = _find_model_dump_in_impl()
        assert len(all_violations) == len(KNOWN_VIOLATIONS), (
            f"Violation count changed: found {len(all_violations)}, "
            f"allowlist has {len(KNOWN_VIOLATIONS)}. "
            f"Update the allowlist (remove fixed entries or investigate new ones)."
        )


class TestDetectorMetaTests:
    """The widened detector must catch the evasion shape AND not invent violations.

    A structural guard that produces false positives gets disabled, so the negative
    cases matter as much as the positive one.
    """

    @staticmethod
    def _violations(source: str) -> list[tuple[int, str, str]]:
        """Run the real traversal over *source* and return (lineno, func, method)."""
        tree = ast.parse(source)
        defined = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        reached: set[str] = set()
        queue = [name for name in defined if name.endswith("_impl")]
        while queue:
            name = queue.pop()
            if name in reached:
                continue
            reached.add(name)
            queue.extend(_module_local_callees(defined[name], defined) - reached)
        return [
            (lineno, name, method)
            for name in sorted(reached)
            if _returns_dicts(defined[name])
            for lineno, method in _banned_calls_in(defined[name])
        ]

    @pytest.mark.arch_guard
    def test_catches_dump_moved_into_a_same_module_helper(self):
        """The evasion this guard was widened for: the call one frame out of the _impl."""
        found = self._violations(
            "def _serialize(x) -> dict[str, object]:\n"
            "    return x.model_dump(mode='json')\n"
            "def _sync_impl(req):\n"
            "    return _serialize(req)\n"
        )
        assert [(f, m) for _, f, m in found] == [("_serialize", "model_dump")]

    @pytest.mark.arch_guard
    def test_ignores_a_helper_no_impl_reaches(self):
        """Only the _impl call graph is in scope — an unreached helper is not a violation."""
        found = self._violations(
            "def _serialize(x) -> dict[str, object]:\n    return x.model_dump()\ndef _sync_impl(req):\n    return req\n"
        )
        assert found == []

    @pytest.mark.arch_guard
    def test_ignores_a_model_to_model_transform(self):
        """The scrubber shape: dumps to drop a write-only field, hands back a model."""
        found = self._violations(
            "def _scrub(config) -> NotificationConfig:\n"
            "    data = config.model_dump(mode='json')\n"
            "    data.pop('credentials', None)\n"
            "    return NotificationConfig.model_validate(data)\n"
            "def _sync_impl(req):\n"
            "    return _scrub(req)\n"
        )
        assert found == []

    @pytest.mark.arch_guard
    def test_unannotated_return_fails_closed(self):
        """No return annotation means no evidence it returns models — treat as a boundary."""
        found = self._violations(
            "def _serialize(x):\n    return x.model_dump()\ndef _sync_impl(req):\n    return _serialize(req)\n"
        )
        assert [(f, m) for _, f, m in found] == [("_serialize", "model_dump")]

    @pytest.mark.arch_guard
    def test_method_call_is_not_confused_with_a_same_named_module_function(self):
        """The false positive the first draft produced, pinned so it cannot come back.

        ``adapter.create_media_buy(...)`` inside an _impl must NOT drag in the module's
        own ``create_media_buy`` transport wrapper, whose model_dump() is legitimate.
        Traversal follows BARE calls only.
        """
        found = self._violations(
            "def create_media_buy(ctx) -> dict[str, object]:\n"
            "    return ctx.model_dump()\n"
            "def _create_impl(req, adapter):\n"
            "    return adapter.create_media_buy(req)\n"
        )
        assert found == [], f"attribute call wrongly resolved to the module-level function: {found}"
