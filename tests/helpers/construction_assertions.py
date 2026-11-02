"""Assertions for a request DTO refusing a payload, at an IN-PROCESS seam.

Use this when the code under test is a builder or an ``_impl``, not a transport.
There is no wire at such a seam, so ``assert_envelope_shape`` has nothing to read;
what there IS is the pydantic ``ValidationError`` the DTO raised, and the field path
production will derive from it.

Why the field path and not the exception class. These assertions replaced
``pytest.raises(AdCPValidationError)`` at eleven builder call sites, which passed
only because those builders each opened an ``adcp_validation_boundary`` that
converted the ``ValidationError`` before it left the frame. Those wrappers are gone
-- the transport boundary performs the identical conversion one frame later, off the
same exception -- so the class assertion was grading a wrapper rather than a
rejection. ``first_validation_error_field`` is PRODUCTION's derivation, the same call
that fills ``error.field`` on the wire, so pinning it here grades the value the buyer
actually receives, and grades more than the class assertion did: most of those sites
asserted no field at all.

This does NOT substitute for wire-level grading. The envelope a rejection becomes is
graded where a rejection becomes an envelope -- ``assert_envelope_shape`` on a real
transport result (tests/CLAUDE.md, Error Verification Policy), and centrally by
``tests/unit/test_validation_error_at_the_boundary.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.exceptions import first_validation_error_field


def assert_construction_rejects(build: Callable[[], Any], *, field: str) -> ValidationError:
    """``build()`` must fail request validation, naming ``field`` as the offending path.

    ``field`` is in the JSONPath-lite form core/error.json specifies (``packages[0].budget``),
    because that is what ``first_validation_error_field`` produces and therefore what the
    buyer reads off ``error.field``.
    """
    with pytest.raises(ValidationError) as excinfo:
        build()
    derived = first_validation_error_field(excinfo.value)
    assert derived == field, (
        f"request construction was refused, but the buyer would be told the offending field is "
        f"{derived!r}, not {field!r}. error.field is a path into the document the buyer SENT, so a "
        f"mismatch here is a mismatch on the wire."
    )
    return excinfo.value
