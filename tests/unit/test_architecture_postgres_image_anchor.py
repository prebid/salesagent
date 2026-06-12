"""Guard: postgres image tag is unified across the whole repo tree.

Per D24 + PR 5 of issue #1234.
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import format_failure, iter_postgres_image_refs, repo_root


@pytest.mark.arch_guard
def test_postgres_image_unified() -> None:
    """Every postgres: literal must resolve to exactly one tag across the repo."""
    refs = list(iter_postgres_image_refs(repo_root()))
    assert refs, "no postgres image references found"

    by_tag: dict[str, list[str]] = {}
    for path, tag in refs:
        rel = str(path.relative_to(repo_root()))
        by_tag.setdefault(tag, []).append(rel)

    assert len(by_tag) == 1, format_failure(
        summary="postgres image tag drift — expected exactly one tag repo-wide",
        violations=[f"{tag}: {sorted(paths)}" for tag, paths in sorted(by_tag.items())],
    )


@pytest.mark.arch_guard
def test_postgres_scan_finds_script_and_doc_anchors() -> None:
    """Whole-tree scan must include non-YAML surfaces (scripts, docs)."""
    refs = {str(path.relative_to(repo_root())) for path, _ in iter_postgres_image_refs(repo_root())}
    assert any("scripts/" in path for path in refs), "expected postgres refs under scripts/"
    assert any(path.endswith(".md") for path in refs), "expected postgres refs in markdown docs"
