"""What a code resolves to: :class:`Recovery` and :class:`CodeEntry`. A LEAF.

Split out of :mod:`src.core.errors.codes` so that a module contributing entries to
``CODE_TABLE`` can name the entry type without importing the table that will contain it.
:mod:`src.core.errors.signature_codes` is the first such contributor, and the alternative
was a mid-file import in ``codes.py`` (E402) or a builder taking its own entry class as a
parameter. Both types are re-exported from ``codes`` so every existing import site is
unchanged, and this module imports nothing of ours.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["CodeEntry", "CodeGroup", "Recovery"]


class CodeGroup(StrEnum):
    """Which family a code belongs to, when the family decides how it is TRANSPORTED.

    ONE MEMBER BESIDES THE DEFAULT, and that is the honest size of it. AdCP requires the
    request-signing family to be echoed in ``WWW-Authenticate: Signature error="<code>"``
    on top of the response envelope every code already travels in — and gives the wire no
    way to mark which codes those are: ``core/error.json`` types ``error.code`` as an open
    string, and ``enums/error-code.json`` does not publish the family at all
    (adcontextprotocol/adcp#7642). So the fact has to be re-added on this side, and this is
    where it goes.

    ON THE CODE, NOT ON ITS CONSUMER. The renderer that needs it
    (``AuthChallengeResponder``) reads a code off a FINISHED response body, by which point
    the exception class that knew is gone. The alternative was a set of code strings held
    by that renderer, which works and is one line — and makes the middleware a second
    vocabulary that drifts from this table. A field named for what the code IS, rather
    than for what one reader does with it, keeps the dependency pointing the right way.

    Not named ``challenge_scheme``: there is exactly one scheme, so a field naming schemes
    would imply a choice that does not exist. A GROUP can gain a member without renaming,
    and grouping is the actual concept — these codes are processed differently because of
    the family they belong to.

    If #7642 lands and the family is published like any other, this stays as it is: the
    challenge requirement is independent of where the codes are declared.
    """

    GENERAL = "general"
    SIGNATURE = "signature"


class Recovery(StrEnum):
    """What a buyer can do about an error.

    Closed at three values by the wire schema, and the one field a receiver is required to
    read when it meets a code it does not know.
    """

    CORRECTABLE = "correctable"
    TRANSIENT = "transient"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class CodeEntry:
    """Everything a code resolves to. Frozen: the table is a fact, not state.

    Refuses an empty ``suggestion`` or ``message`` at construction: every error on the wire
    derives both from its table entry, so an empty string here would put a blank
    buyer-facing field on every raise of that code. Checking it at the one place entries are
    built — pinned-schema load and platform authorship alike — means no test or raise site
    ever needs to re-check non-emptiness.

    ``status`` is the HTTP status the failure is signalled with. It belongs to the CODE, not
    to whichever exception class happened to raise it: the pinned schema states the
    transport-level failure marker per code — ``HTTP 5xx`` for CONFIGURATION_ERROR and
    GOVERNANCE_UNAVAILABLE, ``HTTP 4xx`` for GOVERNANCE_DENIED and CREDENTIAL_IN_ARGS
    (``enums/error-code.json``, ``enumDescriptions``, AdCP 3.1.1) — so a class that emitted a
    code with a status from a different band would contradict the pin. The band is the
    spec's; the exact number inside it is this seller's.
    """

    recovery: Recovery
    suggestion: str
    message: str
    status: int
    #: Defaults to the family that needs no special transport, so the 92 published codes
    #: and the 8 platform codes declare nothing. Only a code whose family changes how it
    #: reaches the buyer says so.
    group: CodeGroup = CodeGroup.GENERAL

    def __post_init__(self) -> None:
        if not self.suggestion or not self.message:
            raise ValueError(
                f"CodeEntry requires non-empty suggestion and message, got "
                f"suggestion={self.suggestion!r}, message={self.message!r}"
            )
        if not 100 <= self.status <= 599:
            raise ValueError(f"CodeEntry.status must be an HTTP status code, got {self.status!r}")
