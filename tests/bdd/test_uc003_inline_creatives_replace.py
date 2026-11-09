"""BDD binding for the locally-added UC-003 inline-creatives replacement feature.

Grades package-update.json /properties/creatives ("Replace this package's inline
creative assets") on every transport: the inline bodies an update sends replace the
package's earlier inline creatives, and leave its library creatives assigned. Retire this
file together with the local feature once the generated BR-UC-003 flow grades both.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc003-inline-creatives-replace.feature")
