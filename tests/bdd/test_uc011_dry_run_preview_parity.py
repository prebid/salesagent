"""BDD binding for the locally-added UC-011 dry_run preview-parity feature.

Grades BR-RULE-062 where the generated feature file leaves it ungraded: a
``sync_accounts`` preview must describe an outcome a real run produces. The
generated scenarios partition ``dry_run`` and ``delete_missing`` separately and
never combine them, and none repeats a natural key that ALREADY has an account —
which is how the preview came to omit every closure it would perform and to echo
the field values the buyer was replacing (#1721).

Retire this file together with the local feature once the upstream storyboard
grows equivalent scenarios; ``dry_run``/``delete_missing`` appear nowhere in
``dist/compliance/3.1.1`` today.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc011-dry-run-preview-parity.feature")
