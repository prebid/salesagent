"""Guard: the BDD shards partition what pytest really collected.

It needs the record a real session published, because pytest's own collection is the
second opinion against ``shard_split``'s glob.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.shard_split import SHARD_COUNTS, assign_files_to_shards
from tests._collection_manifest import BDD_TREE, load, manifest_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _pytest_bdd_module_paths() -> set[str]:
    """Every BDD module the real run collected, independent of shard_split's glob.

    Reads the FULL-TREE record, never a shard's. A shard is invoked with the
    paths `shard_paths.py` produced, so its rows ARE the assignment, and
    comparing those against `assign_files_to_shards()` below would be circular:
    a file the glob misses is absent from both sides and the guard would agree
    with itself. `[testenv:bdd]` and `[testenv:bdd_e2e]` are both invoked as
    `pytest tests/bdd/`, so the full-tree record exists on either run path and
    is derived from pytest's own collection rather than from the glob.

    `-k e2e_rest` on the bdd_e2e path is irrelevant here: rows are retained
    before deselection, so the module set is complete either way.
    """
    rows = load(manifest_dir(), target=BDD_TREE)
    paths = {row["nodeid"].split("::")[0] for row in rows}
    if not paths:
        pytest.fail("the full-tree collection record contains no BDD module paths")
    return paths


@pytest.mark.arch_guard
def test_bdd_shards_partition_suite() -> None:
    expected = _pytest_bdd_module_paths()
    buckets = assign_files_to_shards("bdd", repo_root=_REPO_ROOT)
    assigned = {path for paths in buckets.values() for path in paths}

    assert len(buckets) == SHARD_COUNTS["bdd"]
    assert assigned == expected
