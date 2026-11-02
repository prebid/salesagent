"""Accounts management blueprint.

List, create, edit, and manage account status via the admin UI.
Uses AccountRepository via AccountUoW for all data access.

"""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.helpers import require_tenant_access
from src.core.billing_policy import BILLING_PARTY_VALUES
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.uow import AccountUoW

logger = logging.getLogger(__name__)

accounts_bp = Blueprint("accounts", __name__)

# Valid status transitions
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "active": {"suspended", "closed"},
    "pending_approval": {"active", "rejected"},
    "rejected": {"pending_approval"},
    "payment_required": {"active", "suspended"},
    "suspended": {"active", "closed"},
    # closed is terminal
}


@accounts_bp.route("/")
@require_tenant_access()
def list_accounts(tenant_id):
    """List all accounts for the tenant."""
    status_filter = request.args.get("status")

    with AccountUoW(tenant_id) as uow:
        accounts = uow.accounts.list_all(status=status_filter)
        # Render inside session context to avoid DetachedInstanceError
        return render_template(
            "accounts_list.html",
            tenant_id=tenant_id,
            accounts=accounts,
            status_filter=status_filter,
            statuses=["active", "pending_approval", "rejected", "payment_required", "suspended", "closed"],
        )


@accounts_bp.route("/create", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action("create_account")
def create_account(tenant_id):
    """Create a new account."""
    if request.method == "GET":
        return render_template(
            "create_account.html",
            tenant_id=tenant_id,
            edit_mode=False,
            billing_options=BILLING_PARTY_VALUES,
        )

    # POST — process form
    name = request.form.get("name", "").strip()
    brand_domain = request.form.get("brand_domain", "").strip()
    operator = request.form.get("operator", "").strip()
    billing = request.form.get("billing", "").strip() or None
    payment_terms = request.form.get("payment_terms", "").strip() or None
    sandbox = request.form.get("sandbox") == "on"
    brand_id = request.form.get("brand_id", "").strip() or None

    if not name:
        flash("Account name is required.", "error")
        return redirect(request.url)

    # Identity and row assembly BOTH belong to the repository: this form used to
    # mint its own account_id and hand-build the Account, so the admin surface
    # and sync_accounts were two independent definitions of what an account row
    # is. The gate divergence below is deliberate and documented; the row shape
    # is not.
    try:
        with AccountUoW(tenant_id) as uow:
            new_account = AccountRepository.build_row(
                tenant_id=tenant_id,
                account_id=AccountRepository.mint_account_id(),
                name=name,
                status="active",
                brand_domain=brand_domain,
                brand_id=brand_id,
                operator=operator or "",
                principal_id=None,
                created_fields={
                    "billing": billing,
                    "payment_terms": payment_terms,
                    "sandbox": sandbox or None,
                },
            )
            uow.accounts.create(new_account)
    except ValueError as exc:
        # The repository refuses a create whose natural key is already occupied
        # (salesagent-0njj). Surfaced as a form error rather than a 500: this is
        # an operator mistake with an obvious remedy — edit the existing account.
        #
        # `operator_message` rather than str(exc): NaturalKeyConflict is now an
        # AdCPConflictError too, so str() returns CODE_TABLE's buyer-facing sentence
        # ("Revision conflict - refetch and retry"), which tells an OPERATOR nothing
        # about which account to edit. This is an operator surface, not the buyer
        # wire, so the specific text is appropriate here and only here.
        flash(getattr(exc, "operator_message", None) or str(exc), "error")
        return redirect(request.url)

    flash(f"Account '{name}' created successfully.", "success")
    return redirect(url_for("accounts.list_accounts", tenant_id=tenant_id))


@accounts_bp.route("/<account_id>")
@require_tenant_access()
def account_detail(tenant_id, account_id):
    """Show account detail page."""
    with AccountUoW(tenant_id) as uow:
        account = uow.accounts.get_by_id(account_id)
        if account is None:
            flash("Account not found.", "error")
            return redirect(url_for("accounts.list_accounts", tenant_id=tenant_id))

        # Get allowed transitions for current status
        allowed_transitions = _STATUS_TRANSITIONS.get(account.status, set())

        # Render inside session context to avoid DetachedInstanceError
        return render_template(
            "account_detail.html",
            tenant_id=tenant_id,
            account=account,
            allowed_transitions=sorted(allowed_transitions),
        )


@accounts_bp.route("/<account_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action("edit_account")
def edit_account(tenant_id, account_id):
    """Edit an existing account."""
    with AccountUoW(tenant_id) as uow:
        account = uow.accounts.get_by_id(account_id)
        if account is None:
            flash("Account not found.", "error")
            return redirect(url_for("accounts.list_accounts", tenant_id=tenant_id))

        if request.method == "GET":
            return render_template(
                "create_account.html",
                tenant_id=tenant_id,
                account=account,
                edit_mode=True,
                billing_options=BILLING_PARTY_VALUES,
            )

        # POST — update mutable fields.
        #
        # `operator` and `sandbox` are deliberately ABSENT: they are natural-key
        # components (AccountRepository.get_by_natural_key), so editing them
        # re-keys the account and the buyer's next sync_accounts call provisions a
        # duplicate instead of matching (salesagent-8sfr). The repository refuses
        # them outright; leaving them here would only turn that into a 500.
        #
        # `billing` IS editable, and that is a deliberate authority difference
        # rather than an oversight. AdCP forbids mutating it on the settings-update
        # WIRE path (sync-accounts-request.json
        # #/properties/accounts/items/oneOf/1/allOf/2, enforced in
        # _sync_accounts_impl), but that binds the BUYER. This is the seller's own
        # operator surface acting on its own account, which the buyer-facing
        # restriction does not govern.
        updates = {}
        for field in ("name", "billing", "payment_terms", "rate_card"):
            value = request.form.get(field, "").strip()
            if value:
                updates[field] = value

        if updates:
            uow.accounts.update_fields(account_id, **updates)
            flash("Account updated.", "success")
        else:
            flash("No changes made.", "info")

    return redirect(url_for("accounts.account_detail", tenant_id=tenant_id, account_id=account_id))


@accounts_bp.route("/<account_id>/status", methods=["POST"])
@require_tenant_access()
@log_admin_action("change_account_status")
def change_status(tenant_id, account_id):
    """Change account status (JSON API for AJAX calls)."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")

    if not new_status:
        return jsonify({"success": False, "error": "Status is required."}), 400

    with AccountUoW(tenant_id) as uow:
        account = uow.accounts.get_by_id(account_id)
        if account is None:
            return jsonify({"success": False, "error": "Account not found."}), 404

        allowed = _STATUS_TRANSITIONS.get(account.status, set())
        if new_status not in allowed:
            return jsonify(
                {
                    "success": False,
                    "error": f"Cannot transition from '{account.status}' to '{new_status}'. "
                    f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}.",
                }
            ), 400

        uow.accounts.update_status(account_id, new_status)

    return jsonify({"success": True, "status": new_status})
