"""Guard: every creative status production can WRITE is an AdCP CreativeStatus member.

``list_creatives`` parses ``creatives.status`` through the closed AdCP
``CreativeStatus`` enum. A value production writes that is NOT a member of that enum
cannot be read back honestly: the reader has to substitute a placeholder and tell the
buyer, on ``errors[]``, that the seller's own record is undescribable
(``src/core/tools/creatives/listing.py``).

That is exactly what happened (salesagent-zm5l): ``creatives.status`` defaulted to
``"pending"`` — not an AdCP status — since the initial schema, so every row written
with the field omitted read back as a fabricated ``pending_review``.

This guard is the write-side half of the fix, and it is the durable piece borrowed
from ``tests/unit/test_media_buy_status_consistency.py``: an SDK/spec bump that
renames or removes a member fails loudly here instead of silently re-creating the bug.
The read-side half — that the reader surfaces rather than fabricates — is graded by
``tests/integration/test_list_creatives_unrecognized_status.py``.

What is deliberately NOT built here: a creatives ``PERSISTED_STATUS_TO_CANONICAL``
map like ``src/core/tools/_media_buy_status.py``'s. That map has 15 entries because
live media-buy writers still produce a rich legacy vocabulary that cannot be migrated
away. Creatives had exactly ONE non-spec value and no production writer emits it, so
such a map would be dead on arrival and would leave a second vocabulary behind.

Two write surfaces are covered:

1. every string LITERAL production assigns to a creative's ``status`` attribute or
   passes as ``status=`` to a creative construction / repository ``create``;
2. the ``status`` parameter DEFAULT on ``CreativeRepository.create`` — the value a
   caller that omits the field writes, which is the route the bug actually took.

The allowlist is EMPTY and stays that way: a non-spec creative status is not debt to
be tracked, it is a row the buyer cannot be told the truth about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from adcp.types import CreativeStatus

from tests.unit._architecture_helpers import REPO_ROOT, format_failure, iter_module_trees, safe_parse

SCAN_DIRS = [REPO_ROOT / "src"]

_SPEC_STATUSES = frozenset(member.value for member in CreativeStatus)

_REPOSITORY_PATH = Path("src/core/database/repositories/creative.py")
_REPOSITORY_CLASS = "CreativeRepository"
_REPOSITORY_METHOD = "create"

#: Non-spec creative statuses this guard tolerates. EMPTY by design — see the module
#: docstring. Growth is not an option; fix the writer.
CREATIVE_STATUS_WRITE_ALLOWLIST: set[tuple[str, int, str]] = set()

_FIX_HINT = (
    "Write a member of the AdCP CreativeStatus enum "
    f"({', '.join(sorted(_SPEC_STATUSES))}). A value outside it is unreadable to "
    "list_creatives, which then reports the creative as 'processing' and names it in the "
    "response's errors[] as a seller-side data defect. If the spec genuinely lacks the "
    "state you need, raise it upstream — do not persist a private vocabulary behind a "
    "closed enum, and do not allowlist it here."
)


def _mentions_creative(node: ast.expr) -> bool:
    """True when an attribute/name chain names a creative object.

    Scoped by NAME rather than by type inference: this guard reads source, not a type
    checker, and every creative write in ``src/`` goes through a variable whose name says
    so (``creative``, ``db_creative``, ``existing_creative``, ``creative_repo``,
    ``uow.creatives``). Sibling entities that also carry a ``status`` (``sync_job``,
    ``media_buy``, ``step``, ``approval_job``) are excluded by the same test, which is
    what keeps this guard about creatives and nothing else.
    """
    if isinstance(node, ast.Name):
        return "creative" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "creative" in node.attr.lower() or _mentions_creative(node.value)
    return False


def _literal_status_writes(tree: ast.Module, relpath: str) -> list[tuple[str, int, str]]:
    """Return ``(relpath, lineno, status_literal)`` for every creative status literal written."""
    writes: list[tuple[str, int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "status" and _mentions_creative(target.value):
                    writes.append((relpath, node.lineno, node.value.value))
        elif isinstance(node, ast.Call):
            func = node.func
            constructs_creative = (isinstance(func, ast.Name) and func.id == "Creative") or (
                isinstance(func, ast.Attribute) and func.attr == "create" and _mentions_creative(func.value)
            )
            if not constructs_creative:
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "status"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    writes.append((relpath, node.lineno, keyword.value.value))

    return writes


def _scan_literal_writes() -> list[tuple[str, int, str]]:
    writes: list[tuple[str, int, str]] = []
    for tree, relpath in iter_module_trees(SCAN_DIRS):
        writes.extend(_literal_status_writes(tree, relpath))
    return writes


def _repository_create_status_default() -> tuple[int, str]:
    """Return ``(lineno, default)`` for ``CreativeRepository.create``'s ``status`` parameter."""
    tree = safe_parse(REPO_ROOT / _REPOSITORY_PATH)
    assert tree is not None, f"{_REPOSITORY_PATH} is missing or unparseable"

    for klass in ast.walk(tree):
        if not (isinstance(klass, ast.ClassDef) and klass.name == _REPOSITORY_CLASS):
            continue
        for func in klass.body:
            if not (isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) and func.name == _REPOSITORY_METHOD):
                continue
            args = func.args
            # ``create`` is keyword-only (``*`` separator), so the default lives in
            # kw_defaults, positionally aligned with kwonlyargs.
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
                if arg.arg != "status":
                    continue
                assert isinstance(default, ast.Constant) and isinstance(default.value, str), (
                    f"{_REPOSITORY_PATH}: {_REPOSITORY_CLASS}.{_REPOSITORY_METHOD}'s status default is "
                    f"no longer a string literal — re-point this guard rather than deleting it"
                )
                return default.lineno, default.value

    raise AssertionError(
        f"{_REPOSITORY_CLASS}.{_REPOSITORY_METHOD} has no keyword-only 'status' parameter in "
        f"{_REPOSITORY_PATH} — re-point this guard rather than deleting it"
    )


class TestCreativeStatusWriteVocabulary:
    """Everything production writes to a creative's status is an AdCP status."""

    @pytest.mark.arch_guard
    def test_spec_statuses_are_the_pinned_sdk_enum(self):
        """Pin the vocabulary itself, so an SDK bump that changes it is visible here.

        Without this the guard would silently re-scope: a bump that dropped
        ``pending_review`` would make the repository default a violation, and one that
        added the writers' values would make the guard vacuous.
        """
        assert _SPEC_STATUSES == {
            "processing",
            "pending_review",
            "approved",
            "suspended",
            "rejected",
            "archived",
        }, (
            f"The pinned AdCP CreativeStatus enum is now {sorted(_SPEC_STATUSES)}. Reconcile every "
            f"creative status writer in src/ with the new vocabulary, then update this pin — the "
            f"reader's placeholder ('processing') and the data migration's target ('pending_review') "
            f"both assume the members listed here."
        )

    @pytest.mark.arch_guard
    def test_every_literal_status_write_is_a_spec_status(self):
        """No production writer persists a creative status the reader cannot parse."""
        writes = _scan_literal_writes()
        assert writes, (
            "No creative status literal writes found in src/ — the detector stopped matching, so "
            "this test can no longer fail. Re-point it (the known writers are the admin creative "
            "review path and the sync tool) rather than leaving it vacuous."
        )

        violations = [
            write for write in writes if write[2] not in _SPEC_STATUSES and write not in CREATIVE_STATUS_WRITE_ALLOWLIST
        ]
        assert not violations, format_failure(
            summary=f"{len(violations)} creative status write(s) persist a value AdCP does not define:",
            violations=[f"{relpath}:{line}: writes {status!r}" for relpath, line, status in violations],
            fix_hint=_FIX_HINT,
            docs_link="AdCP 3.1.1 dist/schemas/3.1.1/enums/creative-status.json",
        )

    @pytest.mark.arch_guard
    def test_repository_create_default_is_a_spec_status(self):
        """``CreativeRepository.create``'s default is the route the original bug took.

        The default fires on every caller that omits ``status=``, so a non-spec default
        makes the write API produce unreadable rows without anyone naming a bad value.
        The runtime sibling of this static check is
        ``tests/integration/test_list_creatives_unrecognized_status.py::TestWriteSideDefault``.
        """
        lineno, default = _repository_create_status_default()
        assert default in _SPEC_STATUSES, format_failure(
            summary=f"{_REPOSITORY_CLASS}.{_REPOSITORY_METHOD}'s status default is not an AdCP creative status:",
            violations=[f"{_REPOSITORY_PATH}:{lineno}: default is {default!r}"],
            fix_hint=_FIX_HINT,
            docs_link="AdCP 3.1.1 dist/schemas/3.1.1/enums/creative-status.json",
        )

    @pytest.mark.arch_guard
    def test_allowlist_is_empty(self):
        """A non-spec creative status is never acceptable debt.

        Every other guard in this suite carries a shrink-only allowlist because it
        inherited pre-existing violations. This one landed with none, and there is no
        conformant way to persist a value the buyer-facing reader cannot describe — so
        the ratchet here is "empty", not "smaller than last time".
        """
        assert not CREATIVE_STATUS_WRITE_ALLOWLIST, (
            f"CREATIVE_STATUS_WRITE_ALLOWLIST gained {sorted(CREATIVE_STATUS_WRITE_ALLOWLIST)}. {_FIX_HINT}"
        )

    @pytest.mark.arch_guard
    def test_detector_catches_a_non_spec_write(self):
        """Detector self-test: the shapes this guard exists to catch are actually matched."""
        source = (
            "def repair(creative, uow):\n"
            "    creative.status = 'pending'\n"
            "    uow.creatives.create(name='n', status='queued')\n"
            "    Creative(name='n', status='draft')\n"
            "    sync_job.status = 'running'\n"  # sibling entity: must NOT match
            "    media_buy.status = 'draft'\n"  # sibling entity: must NOT match
        )
        tree = ast.parse(source, filename="<detector-self-test>")
        found = sorted(status for _, _, status in _literal_status_writes(tree, "<snippet>"))
        assert found == ["draft", "pending", "queued"], (
            f"detector matched {found}; it must see the attribute write, the repository create and "
            f"the direct construction, and must NOT see sibling entities' status writes"
        )
