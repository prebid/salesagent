"""Guard: postgres image tag is unified across compose, CI, and helper scripts.

Per D24 + PR 5 of issue #1234. Canonical tag: postgres:17-alpine.
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import iter_postgres_image_refs, repo_root

CANONICAL_TAG = "17-alpine"


@pytest.mark.arch_guard
def test_postgres_image_unified() -> None:
    """Every postgres image reference must use the canonical 17-alpine tag."""
    refs = list(iter_postgres_image_refs(repo_root()))
    assert refs, "no postgres image references found — expected postgres:17-alpine anchors"

    by_tag: dict[str, list[str]] = {}
    for path, tag in refs:
        rel = str(path.relative_to(repo_root()))
        by_tag.setdefault(tag, []).append(rel)

    assert set(by_tag) == {CANONICAL_TAG}, (
        f"postgres image drift — expected only '{CANONICAL_TAG}', found: {sorted(by_tag)}\n"
        + "\n".join(f"  {tag}: {sorted(paths)}" for tag, paths in sorted(by_tag.items()))
    )
