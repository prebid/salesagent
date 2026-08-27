"""The signing layer's PUBLIC SURFACE — the one place callers import signing behaviour.

#1291 (``salesagent-z6nr.33``). ``src/core/signing`` owns the application's signing
contract. Where the pinned ``adcp.signing`` is correct the layer delegates; where
upstream has merged a fix the pin lacks, the layer carries a verbatim, provenance-cited
copy (:mod:`src.core.signing_contract._upstream`). The property this surface exists to hold: a
caller can never observe — and never needs to know — which of the two provided a
behaviour, so an SDK bump is a layer-internal event that changes no caller.

Everything below this package is PRIVATE to the layer. Callers import names from
``src.core.signing`` only — never ``src.core.signing.<submodule>``, and never
``adcp.signing`` directly. Both rules are enforced, with empty shrink-only allowlists,
by ``tests/unit/test_architecture_signing_layer_boundary.py``.

The exports are grouped by submodule below; each group is the deliberate public
surface of that unit. Add to these lists consciously — an export is a contract.

Zero hand-rolled crypto anywhere in the layer: key generation, PEM loading, JWK
derivation and signing come from ``adcp.signing``; the layer adds lifecycle,
canonicalization gating and posture on top.

Note: ``adcp.signing`` exports a symbol named ``SigningConfig`` (the SDK's
auto-signing bundle). Ours — :class:`src.core.config.SigningConfig` — is a different
thing. Alias at any import site that touches both.
"""

from src.core.signing._mcp_client_signing_shim import (
    signed_agent_call,
)

# These thirteen were resolved LAZILY (PEP 562) until salesagent-n78j0.3. They are eager
# now, and the cycle that forced the deferral is GONE — removed at its cause rather than
# deferred at its symptom.
#
# THE CAUSE WAS THE LEAF'S LOCATION, not the import shape. Python runs a package's
# ``__init__`` before any of its submodules, so ``database.models`` importing
# ``src.core.signing.algorithms`` still executed THIS file, which imports ``keys`` ->
# ``database.models`` -> a half-initialised module. Re-pointing that import at the
# submodule could never have helped. The value-sets therefore left the package for
# :mod:`src.core.signing_contract`; ``database.models`` and
# ``database.repositories.signing_key`` now import THAT, reach this package not at all,
# and the dependency runs one way: contract -> models -> signing operations.
#
# Do not reintroduce a lazy export. It moved a STATIC layering fault to first attribute
# access, where nothing checks it: the illegal edge became invisible to the import graph
# and to mypy, leaving the layer's most security-relevant exports untyped at every call
# site. If a cycle reappears, something below this package imports something above it —
# move the leaf, do not defer the import.
from src.core.signing.keys import MINTABLE_REF_SCHEMES, provision_signing_key, revoke_signing_key
from src.core.signing.operations import (
    ADCP_SURFACE_PREFIXES,
    is_adcp_surface,
    matches_surface_prefix,
    operation_for_rest_route,
    resolved_operation_names,
    sdk_operation_names,
)
from src.core.signing.posture import (
    IdentityDeclaration,
    KeyBacking,
    RequestSigningPosture,
    WebhookSigningPosture,
    bucket_names,
    emitted_identity,
    origin_is_publishable,
    posture_for_tenant,
    posture_from_declarations,
    request_signing_buckets_declared,
    requires_trust_root,
    signing_key_backed,
    unsupported_webhook_signing_posture,
    webhook_signing_posture,
)
from src.core.signing.provider import (
    clear_signing_provider_cache,
    resolve_signing_material,
    signing_config_from_material,
)
from src.core.signing.request_verifier_middleware import RequestSignatureMiddleware
from src.core.signing.revocation_list import (
    build_revocation_list,
    publishable_revocation_list,
    sign_revocation_list,
)
from src.core.signing.trust_root import (
    build_adagents_json,
    build_brand_json,
    build_jwks,
)
from src.core.signing.webhook_sender_factory import (
    adcp_challenge_signer,
    credential_fingerprint,
    declared_auth,
    deliver_adcp_webhook,
    deliver_adcp_webhook_sync,
    delivery_auth_mode,
    send_signed_challenge,
    signing_repo,
)
from src.core.signing_contract import (
    CACHE_MAX_AGE_SECONDS,
    MINTABLE_PURPOSES,
    REQUEST_SIGNING,
    SIGNING_ALG_VALUES,
)
from src.core.signing_contract._upstream.errors import (
    REQUEST_TO_WEBHOOK_CODE,
    WEBHOOK_TARGET_URI_MALFORMED,
)
from src.core.signing_contract.canonical import (
    REQUEST_TARGET_URI_MALFORMED,
    TargetUriMalformedError,
    canonical_authority,
    canonical_target_uri,
    malformed_authority_reason,
    reject_malformed_target,
)

__all__ = [
    "ADCP_SURFACE_PREFIXES",
    "CACHE_MAX_AGE_SECONDS",
    "IdentityDeclaration",
    "KeyBacking",
    "MINTABLE_PURPOSES",
    "MINTABLE_REF_SCHEMES",
    "REQUEST_SIGNING",
    "REQUEST_TARGET_URI_MALFORMED",
    "REQUEST_TO_WEBHOOK_CODE",
    "RequestSignatureMiddleware",
    "RequestSigningPosture",
    "SIGNING_ALG_VALUES",
    "TargetUriMalformedError",
    "WEBHOOK_TARGET_URI_MALFORMED",
    "WebhookSigningPosture",
    "adcp_challenge_signer",
    "bucket_names",
    "build_adagents_json",
    "build_brand_json",
    "build_jwks",
    "build_revocation_list",
    "canonical_authority",
    "canonical_target_uri",
    "clear_signing_provider_cache",
    "credential_fingerprint",
    "declared_auth",
    "deliver_adcp_webhook",
    "deliver_adcp_webhook_sync",
    "delivery_auth_mode",
    "emitted_identity",
    "is_adcp_surface",
    "malformed_authority_reason",
    "matches_surface_prefix",
    "operation_for_rest_route",
    "origin_is_publishable",
    "posture_for_tenant",
    "publishable_revocation_list",
    "posture_from_declarations",
    "provision_signing_key",
    "reject_malformed_target",
    "request_signing_buckets_declared",
    "requires_trust_root",
    "resolved_operation_names",
    "resolve_signing_material",
    "revoke_signing_key",
    "sdk_operation_names",
    "send_signed_challenge",
    "sign_revocation_list",
    "signed_agent_call",
    "signing_repo",
    "signing_config_from_material",
    "signing_key_backed",
    "unsupported_webhook_signing_posture",
    "webhook_signing_posture",
]
