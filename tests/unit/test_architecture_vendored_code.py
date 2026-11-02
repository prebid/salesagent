"""``src/vendor/`` is excluded from every structural guard, so bound what may live there.

``src_python_files`` skips ``src/vendor/`` (see ``VENDOR_DIR`` in
``_architecture_helpers``): those guards grade authorship decisions, and copied
third-party source makes none of them. That exclusion is correct and it is also the only
place in ``src/`` where the architecture checks do not run — which makes it the obvious
place to park code that would otherwise fail one.

These tests make that impractical. A vendored package must carry its licence and say
where it came from, and the rest of the codebase may only reach it through the one seam
that owns the concept — so a module cannot quietly acquire an unchecked dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import REPO_ROOT, scan_src, src_python_files

VENDOR_ROOT = REPO_ROOT / "src" / "vendor"

#: Modules permitted to import from ``src.vendor``. One entry per vendored package,
#: naming the seam that owns that concept for the rest of the codebase. This list may
#: SHRINK. Growing it means a second caller reached past the seam -- fix the caller.
SANCTIONED_IMPORTERS = {
    # The signing layer's canonicalization seam owns the vendored canonicalizer, and both
    # sides of url-canonicalization.mdx step 2 hang off it: ``canonical_target_uri`` for a
    # verifier comparing a signature base, ``producer_target_uri`` for a producer computing
    # a comparison key. ``canonical_agent_url`` -- which used to be the entry here -- now
    # reaches the package through that seam like everything else.
    "src/core/signing/canonical.py",
}


def _vendored_packages() -> list[Path]:
    if not VENDOR_ROOT.is_dir():
        return []
    return sorted(p for p in VENDOR_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists())


def test_src_python_files_actually_skips_the_vendor_directory():
    """The exclusion exists and works — otherwise every test below is theatre."""
    scanned = list(src_python_files(REPO_ROOT))
    assert scanned, "src_python_files returned nothing; the scan is broken"
    vendored = [p for p in scanned if VENDOR_ROOT in p.parents]
    assert not vendored, f"structural scans are still walking vendored code: {vendored}"


def test_scan_src_also_skips_the_vendor_directory():
    """The OTHER walker. Two enumerators exist; both must agree or the exclusion leaks.

    Uses a detector that flags every module it is handed, so the result is exactly the
    set of files `scan_src` walks.
    """
    walked = scan_src(lambda tree: [1])
    vendored = [rel for rel in walked if rel.replace("\\", "/").startswith("src/vendor/")]
    assert walked, "scan_src walked nothing; the scan is broken"
    assert not vendored, f"scan_src is still walking vendored code: {vendored}"


@pytest.mark.skipif(not _vendored_packages(), reason="nothing vendored")
@pytest.mark.parametrize("package", _vendored_packages(), ids=lambda p: p.name)
def test_each_vendored_package_carries_its_licence(package: Path):
    """Copied source keeps its licence beside it, or it should not have been copied."""
    licences = [n for n in ("LICENSE", "LICENSE.txt", "LICENCE", "COPYING") if (package / n).exists()]
    assert licences, (
        f"src/vendor/{package.name} has no licence file. Vendoring redistributes someone "
        f"else's work; the licence travels with it."
    )


@pytest.mark.skipif(not _vendored_packages(), reason="nothing vendored")
@pytest.mark.parametrize("package", _vendored_packages(), ids=lambda p: p.name)
def test_each_vendored_package_names_its_upstream_version_and_its_exit(package: Path):
    """A copy nobody can date is a copy nobody will ever retire.

    The ``__init__`` docstring has to say which upstream release this is, and what
    removes it. Without the first, no one can tell whether the copy is stale; without
    the second, a temporary measure becomes permanent by default.
    """
    doc = ast.get_docstring(ast.parse((package / "__init__.py").read_text())) or ""
    assert doc.strip(), f"src/vendor/{package.name}/__init__.py has no module docstring"

    import re

    assert re.search(r"\d+\.\d+\.\d+", doc), (
        f"src/vendor/{package.name}'s docstring names no upstream VERSION, so nobody can "
        f"tell whether the copy has gone stale"
    )
    assert re.search(r"\b(salesagent-|#)\w+", doc), (
        f"src/vendor/{package.name}'s docstring names no issue that DELETES it. A vendored "
        f"copy with no exit is permanent by accident."
    )


def test_only_the_sanctioned_seam_imports_vendored_code():
    """Vendored code is reached through one seam per concept, never scattered.

    The seam is what makes the copy removable: when upstream is fixed, one module changes
    its import and the directory goes. A second caller reaching in directly turns a
    one-line migration into a search-and-replace, and puts unscanned code on a path
    nobody reviewed for it.
    """
    offenders: dict[str, list[str]] = {}
    for path in src_python_files(REPO_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in SANCTIONED_IMPORTERS:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.vendor"):
                offenders.setdefault(rel, []).append(node.module or "")
            elif isinstance(node, ast.Import):
                # setdefault().extend() on an empty generator would CREATE the key and
                # report every file in src/ as an offender. Only touch the dict on a hit.
                hits = [a.name for a in node.names if a.name.startswith("src.vendor")]
                if hits:
                    offenders.setdefault(rel, []).extend(hits)

    assert not offenders, (
        "src/vendor/ is imported outside its sanctioned seam:\n"
        + "\n".join(f"  {mod}: {', '.join(names)}" for mod, names in sorted(offenders.items()))
        + "\n\nReach the concept through the seam that owns it (see SANCTIONED_IMPORTERS), "
        "so retiring the vendored copy stays a one-line change."
    )


def test_the_sanctioned_importer_list_has_not_gone_stale():
    """Every entry names a file that exists and actually imports vendored code."""
    stale = []
    for rel in sorted(SANCTIONED_IMPORTERS):
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file is gone)")
            continue
        if "src.vendor" not in path.read_text():
            stale.append(f"{rel} (no longer imports src.vendor — drop the entry)")
    assert not stale, "SANCTIONED_IMPORTERS is stale:\n  " + "\n  ".join(stale)
