"""BDD binding for the locally-added UC-010 declaration-backing feature.

Grades the STRICT backing rules in
``src.core.schemas.capability_declarations.CapabilityDeclarations.validate_backing``:
a tenant may declare only protocols and specialisms this deployment actually
implements, and a specialism claim must carry its parent protocol.

The generated ``T-UC-010-v31-specialisms`` scenario cannot grade them — it declares
``creative-generative`` plus the ``creative`` protocol, both unbacked, so it stays
xfailed against #1724. Retire this file together with the local feature once the
upstream storyboard grows an equivalent rejection scenario.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc010-declaration-backing.feature")
