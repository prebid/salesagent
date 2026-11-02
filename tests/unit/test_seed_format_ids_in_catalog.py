"""Every format id a setup or sample-data seeder writes exists in the creative catalog.

The seeders write format ids into two columns: ``Product.format_ids`` (``{agent_url, id}``
objects) and ``Tenant.auto_approve_format_ids`` (bare id strings that the mock creative
engine compares against a creative's ``format_id.id``). An id the reference creative
agent does not publish names a format no buyer can build a creative for, and an
auto-approve id that matches no catalog format never approves anything.

The catalog is the checked-in capture of the pinned reference creative agent
(``tests/fixtures/creative_formats/reference_formats.json``, #1418).

Every module under ``src/`` and ``scripts/`` is scanned, rather than a list of known
seeders, so a seeder added later is checked the day it lands.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.core.format_cache import load_reference_formats

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = ("src", "scripts")

AUTO_APPROVE = "auto_approve_format_ids"


def _list_strings(node: ast.AST) -> list[str]:
    """String elements of every list literal under ``node``."""
    return [
        elt.value
        for lst in ast.walk(node)
        if isinstance(lst, ast.List)
        for elt in lst.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]


def _seeded_format_ids(source: str) -> list[str]:
    """Format ids written by literal: ``{agent_url, id}`` dicts and auto-approve lists."""
    ids: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = {k.value: v for k, v in zip(node.keys, node.values, strict=True) if isinstance(k, ast.Constant)}
            fid = keys.get("id")
            if "agent_url" in keys and isinstance(fid, ast.Constant) and isinstance(fid.value, str):
                ids.append(fid.value)
        elif isinstance(node, ast.keyword) and node.arg == AUTO_APPROVE:
            ids.extend(_list_strings(node.value))
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == AUTO_APPROVE for t in node.targets
        ):
            ids.extend(_list_strings(node.value))
    return ids


def test_seeded_format_ids_exist_in_reference_catalog() -> None:
    catalog = {fmt.format_id.id for fmt in load_reference_formats()}
    seeded = {
        path.relative_to(REPO_ROOT).as_posix(): ids
        for root in SCANNED_ROOTS
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if (ids := _seeded_format_ids(path.read_text()))
    }

    # A scanner that stopped recognizing the seed shapes would pass the membership check vacuously.
    assert seeded, f"no seeded format ids found under {SCANNED_ROOTS}; the scanner no longer matches the seeders"
    missing = {module: sorted(set(ids) - catalog) for module, ids in seeded.items() if set(ids) - catalog}
    assert not missing, f"seeded format ids absent from the reference catalog: {missing}"
