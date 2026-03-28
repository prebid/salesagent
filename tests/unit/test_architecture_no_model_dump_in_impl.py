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
# FIXME(salesagent-hr8n): 24 violations remain (5 fixed by salesagent-lfto).
# Line numbers reflect FIXME comment restored in media_buy_update.py (PR04 review item 1).
KNOWN_VIOLATIONS = {
    # _update_media_buy_impl: 22 violations (workflow step response_data)
    ("media_buy_update.py", 224),
    ("media_buy_update.py", 279),
    ("media_buy_update.py", 280),
    ("media_buy_update.py", 334),
    ("media_buy_update.py", 391),
    ("media_buy_update.py", 415),
    ("media_buy_update.py", 449),
    ("media_buy_update.py", 477),
    ("media_buy_update.py", 494),
    ("media_buy_update.py", 549),
    ("media_buy_update.py", 580),
    ("media_buy_update.py", 600),
    ("media_buy_update.py", 626),
    ("media_buy_update.py", 806),
    ("media_buy_update.py", 834),
    ("media_buy_update.py", 863),
    ("media_buy_update.py", 1035),
    ("media_buy_update.py", 1053),
    ("media_buy_update.py", 1104),
    ("media_buy_update.py", 1198),
    ("media_buy_update.py", 1230),
    ("media_buy_update.py", 1292),
    # _get_products_impl: 1 violation (logging)
    ("products.py", 637),
    # _list_creatives_impl: 1 violation (filter dict conversion)
    ("creatives/listing.py", 153),  # filters.model_dump(exclude_none=True)
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
