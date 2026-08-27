"""Guard: the vendored error-code.json fixture must match its recorded SHA-256
exactly (#1721 M5, R1-1).

``tests/fixtures/adcp_schemas_pinned/enums/error-code.json`` is claimed to be
the AdCP v3.1.1 enum verbatim (``tests/harness/transport.py``'s
``_pinned_error_metadata`` cites it as authoritative), but that claim was
previously just prose with no verifier: the file had drifted from v3.1.1 on
4+ ``enumMetadata.suggestion``/description strings while still claiming to be
"the pinned enum". A claim of ground truth needs a hash, not a comment --
drift becomes a red test here, not a review find (same pattern shipped
upstream in adcp-client-python #980).

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
