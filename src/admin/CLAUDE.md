# src/admin — pointer

This directory is the Flask admin application: the factory (`app.py`), one module per
blueprint (`blueprints/`), the access and audit decorators (`utils/`), and the two operator
APIs (`tenant_management_api.py`, `sync_api.py`). Its templates and static files are at the
repository root, in `templates/` and `static/`.

**Read these before you edit.** Each one is the contract for its subject, and this file only
says where to look and what not to do here.

- To understand where the admin application runs and how a request reaches a view, read
  [Admin UI architecture](../../docs/development/admin-ui-architecture.md).
- To add or change a route, a template, or a page script, read
  [Add an Admin UI page](../../docs/development/admin-ui-add-a-page.md).
- To choose an access check, or to change sign-in or the session, read
  [Admin UI authentication and permissions](../../docs/development/admin-ui-authentication.md).
- To find a route, its access check, or its audit operation, read
  [Admin UI route reference](../../docs/development/admin-ui-routes.md).
- To change the Tenant Management API or the Inventory Sync API, read
  [Tenant Management API and Inventory Sync API](../../docs/operations/admin-apis.md).
- To write or run an admin test, read [tests/admin/README.md](../../tests/admin/README.md).

## The files to copy

`blueprints/accounts.py` and `blueprints/signing_keys.py` are the model. A view in them does
the following and nothing else:

```python
@accounts_bp.route("/create", methods=["GET", "POST"])
@require_tenant_access()            # 1. the access check, directly under the route
@log_admin_action("create_account") # 2. the audit decorator, on a state-changing route
def create_account(tenant_id):
    name = request.form.get("name", "").strip()
    if not name:                    # 3. validate, flash, redirect
        flash("Account name is required.", "error")
        return redirect(request.url)
    with AccountUoW(tenant_id) as uow:   # 4. one Unit of Work, repository methods only
        uow.accounts.create(...)
    flash(f"Account '{name}' created successfully.", "success")
    return redirect(url_for("accounts.list_accounts", tenant_id=tenant_id))  # 5. redirect
```

## What does not belong here

Most other blueprints predate these rules. What you find in them is tracked debt, not a
pattern.

- **No `get_db_session()`, `session.add()`, or `select()` in a view.** Add a repository
  method and call it through a Unit of Work. `.admin-raw-session-baseline` counts the
  existing calls, and `make quality` fails when the count rises.
- **No `require_auth()` on a URL with a `tenant_id`.** It checks that a caller is signed in
  and does not check the tenant. Use `require_tenant_access()`.
- **No access decision from `session["role"]`.** Production code writes only `super_admin`
  and `admin` to the session role, so a comparison with `viewer` or `tenant_admin` never
  matches. For an admin-only route, pass `role=UserRole.ADMIN` to `require_tenant_access()`,
  which reads the role stored for the user.
- **No `log_admin_action` above the access check.** In that order, the audit decorator
  records a successful action for a caller that the check refused.
- **No root-relative URL in a template or a script.** The application runs under `/admin`.
  Use `url_for(...)` in HTML and `scriptRoot + '/...'` in a script.
- **No exception text in a response.** Use `safe_error_message(e)` from
  `utils/operator_errors.py`.
- **No secret in a flash message.** A flash travels in the session cookie, which is signed
  and not encrypted.
- **No hand-written URL or address check.** A field that holds a URL goes through
  `utils/url_policy.py`, which asks the egress gateway.

## After you change a route

Run `uv run python scripts/generate_admin_route_reference.py`. It rewrites the route
reference from the URL map of `create_app()`. Commit the result with your change.
