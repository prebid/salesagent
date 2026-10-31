"""The ``meta.json`` a run directory carries, for the tests that grade its READERS.

``scripts/audit/run_identity.document`` is the one place the record's shape is
declared, and this builds the fixture THROUGH it rather than beside it. Two test
modules used to hand-build the same nine-key dict, which is how the duplication
ratchet first noticed them; the reason to derive it instead is not the ratchet.
``run_identity.read`` reaches every field with ``.get``, so a field renamed in
``document`` reads as ``None`` rather than raising -- and against a hand-written
fixture both modules would keep passing over a record production no longer
writes. Derived here, that rename surfaces as these tests failing, which is what
a reader test is for.

A fixture built this way is also the fuller document: ``document`` adds
``started_utc`` and ``host``, which a real run carries and the hand-written dicts
omitted.
"""

from __future__ import annotations

import json
import pathlib

from scripts.audit.run_identity import IDENTITY_FILE, Identity, document

#: The tree a fixture run claims. Well-formed rather than real, and THE SAME SHA
#: on both sides of a baseline pair: these tests grade suite accounting and report
#: text, not a delta between trees, and ``run_identity.ancestry`` answers an equal
#: pair without consulting git at all -- so they stay hermetic and do not rot when
#: the repo's history moves under them. The ancestry refusal gets its own tests,
#: against real commits, in ``test_run_identity.py``.
FIXTURE_SHA = "1111111111111111111111111111111111111111"

#: What a fixture run reports as the SDK it graded against. Any well-formed pair
#: does: no reader asserts on these values, they only have to be present.
FIXTURE_ADCP = {"spec_version": "3.2.1", "distribution": "9.0.0b3+sa.test.0"}


def write_run_identity(
    directory: pathlib.Path,
    *,
    commit: str = FIXTURE_SHA,
    branch: str = "work/lane",
    tree: str = "clean",
    source: str = "git",
) -> pathlib.Path:
    """Write ``directory/meta.json`` for a run that identifies itself.

    A run carrying NO record is modelled by not calling this. ``read`` returns
    ``None`` for the absent file, which is a DIFFERENT state from a record present
    but naming no commit (``source="env"`` with a reason), so leaving the choice at
    the call site keeps both expressible rather than overloading ``commit=None``.
    """
    path = directory / IDENTITY_FILE
    path.write_text(
        json.dumps(
            document(
                Identity(
                    commit=commit,
                    branch=branch,
                    detached=False,
                    tree=tree,
                    source=source,
                    unidentified_because=None,
                    adcp=FIXTURE_ADCP,
                ),
                directory,
                FIXTURE_ADCP,
                "fixture-host",
                "2026-01-01T00:00:00Z",
            )
        )
    )
    return path
