"""BDD binding for the locally-added UC-006 feature on a creative agent's preview and the stored creative.

Grades that a sync keeps the agent's preview page and size out of the creative, on the create
and update branches. Graded on every transport.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-preview-kept-out-of-creative.feature")
