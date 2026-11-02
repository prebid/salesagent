"""A local detail class must not drift from the pinned schema it transcribes.

Covers salesagent-rys3u.3. ``src/core/errors/details.py`` TRANSCRIBES the AdCP
error-details schemas — it does not load them. The parent epic's rule is that a
loaded table cannot drift and so needs no guard, but a transcribed one does, and
this is the transcription.

The drift that matters is one-directional and silent: an ``adcp`` bump adds a
REQUIRED field to a pinned schema, our class keeps its old field set, and every
raise site keeps compiling while emitting a block the pin now rejects.
``tests/unit/test_adcp_spec_version.py`` pins the SDK VERSION, not field-level
agreement, so nothing else catches it.

Scope is deliberately narrow. Only schemas with ``required`` fields are checked,
and only where a local class exists — a pinned shape nothing emits needs no class,
which is why four of the five required-field schemas have none today. The guard
grows on its own as raise sites appear.

It is NOT a full equivalence check. The pin declares these shapes open
(``additionalProperties: true``) and our classes legitimately add fields the pin
does not name (``VersionUnsupportedDetails.adcp_version`` echoes what the caller
asked for). Asserting equality would fight the schema; asserting the pin's
required fields are present and required is the property that can actually break.
"""

from __future__ import annotations

import json
from pathlib import Path

import adcp
import pytest

import src.core.errors.details as details_module


def _locate_bundle() -> Path:
    """The pinned error-details schemas, found via the INSTALLED adcp package.

    Derived from ``adcp.__file__`` rather than walked up from this repo's tree, so
    it does not depend on where the venv sits. The minor-version directory is
    globbed rather than spelled: an adcp bump that renames it must fail
    ``test_bundle_is_readable`` loudly instead of silently matching nothing.
    """
    schemas = Path(adcp.__file__).parent / "_schemas"
    candidates = sorted(schemas.glob("*/error-details"))
    return candidates[-1] if candidates else schemas / "error-details"


_BUNDLE = _locate_bundle()

#: Pinned schema stem -> the local class that transcribes it. Only pairings that
#: exist; a schema with no live raise site has no class and is not listed.
_PAIRINGS = {
    "version-unsupported": "VersionUnsupportedDetails",
}


def _required_fields(stem: str) -> list[str]:
    return json.loads((_BUNDLE / f"{stem}.json").read_text()).get("required") or []


def test_bundle_is_readable() -> None:
    """Fail loudly if the pin moves rather than skipping every check below.

    Without this, an SDK layout change would make every parametrized case vanish
    and the guard would report green while checking nothing.
    """
    assert _BUNDLE.is_dir(), f"pinned error-details bundle not found at {_BUNDLE}"
    assert list(_BUNDLE.glob("*.json")), f"pinned bundle at {_BUNDLE} is empty"


@pytest.mark.parametrize(("stem", "class_name"), sorted(_PAIRINGS.items()))
def test_local_class_declares_every_pinned_required_field(stem: str, class_name: str) -> None:
    """Each ``required`` field of the pin is declared, and required, locally."""
    cls = getattr(details_module, class_name)
    pinned = _required_fields(stem)
    assert pinned, f"{stem}.json declares no required fields; drop it from _PAIRINGS"

    missing = [f for f in pinned if f not in cls.model_fields]
    assert not missing, (
        f"{class_name} does not declare {missing}, which {stem}.json requires. "
        "An adcp bump added a required field; add it to the class."
    )

    optional_locally = [f for f in pinned if not cls.model_fields[f].is_required()]
    assert not optional_locally, (
        f"{class_name} declares {optional_locally} as optional, but {stem}.json requires them. "
        "A raise site could omit a key the pin demands and mypy would not object."
    )


def test_every_required_field_schema_is_either_paired_or_has_no_class() -> None:
    """A class that appears for an unpaired schema must be added to _PAIRINGS.

    This is what stops the guard silently narrowing: without it, someone adding
    ``StaleResponseDetails`` would get no field check and no failure telling them
    the pairing is missing.
    """
    unpaired: list[str] = []
    for path in sorted(_BUNDLE.glob("*.json")):
        stem = path.stem
        if stem in _PAIRINGS or stem == "vendor-error-codes":
            continue
        if not _required_fields(stem):
            continue
        guess = "".join(part.capitalize() for part in stem.split("-")) + "Details"
        if hasattr(details_module, guess):
            unpaired.append(f"{stem}.json <-> {guess}")

    assert not unpaired, (
        "These pinned schemas have required fields AND a local class, but are not in "
        "_PAIRINGS, so their fields are unchecked:\n  " + "\n  ".join(unpaired)
    )
