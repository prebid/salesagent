"""Guard: no SHARED TEST BUILDER seeds the pre-3.1.1 creative shape.

Three builders used to. ``tests/fixtures/factories.py::CreativeFactory`` returned a
creative as a bare ``format_id`` string plus a JSON-dumped ``content`` blob and no
``assets`` at all, and its ``create_video_creative`` seeded ``"duration": 30``;
``make_test_creative`` and ``make_test_creative_list``
(``tests/helpers/creative_test_helpers.py``) each seeded a vestigial ``variants``.
salesagent-b341x.10 killed the first and cleaned the other two.

WHY A BUILDER IS GRADED DIFFERENTLY FROM A LITERAL. A one-off literal in a test module
is wrong once. A builder re-seeds its shape into every caller, including callers written
after a migration has just cleaned them -- which is the specific way this shape kept
coming back. So the scan here is deliberately NARROW: the two directories that exist to
be imported by other tests (``tests/fixtures/``, ``tests/helpers/``), not the test
modules. The one-off literals in those modules are a census with its own owners
(salesagent-b341x.6, salesagent-b341x.11) and grading them here would be a second,
disagreeing ratchet over the same sites.

WHAT IT CATCHES THAT THE CENSUS CANNOT. ``scripts/audit/creative_literal_sites.py``
walks ``ast.Dict`` displays only, so ``Creative(creative_id=..., variants=[], ...)`` --
a call, not a dict -- was invisible to it, and ``make_test_creative_list`` had to be
named by hand in the ticket. This detector reads dict displays AND call keywords, and
``test_detector_catches_the_kwarg_form_the_census_misses`` is the meta-test that pins it.

The key sets are IMPORTED from that census script, not restated: two definitions of
"which fields are pre-3.1.1" would drift, and the drift would be silent in both
directions.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.audit.creative_literal_sites import CREATIVE_KEYS, PRE_311_KEYS
from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    iter_module_trees,
    repo_root,
)

#: The directories whose modules exist to be imported by other tests. A creative shape
#: written here is a shape every caller inherits.
BUILDER_ROOTS = ("tests/fixtures", "tests/helpers")

#: Same rule the census uses: two creative keys, because one on its own is a reference
#: rather than a creative.
MIN_CREATIVE_KEYS = 2


def _keys_of(node: ast.AST) -> set[str]:
    """String keys of a dict display, or keyword names of a call. Empty for anything else."""
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if isinstance(node, ast.Call):
        return {kw.arg for kw in node.keywords if kw.arg is not None}
    return set()


def find_pre_311_creative_seeds(tree: ast.Module) -> list[int]:
    """Line numbers where a creative-shaped payload carries a pre-3.1.1 field."""
    lines: list[int] = []
    for node in ast.walk(tree):
        keys = _keys_of(node)
        if len(keys & CREATIVE_KEYS) >= MIN_CREATIVE_KEYS and keys & PRE_311_KEYS:
            lines.append(node.lineno)
    return lines


def _builder_dirs() -> list[Path]:
    root = repo_root()
    return [root / d for d in BUILDER_ROOTS]


@pytest.mark.arch_guard
def test_scanned_roots_exist() -> None:
    """A guard pointed at a moved directory passes by scanning nothing."""
    missing = [d for d in _builder_dirs() if not d.is_dir()]
    assert not missing, f"BUILDER_ROOTS no longer exist: {missing}"


@pytest.mark.arch_guard
def test_no_shared_builder_seeds_a_pre_311_creative() -> None:
    """The live scan. Every hit is a builder handing the old shape to its callers."""
    violations = [
        f"{relpath}:{line}"
        for tree, relpath in iter_module_trees(_builder_dirs())
        for line in find_pre_311_creative_seeds(tree)
    ]
    assert not violations, (
        "Shared test builder(s) seed a pre-3.1.1 creative shape "
        f"(fields {sorted(PRE_311_KEYS)}). Put the content in the assets slot map "
        "(tests/factories/creative_asset.py: build_assets/image_spec):\n" + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.arch_guard
def test_detector_catches_known_bad_shapes() -> None:
    """Positive control: reintroduce each killed shape and the detector must fire."""
    assert_detector_catches_ast_snippets(
        find_pre_311_creative_seeds,
        snippets={
            "the killed CreativeFactory dict": (
                'def create():\n    return {"creative_id": "c1", "format_id": "display_300x250",\n'
                '            "content": "{}", "duration": 30}\n'
            ),
            "the vestigial variants key": (
                'def make():\n    return {"creative_id": "c1", "name": "n", "assets": {}, "variants": []}\n'
            ),
            "inline snippet instead of assets": (
                'def make():\n    return {"creative_id": "c1", "name": "n", "snippet": "<div/>"}\n'
            ),
        },
    )


@pytest.mark.arch_guard
def test_detector_catches_the_kwarg_form_the_census_misses() -> None:
    """The would-be-missed case: a MODEL CALL, which the dict-only census cannot see.

    ``make_test_creative_list`` was exactly this and the ticket had to name it by hand.
    Asserted twice over: the detector fires, and the census does not, so the meta-test
    fails if this guard is ever narrowed back to dict displays.
    """
    source = 'def make():\n    return Creative(creative_id="c1", variants=[], name="n", format={}, assets={})\n'
    tree = ast.parse(source)
    assert find_pre_311_creative_seeds(tree), "detector missed the call-keyword form"
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Dict) and _keys_of(n) & PRE_311_KEYS], (
        "census-style dict-display scanning would have caught this after all — "
        "re-check whether this guard is still adding anything"
    )


@pytest.mark.arch_guard
def test_negative_control_modern_builder_is_not_flagged() -> None:
    """A builder in the 3.1.1 shape must pass, or the guard bans the fix as well as the bug."""
    source = (
        "def make_creative_dict():\n"
        '    return {"creative_id": "c1", "name": "Test Banner",\n'
        '            "format_id": {"agent_url": AGENT_URL, "id": "display_300x250_image"},\n'
        '            "assets": build_assets(image_spec("banner_image"))}\n'
    )
    assert not find_pre_311_creative_seeds(ast.parse(source))


@pytest.mark.arch_guard
def test_negative_control_a_non_creative_dict_carrying_duration_is_not_flagged() -> None:
    """``duration`` alone is not a creative. Only one creative key, so no hit.

    Keeps the guard off video/format descriptors and GAM-shaped mocks, which is why the
    two-key floor is imported from the census rather than relaxed here.
    """
    assert not find_pre_311_creative_seeds(ast.parse('x = {"duration": 30, "codec": "h264"}\n'))
