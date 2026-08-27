"""Guard: the vendored upstream signing units stay byte-equal to their cited source.

#1291 (``salesagent-z6nr.33``). ``src/core/signing_contract/_upstream/`` carries VERBATIM copies
of merged upstream ``adcp-client-python`` fixes; "verbatim" is the load-bearing word —
a local edit to a vendored unit turns an auditable copy into a silent fork, and the
divergence surfaces only after the SDK bump deletes the copy. ``_upstream/`` is
excluded from ruff formatting for the same reason (pyproject ``[tool.ruff] exclude``).

This test pins the sha256 of every vendored unit's source segment. It goes red on ANY
local edit — including a well-meaning formatting or docstring tweak. There are exactly
two legitimate ways to change a hash here:

1. **Re-vendor from upstream**: the unit was re-copied from a NEWER upstream merge
   commit. Update the provenance header in the vendored module AND the hash here, in
   the same change, citing the new merge SHA.
2. **Delete the unit**: the pinned SDK now ships the fix (``salesagent-z6nr.28``).
   Remove the unit, its hash, and re-point the delegation — no caller changes.

To re-derive an expected hash from the cited upstream source::

    git -C ~/projects/adcp-client-python show <merge-sha>:src/adcp/signing/canonical.py
    # extract the unit's source segment (ast.get_source_segment) and sha256 it —
    # or run this test's helper: pytest ... -k vendored_provenance --tb=long shows
    # the computed hash for any diverging unit.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

# Path tracks the leaf move (salesagent-n78j0.3): ``_upstream/`` relocated with the rest
# of the dependency-free leaf to ``src/core/signing_contract/``. Same vendored bytes, same
# provenance rule — only the directory changed.
VENDORED_CANONICAL = Path(__file__).resolve().parents[2] / "src/core/signing_contract/_upstream/canonical.py"

#: sha256 of each vendored unit's exact source segment at the provenance commits
#: (PR #985 merge ``be233e4b`` + PR #987 merge ``afa04545``).
EXPECTED_UNIT_HASHES = {
    "TargetUriMalformedError": "59b523c95b9ecff886ca18b8be7182a415ee761ef54139e2a11b1bcc8709f400",
    "host_has_raw_non_ascii": "83574c34add62f60cd3170f6908730957b6ad692c802e3f43bfd52cba64d4eef",
    "canonicalize_target_uri": "2d98e34ded6aa25dec5fbf26ab29c911078767f615d7ee1c89f27e92236f51e2",
    "canonicalize_authority": "3d01dba5ca18a571b041c45626ce0c092f6556024bdf68a6a7223731a0c3d200",
    "_split_or_reject": "dca540dff7b8604d5264e0f0b84a4b39c220f726e638b7c2a63d518390f6967c",
    "_malformed_authority_reason": "535d7dc2ef54d3e8d4a410db3c33f8fe159a4d13ea3557243046121cdf9a3052",
    "_bracketed_host_reason": "4b3e109657641753152e75c23bd52642365f1414ff0c9e0922015284c9aa37ef",
    "_canon_authority": "b38b42eac1dfd5c56b7a637a17e407508d6d5fb5ffeb2f22115bdc295977d6d3",
    "_port_or_reject": "b68e65fb75638eb18980e212e833ade5248bcc6997c97dc8299996ed80d0d04c",
    "_canon_host": "9a84c67f745e505abc145501315144da56a6f8c1ea686f2f0287ff7fac525675",
}


def _vendored_units() -> dict[str, str]:
    src = VENDORED_CANONICAL.read_text(encoding="utf-8")
    return {
        node.name: ast.get_source_segment(src, node) or ""
        for node in ast.parse(src).body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }


def test_every_vendored_unit_is_byte_equal_to_its_cited_upstream_source() -> None:
    """Each vendored unit's bytes hash to the value pinned at vendoring time."""
    units = _vendored_units()
    computed = {name: hashlib.sha256(seg.encode()).hexdigest() for name, seg in units.items()}
    assert computed == EXPECTED_UNIT_HASHES, (
        "A vendored unit diverged from (or was added/removed relative to) its pinned "
        "upstream bytes. Verbatim means verbatim: re-vendor from upstream and update the "
        "provenance header + this pin together, or delete the unit and its pin "
        f"(salesagent-z6nr.28). Computed: {computed}"
    )


def test_the_vendored_module_reuses_unchanged_sdk_units_instead_of_copying() -> None:
    """Function-granular vendoring: unchanged 6.6.0 units are imported, never duplicated.

    Two copies of the same parser in one process is the hazard the layer exists to
    prevent — the vendored module must keep importing the unchanged helpers
    (``_normalize_path``, ``_DEFAULT_PORTS``, ``canonicalize_host``) from the SDK.
    """
    src = VENDORED_CANONICAL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_from_sdk = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("adcp.signing")
        for alias in node.names
    }
    assert {"_normalize_path", "_DEFAULT_PORTS", "canonicalize_host"} <= imported_from_sdk
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    assert not (
        {"_normalize_path", "_remove_dot_segments", "_normalize_pct", "split_structured_field", "build_signature_base"}
        & defined
    ), (
        "An unchanged SDK unit was copied into the vendored module — import it from the "
        "pinned SDK instead (function-granular vendoring rule)."
    )
