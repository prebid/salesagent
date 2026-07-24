"""Guard: every ``_log_dropped_fields`` call site must use a canonical label constant.

Regression guard for a cross-transport audit-log drift: one A2A call site
hardcoded the bare string literal ``"inert read idempotency"`` where every
other MCP/A2A call site for the same semantic event (an AdCP envelope field
the tool doesn't declare, dropped before dispatch) used
``DROPPED_FIELDS_UNDECLARED_ENVELOPE``. An operator grepping or alerting on
the canonical label would silently miss that one transport's occurrences.

Keying on the ``_log_dropped_fields`` call's ``kind`` argument shape (must be
a ``Name`` referencing one of the two constants in
``src.core.request_compat``, never a bare string ``Constant``) makes a future
one-off literal fail the build instead of drifting unnoticed again.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import iter_call_expressions

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"

_CANONICAL_LABEL_NAMES = {"DROPPED_FIELDS_NEGOTIATION", "DROPPED_FIELDS_UNDECLARED_ENVELOPE"}


def _literal_kind_sites_in(path: Path) -> list[str]:
    """Return ``line`` markers for ``_log_dropped_fields(...)`` calls whose 2nd arg is a bare literal."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for call in iter_call_expressions(tree, "_log_dropped_fields"):
        if len(call.args) < 2:
            continue
        kind_arg = call.args[1]
        if not (isinstance(kind_arg, ast.Name) and kind_arg.id in _CANONICAL_LABEL_NAMES):
            offenders.append(f"{path}:{kind_arg.lineno}")
    return offenders


def test_every_dropped_fields_call_site_uses_a_canonical_label():
    """No call site may pass a bare string (or off-catalog name) as the audit-log label."""
    offenders: list[str] = []
    for path in _SRC_DIR.rglob("*.py"):
        try:
            offenders.extend(_literal_kind_sites_in(path))
        except SyntaxError:
            continue
    assert not offenders, (
        "_log_dropped_fields() called with a non-canonical 'kind' argument at: "
        f"{offenders}. Use DROPPED_FIELDS_NEGOTIATION or DROPPED_FIELDS_UNDECLARED_ENVELOPE "
        "from src.core.request_compat (add a new constant there if this is genuinely a new "
        "category of drop) so every transport's occurrence of the same event correlates under "
        "one label."
    )


class TestMatcherModelsTheForm:
    """Self-tests: the matcher flags a literal and passes a canonical-name reference."""

    def test_bare_literal_is_flagged(self, tmp_path):
        src = tmp_path / "offender.py"
        src.write_text('_log_dropped_fields(tool_name, "inert read idempotency", ["idempotency_key"])')
        assert _literal_kind_sites_in(src)

    def test_off_catalog_name_is_flagged(self, tmp_path):
        src = tmp_path / "offender.py"
        src.write_text("_log_dropped_fields(tool_name, SOME_OTHER_CONSTANT, dropped)")
        assert _literal_kind_sites_in(src)

    def test_canonical_name_passes(self, tmp_path):
        src = tmp_path / "clean.py"
        src.write_text("_log_dropped_fields(tool_name, DROPPED_FIELDS_UNDECLARED_ENVELOPE, dropped)")
        assert not _literal_kind_sites_in(src)

    def test_unrelated_call_ignored(self, tmp_path):
        src = tmp_path / "clean.py"
        src.write_text('logger.debug("unrelated", "literal", "call")')
        assert not _literal_kind_sites_in(src)
