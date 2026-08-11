"""Guard: a pre-check against a unique index must handle the index's verdict.

Disease (#1721, inverse of the narrowing guard): a handler SELECTs by a unique
key, finds nothing, and writes — with no arm for the case where the index
disagrees. The pre-check runs inside the caller's own transaction, so it cannot
see a row a concurrent transaction has staged and not yet committed. The index is
the only authority, and the loser of the race takes an ``IntegrityError`` out of
flush that nothing in the function was written to answer. Whatever the enclosing
``except Exception`` does then — a raw driver message flashed at a publisher, a
500 — diverges from the polite answer the winner's own pre-check produces for the
identical condition.

The sibling guard (``test_guards_narrowed_integrity_recovery.py``) proves that a
recovery arm, where one exists, names the constraint it recovers from. This one
proves the arm EXISTS. That is the shape review missed six times over, which is
the argument for a guard rather than vigilance.

The rule: when a function pre-checks a model against a full unique key and then
writes that same model, the write must resolve the index's verdict — via
``resolve_or_write`` (``src/core/database/integrity.py``), inside a ``try`` that
catches ``IntegrityError``, or by carrying the per-site marker
``# structural-guard: uniqueness-index-verdict - <why>``. Exemption is scoped to
the WRITE, not the function: a function that resolves one collision correctly says
nothing about the three other rows it inserts.

Known blind spots, stated rather than silently tolerated:

- **Contested UPDATEs.** The write anchor is ``session.add``; a contested write
  expressed as an attribute assignment on an already-dirty row (``settings.py``'s
  ``virtual_host`` claim) is invisible here.
- **Pre-checks behind indirection.** A pre-check that has moved into a repository
  method leaves no ``select(Model)`` in the handler to anchor on. This is the
  repo's mandated direction (``test_architecture_no_raw_select.py``), so this
  guard's coverage SHRINKS as the migration proceeds — it is a tripwire for
  pre-migration code, and the repository/UoW layer is the destination, not this
  guard's growth.
- **Expression and partial unique indexes** are excluded from the inventory by
  ``unique_key_constraints()`` rather than truncated to the columns they happen to
  name. ``uq_accounts_natural_key`` reduced to ``{tenant_id, operator}`` would
  assert a uniqueness the database never promised.
"""

import ast
from collections.abc import Iterator
from typing import NamedTuple

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    format_failure,
    handles_integrity_error,
    iter_call_expressions,
    iter_statement_scoped_nodes,
    orm_model_class_defs,
    parse_module,
    rel,
    select_call_model_name,
    src_python_files,
    structural_guard_marker_re,
    unique_key_constraints,
    usable_unique_keys_by_model,
)

GUARD_NAME = "uniqueness-index-verdict"
MARKER = f"structural-guard: {GUARD_NAME}"
RESOLVE_HELPER = "resolve_or_write"

FIX_HINT = (
    "A pre-check cannot see an uncommitted competitor, so the unique index is the only "
    "authority and its verdict has to be answered. Handle it via the repository layer or "
    f"{RESOLVE_HELPER}(session, conflict=<your own pre-check>, write=..., constraint=<index>) "
    "so the loser of the race is told exactly what the winner's pre-check tells it — do not "
    "add a bare `except IntegrityError` in the blueprint. Where the recovery genuinely belongs "
    f"one frame up, mark the site `# {MARKER} - <why>`."
)

_ORM_MODEL_NAMES = frozenset(cls.name for cls in orm_model_class_defs())


class IndexVerdictViolation(NamedTuple):
    path: str
    function: str
    model: str
    lineno: int

    def triple(self) -> tuple[str, str, str]:
        """Identity that survives edits above it — line numbers do not."""
        return (self.path, self.function, self.model)


# ── Function partition ──────────────────────────────────────────────


def _outermost_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield each function not lexically nested inside another function.

    Closures and ``lambda``s are analysed as part of their enclosing function: the
    canonical compliant shape puts the pre-check in a closure and the write in a
    ``write=`` lambda, and splitting them apart would hide both halves.
    """
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    nested = {id(inner) for func in functions for inner in ast.walk(func) if inner is not func}
    for func in functions:
        if id(func) not in nested:
            yield func


# ── Pre-checks ──────────────────────────────────────────────────────


def _equality_column(node: ast.Compare, model: str) -> str | None:
    """Column name from ``Model.col == value`` (either orientation), else None."""
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return None
    for side in (node.left, *node.comparators):
        if isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name) and side.value.id == model:
            return side.attr
    return None


def _statement_precheck(stmt: ast.stmt) -> tuple[str, frozenset[str]] | None:
    """The ``(model, filter_keys)`` this ONE statement checks, else None.

    Keys bind per statement rather than per function so that two unrelated queries
    on the same model cannot jointly compose a unique tuple neither of them checks.
    A statement naming more than one model is skipped: its keys cannot be attributed.
    """
    nodes = list(iter_statement_scoped_nodes(stmt))
    calls = [node for node in nodes if isinstance(node, ast.Call)]
    models = {name for call in calls if (name := select_call_model_name(call))}
    if len(models) != 1:
        return None
    model = models.pop()

    keys: set[str] = set()
    for call in calls:
        if isinstance(call.func, ast.Attribute) and call.func.attr == "filter_by":
            keys.update(kw.arg for kw in call.keywords if kw.arg)
    for node in nodes:
        if isinstance(node, ast.Compare) and (column := _equality_column(node, model)):
            keys.add(column)
    return (model, frozenset(keys)) if keys else None


def _prechecked_keys(func: ast.AST) -> dict[str, list[frozenset[str]]]:
    by_model: dict[str, list[frozenset[str]]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.stmt):
            continue
        precheck = _statement_precheck(node)
        if precheck is not None:
            by_model.setdefault(precheck[0], []).append(precheck[1])
    return by_model


# ── Writes ──────────────────────────────────────────────────────────


def _constructed_models(func: ast.AST) -> dict[str, str]:
    """Local name → ORM model class it was constructed from."""
    built: dict[str, str] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        constructor = getattr(node.value.func, "id", None)
        if constructor not in _ORM_MODEL_NAMES:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                built[target.id] = constructor
    return built


def _write_model(call: ast.Call, constructed: dict[str, str]) -> str | None:
    """Model written by a ``session.add(...)`` call, else None."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "add" or len(call.args) != 1:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Name):
        return constructed.get(arg.id)
    if isinstance(arg, ast.Call):
        constructor = getattr(arg.func, "id", None)
        return constructor if constructor in _ORM_MODEL_NAMES else None
    return None


# ── Write-scoped exemptions ─────────────────────────────────────────


def _exempt_node_ids(func: ast.AST) -> set[int]:
    """Nodes under a handled write path — a ``write=`` callable or an IntegrityError try."""
    exempt: set[int] = set()
    for call in iter_call_expressions(func, name=RESOLVE_HELPER):
        for keyword in call.keywords:
            if keyword.arg == "write":
                exempt.update(id(node) for node in ast.walk(keyword.value))
    for node in ast.walk(func):
        if isinstance(node, ast.Try) and any(handles_integrity_error(h) for h in node.handlers):
            for body_stmt in [*node.body, *node.orelse]:
                exempt.update(id(inner) for inner in ast.walk(body_stmt))
    return exempt


def _marked_line_numbers(source: str, marker_re) -> set[int]:
    return {lineno for lineno, line in enumerate(source.splitlines(), 1) if marker_re.search(line)}


# ── Detector ────────────────────────────────────────────────────────


def find_unhandled_index_verdicts(
    tree: ast.Module,
    source: str,
    path: str = "<snippet>",
    *,
    apply_exemptions: bool = True,
) -> list[IndexVerdictViolation]:
    """Pre-check-anchored detector: unique-key pre-check + unhandled write, same function.

    ``apply_exemptions=False`` is the mutation probe's lever — with the exemption
    predicate disabled the guard must re-flag the sites the fix repaired, which is
    what proves the fix is what keeps this guard green.
    """
    marked_lines = _marked_line_numbers(source, structural_guard_marker_re(GUARD_NAME))
    unique_keys = usable_unique_keys_by_model()
    violations: dict[tuple[str, str, str], IndexVerdictViolation] = {}

    for func in _outermost_functions(tree):
        prechecked = _prechecked_keys(func)
        if not prechecked:
            continue
        constructed = _constructed_models(func)
        exempt = _exempt_node_ids(func) if apply_exemptions else set()

        for call in iter_call_expressions(func, name="add"):
            model = _write_model(call, constructed)
            if model is None or model not in unique_keys:
                continue
            covered = any(
                keys >= tuple_columns for keys in prechecked.get(model, []) for tuple_columns in unique_keys[model]
            )
            if not covered:
                continue
            if apply_exemptions and (id(call) in exempt or call.lineno in marked_lines):
                continue
            violation = IndexVerdictViolation(path, func.name, model, call.lineno)
            violations.setdefault(violation.triple(), violation)

    return sorted(violations.values())


def scan_src(*, apply_exemptions: bool = True) -> list[IndexVerdictViolation]:
    found: list[IndexVerdictViolation] = []
    for py_file in sorted(src_python_files(REPO_ROOT)):
        source = py_file.read_text(encoding="utf-8")
        if ".add(" not in source or "select(" not in source:
            continue
        found.extend(
            find_unhandled_index_verdicts(
                parse_module(py_file),
                source,
                rel(py_file),
                apply_exemptions=apply_exemptions,
            )
        )
    return found


# ── The guard ───────────────────────────────────────────────────────


def test_every_precheck_handles_the_index_verdict():
    violations = scan_src()
    assert not violations, format_failure(
        summary=(
            "Pre-check against a unique index with no arm for the index's verdict — the loser "
            "of the race gets whatever the enclosing `except` happens to do, not the polite "
            "answer the winner's own pre-check produces (#1721):"
        ),
        violations=[
            f"{v.path}:{v.lineno}: {v.function} writes {v.model} after pre-checking its unique key" for v in violations
        ],
        fix_hint=FIX_HINT,
    )


# ── Inventory non-vacuity ───────────────────────────────────────────

#: Measured against models.py. Counts, not just presence: a silently emptied
#: inventory makes every scan below vacuously green.
EXPECTED_DECLARED_TUPLES = 18
EXPECTED_DECLARED_MODELS = 16
EXPECTED_FORM_COUNTS = {"unique-constraint": 10, "unique-index": 5, "column-unique": 3}

#: Excluded, never truncated — the column subset these name is NOT unique in the DB.
EXPECTED_UNUSABLE = {
    ("Account", "uq_accounts_natural_key"),
    ("MediaBuy", "idx_media_buys_idempotency_key"),
}

#: Composite primary keys are unique indexes too, and for these models the ONLY one.
EXPECTED_PK_ADDITIONS = {
    ("AuthorizedProperty", frozenset({"property_id", "tenant_id"})),
    ("PropertyTag", frozenset({"tag_id", "tenant_id"})),
}


def test_unique_key_inventory_is_complete_and_untruncated():
    keys = unique_key_constraints()
    declared = [key for key in keys if key.kind != "composite-pk"]

    assert len(declared) == EXPECTED_DECLARED_TUPLES
    assert len({key.model for key in declared}) == EXPECTED_DECLARED_MODELS

    form_counts = {form: sum(1 for key in declared if key.kind == form) for form in EXPECTED_FORM_COUNTS}
    assert form_counts == EXPECTED_FORM_COUNTS, (
        "every declaration form is the ONLY form for at least one model — Tenant.subdomain and "
        "Principal.access_token are column-level only, ix_tenants_virtual_host is Index-only"
    )

    unusable = {(key.model, key.name) for key in keys if not key.usable}
    assert unusable == EXPECTED_UNUSABLE, "expression/partial indexes must be recorded and excluded, never truncated"

    by_model = usable_unique_keys_by_model()
    for model, columns in EXPECTED_PK_ADDITIONS:
        assert columns in by_model.get(model, frozenset()), f"{model}'s composite PK must be in the inventory"

    # The truncation this inventory refuses: Account's natural key mentions
    # tenant_id and operator, but the pair is NOT unique — repository lookups
    # legitimately select many rows by exactly that pair.
    assert frozenset({"tenant_id", "operator"}) not in by_model.get("Account", frozenset())


# ── Mutation probe ──────────────────────────────────────────────────

#: Sites whose EXEMPTION is what keeps this guard green. Disable the exemption
#: predicate and every one of them must come back — that is what proves the guard
#: covers the shape the fix repaired, rather than passing because it sees nothing.
#: Pinned as module::function → model; line numbers move, these do not.
EXEMPTION_LOAD_BEARING: set[tuple[str, str, str]] = {
    ("src/admin/blueprints/inventory_profiles.py", "add_inventory_profile", "InventoryProfile"),
    ("src/admin/blueprints/public.py", "provision_tenant", "User"),
    ("src/admin/blueprints/publisher_partners.py", "add_publisher_partner", "PublisherPartner"),
    ("src/admin/blueprints/users.py", "add_user", "User"),
}
EXEMPTION_FLOOR = len(EXEMPTION_LOAD_BEARING)


def test_exemptions_are_load_bearing():
    with_exemptions = {v.triple() for v in scan_src()}
    without_exemptions = {v.triple() for v in scan_src(apply_exemptions=False)}
    re_flagged = without_exemptions - with_exemptions

    missing = EXEMPTION_LOAD_BEARING - re_flagged
    assert not missing, (
        "these sites are supposed to be green only because their write resolves the index "
        f"verdict — with the exemption disabled they must re-flag, and they did not: {sorted(missing)}"
    )
    assert len(re_flagged) >= EXEMPTION_FLOOR, (
        f"only {len(re_flagged)} sites re-flag with exemptions disabled (floor {EXEMPTION_FLOOR}) — "
        "the detector has gone blind to the shape it exists to catch"
    )


# ── Meta-cases ──────────────────────────────────────────────────────


def _find(source: str) -> list[IndexVerdictViolation]:
    return find_unhandled_index_verdicts(ast.parse(source), source)


class TestGuardMetaCases:
    """Inline known-good/known-bad snippets, using real models and real indexes."""

    def test_flags_filter_by_precheck_then_write(self):
        source = (
            "def add_partner(session, tenant_id, domain):\n"
            "    existing = session.scalars(\n"
            "        select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain=domain)\n"
            "    ).first()\n"
            "    if existing:\n"
            "        return 'already there'\n"
            "    partner = PublisherPartner(tenant_id=tenant_id, publisher_domain=domain)\n"
            "    session.add(partner)\n"
        )
        assert [v.model for v in _find(source)] == ["PublisherPartner"]

    def test_flags_where_equality_precheck(self):
        source = (
            "def create_tag(session, tenant_id, tag_id):\n"
            "    stmt = select(PropertyTag).where(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == tag_id)\n"
            "    if session.scalars(stmt).first():\n"
            "        return 'exists'\n"
            "    session.add(PropertyTag(tag_id=tag_id, tenant_id=tenant_id))\n"
        )
        assert [v.model for v in _find(source)] == ["PropertyTag"], "a composite PK is a unique index too"

    def test_flags_precheck_with_extra_filter_keys(self):
        """A filter that covers the tuple AND more still pre-checks the tuple."""
        source = (
            "def add_user(session, tenant_id, email):\n"
            "    stmt = select(User).filter_by(tenant_id=tenant_id, email=email, is_active=True)\n"
            "    if session.scalars(stmt).first():\n"
            "        return 'exists'\n"
            "    session.add(User(tenant_id=tenant_id, email=email))\n"
        )
        assert [v.model for v in _find(source)] == ["User"]

    def test_flags_second_write_in_a_function_that_resolves_the_first(self):
        """The exemption is scoped to the WRITE — one correct recovery does not cover the rest."""
        source = (
            "def provision(session, tenant_id, email, subdomain):\n"
            "    if session.scalars(select(User).filter_by(tenant_id=tenant_id, email=email)).first():\n"
            "        return 'user exists'\n"
            "    if session.scalars(select(Tenant).filter_by(subdomain=subdomain)).first():\n"
            "        return 'subdomain taken'\n"
            "    user = User(tenant_id=tenant_id, email=email)\n"
            "    resolve_or_write(session, conflict=lambda: None, write=lambda: session.add(user),"
            " constraint='uq_users_tenant_email')\n"
            "    tenant = Tenant(tenant_id=tenant_id, subdomain=subdomain)\n"
            "    session.add(tenant)\n"
        )
        assert [v.model for v in _find(source)] == ["Tenant"]

    def test_bare_marker_without_a_reason_is_not_a_justification(self):
        source = (
            "def add_partner(session, tenant_id, domain):\n"
            "    if session.scalars(select(PublisherPartner).filter_by(\n"
            "        tenant_id=tenant_id, publisher_domain=domain)).first():\n"
            "        return 'exists'\n"
            "    session.add(PublisherPartner(tenant_id=tenant_id))  # structural-guard: uniqueness-index-verdict\n"
        )
        assert [v.model for v in _find(source)] == ["PublisherPartner"]

    def test_accepts_resolve_or_write(self):
        source = (
            "def add_partner(session, tenant_id, domain):\n"
            "    def already_exists():\n"
            "        stmt = select(PublisherPartner).filter_by(tenant_id=tenant_id, publisher_domain=domain)\n"
            "        return 'exists' if session.scalars(stmt).first() else None\n"
            "    partner = PublisherPartner(tenant_id=tenant_id, publisher_domain=domain)\n"
            "    return resolve_or_write(session, conflict=already_exists,\n"
            "        write=lambda: session.add(partner), constraint='uq_tenant_publisher')\n"
        )
        assert _find(source) == []

    def test_accepts_write_inside_an_integrity_error_try(self):
        source = (
            "def add_partner(session, tenant_id, domain):\n"
            "    if session.scalars(select(PublisherPartner).filter_by(\n"
            "        tenant_id=tenant_id, publisher_domain=domain)).first():\n"
            "        return 'exists'\n"
            "    try:\n"
            "        session.add(PublisherPartner(tenant_id=tenant_id))\n"
            "        session.flush()\n"
            "    except IntegrityError as exc:\n"
            "        if not is_constraint_violation(exc, 'uq_tenant_publisher'):\n"
            "            raise\n"
            "        return 'exists'\n"
        )
        assert _find(source) == []

    def test_accepts_marked_site_with_a_reason(self):
        source = (
            "def add_partner(session, tenant_id, domain):\n"
            "    if session.scalars(select(PublisherPartner).filter_by(\n"
            "        tenant_id=tenant_id, publisher_domain=domain)).first():\n"
            "        return 'exists'\n"
            "    session.add(PublisherPartner(tenant_id=tenant_id))"
            "  # structural-guard: uniqueness-index-verdict - caller owns the recovery arm\n"
        )
        assert _find(source) == []

    def test_two_unrelated_selects_jointly_covering_a_tuple_are_not_a_precheck(self):
        """Keys bind per STATEMENT: neither query below checks (tenant_id, email)."""
        source = (
            "def report(session, tenant_id, email):\n"
            "    tenant_users = session.scalars(select(User).filter_by(tenant_id=tenant_id)).all()\n"
            "    same_email = session.scalars(select(User).filter_by(email=email)).all()\n"
            "    session.add(User(tenant_id=tenant_id, email=email))\n"
            "    return tenant_users, same_email\n"
        )
        assert _find(source) == []

    def test_partial_index_columns_are_not_a_unique_key(self):
        """Account's natural key is an expression+partial index; the pair it names is not unique."""
        source = (
            "def record(session, tenant_id, operator):\n"
            "    rows = session.scalars(select(Account).where(\n"
            "        Account.tenant_id == tenant_id, Account.operator == operator)).all()\n"
            "    session.add(Account(tenant_id=tenant_id, operator=operator))\n"
            "    return rows\n"
        )
        assert _find(source) == []

    def test_generated_value_write_with_no_precheck_is_out_of_scope(self):
        """Anchor is the PRE-CHECK: a bare insert of a UUID/secret key is a different defect."""
        source = (
            "def create_principal(session, tenant_id):\n"
            "    rows = session.scalars(select(Principal).filter_by(tenant_id=tenant_id)).all()\n"
            "    session.add(Principal(tenant_id=tenant_id, access_token=secrets.token_urlsafe()))\n"
            "    return rows\n"
        )
        assert _find(source) == []

    def test_precheck_on_a_non_unique_column_is_not_a_precheck(self):
        source = (
            "def add_user(session, tenant_id, email):\n"
            "    rows = session.scalars(select(User).filter_by(role='admin')).all()\n"
            "    session.add(User(tenant_id=tenant_id, email=email))\n"
            "    return rows\n"
        )
        assert _find(source) == []
