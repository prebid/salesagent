"""Guard: the vendored error-code.json fixture must match its recorded SHA-256
exactly (#1721 M5, R1-1).

``tests/fixtures/adcp_schemas_pinned/enums/error-code.json`` is claimed to be
the AdCP v3.1.1 enum verbatim, but that claim was previously just a comment
with no verifier: the file had drifted from v3.1.1 on 4+
``enumMetadata.suggestion``/description strings while still claiming to be
"the pinned enum". A claim of ground truth needs a hash, so drift turns this
test red instead of waiting on a review find (same pattern shipped upstream in
adcp-client-python #980).

The readers that depend on this file's exact bytes in the current tree:

- ``tests/unit/test_pinned_fixture_id_convention.py`` records it in
  ``_TAG_VENDORED_FLAT_FILES`` as the one flat-tree file re-vendored from the
  ``v3.1.1`` TAG, and grades its ``$id`` against that claim. The hash here is
  what makes the tag provenance checkable rather than asserted.
- ``tests/bdd/steps/domain/uc011_accounts.py`` cites this file BY LINE NUMBER
  (``:102`` and ``:149``) as the pinned authority for ``INVALID_REQUEST``. A
  line-number citation holds only while the bytes are frozen.
- ``tests/fixtures/adcp_schemas_pinned/_refresh.py`` vendors it as its sole
  remaining flat root.

``tests/harness/transport.py``'s ``_pinned_error_metadata`` and
``tests/unit/test_architecture_error_suggestion_enum_conformance.py`` read it
as well until #1721 removed both. That shrank the reader set; it retired
neither the fixture nor its provenance claim, so the hash still has something
to pin.

To re-vendor after a real spec bump: re-fetch the file from the new pinned
tag (``git -C <adcp checkout> show <tag>:dist/schemas/<version>/enums/error-code.json``),
overwrite the fixture verbatim, then update ``_EXPECTED_SHA256`` below to the
new file's hash IN THE SAME CHANGE.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"

# SHA-256 of the file verbatim from `git -C ~/projects/adcp show
# v3.1.1:dist/schemas/3.1.1/enums/error-code.json` (#1721 M5).
_EXPECTED_SHA256 = "4d1f8182cf1fa848ca15396a7c68a80228502a043cf40db959f870c1de8c4e58"


def test_error_code_fixture_matches_recorded_sha256() -> None:
    actual = hashlib.sha256(_FIXTURE_PATH.read_bytes()).hexdigest()
    assert actual == _EXPECTED_SHA256, (
        f"{_FIXTURE_PATH} has drifted from its recorded v3.1.1 SHA-256.\n"
        f"Expected: {_EXPECTED_SHA256}\n"
        f"Actual:   {actual}\n"
        "If this is an intentional re-vendor against a new pinned spec version, "
        "update _EXPECTED_SHA256 in this file in the same change. If not, the "
        "fixture has drifted from its claimed provenance -- re-vendor it verbatim."
    )
