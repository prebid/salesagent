"""Guard: MediaBuyRepository is the only writer of ``revision`` and ``confirmed_at``.

Both fields are repository-managed by construction. ``revision`` is the buyer's
optimistic-concurrency token, so it must be bumped by exactly one code path that
cannot be forgotten; ``confirmed_at`` is the seller-commitment instant and is
write-once. Any assignment to either field outside
``src/core/database/repositories/`` re-opens the hole that PR #1941's review
found: a by-construction contract re-implemented as a compensating call that a
future writer forgets to make.

**This guard is a forward-lock, not a first-run red.** It is GREEN at the commit
that introduces it — measured: the only assignments to these two attributes
anywhere in ``src/`` are the two inside
``src/core/database/repositories/media_buy.py``. Its grade is therefore the
MUTATION, not the first run: move either of those writes (or any new stamp/bump
the lane adds) outside ``repositories/`` and this test must go red. The
``test_detector_*`` cases below are the standing non-vacuity proof that the
detector can actually see such a write.

Deliberately NOT guarded: ``.status``. Too many unrelated entities own a status
column, so a ``.status`` scan would be noise rather than a seam.

Precedent: ``tests/unit/test_architecture_no_raw_media_package_select.py``.

GH #1941 (the media-buy write seam)
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    format_failure,
    parse_module,
    rel,
    repo_root,
    src_python_files,
)

# The repository package is the ONLY place allowed to write these fields.
REPOSITORY_DIR = "src/core/database/repositories/"

# Fields whose value is owned by MediaBuyRepository, by construction.
GUARDED_FIELDS = frozenset({"revision", "confirmed_at"})

_KNOWN_BAD_SNIPPETS = {
    "plain_assign_revision": "def f(media_buy):\n    media_buy.revision = 2",
    "plain_assign_confirmed_at": "def f(mb, now):\n    mb.confirmed_at = now",
    "self_attr_assign": "def f(self, now):\n    self.media_buy.confirmed_at = now",
    "aug_assign": "def f(media_buy):\n    media_buy.revision += 1",
    "annotated_assign": "def f(media_buy):\n    media_buy.revision: int = 7",
    "tuple_target": "def f(mb, now):\n    mb.confirmed_at, other = now, None",
    "setattr_literal": 'def f(mb, now):\n    setattr(mb, "confirmed_at", now)',
    "core_update_values": "def f(now):\n    return update(MediaBuy).values(confirmed_at=now)",
}


def _attribute_targets(node: ast.AST) -> list[ast.Attribute]:
    """Every ``ast.Attribute`` written by an assignment-like *node*."""
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = list(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    else:
        return []

    found: list[ast.Attribute] = []
    for target in targets:
        # Unpacking (``a.revision, b = ...``) writes each element.
        elements = target.elts if isinstance(target, ast.Tuple | ast.List) else [target]
        found.extend(el for el in elements if isinstance(el, ast.Attribute))
    return found


def _is_core_dml_with_guarded_field(node: ast.AST) -> bool:
    """True for ``update(MediaBuy).values(confirmed_at=...)`` and the ``insert`` form.

    Core DML bypasses ORM instrumentation entirely, so a guarded field written this way
    never passes ``_stamp_confirmation_if_needed`` and never bumps the revision — the
    same hole as constructing the row with the field pre-set, reached by a different
    spelling.

    Keyed on the guarded KEYWORD rather than on the model class, which is what makes it
    alias-proof without resolving anything: ``update(MediaBuy)``, ``update(MB)`` and
    ``update(models.MediaBuy)`` all reach the same matcher, because the keyword is the
    part that cannot be renamed. Measured against the tree: 55 ``.values(...)`` calls in
    ``src/``, none of them passing a guarded keyword, so this admits no false positives
    and needs no allowlist.
    """
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "values":
        return False
    return any(kw.arg in GUARDED_FIELDS for kw in node.keywords)


def find_media_buy_write_seam_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of writes to a guarded field in *tree*.

    Catches attribute assignment (plain, augmented, annotated, and unpacked), the
    ``setattr(obj, "<field>", ...)`` string-literal escape hatch, and Core DML —
    ``update(MediaBuy).values(confirmed_at=...)`` — which bypasses ORM instrumentation
    entirely.

    Construction is deliberately NOT scanned any more. It used to be, and that matcher had
    to recognise every spelling of a constructor call; it never recognised
    ``MediaBuy(**kwargs)``, the spelling the repository itself uses, because a double-star
    call carries one keyword whose ``arg`` is ``None``. ``MediaBuy.__init__`` now refuses
    the guarded fields outright, so the shape cannot be written at all and there is no
    spelling left to enumerate.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        lines.extend(attr.lineno for attr in _attribute_targets(node) if attr.attr in GUARDED_FIELDS)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in GUARDED_FIELDS
        ):
            lines.append(node.lineno)
        if _is_core_dml_with_guarded_field(node):
            lines.append(node.lineno)
    return sorted(lines)


def _scan_src(*, inside_repositories: bool) -> list[str]:
    """Return ``"<rel-path>:<line>"`` for every guarded-field write in (or outside) the seam."""
    repo = repo_root()
    hits: list[str] = []
    for path in src_python_files(repo):
        rel_path = rel(path, repo)
        if rel_path.startswith(REPOSITORY_DIR) is not inside_repositories:
            continue
        hits.extend(f"{rel_path}:{lineno}" for lineno in find_media_buy_write_seam_violations(parse_module(path)))
    return sorted(hits)


@pytest.mark.arch_guard
def test_no_media_buy_write_seam_bypass() -> None:
    """Nothing outside the repository package writes ``revision`` or ``confirmed_at``.

    Zero allowlist entries, by design: a site that cannot be routed through
    MediaBuyRepository is an escalation, not an allowlist entry.
    """
    violations = _scan_src(inside_repositories=False)
    assert not violations, format_failure(
        summary=f"{sorted(GUARDED_FIELDS)} are MediaBuyRepository-owned — nothing outside it may write them",
        violations=violations,
        fix_hint=(
            "Route the write through MediaBuyRepository: update_status / update_fields stamp "
            "confirmed_at and bump revision for you, and the package writers bump the parent. "
            "Do NOT add an allowlist entry — this guard ships with none."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_catches_known_bad_snippets() -> None:
    """Non-vacuity: the detector sees each shape a bypass could take."""
    assert_detector_catches_ast_snippets(find_media_buy_write_seam_violations, snippets=_KNOWN_BAD_SNIPPETS)


@pytest.mark.arch_guard
def test_detector_finds_the_real_repository_writes() -> None:
    """Non-vacuity against production: the seam itself must trip the detector.

    A scan that silently visits nothing (wrong root, wrong prefix, a detector that
    only matches synthetic snippets) would pass ``test_no_media_buy_write_seam_bypass``
    vacuously. The repository's own two writes are the fixed point that proves the
    scan reaches real ``src/`` code.
    """
    seam_writes = _scan_src(inside_repositories=True)
    assert seam_writes, "Detector found no guarded-field write inside " + REPOSITORY_DIR

    repo = repo_root()
    written_fields = {
        attr.attr
        for path in src_python_files(repo)
        if rel(path, repo).startswith(REPOSITORY_DIR)
        for node in ast.walk(parse_module(path))
        for attr in _attribute_targets(node)
        if attr.attr in GUARDED_FIELDS
    }
    assert written_fields == GUARDED_FIELDS, (
        f"Expected the repository package to write every guarded field {sorted(GUARDED_FIELDS)}, "
        f"but the detector only saw {sorted(written_fields)}. Either a write moved out of the "
        f"seam (that is the regression this guard exists for) or the detector stopped matching it."
    )
