"""Guard: sync_creatives builds an ``updated`` result in exactly ONE place.

Disease (the sync_creatives twin of #1721): the dry_run preview arm and the
live update arm each
described the update they were reporting with their own field list. Nothing
forced them to agree, and they did not. Two entries carrying the same
``creative_id`` in one payload previewed ``[created, created]`` where a real run
produced ``[created, updated]`` — the preview reads only persisted state, while
the live path resolves the second entry against the row the first one flushed.
A single-entry update of an existing creative diverged too: the preview reported
no ``changes`` at all where live reported six.

This is the same shape as the accounts defect (#1721,
``test_guards_sync_accounts_row_builder``); the sibling guard's docstring is the
fuller statement of why a second builder is the mechanism of drift.

The fix routes both arms through ``build_update_sync_result``. This guard keeps
them there.

Scope notes that make this guard neither blind nor noisy:

- Only ``updated`` results are in scope. The package builds ``created``,
  ``failed`` and ``deleted`` results in several legitimate places
  (``_failed_sync_result``, the create arm, the delete arm), and forbidding
  those would fire on code that has no parity obligation.
- The whole ``src/core/tools/creatives/`` package is scanned, not one module,
  because the two arms live in different files (``_sync.py`` previews,
  ``_processing.py`` updates) — which is precisely how they drifted unnoticed.

Note what this guard does NOT do: it pins the mechanism, not the behaviour. The
behavioural protection is ``TestDryRunPreviewMatchesLiveRun`` in
tests/integration/test_creative_sync_behavioral.py, which runs the SAME payload
through a preview and a live sync and compares them — the live run is the
oracle, so those tests cannot drift from the behaviour they mirror either.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import REPO_ROOT, parse_module

PACKAGE = "src/core/tools/creatives"
BUILDER = "build_update_sync_result"
RESULT_CLASS = "SyncCreativeResult"
UPDATED_ACTION = "updated"


def _is_result_construction(call: ast.Call) -> bool:
    return (getattr(call.func, "id", None) or getattr(call.func, "attr", None)) == RESULT_CLASS


def _builds_an_updated_result(call: ast.Call) -> bool:
    """True when this construction's ``action`` is (or can be) ``updated``.

    Matches the literal ``"updated"``, the ``CreativeAction.updated`` enum member,
    and any non-constant expression (e.g. ``"updated" if changes else
    "unchanged"``) — an expression is exactly how a second update-result builder
    would hide from a literal-only check.
    """
    for keyword in call.keywords:
        if keyword.arg != "action":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant):
            return value.value == UPDATED_ACTION
        if isinstance(value, ast.Attribute):
            return value.attr == UPDATED_ACTION
        # A computed action — cannot be ruled out, so it is in scope.
        return True
    return False


def find_updated_result_constructions_outside_builder(tree: ast.Module) -> list[int]:
    """Line numbers constructing an ``updated`` SyncCreativeResult outside the builder."""
    inside_builder: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == BUILDER:
            inside_builder.update(sub.lineno for sub in ast.walk(node) if isinstance(sub, ast.Call))

    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_result_construction(node)
        and _builds_an_updated_result(node)
        and node.lineno not in inside_builder
    )


def _package_modules() -> list[Path]:
    return sorted((REPO_ROOT / PACKAGE).glob("*.py"))


def test_the_package_still_constructs_sync_results():
    """The guard is only as good as what it can see — pin that it sees something.

    If the package stopped constructing ``SyncCreativeResult`` under that name,
    every scan below would return an empty list and pass while checking nothing.
    """
    total = sum(
        1
        for path in _package_modules()
        for node in ast.walk(parse_module(path))
        if isinstance(node, ast.Call) and _is_result_construction(node)
    )
    assert total, f"{PACKAGE} no longer constructs {RESULT_CLASS} — the guard would be inert"


def test_sync_creatives_builds_its_updated_result_in_one_place():
    violations = {
        str(path.relative_to(REPO_ROOT)): found
        for path in _package_modules()
        if (found := find_updated_result_constructions_outside_builder(parse_module(path)))
    }
    assert not violations, (
        f"an 'updated' {RESULT_CLASS} is constructed outside {BUILDER}() at {violations}. "
        "The dry_run preview and the live update must describe the SAME outcome — two field "
        "lists is how the preview came to report 'created' for an id a real run reports as "
        "'updated', and to omit the changed-field list entirely. BR-RULE-062: a preview "
        "must describe an outcome a real run can produce. Route it "
        "through the shared builder."
    )


def test_guard_catches_a_second_updated_builder():
    """Positive meta-test: a second update-result site is a violation, however spelled."""
    literal = (
        "def build_update_sync_result(**kw):\n"
        "    return SyncCreativeResult(action='updated', **kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return SyncCreativeResult(creative_id=entry.id, action='updated')\n"
    )
    assert find_updated_result_constructions_outside_builder(ast.parse(literal)), (
        "a literal 'updated' construction outside the builder must be flagged"
    )

    enum_form = (
        "def _preview(entry):\n    return SyncCreativeResult(creative_id=entry.id, action=CreativeAction.updated)\n"
    )
    assert find_updated_result_constructions_outside_builder(ast.parse(enum_form)), (
        "the enum spelling is the same defect wearing a different name"
    )

    # The would-be-missed case: the action is COMPUTED, so a check that only
    # matched literals and enum members would wave this through — and a computed
    # action is exactly what the live arm uses.
    computed = (
        "def _preview(entry, changes):\n"
        "    return SyncCreativeResult(creative_id=entry.id, action='updated' if changes else 'unchanged')\n"
    )
    assert find_updated_result_constructions_outside_builder(ast.parse(computed)), (
        "a computed action must be treated as in scope — it cannot be ruled out"
    )


def test_guard_ignores_the_builder_itself_and_other_actions():
    """Negative meta-test: the builder is the point, and other actions are legitimate."""
    single_site = (
        "def build_update_sync_result(**kw):\n"
        "    return SyncCreativeResult(action='updated' if kw['c'] else 'unchanged', **kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return build_update_sync_result(c=entry.changes)\n"
        "\n"
        "def _failed(entry):\n"
        "    return SyncCreativeResult(creative_id=entry.id, action='failed')\n"
        "\n"
        "def _created(entry):\n"
        "    return SyncCreativeResult(creative_id=entry.id, action=CreativeAction.created)\n"
        "\n"
        "def _deleted(entry):\n"
        "    return SyncCreativeResult(creative_id=entry.id, action='deleted')\n"
    )
    assert find_updated_result_constructions_outside_builder(ast.parse(single_site)) == []
