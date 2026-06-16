"""Guard: WorkflowRepository methods must enforce tenant isolation via Context join.

WorkflowStep and ObjectWorkflowMapping have no tenant_id column. Tenant isolation
requires joining through Context (DBContext) which does have tenant_id. The
WorkflowRepository already has a correct tenant-scoped get_by_step_id() that
demonstrates the required pattern:

    select(WorkflowStep).join(DBContext).where(
        WorkflowStep.step_id == step_id,
        DBContext.tenant_id == self._tenant_id,
    )

Any WorkflowRepository method that queries WorkflowStep or ObjectWorkflowMapping
WITHOUT this join is a tenant isolation violation.

Scanning approach: text-based (regex) scan of WorkflowRepository methods.
The guard looks for select(WorkflowStep), select(ObjectWorkflowMapping), and
session.get(WorkflowStep) calls that are not accompanied by a DBContext/Context
join in the same method body.

beads: beads-bou.2 (guard: WorkflowStep/ObjectWorkflowMapping queries without Context join)
"""

import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

ROOT = Path(__file__).resolve().parents[2]

WORKFLOW_REPO_FILE = "src/core/database/repositories/workflow.py"

# Patterns that indicate a WorkflowStep or ObjectWorkflowMapping query.
# These REQUIRE a DBContext/Context join for tenant isolation.
_MULTI_TENANT_QUERY_PATTERNS = [
    re.compile(r"select\(\s*WorkflowStep\s*\)"),
    re.compile(r"select\(\s*ObjectWorkflowMapping\s*\)"),
    re.compile(r"session\.get\(\s*WorkflowStep\s*,"),
    re.compile(r"self\._session\.get\(\s*WorkflowStep\s*,"),
    re.compile(r"self\._session\.scalars\(\s*select\(\s*WorkflowStep"),
    re.compile(r"self\._session\.scalars\(\s*select\(\s*ObjectWorkflowMapping"),
]

# Pattern that indicates tenant isolation is present (DBContext join).
_CONTEXT_JOIN_PATTERN = re.compile(r"DBContext|join\(Context\)")

# Pre-existing violations: method names in WorkflowRepository that are known
# to lack tenant isolation. Each entry needs a FIXME tracking its fix.
# Allowlist shrinks as the workflow tenant isolation epic progresses.
# All methods now properly scoped via Context join (beads-bou.4).
WORKFLOW_ISOLATION_ALLOWLIST: set[str] = set()


def _extract_methods(source: str) -> dict[str, str]:
    """Extract method bodies from a Python class file.

    Returns a dict mapping method_name -> method_body (lines between def and next def).
    Simple line-based extraction — sufficient for this guard.
    """
    methods: dict[str, str] = {}
    current_method: str | None = None
    current_lines: list[str] = []
    method_re = re.compile(r"^\s{4}def (\w+)\s*\(")  # 4-space indent = class method

    for line in source.splitlines():
        m = method_re.match(line)
        if m:
            if current_method is not None:
                methods[current_method] = "\n".join(current_lines)
            current_method = m.group(1)
            current_lines = [line]
        elif current_method is not None:
            current_lines.append(line)

    if current_method is not None:
        methods[current_method] = "\n".join(current_lines)

    return methods


def _method_queries_without_context_join(method_name: str, body: str) -> bool:
    """Return True if the method queries WorkflowStep/ObjectWorkflowMapping
    WITHOUT a DBContext/Context join in the same method body."""
    has_multi_tenant_query = any(p.search(body) for p in _MULTI_TENANT_QUERY_PATTERNS)
    if not has_multi_tenant_query:
        return False
    has_context_join = _CONTEXT_JOIN_PATTERN.search(body) is not None
    return not has_context_join


def _workflow_isolation_violations() -> set[str]:
    source_path = ROOT / WORKFLOW_REPO_FILE
    if not source_path.exists():
        return set()
    methods = _extract_methods(source_path.read_text(encoding="utf-8"))
    return {name for name, body in methods.items() if _method_queries_without_context_join(name, body)}


class TestWorkflowRepositoryTenantIsolation:
    """WorkflowRepository must scope all queries to the current tenant.

    WorkflowStep and ObjectWorkflowMapping have no tenant_id column. The only
    way to enforce tenant isolation for these tables is to join through Context
    (DBContext) which has tenant_id. The reference implementation is
    get_by_step_id() which uses .join(DBContext).where(DBContext.tenant_id == ...).

    Any new method that queries these tables without this join is a tenant
    isolation breach — an authenticated user from one tenant could potentially
    read or modify another tenant's workflow steps.
    """

    @pytest.mark.arch_guard
    def test_workflow_isolation_allowlist_matches_violations(self):
        """Found violations must exactly match WORKFLOW_ISOLATION_ALLOWLIST (new + stale in one check)."""
        assert_violations_match_allowlist(
            _workflow_isolation_violations(),
            WORKFLOW_ISOLATION_ALLOWLIST,
            fix_hint=(
                "Fix: Add .join(DBContext).where(DBContext.tenant_id == self._tenant_id) "
                "to the query, following the pattern in get_by_step_id(). "
                "When fixed, remove the method from WORKFLOW_ISOLATION_ALLOWLIST."
            ),
        )
