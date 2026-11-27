"""The signed-requests test counterparty's relaxations, carried on its onboarding record.

THE LAYER'S SECOND DEPENDENCY-FREE LEAF, beside :mod:`.algorithms`: it imports only
pydantic, so ``src.core.database.models`` can type the ``principals.signed_requests_grading``
column with it without pulling the verifier in behind it.

The signed-requests test kit (``dist/compliance/<pin>/test-kits/signed-requests-runner.yaml``,
line numbers at 3.2.7) asks the agent under test to pre-configure three relaxations "for the
test-kit counterparty only" (L41-43, L64-69, L187-190): an accepted JWKS holding the kit's
published keys, a per-keyid replay cap far below the production floor (L146-164), and a
replay-row lifetime short enough to drain between vectors (L114-130). All three weaken a
protection, and the kit's private keys are public, so they live on the test counterparty's
own principal row and apply to that counterparty alone: the verifier reads them off the
counterparty it resolved, so pinned keys can only ever authenticate as their own principal.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SignedRequestsGrading(BaseModel):
    """The test-kit relaxations one counterparty carries.

    ``jwks`` is the kit's public key set, verified against in place of the brand.json walk,
    because the kit's counterparty has no origin to walk. The JWKs stay dicts: checklist
    step 8 reads ``use``, ``key_ops`` and ``adcp_use``, which the SDK's ``AgentSigningKey``
    does not declare.

    ``replay_cap`` and ``replay_ttl_seconds`` tune the replay store for this counterparty's
    requests.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    jwks: dict[str, Any]
    replay_cap: int | None = Field(default=None, gt=0)
    replay_ttl_seconds: float | None = Field(default=None, gt=0)
