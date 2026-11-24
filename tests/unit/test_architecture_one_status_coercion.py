"""Guard: the persisted status vocabulary is coerced in exactly one place.

``media_buys.status`` is a closed vocabulary (``PersistedMediaBuyStatus``) stored in
a ``String`` column, so SOMETHING must turn a column string into a member. That
coercion is ``PersistedMediaBuyStatus.parse`` / ``parse_or_none`` and nothing else.

Why a guard rather than a convention: before this rule there were three coercions
with three different failure policies — one returned ``False``, one raised a located
``ValueError``, one raised a bare ``ValueError`` from inside a per-row loop, so a
single unmapped row failed an entire tenant listing and reached the buyer as
``VALIDATION_ERROR``/correctable for a defect in the SELLER's own store. Each was
locally reasonable. The damage came from there being three.

Two spellings are refused:

1. ``PersistedMediaBuyStatus(x)`` — direct construction. It raises ``ValueError``,
   which no boundary translates into a spec envelope, and it skips the case
   normalization every other reader assumes.
2. ``(x or "").lower()`` on a status — the hand-rolled normalize-then-compare that
   each of the three sites had grown its own copy of.
"""

from __future__ import annotations

import ast
import re

import pytest

from tests.unit._architecture_helpers import format_failure, parse_module, rel, repo_root

# No production file is exempt, models.py included. The definition itself does not
# need an exemption: ``parse_or_none`` coerces via ``cls(...)`` (not the class name)
# and lowers ``raw`` (not a status-named variable), so neither detector matches it.
# That is deliberate — an exemption for the defining module is exactly where a fourth
# coercion would reappear, and the pre-fix ``is_media_buy_seller_confirmed`` lived
# there.
ALLOWED_FILES = {
    "tests/unit/test_architecture_one_status_coercion.py",
}

_STATUS_LOWER = re.compile(r"\(\s*[\w.]*status[\w.]*\s+or\s+[\"']{2}\s*\)\s*\.lower\(\)")

_KNOWN_BAD_CONSTRUCTION = """
def read(buy):
    return MAP[PersistedMediaBuyStatus(buy.status)]
"""

_KNOWN_BAD_LOWER = """
def confirmed(status):
    return (status or "").lower() in COMMITTED
"""

_KNOWN_GOOD = """
def read(buy):
    return MAP[PersistedMediaBuyStatus.parse(buy.status, media_buy_id=buy.media_buy_id)]

def confirmed(status):
    member = PersistedMediaBuyStatus.parse_or_none(status)
    return member is not None and member.seller_confirmed
"""


def _constructs_status(func: ast.expr) -> bool:
    """Whether *func* names the status class itself, bare or module-qualified.

    ``PersistedMediaBuyStatus(x)`` is an ``ast.Name``; ``models.PersistedMediaBuyStatus(x)``
    is an ``ast.Attribute`` whose ``attr`` is the class name. Matching only the first left
    the qualified spelling — the one a module-style import produces — invisible to this
    guard, which is a hole rather than a policy.

    ``PersistedMediaBuyStatus.parse(...)`` is also an ``ast.Attribute``, but its ``attr``
    is ``parse``, not the class name, so it still does not match. That exclusion is the
    point of the guard and is preserved by construction rather than by a special case.
    """
    if isinstance(func, ast.Name):
        return func.id == "PersistedMediaBuyStatus"
    return isinstance(func, ast.Attribute) and func.attr == "PersistedMediaBuyStatus"


def find_direct_construction(tree: ast.Module, relpath: str) -> list[str]:
    """``PersistedMediaBuyStatus(...)`` called as a constructor, however qualified."""
    return [
        f"{relpath}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _constructs_status(node.func)
    ]


def find_hand_rolled_normalization(source: str, relpath: str) -> list[str]:
    """``(<something status> or "").lower()`` — the spelling all three sites shared."""
    return [f"{relpath}:{lineno}" for lineno, line in enumerate(source.splitlines(), 1) if _STATUS_LOWER.search(line)]


def _scan(repo, *, ast_finder=None, text_finder=None) -> list[str]:
    violations: list[str] = []
    for path in sorted((repo / "src").rglob("*.py")):
        relpath = rel(path, repo)
        if relpath in ALLOWED_FILES:
            continue
        if ast_finder is not None:
            tree = parse_module(path)
            if tree is not None:
                violations.extend(ast_finder(tree, relpath))
        if text_finder is not None:
            violations.extend(text_finder(path.read_text(encoding="utf-8"), relpath))
    return violations


@pytest.mark.arch_guard
def test_no_direct_vocabulary_construction() -> None:
    violations = _scan(repo_root(), ast_finder=find_direct_construction)
    assert not violations, format_failure(
        summary="The status vocabulary is entered through PersistedMediaBuyStatus.parse, not the constructor",
        violations=violations,
        fix_hint=(
            "Use PersistedMediaBuyStatus.parse(raw, media_buy_id=...) (typed terminal "
            "refusal) or parse_or_none(raw) where an unknown value must read as absent. "
            "The bare constructor raises ValueError, which reaches the buyer as "
            "VALIDATION_ERROR/correctable for a seller-side store defect."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_no_hand_rolled_status_normalization() -> None:
    violations = _scan(repo_root(), text_finder=find_hand_rolled_normalization)
    assert not violations, format_failure(
        summary="Status casing is normalized once, inside PersistedMediaBuyStatus.parse",
        violations=violations,
        fix_hint="Call PersistedMediaBuyStatus.parse / parse_or_none instead of lowering the string here.",
        docs_link="docs/development/structural-guards.md",
    )


def _probe(tmp_path, source: str, **finders) -> list[str]:
    probe = tmp_path / "src" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")
    return _scan(tmp_path, **finders)


@pytest.mark.arch_guard
def test_construction_detector_catches_known_bad(tmp_path) -> None:
    assert _probe(tmp_path, _KNOWN_BAD_CONSTRUCTION, ast_finder=find_direct_construction)


@pytest.mark.arch_guard
def test_construction_detector_passes_parse_calls(tmp_path) -> None:
    assert not _probe(tmp_path, _KNOWN_GOOD, ast_finder=find_direct_construction), (
        "parse()/parse_or_none() are the sanctioned form and must not be flagged"
    )


@pytest.mark.arch_guard
def test_normalization_detector_catches_known_bad(tmp_path) -> None:
    assert _probe(tmp_path, _KNOWN_BAD_LOWER, text_finder=find_hand_rolled_normalization)


@pytest.mark.arch_guard
def test_normalization_detector_passes_known_good(tmp_path) -> None:
    assert not _probe(tmp_path, _KNOWN_GOOD, text_finder=find_hand_rolled_normalization)


@pytest.mark.arch_guard
def test_normalization_detector_ignores_unrelated_lowering(tmp_path) -> None:
    """The regex-slip case, in the direction that matters: DON'T flag other fields.

    ``(suggestion or "").lower()`` and ``(message or "").lower()`` are legitimate and
    common in error-assertion code. A rule anchored on ``or ""`` alone would flag
    them, the guard would be muted with an allowlist, and the real rule would rot.
    """
    unrelated = 'def f(suggestion, message):\n    return (suggestion or "").lower() + (message or "").lower()\n'
    assert not _probe(tmp_path, unrelated, text_finder=find_hand_rolled_normalization)
