"""Guard: one canonical format/agent-URL identity comparison (#1172, salesagent-hk21).

The format-identity bug class was two divergent canonicalizers for the same
logical comparison: ``normalize_agent_url`` (suffix-tolerant, not canonical)
vs ``format_id_identity``/``canonical_agent_url`` (canonical, previously not
suffix-tolerant). The fix folded transport-suffix stripping into
``canonical_agent_url`` and migrated every comparison site to the shared
helpers (``supported_format_keys``/``format_key``); ``normalize_agent_url``
survives only as a delegating input-normalization util with ZERO production
callers. This guard keeps it that way: new production code must use the
canonical helpers, never re-introduce the second camp.
"""

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

_SRC = Path(__file__).parent.parent.parent / "src"

#: Files allowed to reference normalize_agent_url (its definition site only).
ALLOWLIST: set[str] = {"src/core/validation.py"}


def _normalize_call_sites(tree: ast.AST) -> list[str]:
    """Return the names of normalize_agent_url call/import references in a module."""
    hits: list[str] = ["normalize_agent_url" for _ in iter_call_expressions(tree, "normalize_agent_url")]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "normalize_agent_url":
                    hits.append(alias.name)
    return hits


class TestSingleFormatIdentityCanonicalizer:
    @pytest.mark.arch_guard
    def test_no_normalize_agent_url_in_production_code(self):
        violations: list[str] = []
        for path in sorted(_SRC.rglob("*.py")):
            rel = str(path.relative_to(_SRC.parent))
            if rel in ALLOWLIST:
                continue
            if _normalize_call_sites(ast.parse(path.read_text())):
                violations.append(rel)
        assert not violations, (
            "normalize_agent_url used in production code — identity comparison must go "
            "through the canonical helpers (canonical_agent_url / format_id_identity / "
            f"supported_format_keys, see src/core/helpers/creative_helpers.py): {violations}"
        )

    @pytest.mark.arch_guard
    def test_guard_detects_planted_call(self):
        """Positive meta-test: the scanner catches a call in tool-shaped source."""
        tree = ast.parse("def _impl(fmt):\n    key = (normalize_agent_url(str(fmt.agent_url)), fmt.id)\n")
        assert _normalize_call_sites(tree) == ["normalize_agent_url"]

    @pytest.mark.arch_guard
    def test_guard_detects_planted_import(self):
        """Positive meta-test: importing the util is flagged too (not just calling)."""
        tree = ast.parse("from src.core.validation import normalize_agent_url\n")
        assert _normalize_call_sites(tree) == ["normalize_agent_url"]

    @pytest.mark.arch_guard
    def test_guard_ignores_canonical_helpers(self):
        """Negative meta-test: the canonical helpers do not trip the scanner."""
        tree = ast.parse(
            "from src.core.schemas import canonical_agent_url, format_id_identity\n"
            "key = format_id_identity(fmt)\n"
            "url = canonical_agent_url(u)\n"
        )
        assert _normalize_call_sites(tree) == []
