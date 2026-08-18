"""The vendored AdCP fixture keeps upstream's version-free ``$id``, and the
refresh script refuses to write anything else.

``tests/fixtures/adcp_schemas_pinned/`` vendors exactly one frozen upstream
artifact — ``enums/error-code.json`` at ``PINNED_SHA``, kept only for its
``enumMetadata`` ``suggestion`` text (see ``_refresh.py``'s docstring). GH #1881
flagged that ``_refresh.py`` wrote the fetched body verbatim with no ``$id``
check and no documented convention, so running the documented refresh procedure
could regress the file with nothing turning red until a downstream reader
tripped over it much later.

The decided convention is upstream's own, version-free form:
``/schemas/<category>/<name>.json``. This module grades both halves — the
artifact on disk obeys it, and ``_refresh.py``'s guard actually rejects a
violation. Both offline: no clone, no network.

GH #1881, #1868
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

# The convention: site-rooted, category-qualified, and carrying NO version
# segment (not "/schemas/3.1.1/enums/...").
_ID_CONVENTION = re.compile(r"^/schemas/[a-z0-9-]+/[a-z0-9-]+\.json$")


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


def test_the_fixture_set_is_not_empty():
    """Meta-guard: the parametrized checks below must grade something.

    If the last vendored file is ever removed (the #1883 reconciliation would do
    exactly that), these tests would otherwise pass vacuously instead of
    prompting deletion of this module along with the directory.
    """
    assert _vendored_files(), (
        f"No vendored schemas under {_FIXTURE_DIR.relative_to(REPO_ROOT)} — if the directory "
        "was retired, delete this module and _refresh.py with it."
    )


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: str(p.relative_to(_FIXTURE_DIR)))
def test_vendored_id_is_version_free_and_matches_its_path(path: Path):
    """Each vendored file's $id is upstream's version-free form for its own path."""
    schema_id = json.loads(path.read_text())["$id"]
    expected = "/schemas/" + str(path.relative_to(_FIXTURE_DIR)).replace("\\", "/")

    assert schema_id == expected, (
        f"{path.relative_to(REPO_ROOT)} has $id {schema_id!r}, expected {expected!r} — vendored "
        "fixtures keep upstream's version-free /schemas/<category>/<name>.json verbatim "
        "(see _refresh.py's '$id convention' section)."
    )
    assert _ID_CONVENTION.match(schema_id), (
        f"$id {schema_id!r} carries a version segment or an unexpected shape; the pin here is a "
        "SHA, not a spec version."
    )


def test_refresh_rejects_a_divergent_id():
    """_refresh.py's guard is real: a wrong $id raises instead of being written.

    Without this, the convention would be documented prose again — the exact
    'asserted in a docstring, graded by nothing' shape this PR's review named.
    """
    ref = "/schemas/enums/error-code.json"
    versioned = {"$id": "/schemas/3.1.1/enums/error-code.json", "enum": []}

    with pytest.raises(_refresh.IdConventionError, match=r"expected '/schemas/enums/error-code\.json'"):
        _refresh.check_id_convention(ref, versioned)


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


def test_refresh_accepts_the_convention():
    """Negative control: the conforming form passes, so the guard is not a blanket reject."""
    ref = "/schemas/enums/error-code.json"
    _refresh.check_id_convention(ref, json.loads((_FIXTURE_DIR / "enums" / "error-code.json").read_text()))


def test_refresh_docstring_documents_the_convention():
    """The decision #1881 asked for is recorded where a refresher will read it."""
    # Backticks stripped so the assertion grades the prose, not its RST markup.
    docstring = (_refresh.__doc__ or "").replace("`", "")
    assert "$id convention" in docstring, "_refresh.py's docstring must name the decided $id convention (GH #1881)"
    assert "/schemas/<category>/<name>.json" in docstring, (
        "_refresh.py's docstring must state the concrete form, not just that a convention exists"
    )
