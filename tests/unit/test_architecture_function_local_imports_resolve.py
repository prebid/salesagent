"""Every function-local ``from src.* import NAME`` names something that EXISTS.

SCAFFOLDING. DELETE THIS FILE WHEN THE IMPORT CYCLES ARE GONE.

Owner ruling, and it is the right one: wrong architecture should be UNREPRESENTABLE, not
guarded -- you cannot enumerate the infinite ways of writing bad code, and every guard added
instead of a fix is a permanent tax that still misses the next variant.

This file does not pass that bar, and the proof is in its own subject. ``AdCPError`` ALREADY
did not exist; the breakage was already unrepresentable. What failed is that a FUNCTION-LOCAL
import defers the NameError to call time instead of import time. The 555 function-local
``from src.*`` imports in ``src/`` and 2011 in ``tests/`` exist to break import cycles -- so
the real fix is the module graph, and with no cycles every rename fails eagerly at import,
for free, with no guard at all and no allowlist to rot.

So this is a stopgap that buys visibility until that work lands. It is not the answer, it
must not be cited as precedent for adding another guard, and it comes out with the cycles.

WHY THIS GUARD EXISTS, and why nothing else catches it.

A module-level import that names a deleted symbol fails at IMPORT time, so collection goes
red and the whole suite tells you at once. A FUNCTION-LOCAL import of the same deleted symbol
fails only when that function is CALLED — and if the caller is an error path, a rarely-taken
branch, or a harness leg one transport uses, it can sit green for weeks and then surface as
somebody else's mystery.

Three instances of exactly this shipped during the RFC 9421 merge, all from #1721 renaming a
symbol out from under a caller:

* ``tests/harness/_base.py`` — ``from src.core.exceptions import AdCPError`` (renamed to
  ``AdCPSalesAgentError`` 2026-08-27). It raised only when an ``/a2a`` call FAILED, and the
  harness then reported the ImportError as "no envelope to grade", hiding the real error
  behind a harness assertion.
* ``tests/harness/task_management.py`` and ``tests/bdd/steps/domain/uc002_task_query.py`` —
  ``from src.core.tools.task_management import list_tasks``, a name #1721 replaced with
  ``_list_tasks_impl``. AttributeError at call time.

THE METHOD HOLE THIS CLOSES. A merge's review surface is "files changed by either side".
This defect class is "files REFERENCING a symbol the merge removed", which includes files
NEITHER side touched — the two ``list_tasks`` sites are not in that merge's 725-file
manifest for exactly that reason. No surface computed from changed files can ever contain
them, so the check has to be over the whole tree, keyed on the SYMBOL rather than the diff.

NOT a duplicate of ``test_architecture_import_usage.py``: that one checks the INVERSE
(a name USED in ``src/`` must be imported) and scans only ``src/``. This one checks that an
IMPORTED name resolves, and scans ``src/`` and ``tests/`` both.

Resolution is by IMPORT, not by parsing the target module, because ``from src.core.schemas
import ListTasksRequest`` is legitimate re-export: the name is not defined there, it is
imported there. Only an actual import can tell that apart from a typo.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("src", "tests", "scripts")

#: Modules that DO NOT EXIST YET and are deliberately referenced ahead of implementation.
#: MAY ONLY SHRINK -- an entry comes off when the module lands, never on for a new dangling
#: name. Both entries here PRE-DATE this guard and are absent from every ref in the RFC 9421
#: merge (the merge base, both parents and the result), so neither is a merge regression;
#: they are the standing debt the guard found on its first run.
#:
#: ``src.core.tools.property_list`` -- 71 sites across three integration modules whose own
#: docstring says "Tests for unimplemented _impl functions are marked xfail". Tests written
#: ahead of the implementation, which is the sanctioned stub shape; the resolver that DOES
#: exist is ``src.core.property_list_resolver``.
#:
#: ``src.adapters.gam_order_sync`` -- ONE site, and unlike the others it is PRODUCTION:
#: ``src/admin/blueprints/inventory.py:400`` imports ``sync_gam_orders`` inside the
#: order-sync endpoint. The module has never existed on any of these refs, so that endpoint
#: raises ImportError into its own ``except Exception`` and answers 500. It is dead, not
#: merely unimplemented, and it wants a GitHub issue -- recorded here rather than silently
#: tolerated, because the point of this guard is that a deferred crash should be visible
#: before a user finds it.
_ABSENT_MODULES: frozenset[str] = frozenset(
    {
        "src.core.tools.property_list",
        "src.adapters.gam_order_sync",
    }
)


def _function_local_src_imports() -> list[tuple[str, int, str, str]]:
    """``(file, lineno, module, name)`` for every ``from src.* import NAME`` inside a def.

    Inside a def, not at module scope: a module-level one already fails loudly at import,
    which is the whole distinction this guard is about.
    """
    found: list[tuple[str, int, str, str]] = []
    for root in SCANNED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for func in ast.walk(tree):
                if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for node in ast.walk(func):
                    if not isinstance(node, ast.ImportFrom) or node.level:
                        continue
                    if not node.module or not node.module.startswith("src."):
                        continue
                    rel = str(path.relative_to(REPO_ROOT))
                    for alias in node.names:
                        if alias.name != "*":
                            found.append((rel, node.lineno, node.module, alias.name))
    return found


@pytest.mark.arch_guard
def test_every_function_local_src_import_resolves() -> None:
    """A function-local import must name something that exists, or it is a deferred crash."""
    broken: list[str] = []
    for rel, lineno, module, name in _function_local_src_imports():
        if module in _ABSENT_MODULES:
            continue
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - any failure to import is a finding
            broken.append(f"{rel}:{lineno}: cannot import {module} ({type(exc).__name__}: {exc})")
            continue
        if not hasattr(mod, name):
            broken.append(f"{rel}:{lineno}: {module} has no attribute {name!r}")

    assert not broken, (
        "function-local import(s) naming a symbol that does not exist:\n  "
        + "\n  ".join(broken)
        + "\n\nThese do NOT fail at import time — they fail when the enclosing function is "
        "called, which may be an error path, a rare branch, or one transport's harness leg. "
        "Fix the name (or the module), do not add an exemption."
    )


@pytest.mark.arch_guard
def test_the_scan_reaches_the_corpus() -> None:
    """A resolver bug that silenced the scan would make the guard above pass on nothing."""
    found = _function_local_src_imports()
    assert len(found) > 500, f"only {len(found)} function-local src imports found — the scan is not reaching the tree"
    files = {rel for rel, _, _, _ in found}
    assert any(f.startswith("tests/") for f in files), "scan reached no tests/ file"
    assert any(f.startswith("src/") for f in files), "scan reached no src/ file"


@pytest.mark.arch_guard
def test_the_scan_would_catch_a_dangling_name(tmp_path: Path) -> None:
    """Meta-test: a planted bad import must be detected, in a SANDBOX not in the tree.

    Planted in ``tmp_path``, never under ``src/`` or ``tests/``: the unit suite runs under
    xdist, and a specimen written into the scanned tree is found by whichever sibling worker
    happens to be running the real scan — and by every other tree-scanning guard in this repo.
    """
    specimen = tmp_path / "probe.py"
    specimen.write_text(
        "def f():\n    from src.core.exceptions import AdCPError\n    return AdCPError\n",
        encoding="utf-8",
    )
    tree = ast.parse(specimen.read_text(encoding="utf-8"))
    names = [
        (n.module, a.name)
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
        for n in ast.walk(func)
        if isinstance(n, ast.ImportFrom) and n.module
        for a in n.names
    ]
    assert names == [("src.core.exceptions", "AdCPError")], "the AST shape this guard keys on changed"
    mod = importlib.import_module("src.core.exceptions")
    assert not hasattr(mod, "AdCPError"), (
        "AdCPError resolves again — either it was reinstated (then this specimen is stale) or the guard is now vacuous"
    )


@pytest.mark.arch_guard
def test_no_absent_module_entry_is_stale() -> None:
    """An allowlisted module that now EXISTS must come off the list in the same change.

    Without this, the list only ever grows in effect: an entry whose module has since landed
    would go on silently exempting every import from it, including a genuinely misspelled
    name. Ratchets shrink; a stale entry is how they stop.
    """
    landed = []
    for module in sorted(_ABSENT_MODULES):
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001 - still absent, which is the expected state
            continue
        landed.append(module)
    assert not landed, (
        f"these modules now exist and must be removed from _ABSENT_MODULES: {landed}. "
        "Remove the entry in the same change that landed the module, so the guard starts "
        "grading its imports."
    )
