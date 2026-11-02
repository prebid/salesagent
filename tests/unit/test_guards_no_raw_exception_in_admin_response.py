"""Guard: swept admin files must not render a caught blanket exception raw.

Inside an ``except Exception as e:`` handler, putting ``str(e)`` (or an ``{e}``
f-string interpolation) into a flash message or JSON body shows the operator
whatever the exception carries — for database errors that is the raw driver
dump: ``(psycopg2.errors.…)``, the failing statement, and a DETAIL line that
can carry tenant data. Handlers must route through
``src.admin.utils.operator_errors.safe_error_message`` instead, which collapses
database errors to a generic message and logs the details.

Scope: the check-then-write admin files already swept onto the helper. The
remaining admin surface is tracked for the same sweep — extend ``_FILES`` as
files are migrated; the list may only grow (each addition is a file that must
STAY clean).
"""

import ast
import re

from tests.unit._architecture_helpers import REPO_ROOT

_FILES = [
    "src/admin/blueprints/publisher_partners.py",
    "src/admin/blueprints/users.py",
    "src/admin/blueprints/core.py",
    "src/admin/blueprints/inventory_profiles.py",
    "src/admin/blueprints/settings.py",
    "src/admin/tenant_management_api.py",
]

_RESPONSE_HINT = re.compile(r"flash\(|jsonify\(|[\"']error[\"']|[\"']message[\"']")
_RAW_E = re.compile(r"(?<![\w.])str\(e\)|\{e\}|\{str\(e\)\}")


def _blanket_handler_ranges(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
            and node.name == "e"
        ):
            yield node.lineno, node.end_lineno


def _scan_source(source: str, filename: str) -> list[str]:
    offenders = []
    ranges = list(_blanket_handler_ranges(ast.parse(source)))
    for lineno, line in enumerate(source.split("\n"), start=1):
        if not any(a <= lineno <= b for a, b in ranges):
            continue
        if _RESPONSE_HINT.search(line) and _RAW_E.search(line):
            offenders.append(f"{filename}:{lineno}: {line.strip()[:100]}")
    return offenders


def test_swept_admin_files_use_safe_error_message():
    offenders = []
    for rel in _FILES:
        path = REPO_ROOT / rel
        offenders += _scan_source(path.read_text(encoding="utf-8"), rel)
    assert not offenders, (
        "A blanket except-Exception handler renders the raw exception into an "
        "operator-facing response — route it through safe_error_message(e) "
        "(src/admin/utils/operator_errors.py) so database errors cannot leak "
        "driver internals:\n" + "\n".join(offenders)
    )


class TestGuardMetaCases:
    """The guard's own detection contract (positive / negative)."""

    def test_flags_str_e_in_flash_inside_blanket_handler(self):
        src = 'try:\n    x()\nexcept Exception as e:\n    flash(f"Error: {str(e)}", "error")\n'
        assert _scan_source(src, "m.py") != []

    def test_flags_bare_e_interpolation_in_jsonify(self):
        src = 'try:\n    x()\nexcept Exception as e:\n    return jsonify({"error": f"failed: {e}"}), 500\n'
        assert _scan_source(src, "m.py") != []

    def test_accepts_safe_error_message(self):
        src = 'try:\n    x()\nexcept Exception as e:\n    flash(f"Error: {safe_error_message(e)}", "error")\n'
        assert _scan_source(src, "m.py") == []

    def test_ignores_typed_except_handlers(self):
        """A typed except's message is intentional operator text, not a blanket leak."""
        src = 'try:\n    x()\nexcept ValueError as e:\n    flash(f"Invalid value: {e}", "error")\n'
        assert _scan_source(src, "m.py") == []

    def test_ignores_logging_inside_blanket_handler(self):
        src = 'try:\n    x()\nexcept Exception as e:\n    logger.error(f"boom: {e}", exc_info=True)\n'
        assert _scan_source(src, "m.py") == []
