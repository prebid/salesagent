"""TMP Provider management blueprint for admin UI.

Trusted Match Protocol providers are buyer-side agents that the TMP Router
fans out to during ad selection. Each provider evaluates context signals
and/or identity signals against their synced package set and returns scored
offers. The TMP Router calls all active providers in parallel within the
configured latency budget, then merges results for the publisher-side join.

The Sales Agent exposes active registrations via the FastAPI discovery endpoint
(``GET`` :data:`src.routes.tmp_providers.DISCOVERY_ROUTE`) so the router can poll
for discovery — it never reads the DB directly.

The Flask blueprint here handles the **admin CRUD UI** only.  The
machine-to-machine discovery endpoint lives in ``src/routes/tmp_providers.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import TypedDict

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

# werkzeug's Response, not flask's: `redirect()` returns the werkzeug base class
# while `jsonify()` returns the flask subclass, so the base type is the one that
# annotates both helper families here.
from werkzeug.wrappers import Response

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.helpers import tenant_not_found_redirect
from src.core.database.models import TMPProvider
from src.core.database.repositories.uow import TMPProviderUoW
from src.core.schemas.tmp_provider import (
    VALID_AUTH_SCHEMES,
    VALID_STATUSES,
    VALID_UID_TYPES,
    TMPProviderFields,
    TMPProviderRegistration,
    TMPProviderValidationError,
)

logger = logging.getLogger(__name__)

# Create Blueprint
tmp_providers_bp = Blueprint("tmp_providers", __name__)


# ---------------------------------------------------------------------------
# View shapes — what the Jinja templates read.
#
# These live in the admin layer, not in src/core/schemas: neither has an AdCP
# counterpart (``name`` is not in the closed provider-registration schema, and
# CSV strings are an HTML-form artifact), so the protocol-schema package is the
# wrong owner. The machine wire is the pinned SDK type
# ``TMPProviderDiscoveryEntry`` (#1197 review).
# ---------------------------------------------------------------------------


class _TMPProviderAdminScalars(TypedDict):
    """The scalar half shared by the two admin-side views (see below).

    Split out only so the two views can type ``countries``/``uid_types``/
    ``properties`` differently without restating the nine keys they agree on —
    a TypedDict subclass may add keys but not retype inherited ones.
    """

    provider_id: str
    name: str
    endpoint: str
    context_match: bool
    identity_match: bool
    timeout_ms: int
    priority: int
    status: str


class TMPProviderAdminDict(_TMPProviderAdminScalars, total=False):
    """What :func:`_admin_view` returns — the admin list view's row.

    Typed rather than a bare ``dict`` because its consumer
    (``templates/tmp_providers.html``) breaks on a renamed key. It lives HERE,
    with the layer that consumes it, rather than in the protocol-schema package:
    it is a Jinja view shape with no counterpart in the AdCP schema, and the
    machine wire is the SDK type ``TMPProviderDiscoveryEntry`` (#1197 review).

    Built by :func:`_admin_view`, which is annotated with it — so a key the
    template reads and the mapper does not build is a type error rather than a
    silently blank badge.

    The three conditional arrays are ``list[str] | None`` (never omitted):
    unlike the discovery wire, which omits them, the list view distinguishes
    "no restriction" from "not shown", and the edit template renders all three
    unconditionally.
    """

    countries: list[str] | None
    uid_types: list[str] | None
    properties: list[str] | None
    created_at: datetime
    #: The badge the list template renders. It reads ``provider.auth_type``, and
    #: this shape not carrying it is what made every row say "⚠️ No Auth"
    #: (#1197 review).
    auth_type: str | None
    #: Presence only — the credential itself is never put in a view shape.
    has_auth_credentials: bool


class TMPProviderFormDict(_TMPProviderAdminScalars, total=False):
    """The edit form's view of a provider — ``templates/tmp_provider_form.html``.

    Diverges from :class:`TMPProviderAdminDict` in exactly the ways an HTML form
    does: the three arrays are the comma-separated strings a text input round-trips,
    and the two auth keys the handler adds are present (``auth_credentials`` being
    a mask, never the credential).  Form shape belongs to the blueprint, so the
    conversion lives there (``_form_view``) rather than on the model.
    """

    countries: str
    uid_types: str
    properties: str
    auth_type: str | None
    #: Presence, not a value. The template branches on this to show "(set — leave
    #: blank to keep)"; it previously received a ``"••••••••"`` string that nothing
    #: rendered and that existed only to be truthy (#1197 review).
    has_auth_credentials: bool


# ---------------------------------------------------------------------------
# Guard helpers (DRY: used by multiple route handlers)
# ---------------------------------------------------------------------------


def _log_and_500(action: str, exc: Exception) -> tuple[Response, int]:
    """Log *exc* at ERROR level and return a JSON 500 response.

    Collapses the copy-pasted ``except Exception as e: logger.error(...)``
    blocks in JSON-returning route handlers into one call site.  The *action*
    string appears in the log message and the JSON error body so operators can
    identify which handler failed.

    ``[TMP admin]`` prefix matches the sync/health/discovery modules' tags
    (``[TMP sync]``, ``[TMP health]``, ``[TMP discovery]``) so an operator
    grepping ``[TMP`` for this feature's logs sees the admin-side lines too.

    Usage::

        except Exception as e:
            return _log_and_500("deactivating TMP provider", e)
    """
    logger.error("[TMP admin] Error %s: %s", action, exc, exc_info=True)
    return jsonify({"error": f"Error {action}"}), 500


def _log_flash_and_redirect(action: str, exc: Exception, redirect_response: Response) -> Response:
    """Log *exc* at ERROR level, flash an error message, and return *redirect_response*.

    Collapses the copy-pasted ``except Exception as e: logger.error(...); flash(...);
    return redirect(...)`` blocks in flash-based route handlers into one call site.

    ``[TMP admin]`` prefix matches the sync/health/discovery modules' tags —
    see ``_log_and_500`` above.

    Usage::

        except Exception as e:
            return _log_flash_and_redirect(
                "loading TMP providers", e,
                redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id)),
            )
    """
    logger.error("[TMP admin] Error %s: %s", action, exc, exc_info=True)
    flash(f"Error {action}", "error")
    return redirect_response


def _provider_not_found_json() -> tuple[Response, int]:
    """Return a JSON 404 response when a TMP provider cannot be found.

    Returns the response so callers can ``return`` it directly::

        provider = uow.tmp_providers.get_by_id(provider_id)
        if not provider:
            return _provider_not_found_json()
    """
    return jsonify({"error": "TMP provider not found"}), 404


def _provider_not_found_redirect(tenant_id: str) -> Response:
    """Flash an error and redirect to the provider list when a TMP provider cannot be found.

    Returns the redirect response so callers can ``return`` it directly::

        provider = uow.tmp_providers.get_by_id(provider_id)
        if not provider:
            return _provider_not_found_redirect(tenant_id)
    """
    flash("TMP provider not found", "error")
    return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Shared form validation helper (DRY: used by both add and edit routes)
# ---------------------------------------------------------------------------


def _parse_int_field(form: Mapping[str, str], field: str, default: str, label: str) -> int:
    """Parse *field* as an int, raising ``TMPProviderValidationError`` on bad input.

    Collapses the two identical ``try: int(form.get(...)) except (ValueError,
    TypeError)`` blocks (timeout_ms, priority) into one call site.  Raises the
    same domain error the registration model raises, so the caller has one
    rejection path rather than two.
    """
    try:
        return int(form.get(field, default))
    except (ValueError, TypeError) as exc:
        raise TMPProviderValidationError(f"{label} must be a numeric value") from exc


def _split_csv(raw: str) -> list[str] | None:
    """Split a comma-separated form field into a list, or ``None`` when empty.

    ``None`` (not ``[]``) is the "accepts all" sentinel used by the DB columns
    and the discovery contract, so an empty field must not become an empty
    list.

    Pure form shape — no value normalization. It used to uppercase country codes
    (``upper=True``), which made this helper the only thing anywhere moving a
    value toward the schema's ``^[A-Z]{2}$``: a domain rule stranded in a form
    helper, which the second write surface inherited nothing of. The rule now
    lives on ``TMPProviderRegistration.countries`` (#1197 review).
    """
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def _validate_provider_form(form: Mapping[str, str]) -> TMPProviderRegistration:
    """Parse TMP provider form data into a validated :class:`TMPProviderRegistration`.

    This function owns **form shape only** — CSV splitting, checkbox ``"on"``,
    int parsing, whitespace stripping.  Registration *validity* (the uid-type
    and status enums, the at-least-one-match-mode rule, the
    ``identity_match ⇒ countries + uid_types`` rule, the SSRF check) belongs to
    ``TMPProviderRegistration`` in ``src/core/schemas/tmp_provider.py`` so that
    a future programmatic write surface enforces the same rules without
    reimplementing them (#1197 review).

    ``form`` is typed as ``Mapping[str, str]`` — callers pass
    ``request.form``, a Werkzeug ``ImmutableMultiDict``, not a plain ``dict``.
    Only ``.get()`` is used here, so the narrower ``Mapping`` contract is
    what's actually relied on.

    Raises:
        TMPProviderValidationError: on any form-shape or registration-validity
            rejection; ``str(exc)`` is the operator-facing message to flash.
    """
    return TMPProviderRegistration.parse(
        TMPProviderFields(
            name=form.get("name", "").strip(),
            endpoint=form.get("endpoint", "").strip(),
            context_match=form.get("context_match") == "on",
            identity_match=form.get("identity_match") == "on",
            countries=_split_csv(form.get("countries", "").strip()),
            uid_types=_split_csv(form.get("uid_types", "").strip()),
            properties=_split_csv(form.get("properties", "").strip()),
            timeout_ms=_parse_int_field(form, "timeout_ms", "50", "Timeout (ms)"),
            priority=_parse_int_field(form, "priority", "0", "Priority"),
            status=form.get("status", "active").strip(),
            auth_type=form.get("auth_type", "").strip() or None,
            auth_credentials=form.get("auth_credentials", "").strip() or None,
        )
    )


def _validated_registration_or_redirect(
    form: Mapping[str, str], on_error_redirect: Response
) -> TMPProviderRegistration | Response:
    """Parse and validate *form*, or flash the rejection and return *on_error_redirect*.

    The add and edit POST handlers ran the identical
    parse-or-flash-and-bounce-back block, differing only in the redirect target
    (#1197 review), so a change to the rejection UX meant two edits.  They now
    each pass their own target and express nothing else::

        registration = _validated_registration_or_redirect(request.form, redirect(...))
        if isinstance(registration, Response):
            return registration

    The rejection is handled here rather than by the handlers' generic
    ``except Exception``: a validation failure must flash its own
    operator-facing message, not the generic "Error adding TMP provider".
    Returning the redirect (rather than raising) keeps that distinction
    structural — there is no exception for the generic handler to swallow.
    """
    try:
        return _validate_provider_form(form)
    except TMPProviderValidationError as exc:
        flash(str(exc), "error")
        return on_error_redirect


def _form_render_context() -> dict[str, object]:
    """The enum vocabularies the provider form renders.

    Passed to the template rather than retyped in it: ``VALID_UID_TYPES`` /
    ``VALID_STATUSES`` are SDK-derived and drift-guarded, so the guard covers the
    UI too. The hand-written ``<code>`` list in the template had already gone
    stale (missing ``rampid_derived`` and ``world_id_nullifier``), so the form
    told an operator a value was invalid that the enum accepts and the form never
    offered (#1197 review).
    """
    return {
        "valid_uid_types": sorted(VALID_UID_TYPES),
        "valid_statuses": sorted(VALID_STATUSES),
        "valid_auth_schemes": sorted(VALID_AUTH_SCHEMES),
        "field_bounds": _numeric_field_bounds(),
    }


def _list_render_context() -> dict[str, object]:
    """The vocabularies the list view renders — see :func:`_status_presentation`."""
    return {"status_presentation": _status_presentation()}


#: How each lifecycle status is presented, and whether it can be deactivated.
#:
#: Keyed off ``VALID_STATUSES`` so a spec bump that widens the SDK enum cannot
#: leave the list view rendering a new status as "Inactive" — which is what the
#: template's hand-written ``active`` / ``draining`` / else branch did, while the
#: edit form (rendering from the same enum) offered it. An unrecognized status
#: renders as itself rather than as a wrong label (#1197 review).
#:
#: "Can this be deactivated?" is a domain statement too: the template gated the
#: button on ``status == 'active'`` while ``TMPProviderRepository.deactivate``
#: accepts any status, in a view the render context did not even pass the
#: vocabulary to.
_STATUS_PRESENTATION: dict[str, dict[str, object]] = {
    "active": {"label": "Active", "style": "color: #059669; font-weight: 600", "deactivatable": True},
    "draining": {"label": "⏳ Draining", "style": "color: #d97706; font-weight: 600", "deactivatable": True},
    "inactive": {"label": "Inactive", "style": "color: #9ca3af", "deactivatable": False},
}


def _status_presentation() -> dict[str, dict[str, object]]:
    """Presentation for every status in the vocabulary, including unrecognized ones.

    A status the SDK enum grows but this table does not know is rendered as its own
    value with neutral styling and treated as deactivatable — visible and
    actionable, rather than silently mislabelled.
    """
    presentation = dict(_STATUS_PRESENTATION)
    for status in VALID_STATUSES:
        presentation.setdefault(status, {"label": status, "style": "color: #6b7280", "deactivatable": True})
    return presentation


def _numeric_field_bounds() -> dict[str, dict[str, int | None]]:
    """The numeric input bounds, read off the record rather than typed into the form.

    ``min``/``max`` in the template were hand-written and had drifted: ``min="10"``
    against a record (and pinned schema) minimum of 5, so the browser refused a
    legal 5-9 ms registration before it could be submitted (#1197 review). Reading
    the constraint metadata means the form cannot disagree with the validator.
    """
    bounds: dict[str, dict[str, int | None]] = {}
    for name in ("timeout_ms", "priority"):
        field = TMPProviderRegistration.model_fields[name]
        limits: dict[str, int | None] = {"min": None, "max": None, "default": field.default}
        for meta in field.metadata:
            if (value := getattr(meta, "ge", None)) is not None:
                limits["min"] = value
            if (value := getattr(meta, "le", None)) is not None:
                limits["max"] = value
        bounds[name] = limits
    return bounds


def _admin_view(provider: TMPProvider) -> TMPProviderAdminDict:
    """Turn a provider row into the list view's shape.

    Lives here, next to :func:`_form_view`, rather than on the ORM model. Both are
    row→view mappers, and a mapper has to sit where it can name BOTH types: on
    ``models.py`` this one could only return ``dict[str, Any]`` (the ORM module
    must not import the admin package), and the consequence shipped — the list
    template reads ``provider.auth_type`` for its 🔑/⚠️ badge, the ORM-side mapper
    deliberately omitted the auth fields, and Jinja's falsy ``Undefined`` rendered
    "⚠️ No Auth" on every row, credentialed or not (#1197 review).

    With the annotation on the return, a missing key the template reads is a type
    error instead of a blank badge. ``auth_type`` is carried; the CREDENTIAL never
    is — presence only, via :attr:`TMPProvider.has_auth_credentials`, which does
    not decrypt.
    """
    return TMPProviderAdminDict(
        provider_id=provider.provider_id,
        name=provider.name,
        endpoint=provider.endpoint,
        context_match=provider.context_match,
        identity_match=provider.identity_match,
        timeout_ms=provider.timeout_ms,
        priority=provider.priority,
        status=provider.status,
        countries=provider.countries,
        uid_types=provider.uid_types,
        properties=provider.properties,
        created_at=provider.created_at,
        auth_type=provider.auth_type,
        has_auth_credentials=provider.has_auth_credentials,
    )


def _form_view(provider: TMPProvider) -> TMPProviderFormDict:
    """Turn a provider into the edit form's shape.

    Form shape belongs to this layer, so the list → comma-separated-string
    conversion lives here rather than on the model, and the result is typed
    (:class:`TMPProviderFormDict`) so a renamed key is a type error rather than a
    silently blank input.

    The credential is never carried, in any form — only whether one is set, via
    :attr:`TMPProvider.has_auth_credentials`. That accessor answers "is a
    credential set?" without decrypting (the decrypting getter raises on a rotated
    ciphertext, turning a form render into a 500) and without reaching past the
    property API into the private column. The POST side preserves the stored value
    when the field comes back empty (#1197 review).
    """
    return TMPProviderFormDict(
        provider_id=provider.provider_id,
        name=provider.name,
        endpoint=provider.endpoint,
        context_match=provider.context_match,
        identity_match=provider.identity_match,
        timeout_ms=provider.timeout_ms,
        priority=provider.priority,
        status=provider.status,
        countries=",".join(provider.countries or []),
        uid_types=",".join(provider.uid_types or []),
        properties=",".join(provider.properties or []),
        auth_type=provider.auth_type,
        has_auth_credentials=provider.has_auth_credentials,
    )


@tmp_providers_bp.route("/")
@require_tenant_access()
def list_tmp_providers(tenant_id):
    """List all TMP providers for a tenant."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return tenant_not_found_redirect()

            providers = uow.tmp_providers.list_all()

            # _admin_view(): the UI serialization — carries `name` for the table,
            # `auth_type` for the badge, and always emits
            # countries/uid_types/properties (None means "accepts all"). The
            # machine wire is TMPProviderDiscoveryEntry.from_row() instead, which
            # omits absent conditionals and drops `name` to stay inside the closed
            # AdCP schema.
            providers_list = [_admin_view(p) for p in providers]

            return render_template(
                "tmp_providers.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                providers=providers_list,
                **_list_render_context(),
            )

    except Exception as e:
        return _log_flash_and_redirect(
            "loading TMP providers",
            e,
            redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id)),
        )


@tmp_providers_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_tmp_provider")
@require_tenant_access()
def add_tmp_provider(tenant_id):
    """Add a new TMP provider."""
    if request.method == "GET":
        with TMPProviderUoW(tenant_id) as uow:
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return tenant_not_found_redirect()

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=None,
                **_form_render_context(),
            )

    # POST — create new TMP provider
    try:
        with TMPProviderUoW(tenant_id) as uow:
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return tenant_not_found_redirect()

            registration = _validated_registration_or_redirect(
                request.form,
                redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id)),
            )
            if isinstance(registration, Response):
                return registration

            # create_from_fields is symmetric with update_fields used in the edit path:
            # both take the same TMPProviderFields record without inline ORM construction.
            uow.tmp_providers.create_from_fields(**registration.to_fields())

            flash(f"TMP provider '{registration.name}' added successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        return _log_flash_and_redirect(
            "adding TMP provider",
            e,
            redirect(url_for("tmp_providers.add_tmp_provider", tenant_id=tenant_id)),
        )


@tmp_providers_bp.route("/<provider_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_tmp_provider")
@require_tenant_access()
def edit_tmp_provider(tenant_id, provider_id):
    """Edit an existing TMP provider."""
    if request.method == "GET":
        with TMPProviderUoW(tenant_id) as uow:
            tenant = uow.tenant_config.get_tenant()
            if not tenant:
                return tenant_not_found_redirect()

            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_redirect(tenant_id)

            return render_template(
                "tmp_provider_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                provider=_form_view(provider),
                **_form_render_context(),
            )

    # POST — update TMP provider
    try:
        with TMPProviderUoW(tenant_id) as uow:
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_redirect(tenant_id)

            registration = _validated_registration_or_redirect(
                request.form,
                redirect(url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)),
            )
            if isinstance(registration, Response):
                return registration

            # auth_credentials is written only when a new non-empty value was
            # submitted — otherwise the stored (encrypted) value is preserved.
            # to_update_fields() owns that rule so the edit handler no longer
            # hand-copies eleven field names to express it.
            uow.tmp_providers.update_fields(
                provider_id,
                **registration.to_update_fields(include_credentials=bool(registration.auth_credentials)),
            )

            flash(f"TMP provider '{registration.name}' updated successfully", "success")
            return redirect(url_for("tmp_providers.list_tmp_providers", tenant_id=tenant_id))

    except Exception as e:
        return _log_flash_and_redirect(
            "updating TMP provider",
            e,
            redirect(url_for("tmp_providers.edit_tmp_provider", tenant_id=tenant_id, provider_id=provider_id)),
        )


@tmp_providers_bp.route("/<provider_id>/deactivate", methods=["POST"])
@log_admin_action("deactivate_tmp_provider")
@require_tenant_access()
def deactivate_tmp_provider(tenant_id, provider_id):
    """Soft-deactivate a TMP provider (set status='inactive')."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            provider = uow.tmp_providers.deactivate(provider_id)
            if not provider:
                return _provider_not_found_json()

            return jsonify({"success": True, "message": f"TMP provider '{provider.name}' deactivated"})

    except Exception as e:
        return _log_and_500("deactivating TMP provider", e)


@tmp_providers_bp.route("/<provider_id>/delete", methods=["DELETE"])
@log_admin_action("delete_tmp_provider")
@require_tenant_access()
def delete_tmp_provider(tenant_id, provider_id):
    """Hard-delete a TMP provider."""
    try:
        with TMPProviderUoW(tenant_id) as uow:
            # Get name before deleting for the response message
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_json()

            provider_name = provider.name
            uow.tmp_providers.delete(provider_id)

            return jsonify({"success": True, "message": f"TMP provider '{provider_name}' deleted successfully"})

    except Exception as e:
        return _log_and_500("deleting TMP provider", e)


@tmp_providers_bp.route("/<provider_id>/health", methods=["GET"])
@log_admin_action("health_check_tmp_provider")
@require_tenant_access()
def health_check_tmp_provider(tenant_id, provider_id):
    """Return the last health-check result from the background scheduler.

    The TMP health scheduler polls each provider's ``/health`` endpoint
    every 60 s and writes the result to ``health_status`` /
    ``last_health_checked_at``.  This route reads from the DB — no live
    HTTP call, no worker starvation risk.

    Response shape (one meaning per key, always the same key set):
      ``success``     — the request was served (never the health verdict)
      ``status``      — ``pending`` | ``healthy`` | ``unhealthy`` | ``error``
      ``provider``    — the provider's admin-facing name
      ``last_checked`` — ISO timestamp, or ``null`` when never probed
    """
    try:
        with TMPProviderUoW(tenant_id) as uow:
            provider = uow.tmp_providers.get_by_id(provider_id)
            if not provider:
                return _provider_not_found_json()

            # ONE shape, one meaning per key. ``success`` means "request served"
            # — the same thing it means on the deactivate and delete siblings —
            # and the health verdict is carried by ``status`` alone. Previously
            # ``success`` meant "request served" in the never-probed branch and
            # "provider is healthy" in the other, while the JS read it as the
            # latter, so a provider that had never been probed was reported to the
            # operator as healthy (#1197 review).
            return jsonify(
                {
                    "success": True,
                    "status": provider.health_status or "pending",
                    "provider": provider.name,
                    "last_checked": (
                        provider.last_health_checked_at.isoformat() if provider.last_health_checked_at else None
                    ),
                }
            )

    except Exception as e:
        return _log_and_500("checking TMP provider health", e)
