"""Signing-key management blueprint (#1291, salesagent-7x8t).

The admin transport over ``src.core.signing.keys.provision_signing_key`` — the
ONE function that may mint a ``signing_keys`` row. ``scripts/ops/provision_signing_key.py``
is the second transport over the same function; neither contains provisioning
logic of its own.

Every read and write goes through ``SigningKeyUoW``, shaped on
``src/admin/blueprints/accounts.py``. ``principals.py`` mints per-tenant secrets
with ``get_db_session()`` and raw model kwargs; that is allowlisted pre-existing
debt, not a pattern to copy.

Provisioning is safe to run from a request handler here precisely BECAUSE nothing
is written to a filesystem: the private half is stored on the row as ciphertext
under the deployment KEK. That is what separates this from the deferred defect at
``gcp_service_account_service.py`` (credentials written to the OS temp dir from a
request handler, ``salesagent-dmqk``).

A refusal — no KEK configured, a scheme this deployment forbids — is a flash
error on the list page, never a 500: it is an operator-correctable configuration
state, and a stack trace tells the operator nothing about which knob to set.
"""

import logging
from datetime import UTC, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.helpers import require_tenant_access
from src.core.config import get_config
from src.core.database.repositories.uow import SigningKeyUoW
from src.core.exceptions import AdCPConfigurationError
from src.core.signing import (
    MINTABLE_REF_SCHEMES,
    SIGNING_ALG_VALUES,
    provision_signing_key,
    revoke_signing_key,
)

logger = logging.getLogger(__name__)

signing_keys_bp = Blueprint("signing_keys", __name__)


@signing_keys_bp.route("/")
@require_tenant_access()
def list_signing_keys(tenant_id: str) -> ResponseReturnValue:
    """List the tenant's signing keys: what is PUBLISHED, and which one SIGNS.

    Two selectors because they answer two different questions and disagree during
    a rotation: ``publishable_at`` is what a counterparty fetching
    ``/.well-known/jwks.json`` sees (window-blind, revoked keys linger for the
    grace period), ``active_at`` is the single key this agent signs with now.
    """
    now = datetime.now(UTC)
    with SigningKeyUoW(tenant_id) as uow:
        assert uow.signing_keys is not None
        published = uow.signing_keys.publishable_at(now=now, grace_seconds=get_config().signing.grace_seconds)
        active = uow.signing_keys.active_at(now=now)
        # Rendered inside the session context to avoid DetachedInstanceError.
        return render_template(
            "signing_keys_list.html",
            tenant_id=tenant_id,
            keys=published,
            active_kid=active.kid if active else None,
            algorithms=SIGNING_ALG_VALUES,
            ref_schemes=MINTABLE_REF_SCHEMES,
        )


@signing_keys_bp.route("/create", methods=["POST"])
@require_tenant_access()
@log_admin_action("provision_signing_key")
def create_signing_key(tenant_id: str) -> ResponseReturnValue:
    """Provision a signing key for the tenant.

    The ``kid`` is minted server-side: it must be unique within the published
    JWKS, and an operator-typed value is a collision waiting for a rotation.
    """
    alg = request.form.get("alg", "").strip()
    ref_scheme = request.form.get("ref_scheme", "").strip() or "db"
    env_var_name = request.form.get("env_var_name", "").strip() or None

    try:
        with SigningKeyUoW(tenant_id) as uow:
            assert uow.signing_keys is not None
            provisioned = provision_signing_key(
                uow.signing_keys,
                tenant_id=tenant_id,
                alg=alg,
                ref_scheme=ref_scheme,
                env_var_name=env_var_name,
            )
            kid = provisioned.row.kid
            handoff = provisioned.private_key_pem
    except AdCPConfigurationError as exc:
        # The message names the knob to set (e.g. key_passphrase_env). Surfacing
        # it verbatim is the point: a generic "could not provision" would leave
        # the operator with nothing to act on.
        flash(f"Could not provision a signing key: {exc}", "error")
        return redirect(url_for("signing_keys.list_signing_keys", tenant_id=tenant_id))

    if handoff is not None:
        # An env: key exists nowhere but here until the operator exports it. The
        # PEM is flashed ONCE and never stored, logged or re-rendered.
        flash(
            f"Signing key {kid} provisioned. Export this PEM as {request.form.get('env_var_name')} "
            f"before it signs anything — it is shown once and is not stored:\n"
            f"{handoff.decode()}",
            "warning",
        )
    else:
        flash(f"Signing key {kid} provisioned and published.", "success")
    return redirect(url_for("signing_keys.list_signing_keys", tenant_id=tenant_id))


@signing_keys_bp.route("/<kid>/revoke", methods=["POST"])
@require_tenant_access()
@log_admin_action("revoke_signing_key")
def revoke_signing_key_route(tenant_id: str, kid: str) -> ResponseReturnValue:
    """Revoke a signing key through the layer's one revocation operation.

    ``revoke_signing_key`` owns BOTH effects — the row transition and the cached-provider
    drop — so this route cannot perform half of one (#1757). It replaced two statements
    here, which stayed correct only while every future call site remembered both.

    CORRECTED CLAIM: this docstring used to say the cache bust "is not optional" because
    "a revoke that skips it keeps signing with the retired key for up to a minute". That
    was wrong. ``_resolve_cached`` selects the row BEFORE consulting the cache and
    ``_select_row`` refuses a revoked row on both paths, so a revoked key stops signing
    immediately. The drop is housekeeping — it evicts decrypted material that is already
    unreachable — not the safety mechanism. Pinned by
    ``tests/integration/test_signing_provider.py``.
    """
    with SigningKeyUoW(tenant_id) as uow:
        assert uow.signing_keys is not None
        revoked = revoke_signing_key(uow.signing_keys, kid=kid)

    if revoked is None:
        flash(f"No signing key {kid} for this tenant.", "error")
    else:
        flash(
            f"Signing key {kid} revoked. It stays published with its revocation marker during the grace period.",
            "success",
        )
    return redirect(url_for("signing_keys.list_signing_keys", tenant_id=tenant_id))
