"""Guard: postgres image tag is unified across the whole repo tree.

Per D24 + PR 5 of issue #1234.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    anchor_consistency_detects_drift,
    assert_anchor_consistency,
    iter_postgres_image_refs,
    postgres_tag_pattern_map,
    repo_root,
)

_KNOWN_BAD_POSTGRES_SOURCES = [
    (Path("compose.yaml"), "services:\n  db:\n    image: postgres:17-alpine\n"),
    (Path("docker-compose.yml"), "services:\n  db:\n    image: postgres:16-alpine\n"),
]


@pytest.mark.arch_guard
def test_postgres_image_unified() -> None:
    """Every postgres: literal must resolve to exactly one tag across git-tracked files."""
    repo = repo_root()
    refs = list(iter_postgres_image_refs(repo))
    assert refs, "non-vacuity: iter_postgres_image_refs found no postgres: literals in git-tracked files"

    paths = sorted({path for path, _ in refs})
    sources = [(path, path.read_text(encoding="utf-8")) for path in paths]
    assert_anchor_consistency(
        sources,
        postgres_tag_pattern_map(),
        label="postgres image",
    )


@pytest.mark.arch_guard
def test_postgres_scan_finds_script_and_doc_anchors() -> None:
    """Git-tracked scan must include non-YAML surfaces (scripts, docs)."""
    refs = {str(path.relative_to(repo_root())) for path, _ in iter_postgres_image_refs(repo_root())}
    assert refs, "non-vacuity: postgres scan found no git-tracked anchors"
    assert any("scripts/" in path for path in refs), "expected postgres refs under scripts/"
    assert any(path.endswith(".md") for path in refs), "expected postgres refs in markdown docs"


@pytest.mark.arch_guard
def test_postgres_anchor_detector_catches_known_bad_drift() -> None:
    """Mutation self-test: mismatched postgres: tags across surfaces must fail the guard."""
    assert anchor_consistency_detects_drift(
        _KNOWN_BAD_POSTGRES_SOURCES,
        postgres_tag_pattern_map(),
        label="postgres image",
    ), "Detector must flag drift between postgres image tags"
