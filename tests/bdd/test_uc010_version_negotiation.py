"""BDD binding for the hand-authored UC-010 version-negotiation companion.

The generated UC-010 feature owns schema-derived VERSION_UNSUPPORTED coverage.
This companion owns normative release-resolution boundaries that are absent
from the pinned compliance storyboards: cross-major, sub-min stable, and
unmatched prerelease pins. Shared steps are registered through conftest.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-010-version-negotiation.feature")
