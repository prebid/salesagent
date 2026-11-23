"""BDD binding for the locally-added UC-006 delete_missing active-delivery feature.

Grades that a full-library replace refuses, per creative, a creative an active, non-paused
package delivers, and archives one no delivering package holds. Graded on every transport.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-delete-missing-active-delivery.feature")
