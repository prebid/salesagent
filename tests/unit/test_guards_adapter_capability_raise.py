"""Guard: adapter capability gaps must raise typed AdCP errors, not ValueError.

Disease: an adapter signals "I cannot fulfill this buyer-correctable part of
the request" (unsupported targeting axis, pricing model, feature) with a bare
``ValueError``. Business-logic boundaries wrap every untyped exception as
AdCPAdapterError, so the buyer receives SERVICE_UNAVAILABLE / recovery=
transient — "retry with exponential backoff" — for a request that can never
succeed. The pinned spec (v3.1.1 enums/error-code.json) classifies these as
UNSUPPORTED_FEATURE / correctable. The GAM targeting manager carried 15 such
sites.

Scope: ``raise ValueError(`` in src/adapters whose message matches
capability-gap phrasing. Phrase-anchored on purpose — operational faults
("GAM client required", "Failed to create ...") legitimately stay untyped and
must not be dragged into the buyer-correctable family. The would-be-missed
meta-test documents the phrasing dependency.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Capability-gap phrasing. Matched against the joined literal fragments of
#: the raised message (f-strings included).
_CAPABILITY_PHRASES = re.compile(
    r"not supported|not implemented|only supports|Cannot fulfill|requested but",
    re.IGNORECASE,
)

# Message-prefix allowlist — pre-existing sites tracked by their own bugs;
# shrink-only. (The GAM creative-asset validation ValueErrors are a sibling
# bug in the creative-rejection family, but their phrasing — "missing
# required ..." — never matches the capability anchors, so they need no
# entry here.)
ALLOWLIST_PREFIXES: set[str] = set()


def _literal_text(node: ast.expr) -> str | None:
    """Every literal string fragment of a raise message, joined (f-strings included)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces = [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        return "".join(pieces) if pieces else None
    return None


def _find_capability_valueerrors(tree: ast.AST) -> list[tuple[str, int]]:
    """(message-prefix, lineno) for bare ValueError raises with capability phrasing."""
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        if not (isinstance(func, ast.Name) and func.id == "ValueError"):
            continue
        if not node.exc.args:
            continue
        msg = _literal_text(node.exc.args[0])
        if msg and _CAPABILITY_PHRASES.search(msg):
            hits.append((msg, node.lineno))
    return hits


def _scan_adapters() -> list[str]:
    violations = []
    for path in (REPO_ROOT / "src" / "adapters").rglob("*.py"):
        for msg, lineno in _find_capability_valueerrors(ast.parse(path.read_text())):
            if any(msg.startswith(prefix) for prefix in ALLOWLIST_PREFIXES):
                continue
            violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno} ValueError({msg[:60]!r}...)")
    return violations


class TestAdapterCapabilityRaiseGuard:
    def test_no_untyped_capability_gap_raises_in_adapters(self):
        violations = _scan_adapters()
        assert not violations, (
            "Capability-gap raise sites must use AdCPCapabilityNotSupportedError "
            "(UNSUPPORTED_FEATURE/correctable), not bare ValueError "
            "(wrapped into SERVICE_UNAVAILABLE/transient — retry-the-unretryable):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_allowlist_prefixes_still_match_a_site(self):
        """Stale-entry check: every allowlisted prefix still has a live site."""
        live: set[str] = set()
        for path in (REPO_ROOT / "src" / "adapters").rglob("*.py"):
            for msg, _ in _find_capability_valueerrors(ast.parse(path.read_text())):
                for prefix in ALLOWLIST_PREFIXES:
                    if msg.startswith(prefix):
                        live.add(prefix)
        stale = ALLOWLIST_PREFIXES - live
        assert not stale, f"Allowlist prefixes with no matching site — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    def test_positive_detects_capability_valueerror(self):
        tree = ast.parse('raise ValueError("Device targeting requested but not supported.")\n')
        assert len(_find_capability_valueerrors(tree)) == 1

    def test_positive_detects_fstring_message(self):
        tree = ast.parse('raise ValueError(f"Postal targeting requested but not implemented: {x}.")\n')
        assert len(_find_capability_valueerrors(tree)) == 1

    def test_negative_operational_fault_allowed(self):
        tree = ast.parse('raise ValueError("GAM client required for syncing custom targeting keys")\n')
        assert _find_capability_valueerrors(tree) == []

    def test_negative_typed_raise_passes(self):
        tree = ast.parse('raise AdCPCapabilityNotSupportedError("Device targeting requested but not supported.")\n')
        assert _find_capability_valueerrors(tree) == []

    def test_would_be_missed_phrasing_documented(self):
        """Known limitation: a capability gap phrased without the anchor words
        escapes this guard (phrase-anchoring is what keeps operational faults
        out). Reviewers own the residual."""
        tree = ast.parse('raise ValueError("GAM lacks this axis entirely.")\n')
        assert _find_capability_valueerrors(tree) == []
