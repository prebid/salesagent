"""Guard: an advertised capability value must be DERIVED from its enforced constant.

Regression guard for the idempotency replay-TTL drift: ``get_adcp_capabilities``
once advertised ``replay_ttl_seconds=86400`` as a bare literal while the *enforced*
TTL lived in ``DEFAULT_REPLAY_TTL``. A literal and a constant are two sources for one
value and drift independently — the buyer is then told a window the server does not
enforce. Every ``replay_ttl_seconds=`` site MUST reference the constant
(``int(DEFAULT_REPLAY_TTL.total_seconds())``), never a hardcoded number.

This is the AST-detectable slice of semantic single-source-of-truth. A value-counting
"duplicate literal" guard is deliberately NOT used: 86400 ("seconds per day") and 3600
("seconds per hour") have many legitimate unrelated uses in src/, so counting literals
would be almost all false positives. Keying on the capability KEYWORD instead is exact.
The non-AST-detectable slice (one invariant computed two ways) stays a review concern.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import iter_call_expressions

# Capability keywords whose value must be derived from an enforced constant, not a literal.
_DERIVED_CAPABILITY_KEYWORDS = {"replay_ttl_seconds"}


def _literal_capability_sites_in(source: str) -> list[str]:
    """Return ``keyword@line`` for capability keywords assigned a bare numeric literal."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in iter_call_expressions(tree):
        for kw in node.keywords:
            if (
                kw.arg in _DERIVED_CAPABILITY_KEYWORDS
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, (int, float))
                and not isinstance(kw.value.value, bool)
            ):
                out.append(f"{kw.arg}@{kw.value.lineno}")
    return out


def test_capability_values_are_derived_not_literal():
    """No advertised capability keyword may be a hardcoded number anywhere in src/.

    Core invariant: one source of truth for the replay window — the enforced
    constant. A literal in the capability response drifts from enforcement
    silently (the #1b bug class).
    """
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        try:
            for site in _literal_capability_sites_in(path.read_text()):
                offenders.append(f"{path}:{site.split('@')[1]} ({site.split('@')[0]})")
        except SyntaxError:
            continue
    assert not offenders, (
        "Advertised capability values must derive from their enforced constant "
        "(e.g. replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds())), not a "
        f"bare literal, at: {offenders}"
    )


class TestMatcherModelsTheForm:
    """Self-tests: the matcher flags a literal and passes a derived expression."""

    def test_bare_literal_is_flagged(self):
        assert _literal_capability_sites_in("Idempotency(supported=True, replay_ttl_seconds=86400)")

    def test_derived_expression_passes(self):
        src = "Idempotency(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds()))"
        assert not _literal_capability_sites_in(src)

    def test_unrelated_literal_keyword_ignored(self):
        # A bare number on an UNREGISTERED keyword is not this guard's concern.
        assert not _literal_capability_sites_in("Foo(timeout_seconds=86400)")
