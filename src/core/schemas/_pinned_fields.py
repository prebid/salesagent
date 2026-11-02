"""Read field facts from the pinned AdCP schema tree that ships with the SDK.

The always-include set is a fact the pin states: a field is kept on the wire when
null exactly when the schema lists it in ``required`` AND types it nullable. Every
hand-declared copy of that fact is correct on the day it is written and unverified
afterwards — two of the three adopters had drifted from the pin by the time this
was added, and both emitted schema-invalid nulls to buyers.

Reads the SDK's own installed tree, so the fact moves with the ``adcp`` pin in
``pyproject.toml`` rather than with anyone's memory.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path


class UnderivablePinnedSchema(Exception):
    """The pin composes *ref* in a way no complete ``required`` list can be read from.

    Raised rather than returning an empty set, so a caller can never mistake "I could
    not read the list" for "the pin declares none". The two are indistinguishable by
    return value, which is what let the fail-open survive.
    """


@cache
def _schema_root() -> Path:
    import adcp

    major_minor = ".".join(adcp.get_adcp_spec_version().split(".")[:2])
    return Path(adcp.__file__).parent / "_schemas" / major_minor


@cache
def revision_minimum() -> int:
    """The lower bound the pin puts on ``media_buys[].revision``.

    Subscripted, never ``.get()``: if the pin drops the key, a ``KeyError`` here is a
    loud failure rather than a silent substitute bound.
    """
    schema = json.loads((_schema_root() / "media-buy" / "get-media-buys-response.json").read_text())
    return schema["properties"]["media_buys"]["items"]["properties"]["revision"]["minimum"]
