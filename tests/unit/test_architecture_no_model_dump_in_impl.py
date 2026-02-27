"""Guard: _impl functions must not call .model_dump() or .model_dump_internal().

Serialization is the transport wrapper's responsibility, not business logic.
_impl functions should return Pydantic model objects and let the transport
boundary decide how to serialize them.

Legitimate uses (NOT violations):
- Schema classes defining model_dump() overrides (Pattern #4 nested serialization)
- Transport wrappers calling model_dump() before returning to the client

Current violations are serializing for DB storage (raw_request, workflow step
response_data). These should be replaced with typed repository methods that
accept model objects directly, eliminating the manual serialization.

beads: salesagent-hr8n
"""

import ast
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "tools"

BANNED_METHODS = {"model_dump", "model_dump_internal"}

# Known violations — allowlist shrinks as violations are fixed.
# Each entry is (relative_path_from_tools_dir, line_number).
# FIXME(salesagent-hr8n): All 29 violations need migration to repository pattern.
KNOWN_VIOLATIONS = {
    # _create_media_buy_impl: 4 violations (DB storage + workflow)
    ("media_buy_create.py", 1238),  # push_notification_config.model_dump()
    ("media_buy_create.py", 1316),  # req.model_dump() → request_data_for_workflow
    ("media_buy_create.py", 1932),  # req.model_dump() → raw_request_dict
    ("media_buy_create.py", 2844),  # req.model_dump() → raw_request=
    # _update_media_buy_impl: 23 violations (workflow step response_data)
    ("media_buy_update.py", 196),  # req.model_dump() → request_data_for_workflow
    ("media_buy_update.py", 222),  # response_data.model_dump()
    ("media_buy_update.py", 277),  # approval_response.model_dump()
    ("media_buy_update.py", 278),  # req.model_dump() → approval_data
    ("media_buy_update.py", 348),  # response_data.model_dump()
    ("media_buy_update.py", 415),  # response_data.model_dump()
    ("media_buy_update.py", 439),  # error_response.model_dump()
    ("media_buy_update.py", 473),  # success_response.model_dump()
    ("media_buy_update.py", 501),  # response_data.model_dump()
    ("media_buy_update.py", 518),  # response_data.model_dump()
    ("media_buy_update.py", 551),  # response_data.model_dump()
    ("media_buy_update.py", 580),  # response_data.model_dump()
    ("media_buy_update.py", 613),  # response_data.model_dump()
    ("media_buy_update.py", 639),  # response_data.model_dump()
    ("media_buy_update.py", 822),  # response_data.model_dump()
    ("media_buy_update.py", 848),  # response_data.model_dump()
    ("media_buy_update.py", 877),  # response_data.model_dump()
    ("media_buy_update.py", 1064),  # response_data.model_dump()
    ("media_buy_update.py", 1090),  # response_data.model_dump()
    ("media_buy_update.py", 1137),  # response_data.model_dump()
    ("media_buy_update.py", 1243),  # response_data.model_dump()
    ("media_buy_update.py", 1275),  # response_data.model_dump()
    ("media_buy_update.py", 1348),  # final_response.model_dump()
    # _get_products_impl: 1 violation (logging)
    ("products.py", 571),  # req.filters.model_dump() in logger.info
    # _list_creatives_impl: 1 violation (filter dict conversion)
    ("creatives/listing.py", 151),  # filters.model_dump(exclude_none=True)
}


def _find_model_dump_in_impl() -> list[tuple[str, int, str, str]]:
    """Find all .model_dump()/.model_dump_internal() calls inside _impl functions.

    Returns list of (relative_path, lineno, func_name, method_name).
    """
    violations = []
    seen: set[tuple[str, int]] = set()

    for py_file in TOOLS_DIR.rglob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.endswith("_impl"):
                continue

            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr in BANNED_METHODS:
                    rel_path = str(py_file.relative_to(TOOLS_DIR))
                    key = (rel_path, child.lineno)
                    if key in seen:
                        continue
                    seen.add(key)
                    violations.append((rel_path, child.lineno, node.name, func.attr))

    return violations


class TestNoModelDumpInImpl:
    """_impl functions must not call .model_dump() or .model_dump_internal()."""

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

    def test_known_violations_not_stale(self):
        """Every entry in KNOWN_VIOLATIONS must still exist in the source.

        When a violation is fixed, remove it from the allowlist.
        Stale entries mean the allowlist is not being maintained.
        """
        all_violations = _find_model_dump_in_impl()
        actual_sites = {(v[0], v[1]) for v in all_violations}

        stale = KNOWN_VIOLATIONS - actual_sites
        assert not stale, (
            f"Found {len(stale)} stale entries in KNOWN_VIOLATIONS allowlist.\n"
            f"These violations have been fixed — remove them from the allowlist:\n"
            + "\n".join(f"  {path}:{line}" for path, line in sorted(stale))
        )

    def test_violation_count_documented(self):
        """Track the total violation count — should only decrease over time."""
        all_violations = _find_model_dump_in_impl()
        assert len(all_violations) == len(KNOWN_VIOLATIONS), (
            f"Violation count changed: found {len(all_violations)}, "
            f"allowlist has {len(KNOWN_VIOLATIONS)}. "
            f"Update the allowlist (remove fixed entries or investigate new ones)."
        )
