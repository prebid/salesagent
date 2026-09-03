"""The typed notion of WHERE a URL comes from, as a construction-time value.

Separate from ``UrlProvenance`` (``CounterpartyUrl | OperatorEndpoint``,
``outbound_http.py``, Epic B) on purpose: ``UrlProvenance`` answers "who to
blame in a refusal message" at THE MOMENT a dial fails, using a buyer-facing
role label that never carries the URL itself. :class:`VendorConstant` answers
"where did this constant come from in source" at THE MOMENT a call site builds
its own URL, and DOES carry it. A call site MAY use both — one to type its own
constant, one (optionally) as ``send()``'s ``provenance=`` for refusal
messaging — but neither replaces the other; this type does not touch
``UrlProvenance``, ``send()``/``asend()``'s signature, or any existing
``provenance=`` call site.

Threaded through exactly two sites today (GH #1802):
``APPROXIMATED_BASE_URL``, ``GOOGLE_TOKEN_URL``.

One member, not a union. Two further members and the union over them were
deleted: neither had a single constructor, import, annotation or ``isinstance``
anywhere in ``src/`` or ``tests/``, and both were homonyms of
``UrlProvenance``'s members — two vocabularies for one concept, where the
unconstructed half existed only to make the vocabulary look symmetrical.
Shipping the member that has callers and deleting the rest is the cheapest
available fix for a vocabulary collision. Add a member here when a call site
constructs it, not before.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VendorConstant:
    """A URL that is a literal in source — never environment- or DB-sourced.

    The seam-side answer to "which URLs must never be silently redirectable":
    an env read in front of one is exactly the shape triage F11 found
    (``APPROXIMATED_BASE_URL``), and this type — plus the destination-rewrite
    guard's env-sourced-destination detector — is what makes a repeat
    instance loud instead of a harmless-looking one-liner.
    """

    url: str
