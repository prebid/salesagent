"""Guard: sync_accounts builds an update result in ONE place, off POST-WRITE state.

Disease (#1721, the third head of it): the dry_run preview arm and the live
update arm ALREADY called one ``_build_sync_result`` with a byte-identical
argument list — and still diverged. ``billing=existing.billing`` read the row
AFTER ``repo.update_fields`` had setattr-ed it on the live arm and BEFORE
anything touched it on the dry arm, so a single-entry billing change previewed
the value the buyer was REPLACING as though it were the value they would get,
while ``notification_configs``/``billing_entity`` came out right on both arms
only because those two were routed through ``changes.get(...)`` by hand.

So the invariant here is not "one builder" — it is "one builder whose inputs are
named such that pre-write state cannot be passed in". ``_build_update_result``
takes a ``state`` that is the row as the write would LEAVE it, and reads every
reported value off it.

Two assertions, both AST over the one module:

1. On the PROVISIONING path, only ``_build_update_result`` may pass a COMPUTED
   ``action`` to ``_build_sync_result``. Every other provisioning site passes a
   literal (``"created"``, ``"failed"``, ``"updated"`` for the closure block);
   only an update arm has to decide between ``updated`` and ``unchanged``, so a
   computed action there is exactly a second update arm and nothing else.
2. Inside ``_build_update_result``, the only attribute bases the
   ``_build_sync_result`` arguments may use are ``state`` and ``entry``. This is
   the assertion that fails on the pre-fix code: ``billing=existing.billing``
   sitting next to ``changes.get(...)`` names two more sources, which is how
   "post-write" — a precondition, not a type — has to be enforced.

Scope, and why it is a scope rather than an allowlist: the SETTINGS-UPDATE arm
(``_process_settings_update_entry``) also computes an update action, and it is
deliberately out of this guard's reach. It is not drift from the provisioning
builder — it emits a genuinely different response item, by design: the brand and
operator come off the persisted row (a settings-update entry carries neither,
and the mode-exclusivity guard rejects one that does) and it echoes
``payment_terms``, which no provisioning result does. Collapsing the two would
CHANGE the wire on both. That arm is separately tracked for a defect of its own
— it ignores ``dry_run`` entirely and PERSISTS; when it grows a preview it will
need the same post-write state object, and unifying the two builders is a
decision for that change, not this one. Until then this guard makes no claim
about it.

This is a THIRD guard for this module, not an extension of either sibling.
``test_guards_sync_accounts_row_builder`` pins where a ROW is constructed — a
different invariant. ``test_guards_sync_creatives_update_result_builder`` hunts
``build_update_sync_result``, a symbol that does not and should not exist here:
the two tools mirror each other's PATTERN, deliberately not each other's code
(different prior-state type, different key, and a per-mode field policy
creatives has no axis for).

Note what this guard does NOT do: it pins the mechanism, not the behaviour. The
behavioural protection is ``TestDryRunPreviewMatchesLiveRun`` in
tests/integration/test_sync_accounts.py, which runs the SAME payload through a
preview and a live sync and compares — the live run is the oracle, so those
tests cannot drift from the behaviour they mirror either.
"""

import ast

from tests.unit._architecture_helpers import REPO_ROOT, parse_module

MODULE = "src/core/tools/accounts.py"
BUILDER = "_build_update_result"
RESULT_BUILDER = "_build_sync_result"
#: The only objects an update result may be described from: the POST-WRITE row
#: and the buyer's own entry (whose ``brand`` the response echoes verbatim).
#: ``changes`` is deliberately absent — routing a field through ``changes.get(...)``
#: to make it post-write is the hand-correction that hid the pre-write reads
#: beside it; after the state object exists, needing it means the state is wrong.
PERMITTED_BASES = frozenset({"state", "entry"})

#: The settings-update arm, out of scope — see the module docstring. Named once
#: here so the exclusion is a stated boundary rather than a growable list.
SETTINGS_UPDATE_ARM = "_process_settings_update_entry"


def _is_result_build(call: ast.Call) -> bool:
    return (getattr(call.func, "id", None) or getattr(call.func, "attr", None)) == RESULT_BUILDER


def _function_named(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _builder_body(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return _function_named(tree, BUILDER)


def _call_lines_in(node: ast.AST | None) -> set[int]:
    return {sub.lineno for sub in ast.walk(node) if isinstance(sub, ast.Call)} if node is not None else set()


def find_computed_action_outside_builder(tree: ast.Module) -> list[int]:
    """Lines where a NON-LITERAL ``action`` reaches ``_build_sync_result`` on the provisioning path."""
    out_of_scope = _call_lines_in(_builder_body(tree)) | _call_lines_in(_function_named(tree, SETTINGS_UPDATE_ARM))

    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_result_build(node)) or node.lineno in out_of_scope:
            continue
        for keyword in node.keywords:
            if keyword.arg == "action" and not isinstance(keyword.value, ast.Constant):
                violations.append(node.lineno)
    return sorted(violations)


def find_foreign_bases_in_builder(tree: ast.Module) -> list[str]:
    """Attribute bases used in the builder's ``_build_sync_result`` arguments that aren't permitted."""
    builder = _builder_body(tree)
    if builder is None:
        return []

    foreign: set[str] = set()
    for call in (n for n in ast.walk(builder) if isinstance(n, ast.Call) and _is_result_build(n)):
        for keyword in call.keywords:
            for sub in ast.walk(keyword.value):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                    if sub.value.id not in PERMITTED_BASES:
                        foreign.add(f"{sub.value.id}.{sub.attr}")
    return sorted(foreign)


def test_the_builder_still_exists_and_builds_a_result():
    """The guard is only as good as the function it resolves — pin that it found one.

    If ``_build_update_result`` were renamed away, both scans below would go
    quiet: the first would treat the builder's own computed action as a
    violation (loud, fine), but the second would return an empty list and pass
    while checking nothing.
    """
    builder = _builder_body(parse_module(REPO_ROOT / MODULE))
    assert builder is not None, f"{MODULE} no longer defines {BUILDER}() — the guard would be inert"
    assert [n for n in ast.walk(builder) if isinstance(n, ast.Call) and _is_result_build(n)], (
        f"{BUILDER}() no longer calls {RESULT_BUILDER}() — the guard would be inert"
    )


def test_sync_accounts_computes_its_update_action_in_one_place():
    violations = find_computed_action_outside_builder(parse_module(REPO_ROOT / MODULE))
    assert not violations, (
        f"{MODULE} computes a {RESULT_BUILDER}() action outside {BUILDER}() at line(s) {violations}. "
        "Deciding between 'updated' and 'unchanged' IS the update arm, and the dry_run preview and "
        "the live update must describe the SAME outcome (BR-RULE-062). Route it through the shared "
        "builder, handing it the POST-WRITE state."
    )


def test_the_update_result_describes_only_post_write_state():
    foreign = find_foreign_bases_in_builder(parse_module(REPO_ROOT / MODULE))
    assert not foreign, (
        f"{BUILDER}() reads {foreign} when building the result. Only 'state' (the row as the write "
        "would LEAVE it) and 'entry' (the buyer's echoed brand) are permitted: reading anything else "
        "is how the preview came to echo the value the buyer is REPLACING while the live arm echoed "
        "the value they would get (#1721)."
    )


def test_guard_catches_a_second_update_arm():
    """Positive meta-test: a computed action outside the builder is a violation."""
    drifted = (
        "def _build_update_result(*, entry, operator, state, changes):\n"
        "    return _build_sync_result(action='updated' if changes else 'unchanged', name=state.name)\n"
        "\n"
        "def _preview(entry, existing, changes):\n"
        "    return _build_sync_result(action='updated' if changes else 'unchanged', name=existing.name)\n"
    )
    assert find_computed_action_outside_builder(ast.parse(drifted)) == [5], (
        "a second site computing the update action must be flagged"
    )

    # The pre-fix shape: the arms share one builder, but it is handed a row whose
    # meaning differs per arm. A literal-action check alone would wave this through.
    # Both foreign sources are reported — the pre-write row AND the hand-correction
    # that made two of the ten arguments post-write while the rest stayed stale.
    pre_fix = (
        "def _build_update_result(*, entry, operator, state, changes, existing):\n"
        "    return _build_sync_result(\n"
        "        brand=entry.brand,\n"
        "        action='updated' if changes else 'unchanged',\n"
        "        billing=existing.billing,\n"
        "        notification_configs=changes.get('notification_configs'),\n"
        "    )\n"
    )
    assert find_foreign_bases_in_builder(ast.parse(pre_fix)) == ["changes.get", "existing.billing"], (
        "reading a row other than the post-write state must be flagged"
    )


def test_guard_ignores_the_builder_itself_and_literal_action_sites():
    """Negative meta-test: create/failed/closure sites pass literals and are legitimate."""
    single_site = (
        "def _build_update_result(*, entry, operator, state, changes):\n"
        "    return _build_sync_result(\n"
        "        brand=entry.brand,\n"
        "        operator=operator,\n"
        "        action='updated' if changes else 'unchanged',\n"
        "        status=state.status,\n"
        "        billing=state.billing,\n"
        "    )\n"
        "\n"
        "def _created(entry):\n"
        "    return _build_sync_result(brand=entry.brand, action='created', status='active')\n"
        "\n"
        "def _failed(entry, errors):\n"
        "    return _build_sync_result(brand=entry.brand, action='failed', status='rejected', errors=errors)\n"
        "\n"
        "def _closure(db_acct):\n"
        "    return _build_sync_result(brand=db_acct.brand, action='updated', status='closed')\n"
        "\n"
        # The settings-update arm computes an action too, and emits a different
        # response item by design (brand/operator off the row, payment_terms
        # echoed). Out of scope -- see the module docstring.
        "def _process_settings_update_entry(entry, repo):\n"
        "    return _build_sync_result(brand=existing.brand, action='updated' if changes else 'unchanged')\n"
    )
    assert find_computed_action_outside_builder(ast.parse(single_site)) == []
    assert find_foreign_bases_in_builder(ast.parse(single_site)) == []
