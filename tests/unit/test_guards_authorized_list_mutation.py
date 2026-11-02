"""Guard: authorized_domains / authorized_emails are never whole-list assigned.

Disease: a handler loads the tenant's authorized list (JSONB), mutates it in
Python, and assigns the whole list back. The membership check reads a stale
snapshot and the write is last-writer-wins — a concurrent add landing between
read and write is silently erased. Six admin surfaces carried this shape
(users blueprint add/remove_domain and the four domain_access helpers).

The only APPEND/REMOVE path is TenantConfigRepository.add_to_authorized_list /
remove_from_authorized_list — a single UPDATE whose WHERE clause IS the
membership check, so check and write are atomic. This guard bans the
re-introduction of ``obj.authorized_domains = ...`` / ``obj.authorized_emails
= ...`` attribute assignment anywhere in src/. Reads stay unrestricted.

Whole-list REPLACE semantics (the tenant management API) and construction of a
not-yet-persisted tenant are legitimately not the race — those sites carry a
``# noqa: authorized-list-assign — <why>`` marker on the assignment line, and
the justification is mandatory.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDED_ATTRS = {"authorized_domains", "authorized_emails"}
# Marker must carry a justification after the dash.
_MARKER_RE = re.compile(r"#\s*noqa:\s*authorized-list-assign\s+—\s+\S")


def _find_attr_assignments(tree: ast.AST, source_lines: list[str] | None = None) -> list[tuple[str, int]]:
    """(attr, lineno) for every unmarked ``<expr>.<guarded attr> = ...`` store."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr in GUARDED_ATTRS:
            if source_lines and _MARKER_RE.search(source_lines[node.lineno - 1]):
                continue
            hits.append((node.attr, node.lineno))
    return hits


def _scan_src() -> list[str]:
    violations = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        text = path.read_text()
        for attr, lineno in _find_attr_assignments(ast.parse(text), text.splitlines()):
            violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno} assigns .{attr}")
    return violations


class TestAuthorizedListMutationGuard:
    def test_no_whole_list_assignment_in_src(self):
        """No production code assigns the authorized list attributes directly."""
        violations = _scan_src()
        assert not violations, (
            "Whole-list assignment to an authorized list column (lost-update race — "
            "use TenantConfigRepository.add_to_authorized_list/remove_from_authorized_list):\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestGuardMetaTests:
    def test_positive_detects_attribute_assignment(self):
        tree = ast.parse("tenant.authorized_domains = domains\n")
        assert _find_attr_assignments(tree) == [("authorized_domains", 1)]

    def test_positive_detects_email_list_assignment(self):
        tree = ast.parse("t.authorized_emails = [*t.authorized_emails, e]\n")
        assert _find_attr_assignments(tree) == [("authorized_emails", 1)]

    def test_negative_read_access_allowed(self):
        tree = ast.parse("domains = list(tenant.authorized_domains or [])\n")
        assert _find_attr_assignments(tree) == []

    def test_negative_repo_update_expression_allowed(self):
        """The repository's update(Tenant).values({...}) form is not an attribute store."""
        tree = ast.parse("stmt = update(Tenant).values({'authorized_domains': expr})\n")
        assert _find_attr_assignments(tree) == []

    def test_negative_unrelated_attribute_assignment_allowed(self):
        """Would-be-missed inverse: a non-guarded attr must NOT trip the guard."""
        tree = ast.parse("tenant.authorized_properties = x\n")
        assert _find_attr_assignments(tree) == []

    def test_marker_with_justification_exempts(self):
        src = "t.authorized_domains = x  # noqa: authorized-list-assign — replace semantics\n"
        assert _find_attr_assignments(ast.parse(src), src.splitlines()) == []

    def test_marker_without_justification_does_not_exempt(self):
        """A bare marker is not a justification — the site still violates."""
        src = "t.authorized_domains = x  # noqa: authorized-list-assign\n"
        assert _find_attr_assignments(ast.parse(src), src.splitlines()) == [("authorized_domains", 1)]
