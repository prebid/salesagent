"""Vendored AdCP fixtures keep upstream's own ``$id``, and the refresh script
refuses to write anything else.

``tests/fixtures/adcp_schemas_pinned/`` vendors frozen upstream artifacts from
TWO upstream revisions, which namespace their schemas DIFFERENTLY — so there
are two legitimate ``$id`` conventions in the directory, not one:

- the **flat** tree — artifacts fetched from ``static/schemas/source`` at
  ``PINNED_SHA``, where upstream's own ``$id`` is version-free:
  ``/schemas/<category>/<name>.json``. The pin there is a SHA, not a spec
  version, so a version segment in one of THOSE files would be an assertion of
  a spec identity the commit does not carry (``_refresh.py``, "$id convention").
- the **versioned** tree, ``<major>.<minor>.<patch>/`` — the trust-root
  documents fetched from ``dist/schemas`` at git tag ``v3.1.1`` (#1291 A3),
  where upstream's own ``$id`` DOES carry the version:
  ``/schemas/3.1.1/<path>.json``. Those 73 files are consumed by
  ``tests/integration/test_trust_root_documents.py`` through
  ``tests/helpers/pinned_schema.py``'s version-prefixed refs; 29 of them differ
  STRUCTURALLY from the installed SDK's generated copies, which is why they are
  vendored at all.

Both conventions are graded here — each tree against ITS OWN — because the rule
``_refresh.py`` actually enforces is "upstream's ``$id``, verbatim", and the
layout rule stores each file at that ``$id`` minus the ``/schemas/`` prefix.
Exempting the versioned tree instead would leave 73 files ungraded; grading it
against the flat convention would mark upstream's own namespace as a violation.

GH #1881 flagged that ``_refresh.py`` wrote the fetched body verbatim with no
``$id`` check and no documented convention, so running the documented refresh
procedure could regress a file with nothing turning red until a downstream
reader tripped over it much later. This module grades both halves — the
artifacts on disk obey their convention, and ``_refresh.py``'s guard actually
rejects a violation. Both offline: no clone, no network.

GH #1881, #1868, #1291
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import REPO_ROOT

_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "adcp_schemas_pinned"
_REFRESH_PATH = _FIXTURE_DIR / "_refresh.py"

# A vendored tree directory named for the spec version it was fetched at.
_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+$")

# The flat tree's convention: site-rooted, category-qualified, and carrying NO
# version segment (not "/schemas/3.1.1/enums/...") — a dotted version can never
# match [a-z0-9-]+.
_FLAT_ID_CONVENTION = re.compile(r"^/schemas/[a-z0-9-]+/[a-z0-9-]+\.json$")

# The versioned tree's convention: the same site-rooted form with upstream's own
# version segment, and arbitrary nesting under it (core/assets/…,
# formats/canonical/…, whose upstream filenames use underscores).
_VERSIONED_ID_CONVENTION = re.compile(r"^/schemas/\d+\.\d+\.\d+/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\.json$")

# The one flat-tree file whose $id IS version-qualified, and why: it is pinned
# by SHA-256 to the v3.1.1 TAG's copy (tests/unit/test_guards_error_code_fixture_pin.py,
# #1721 M5) — so upstream's verbatim $id there carries the version — while the
# file itself stays at its stable flat path, because its readers
# (tests/harness/transport.py, the recovery/suggestion enum-conformance guards)
# address it there. Recorded as data, so re-vendoring it from a version-free
# revision turns this module red and forces the reviewed decision.
_TAG_VENDORED_FLAT_FILES = {"enums/error-code.json": "3.1.1"}


def _load_refresh_module():
    """Import _refresh.py by path — the fixture directory is not a package."""
    spec = importlib.util.spec_from_file_location("_adcp_fixture_refresh", _REFRESH_PATH)
    assert spec and spec.loader, f"cannot load {_REFRESH_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_refresh = _load_refresh_module()


def _vendored_files() -> list[Path]:
    return sorted(p for p in _FIXTURE_DIR.rglob("*.json"))


def _relpath(path: Path) -> str:
    return path.relative_to(_FIXTURE_DIR).as_posix()


def _spec_version_of(path: Path) -> str | None:
    """The spec version *path*'s ``$id`` namespace carries, or None if version-free.

    Either from the versioned tree it lives in, or — for the one flat file
    re-vendored from a tag — from ``_TAG_VENDORED_FLAT_FILES``.
    """
    rel = _relpath(path)
    head = rel.split("/", 1)[0]
    if _VERSION_DIR.match(head):
        return head
    return _TAG_VENDORED_FLAT_FILES.get(rel)


def _expected_id(path: Path) -> str:
    """Upstream's own ``$id`` for *path*: ``/schemas/`` + the layout rule's path,
    with the version segment present exactly when upstream's namespace has one."""
    rel = _relpath(path)
    version = _spec_version_of(path)
    if version is not None and not rel.startswith(f"{version}/"):
        rel = f"{version}/{rel}"
    return f"/schemas/{rel}"


def _versioned_tree_files() -> list[Path]:
    return [p for p in _vendored_files() if _VERSION_DIR.match(_relpath(p).split("/", 1)[0])]


def _flat_tree_files() -> list[Path]:
    return [p for p in _vendored_files() if not _VERSION_DIR.match(_relpath(p).split("/", 1)[0])]


def test_the_fixture_set_is_not_empty():
    """Meta-guard: the parametrized checks below must grade something — in BOTH trees.

    If the last vendored file of either root set is ever removed (the #1883
    reconciliation would do exactly that to the flat one), these tests would
    otherwise pass vacuously for that half instead of prompting deletion of the
    corresponding root set from _refresh.py — or of this module along with the
    whole directory.
    """
    where = _FIXTURE_DIR.relative_to(REPO_ROOT)
    assert _flat_tree_files(), (
        f"No flat (SHA-pinned) vendored schemas under {where} — if that root set was retired, "
        "drop ROOTS from _refresh.py and the flat half of this module with it."
    )
    assert _versioned_tree_files(), (
        f"No version-namespaced vendored schemas under {where}/<version>/ — if that root set "
        "was retired, drop V311_ROOTS from _refresh.py and the versioned half of this module "
        "with it (and pinned_schema.py's vendored source)."
    )


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: str(p.relative_to(_FIXTURE_DIR)))
def test_vendored_id_is_upstreams_own_and_matches_its_path(path: Path):
    """Each vendored file's $id is upstream's own form for its own path."""
    schema_id = json.loads(path.read_text())["$id"]
    expected = _expected_id(path)

    assert schema_id == expected, (
        f"{path.relative_to(REPO_ROOT)} has $id {schema_id!r}, expected {expected!r} — vendored "
        "fixtures keep upstream's own site-rooted $id verbatim, version segment included or "
        "omitted exactly as the revision they were fetched from writes it "
        "(see _refresh.py's '$id convention' section)."
    )

    version = _spec_version_of(path)
    convention = _VERSIONED_ID_CONVENTION if version else _FLAT_ID_CONVENTION
    assert convention.match(schema_id), (
        f"$id {schema_id!r} does not match the {'version-qualified' if version else 'version-free'} "
        f"convention {convention.pattern!r} that {_relpath(path)}'s upstream revision uses. A flat, "
        "SHA-pinned artifact must NOT carry a version (the pin there is a SHA, not a spec version); "
        "a tag-vendored one must."
    )


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: str(p.relative_to(_FIXTURE_DIR)))
def test_refresh_accepts_every_vendored_file_at_its_own_id(path: Path):
    """Negative control: the guard is not a blanket reject.

    Every artifact on disk passes ``check_id_convention`` when fetched as the
    ref its own convention derives — so the guard below rejects divergence, not
    the vendoring itself, for BOTH root sets.
    """
    _refresh.check_id_convention(_expected_id(path), json.loads(path.read_text()))


@pytest.mark.parametrize(
    ("ref", "schema_id"),
    [
        # Fetched flat (SHA pin), but carrying a version: the version would be a
        # spec identity the commit does not have, and the file would land under
        # 3.1.1/ instead of where the ref names.
        pytest.param("/schemas/enums/error-code.json", "/schemas/3.1.1/enums/error-code.json", id="version-added"),
        # ...and the mirror: fetched from the tag, but with the version stripped,
        # which would collide with the flat root set's output path.
        pytest.param("/schemas/3.1.1/brand.json", "/schemas/brand.json", id="version-stripped"),
    ],
)
def test_refresh_rejects_a_divergent_id(ref: str, schema_id: str):
    """_refresh.py's guard is real: an $id that disagrees with the ref it was
    fetched as raises instead of being written — in either direction.

    Without this, the convention would be documented prose again — the exact
    'asserted in a docstring, graded by nothing' shape this PR's review named.
    """
    with pytest.raises(_refresh.IdConventionError, match=re.escape(f"expected {ref!r}")):
        _refresh.check_id_convention(ref, {"$id": schema_id, "enum": []})


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"enum": []}, id="no-id-at-all"),
        pytest.param({"$id": "enums/error-code.json", "enum": []}, id="relative"),
        pytest.param({"$id": "https://adcontextprotocol.org/schemas/enums/error-code.json"}, id="absolute-url"),
    ],
)
def test_refresh_rejects_other_id_shapes(schema: dict):
    """Every non-conforming $id shape is rejected, not just the versioned one."""
    with pytest.raises(_refresh.IdConventionError):
        _refresh.check_id_convention("/schemas/enums/error-code.json", schema)


def test_refresh_docstring_documents_both_conventions():
    """The decision #1881 asked for is recorded where a refresher will read it —
    for both root sets, since both are written by the same script."""
    # Backticks stripped so the assertion grades the prose, not its RST markup.
    docstring = (_refresh.__doc__ or "").replace("`", "")
    assert "$id convention" in docstring, "_refresh.py's docstring must name the decided $id convention (GH #1881)"
    assert "/schemas/<category>/<name>.json" in docstring, (
        "_refresh.py's docstring must state the concrete version-free form, not just that a convention exists"
    )
    assert "/schemas/3.1.1/<category>/<name>.json" in docstring, (
        "_refresh.py's docstring must state the version-qualified form too — it writes BOTH root "
        "sets, and a refresher who reads only the version-free rule would 'fix' the 3.1.1 tree"
    )
