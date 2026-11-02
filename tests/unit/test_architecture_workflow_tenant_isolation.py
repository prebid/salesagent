"""Guard: WorkflowRepository queries must scope by tenant AND, where buyer-facing, principal.

WorkflowStep and ObjectWorkflowMapping have no tenant_id column. Isolation requires
joining through Context (DBContext), which carries BOTH tenant_id and principal_id.

TWO AXES, because grading one and staying silent on the other is how this guard certified
a leak. Until #1808 this module enforced the tenant join only, was green, and
its own docstring held up ``get_by_step_id()`` as "a correct tenant-scoped" reference
implementation others should copy -- while that method filtered tenant_id and NOTHING else.
Any method written by imitating the blessed pattern inherited the omission, and three
buyer-facing surfaces did: get_task_status and complete_task (via get_by_step_id_or_raise) and
list_tasks (via list_by_tenant/count_by_tenant). Within one tenant, any authenticated
principal could read and complete another principal's task, and list every one of them.

So the reference implementation is stated on both axes now:

    select(WorkflowStep).join(DBContext).where(
        WorkflowStep.step_id == step_id,
        DBContext.tenant_id == self._tenant_id,
        DBContext.principal_id == principal_id,   # when the caller is a buyer
    )

PRINCIPAL SCOPING IS CONDITIONAL, and that is why the second axis carries an allowlist
rather than being mandatory. Some methods are legitimately publisher-scoped: the admin
blueprints and the background approval service act AS the publisher over the whole tenant,
and narrowing them to a principal would be wrong, not safer. Each such method is named
below with the reason it is exempt, so an exemption is a decision someone made and can be
audited -- not an absence.

Scanning approach: text-based (regex) scan of WorkflowRepository methods, for both axes.
Its limits are worth stating because the leak lived inside them: it reads the method body
for the presence of a filter, so it cannot tell a correct predicate from an incorrect one,
and it cannot see which callers reach a method. It catches OMISSION, which is what both
defects were.

tracker: #1808 (principal axis), and the original tenant-join guard
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

# Pattern that indicates the method can narrow to a single principal.
_PRINCIPAL_SCOPE_PATTERN = re.compile(r"DBContext\.principal_id")

# Pre-existing violations: method names in WorkflowRepository that are known
# to lack tenant isolation. Each entry needs a FIXME tracking its fix.
# Allowlist shrinks as the workflow tenant isolation epic progresses.
# All methods now properly scoped via Context join ().
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
    get_by_step_id(), which joins DBContext and filters tenant_id — and, since
    #1808, principal_id when the caller passes one. It is cited here on
    both axes deliberately: this docstring used to call it "correct" while it leaked
    across principals, and a method held up as exemplary gets copied.

    Any new method that queries these tables without the join is a tenant
    isolation breach — an authenticated user from one tenant could potentially
    read or modify another tenant's workflow steps. The principal axis is graded
    by the sibling class below.
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


#: Methods that query WorkflowStep/ObjectWorkflowMapping and are deliberately NOT narrowed
#: to a principal, each with the reason. Publisher-scoped access is a real requirement, so
#: this axis needs an exemption list rather than a blanket rule -- but the exemption has to
#: name itself. The leak #1808 fixed was invisible precisely because there was
#: no place where "this one is tenant-only" had to be written down and defended.
#:
#: A method serving BOTH a buyer and the publisher belongs here only if the buyer's path is
#: scoped upstream; say where.
WORKFLOW_PRINCIPAL_SCOPE_EXEMPT: dict[str, str] = {
    "get_mappings_for_step": (
        "Takes a step_id the caller already resolved. get_task_status passes the id of a step "
        "returned by the principal-scoped get_by_step_id_or_raise, so the scoping happened "
        "upstream; the admin blueprints act as the publisher."
    ),
    "get_mappings_for_steps": (
        "Bulk sibling of get_mappings_for_step. list_tasks passes ids from its own "
        "principal-scoped page, so the scoping happened upstream."
    ),
    "get_latest_mapping_for_object": (
        "Admin creative review only (src/admin/blueprints/creatives.py) -- the publisher "
        "resolving which workflow step a creative belongs to, across the tenant."
    ),
    "get_all_steps": (
        "Admin workflow dashboard only (src/admin/blueprints/workflows.py) -- a "
        "tenant-wide publisher view by definition."
    ),
}


def _methods_without_principal_scope() -> set[str]:
    source_path = ROOT / WORKFLOW_REPO_FILE
    if not source_path.exists():
        return set()
    methods = _extract_methods(source_path.read_text(encoding="utf-8"))
    return {
        name
        for name, body in methods.items()
        if any(p.search(body) for p in _MULTI_TENANT_QUERY_PATTERNS) and not _PRINCIPAL_SCOPE_PATTERN.search(body)
    }


class TestWorkflowRepositoryPrincipalIsolation:
    """A buyer-facing WorkflowRepository query must be able to narrow to one principal.

    The tenant axis above was green while three buyer surfaces leaked across principals,
    because it asked whether the Context join was present and never what else the WHERE
    clause was missing. This axis asks the second question.

    It grades CAPABILITY, not the call: a method either filters DBContext.principal_id or
    is named in WORKFLOW_PRINCIPAL_SCOPE_EXEMPT with a reason. Whether each caller actually
    passes one is graded where it can be observed -- on the wire, in
    tests/integration/test_get_task_principal_scope.py -- because a text scan cannot see a
    caller.
    """

    @pytest.mark.arch_guard
    def test_principal_scope_exemptions_match_reality(self):
        """Unscoped methods must be exactly the exempt ones (new leaks + stale rows, one check)."""
        assert_violations_match_allowlist(
            _methods_without_principal_scope(),
            set(WORKFLOW_PRINCIPAL_SCOPE_EXEMPT),
            fix_hint=(
                "A WorkflowRepository query reachable from a buyer-facing tool must accept "
                "principal_id and filter DBContext.principal_id on it, as get_by_step_id "
                "does. If the method is publisher-scoped by design, add it to "
                "WORKFLOW_PRINCIPAL_SCOPE_EXEMPT with the reason -- an exemption has to "
                "name itself (#1808)."
            ),
        )

    @pytest.mark.arch_guard
    def test_every_exemption_states_a_reason(self):
        """An exemption with an empty reason is an allowlist entry pretending to be a decision."""
        unexplained = [name for name, reason in WORKFLOW_PRINCIPAL_SCOPE_EXEMPT.items() if not reason.strip()]
        assert not unexplained, (
            f"WORKFLOW_PRINCIPAL_SCOPE_EXEMPT entries with no stated reason: {unexplained}. "
            "The reason is the whole value of this list -- without it the next reader cannot "
            "tell a deliberate publisher-scoped method from one that was never considered."
        )
