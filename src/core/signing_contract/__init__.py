"""The signing layer's DEPENDENCY-FREE LEAF — value-sets, canonicalization, error codes.

Split out of :mod:`src.core.signing` by salesagent-n78j0.3. Everything here depends only
on ``adcp.signing`` and :mod:`src.core.exceptions`; nothing here touches the ORM, config,
metrics, or any signing OPERATION. That is the whole property, and it is what makes the
layer's dependency graph acyclic:

    signing_contract  ->  database.models  ->  src.core.signing (operations)
                      ->  src.core.config

WHY IT HAD TO LEAVE THE PACKAGE. Python executes a package's ``__init__`` before any of
its submodules, so while these lived at ``src.core.signing.<name>``, merely importing a
value-set ran :mod:`src.core.signing`'s ``__init__`` — which imports ``keys`` ->
``database.models``, ``request_verifier_middleware`` -> ``src.core.metrics``, and
``replay_store`` / ``revocation`` -> ``src.core.config``. Every one of those is a module
that needs a value from HERE, so each was a cycle. ``_LAZY_EXPORTS`` (PEP 562) deferred
them to first attribute access instead of removing them, which hid three distinct cycles
from the import graph and from mypy. Moving the leaf out removes all three at the cause.

THE RULE THIS PACKAGE MUST KEEP: nothing in here may import :mod:`src.core.signing`,
:mod:`src.core.config`, :mod:`src.core.metrics`, or :mod:`src.core.database`. If a new
leaf value needs one of those, it is not a leaf value.

Callers outside the signing layer import from HERE for values and from
:mod:`src.core.signing` for behaviour. The facade re-exports these names, so an existing
``from src.core.signing import SIGNING_ALG_VALUES`` keeps working.
"""

from src.core.signing_contract._upstream.errors import (
    REQUEST_TO_WEBHOOK_CODE,
    WEBHOOK_TARGET_URI_MALFORMED,
)
from src.core.signing_contract.algorithms import (
    CACHE_MAX_AGE_SECONDS,
    MINTABLE_PURPOSES,
    REQUEST_SIGNING,
    SIGNING_ALG_VALUES,
    BrandAgentType,
    keygen_alg,
    mint_kid,
    narrow_alg,
    narrow_purpose,
    signing_alg_check_clause,
    signing_purpose_check_clause,
    sql_value_list,
)
from src.core.signing_contract.canonical import (
    REQUEST_TARGET_URI_MALFORMED,
    TargetUriMalformedError,
    canonical_authority,
    canonical_target_uri,
    malformed_authority_reason,
    reject_malformed_target,
)
from src.core.signing_contract.vocabulary import resolved_operation_names

__all__ = [
    "CACHE_MAX_AGE_SECONDS",
    "BrandAgentType",
    "MINTABLE_PURPOSES",
    "REQUEST_SIGNING",
    "REQUEST_TARGET_URI_MALFORMED",
    "REQUEST_TO_WEBHOOK_CODE",
    "SIGNING_ALG_VALUES",
    "TargetUriMalformedError",
    "WEBHOOK_TARGET_URI_MALFORMED",
    "canonical_authority",
    "canonical_target_uri",
    "keygen_alg",
    "malformed_authority_reason",
    "mint_kid",
    "narrow_alg",
    "narrow_purpose",
    "reject_malformed_target",
    "resolved_operation_names",
    "signing_alg_check_clause",
    "signing_purpose_check_clause",
    "sql_value_list",
]
