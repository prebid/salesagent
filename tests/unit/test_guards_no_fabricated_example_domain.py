"""Guard: never fabricate a "<dynamic-value>.example.com" domain as a fallback.

src/core/tools/capabilities.py and src/core/database/models.py
(Tenant.primary_domain) used to fabricate ``f"{subdomain}.example.com"`` whenever
no real domain was configured -- ``example.com`` is RFC 2606 reserved, so the
value is guaranteed unreachable, and callers treat these fields as claims (a
publisher authorization the buyer resolves against, or a guard deciding whether
real domain config exists). This guard bans the exact shape: an f-string literal
whose LAST
segment is a constant string ending in ``.example.com`` and where at least one
value is interpolated before it (the "fabricate a subdomain-based example.com"
form). It intentionally does NOT match a plain literal ``"example.com"`` string
(no interpolation -- a different, separately-tracked pattern) or an f-string where
``.example.com`` is a static substring followed by further interpolation (e.g. a
URL path with a trailing ``{param}`` -- also a different, separately-tracked
pattern), which keeps the guard narrow to the exact disease this ticket fixed.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT, assert_violations_match_allowlist

_SRC_DIR = REPO_ROOT / "src"

# Format: (relative_path_from_repo_root, lineno)
# Pre-existing violations that predate this guard. The list can only shrink.
ALLOWLIST: set[tuple[str, int]] = {
    # FIXME(#1845): Product.publisher_properties fabricates the same shape --
    # needs its own fix (the pinned publisher-property-selector.json schema
    # requires publisher_domain non-empty on every entry, unlike capabilities.py's
    # optional Portfolio, so the capabilities.py fix does not transfer as-is).
    #
    # RE-PINNED (#1757, -1 line each): models.py's signing import shrank by one line when
    # it stopped importing four primitives from the facade and started importing
    # REQUEST_SIGNING plus two CHECK-clause OPERATIONS from the leaf. The SAME THREE
    # violations are listed, at their new coordinates — none added, none fixed.
    # This allowlist is keyed by LINE NUMBER, so any edit above a violation re-reports it
    # as new AND its old coordinate as stale; re-keying it on (file, symbol) is tracked as
    # a follow-up on #1757.
    ("src/core/database/models.py", 424),
    ("src/core/database/models.py", 434),
    ("src/core/database/models.py", 444),
}


def _ends_with_fabricated_example_com(node: ast.JoinedStr) -> bool:
    """True if this f-string interpolates a value immediately before a literal
    ``.example.com`` tail -- the exact fabrication shape, not just any f-string
    that happens to mention example.com somewhere.
    """
    if len(node.values) < 2:
        return False
    last = node.values[-1]
    if not isinstance(last, ast.Constant) or not isinstance(last.value, str):
        return False
    if not last.value.endswith(".example.com"):
        return False
    # At least one interpolated value must precede this trailing constant.
    return any(isinstance(v, ast.FormattedValue) for v in node.values[:-1])


def _scan() -> set[tuple[str, int]]:
    violations: set[tuple[str, int]] = set()
    for path in sorted(_SRC_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and _ends_with_fabricated_example_com(node):
                violations.add((rel, node.lineno))
    return violations


class TestNoFabricatedExampleDomain:
    """No src/ f-string fabricates a '{value}.example.com' fallback domain."""

    def test_no_new_fabricated_example_domains(self):
        violations = _scan() - ALLOWLIST
        assert not violations, (
            "src/ files fabricating a '{value}.example.com' fallback domain "
            "(RFC 2606 reserved, guaranteed unreachable -- omit/return None instead):\n"
            + "\n".join(f"  - {p}:{n}" for p, n in sorted(violations))
        )

    def test_allowlist_entries_are_still_violations(self):
        """Every allowlist entry must still be a real violation -- shrink on fix."""
        assert_violations_match_allowlist(
            _scan(),
            ALLOWLIST,
            fix_hint="Remove fixed entries from ALLOWLIST.",
        )

    def test_positive_meta_detects_fabrication(self):
        """Meta-test: the detector actually flags the disease shape."""
        tree = ast.parse('x = a or f"{tenant.subdomain}.example.com"')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and _ends_with_fabricated_example_com(n)]
        assert found, "detector failed to flag a genuine '{value}.example.com' fabrication"

    def test_negative_meta_ignores_real_domain_fstring(self):
        """Meta-test: a real, non-fabricating f-string domain is not flagged."""
        tree = ast.parse('x = f"https://{tenant.subdomain}.real-configured-domain.com"')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and _ends_with_fabricated_example_com(n)]
        assert not found, "detector false-positived on a real (non-example.com) domain f-string"

    def test_negative_meta_ignores_plain_literal(self):
        """Meta-test: a plain (non-interpolated) 'example.com' literal (e.g. a
        hardcoded dev-seed value) is a different pattern -- no interpolation
        means it can't be the dynamic subdomain-fabrication shape this guard
        targets, tracked separately, not this guard's job."""
        tree = ast.parse('x = "example.com"')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and _ends_with_fabricated_example_com(n)]
        assert not found

    def test_negative_meta_ignores_interpolation_after_example_com(self):
        """Meta-test: an f-string where '.example.com' is a static substring
        FOLLOWED by further interpolation (e.g. a URL path with a trailing
        {param}) is a different pattern, out of this guard's scope."""
        tree = ast.parse('x = f"https://seller.example.com/review?tenant={tenant_id}"')
        found = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and _ends_with_fabricated_example_com(n)]
        assert not found, "detector over-matched a URL-path f-string that isn't the subdomain-fabrication shape"
