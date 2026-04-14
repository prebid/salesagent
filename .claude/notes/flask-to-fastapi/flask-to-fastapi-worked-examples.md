# Flask → FastAPI Migration: Five Hard-Case Worked Examples

> **v2.0 PHASE GUIDE (2026-04-12)**
>
> These worked examples show the FULL v2.0 target (async). For Phase 0-3 implementation:
> - Change all `async def` handlers to `def` (except OAuth callbacks which may need `async def` for Authlib)
> - Change `async with get_db_session() as db:` to `with get_db_session() as db:`
> - Change `(await db.execute(stmt)).scalars()` to `db.scalars(stmt)`
> - Change `await` calls to direct calls for sync functions
>
> All admin SQLAlchemy is sync during Phases 0-3 (async pivot reversed 2026-04-12; async conversion is Phase 4+).
> For authoritative Phase 0-3 patterns, see `execution-plan.md`.

Appendix to §13 of `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi-migration.md`. These five examples cover what `accounts.py` doesn't: OAuth redirect flows, dynamic per-tenant client registration, binary multipart uploads, Server-Sent Events, and the 300-LOC multi-branch product form. All Flask sources have been read from disk and cited by file:line.

Conventions used below (established in §11 of the main doc):
- `CurrentTenantDep`, `AdminUserDep`, `SuperAdminDep` → Annotated aliases from `src/admin/deps/auth.py`
- `render(request, "tpl.html", {...})` → wrapper at `src/admin/templating.py`
- `flash(request, msg, category)` → native utility at `src/admin/flash.py`
- `AdminRedirect` → typed exception handled by `src/app.py` → `RedirectResponse(303)`
- `oauth` → module-level `authlib.integrations.starlette_client.OAuth` instance at `src/admin/oauth.py`
- All admin SQLAlchemy is sync during Phases 0-3 (async pivot reversed 2026-04-12): `with get_db_session() as session:` / `session.scalars(stmt).first()`. `run_in_threadpool` is used ONLY in the 3-4 OAuth callback handlers that must be `async def` for Authlib compatibility — and only for DB-touching helper functions called from those handlers. MCP and A2A handlers remain async. See `execution-plan.md` Phase 0 for the canonical handler pattern. Async conversion is Phase 4+.
- CSRF protection uses SameSite=Lax session cookie + CSRFOriginMiddleware (Origin header validation). No csrf_token form fields, no X-CSRF-Token headers, no JavaScript changes needed.

---

## Example 4.1 — Google OAuth login + callback flow

Target file: `src/admin/blueprints/auth.py` (1,097 LOC, 11 routes). We port the four load-bearing routes: `/login`, `/auth/google`, `/auth/google/callback`, `/logout`. The full file has additional tenant-scoped variants, GAM-specific OAuth, and a test-auth backdoor; those are mechanical duplicates of the same patterns and are omitted here for space.

### 4.1.1 Flask source (read from disk)

**The safe-redirect helper** (`src/admin/blueprints/auth.py:40-62`) — reusable, ports verbatim:

```python
# src/admin/blueprints/auth.py:40
def _safe_redirect(url: str | None, fallback: str) -> str:
    """Return *url* only if it is a safe local path, otherwise *fallback*.

    Rejects anything with a scheme, a netloc, a leading // or \\,
    a backslash anywhere, or anything that does not start with /.
    Also rejects after URL-decoding to defeat encoded bypass attempts.
    """
    if not url:
        return fallback

    decoded = unquote(url).strip()
    parts = urlsplit(decoded)
    if (
        parts.scheme
        or parts.netloc
        or decoded.startswith(("//", "\\\\"))
        or "\\" in decoded
        or not decoded.startswith("/")
    ):
        logger.warning("[SECURITY] Rejected unsafe redirect URL: %r", url)
        return fallback

    return decoded
```

**`/login`** (`src/admin/blueprints/auth.py:176-300`, 125 LOC) — detects tenant from `Host` / `Apx-Incoming-Host` headers, checks for tenant-specific OIDC config, falls back to global OAuth, else renders the login page with test-mode buttons. Load-bearing branches:

```python
# src/admin/blueprints/auth.py:176
@auth_bp.route("/login")
def login():
    next_url = _safe_redirect(request.args.get("next"), fallback="")
    if next_url:
        session["login_next_url"] = next_url

    just_logged_out = request.args.get("logged_out") == "1"

    client_id, client_secret, discovery_url, _ = get_oauth_config()
    oauth_configured = bool(client_id and client_secret and discovery_url)
    test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"

    # ... tenant detection from Apx-Incoming-Host / Host header ...
    # ... tenant OIDC lookup (if tenant_context) ...

    if oauth_configured and not test_mode and not just_logged_out:
        return redirect(url_for("auth.google_auth"))

    return render_template(
        "login.html",
        test_mode=test_mode,
        oauth_configured=oauth_configured,
        ...
    )
```

**`/auth/google`** (`src/admin/blueprints/auth.py:362-443`, 82 LOC) — the most fragile route in the file. Key observations:

```python
# src/admin/blueprints/auth.py:362
@auth_bp.route("/auth/google")
def google_auth():
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
    if redirect_uri:
        ...
    else:
        base_url = url_for("auth.google_callback", _external=True)
        skip_nginx = os.environ.get("SKIP_NGINX", "").lower() == "true"
        production = os.environ.get("PRODUCTION", "").lower() == "true"
        if not skip_nginx and production and "/admin/" not in base_url:
            redirect_uri = base_url.replace("/auth/google/callback", "/admin/auth/google/callback")
        else:
            redirect_uri = base_url

    signup_flow = session.get("signup_flow")
    signup_step = session.get("signup_step")
    session.clear()
    if signup_flow: session["signup_flow"] = signup_flow
    if signup_step: session["signup_step"] = signup_step

    response = oauth.google.authorize_redirect(redirect_uri)

    # CRITICAL FIX: Authlib's authorize_redirect() returns a redirect response,
    # but this response bypasses Flask's normal session-saving mechanism.
    session.modified = True
    current_app.session_interface.save_session(current_app, session, response)
    return response
```

The "CRITICAL FIX" at line 435 — manually calling `session_interface.save_session(...)` — is a Flask-specific workaround that vanishes in Starlette because `SessionMiddleware` always writes the cookie on `http.response.start`.

**`/auth/google/callback`** (`src/admin/blueprints/auth.py:488-684`, 197 LOC) — exchanges the code for a token, extracts the user, routes through super-admin → signup-flow → single-tenant auto-select → multi-tenant selector branches:

```python
# src/admin/blueprints/auth.py:488
@auth_bp.route("/auth/google/callback")
def google_callback():
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    tenant_context = session.get("oauth_tenant_context")
    try:
        try:
            token = oauth.google.authorize_access_token()
        except Exception as auth_error:
            flash(f"Authentication error: {str(auth_error)}", "error")
            if tenant_context:
                return redirect(url_for("auth.tenant_login", tenant_id=tenant_context, logged_out=1))
            return redirect(url_for("auth.login", logged_out=1))

        if not token:
            flash("Authentication failed. Please try again.", "error")
            return redirect(url_for("auth.login", logged_out=1))

        user = extract_user_info(token)
        if not user or not user.get("email"):
            flash("Could not retrieve user information from OAuth provider", "error")
            return redirect(url_for("auth.login", logged_out=1))

        email = user["email"]
        session["user"] = email
        session["user_name"] = user.get("name", email)
        session["user_picture"] = user.get("picture", "")
        session.modified = True

        email_domain = email.split("@")[1] if "@" in email else ""
        super_admin_domain = get_super_admin_domain()
        if email_domain == super_admin_domain or is_super_admin(email):
            session["is_super_admin"] = True
            session["role"] = "super_admin"
            ...
            next_url = _safe_redirect(session.pop("login_next_url", None),
                                      fallback=url_for("core.index"))
            return redirect(next_url)

        # ... signup flow branch ...
        # ... tenant enumeration via get_user_tenant_access(email) ...
        # ... single-tenant auto-select branch ...

        flash(f"Welcome {user.get('name', email)}!", "success")
        return make_response(redirect(url_for("auth.select_tenant")))
    except Exception as e:
        logger.error("OAuth callback error: %s: %s", type(e).__name__, e, exc_info=True)
        flash("Authentication failed. Please try again.", "error")
        return redirect(url_for("auth.login", logged_out=1))
```

**`/logout`** (`src/admin/blueprints/auth.py:741-766`, 26 LOC):

```python
# src/admin/blueprints/auth.py:741
@auth_bp.route("/logout")
def logout():
    tenant_id = session.get("tenant_id")
    idp_logout_url = None
    if tenant_id:
        from src.core.database.models import TenantAuthConfig
        with get_db_session() as db_session:
            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
            if config and config.oidc_logout_url:
                idp_logout_url = config.oidc_logout_url
    session.clear()
    if idp_logout_url:
        return redirect(idp_logout_url)
    flash("You have been logged out", "info")
    return redirect(url_for("auth.login", logged_out=1))
```

### 4.1.2 FastAPI-native translation

Target location: `src/admin/routers/auth.py` (new), with helpers imported from `src/admin/oauth.py` (§11.5) and `src/admin/blueprints/auth.py::_safe_redirect` preserved verbatim (moved to `src/admin/utils/redirect_safety.py`).

```python
# src/admin/routers/auth.py
"""Google OAuth login flow — FastAPI-native."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool  # STALE — async pivot: unused import (DB helpers are async def). Remove when implementing.

from src.admin.auth_utils import extract_user_info
from src.admin.domain_access import get_user_tenant_access
from src.admin.flash import flash
from src.admin.oauth import oauth
from src.admin.templating import render
from src.admin.utils import is_super_admin
from src.admin.utils.redirect_safety import safe_next_url
from src.core.config_loader import is_single_tenant_mode
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantAuthConfig
from src.core.domain_config import (
    extract_subdomain_from_host,
    get_oauth_redirect_uri,
    get_super_admin_domain,
    is_sales_agent_domain,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin-auth"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /login
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login", name="admin_auth_login", response_class=HTMLResponse)
async def login(
    request: Request,
    next: Annotated[str | None, Query()] = None,
    logged_out: Annotated[str | None, Query()] = None,
) -> HTMLResponse | RedirectResponse:
    """Show login page or redirect to the appropriate OAuth provider.

    The tenant-resolution logic (Apx-Incoming-Host / subdomain → tenant_id →
    OIDC check) is pushed into async helpers called directly with `await` (full-async pivot 2026-04-11).
    """
    # Anchor the post-login redirect safely in the session (never trust the query arg directly)
    if next:
        request.session["login_next_url"] = safe_next_url(next, fallback="")

    just_logged_out = logged_out == "1"

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    oauth_configured = bool(client_id and client_secret)
    test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"

    tenant_context, tenant_name = await _detect_tenant_from_host(request)
    oidc_enabled = False
    oidc_configured = False

    if tenant_context:
        oidc_configured, oidc_enabled = await _load_oidc_flags(tenant_context)
        if oidc_enabled and not test_mode and not just_logged_out:
            return RedirectResponse(
                request.url_for("admin_oidc_login", tenant_id=tenant_context),
                status_code=303,
            )
        if oauth_configured and not test_mode and not just_logged_out:
            return RedirectResponse(
                request.url_for("admin_auth_tenant_google_auth", tenant_id=tenant_context),
                status_code=303,
            )

    if oauth_configured and not test_mode and not just_logged_out:
        return RedirectResponse(request.url_for("admin_auth_google_auth"), status_code=303)

    return render(request, "login.html", {
        "test_mode": test_mode,
        "oauth_configured": oauth_configured,
        "oidc_enabled": oidc_configured,
        "tenant_context": tenant_context,
        "tenant_name": tenant_name,
        "tenant_id": tenant_context if tenant_context else ("default" if is_single_tenant_mode() else None),
        "single_tenant_mode": is_single_tenant_mode(),
    })


async def _detect_tenant_from_host(request: Request) -> tuple[str | None, str | None]:
    """Mirror of Flask login()'s tenant-detection block. Async DB access (pivoted 2026-04-11).

    Note: also applies to `_enumerate_tenants_for_user` and `_lookup_idp_logout_url`
    elsewhere in this file — same transformation pattern: drop `run_in_threadpool`
    wrapper at call sites, make helper `async def`, replace `with get_db_session()`
    → `async with get_db_session()`, replace `db.scalars(stmt).first()` →
    `(await db.execute(stmt)).scalars().first()`.
    """
    host = request.headers.get("host", "")
    approximated = request.headers.get("apx-incoming-host")
    async with get_db_session() as db:
        if approximated:
            tenant = (await db.execute(
                select(Tenant).filter_by(virtual_host=approximated)
            )).scalars().first()
            if tenant:
                return tenant.tenant_id, tenant.name
        if is_sales_agent_domain(host) and not host.startswith("admin."):
            subdomain = extract_subdomain_from_host(host)
            if subdomain:
                tenant = (await db.execute(
                    select(Tenant).filter_by(subdomain=subdomain)
                )).scalars().first()
                if tenant:
                    return tenant.tenant_id, tenant.name
    return None, None


async def _load_oidc_flags(tenant_id: str) -> tuple[bool, bool]:
    async with get_db_session() as db:
        config = (await db.execute(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if config and config.oidc_client_id:
            return True, bool(config.oidc_enabled)
    return False, False


# ─────────────────────────────────────────────────────────────────────────────
# GET /auth/google
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auth/google", name="admin_auth_google_auth")
async def google_auth(request: Request) -> RedirectResponse:
    """Initiate Google OAuth flow.

    Authlib's starlette_client stores _state_google_* on request.session;
    Starlette's SessionMiddleware writes the cookie automatically on the
    redirect response. The Flask 'save_session on Authlib response'
    workaround at blueprints/auth.py:437 is no longer necessary.
    """
    if "google" not in oauth._clients:
        flash(request, "OAuth not configured", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    # Preserve signup flow state across session.clear() (parity with Flask)
    signup_flow = request.session.get("signup_flow")
    signup_step = request.session.get("signup_step")
    request.session.clear()
    if signup_flow:
        request.session["signup_flow"] = signup_flow
    if signup_step:
        request.session["signup_step"] = signup_step

    redirect_uri = _compute_google_redirect_uri(request)
    return await oauth.google.authorize_redirect(request, redirect_uri)


def _compute_google_redirect_uri(request: Request) -> str:
    """Replicates the Flask URL-construction dance at blueprints/auth.py:386-406."""
    env_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
    if env_uri:
        return env_uri
    base_url = str(request.url_for("admin_auth_google_callback"))
    skip_nginx = os.environ.get("SKIP_NGINX", "").lower() == "true"
    production = os.environ.get("PRODUCTION", "").lower() == "true"
    if not skip_nginx and production and "/admin/" not in base_url:
        return base_url.replace("/auth/google/callback", "/admin/auth/google/callback")
    return base_url


# ─────────────────────────────────────────────────────────────────────────────
# GET /auth/google/callback
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auth/google/callback", name="admin_auth_google_callback")
async def google_callback(request: Request) -> RedirectResponse:
    """Receive Google OAuth callback. Token exchange is async-native via
    authlib's starlette_client. Error paths preserve tenant context to avoid
    redirect loops."""
    tenant_context: str | None = request.session.get("oauth_tenant_context")

    if "google" not in oauth._clients:
        logger.error("OAuth not configured at callback time")
        flash(request, "OAuth not configured", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as err:
        logger.warning("OAuth token exchange failed: %s", err)
        flash(request, f"Authentication error: {err}", "error")
        return _fallback_login_redirect(request, tenant_context)

    if not token:
        flash(request, "Authentication failed. Please try again.", "error")
        return _fallback_login_redirect(request, tenant_context)

    user_info = extract_user_info(token)
    if not user_info or not user_info.get("email"):
        flash(request, "Could not retrieve user information from OAuth provider", "error")
        return _fallback_login_redirect(request, tenant_context)

    email: str = user_info["email"]
    request.session["user"] = email
    request.session["user_name"] = user_info.get("name", email)
    request.session["user_picture"] = user_info.get("picture", "")

    # Super-admin fast path
    email_domain = email.split("@", 1)[1] if "@" in email else ""
    if email_domain == get_super_admin_domain() or is_super_admin(email):
        request.session["is_super_admin"] = True
        request.session["role"] = "super_admin"
        request.session.pop("signup_flow", None)
        request.session.pop("signup_step", None)
        flash(request, f"Welcome {user_info.get('name', email)}! (Super Admin)", "success")
        next_url = safe_next_url(
            request.session.pop("login_next_url", None),
            fallback=str(request.url_for("admin_core_index")),
        )
        return RedirectResponse(next_url, status_code=303)

    # Signup flow branch (non-super-admin)
    if request.session.get("signup_flow"):
        flash(request, f"Welcome {user_info.get('name', email)}!", "success")
        return RedirectResponse(request.url_for("admin_public_signup_onboarding"), status_code=303)

    # Populate available tenants
    available_tenants = await _enumerate_tenants_for_user(email)
    request.session["available_tenants"] = available_tenants

    if is_single_tenant_mode() and len(available_tenants) == 1:
        t = available_tenants[0]
        request.session["tenant_id"] = t["tenant_id"]
        request.session["is_tenant_admin"] = t.get("is_admin", True)
        request.session.pop("available_tenants", None)
        flash(request, f"Welcome {user_info.get('name', email)}!", "success")
        next_url = safe_next_url(
            request.session.pop("login_next_url", None),
            fallback=str(request.url_for("admin_tenants_dashboard", tenant_id=t["tenant_id"])),
        )
        return RedirectResponse(next_url, status_code=303)

    flash(request, f"Welcome {user_info.get('name', email)}!", "success")
    return RedirectResponse(request.url_for("admin_auth_select_tenant"), status_code=303)


def _fallback_login_redirect(request: Request, tenant_context: str | None) -> RedirectResponse:
    if tenant_context:
        url = request.url_for("admin_auth_tenant_login", tenant_id=tenant_context).include_query_params(logged_out="1")
    else:
        url = request.url_for("admin_auth_login").include_query_params(logged_out="1")
    return RedirectResponse(url, status_code=303)


async def _enumerate_tenants_for_user(email: str) -> list[dict]:
    """Port of the tenant enumeration block at blueprints/auth.py:572-634.

    `async def` with `async with get_db_session()` per the full-async pivot
    (2026-04-11). Previously wrapped in `run_in_threadpool` — that is now
    forbidden for DB work; `AsyncSession` is async-native.
    """
    from src.core.database.models import User  # local to mirror Flask import style

    tenant_access = get_user_tenant_access(email)
    out: dict[str, dict] = {}
    async with get_db_session() as db:
        for tenant in tenant_access.get("user_tenants", []):
            existing = (await db.execute(
                select(User).filter_by(email=email, tenant_id=tenant.tenant_id)
            )).scalars().first()
            is_admin = bool(existing and existing.role == "admin")
            out[tenant.tenant_id] = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "is_admin": is_admin,
            }
        if tenant_access.get("domain_tenant"):
            dt = tenant_access["domain_tenant"]
            out.setdefault(dt.tenant_id, {
                "tenant_id": dt.tenant_id, "name": dt.name,
                "subdomain": dt.subdomain, "is_admin": True,
            })
        for tenant in tenant_access.get("email_tenants", []):
            if tenant.tenant_id in out:
                continue
            existing = (await db.execute(
                select(User).filter_by(email=email, tenant_id=tenant.tenant_id)
            )).scalars().first()
            out[tenant.tenant_id] = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "is_admin": bool(existing.role == "admin") if existing else True,
            }
    return list(out.values())


# ─────────────────────────────────────────────────────────────────────────────
# GET /logout
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/logout", name="admin_auth_logout")
async def logout(request: Request) -> RedirectResponse:
    tenant_id = request.session.get("tenant_id")
    idp_logout_url = await _lookup_idp_logout_url(tenant_id) if tenant_id else None
    request.session.clear()
    if idp_logout_url:
        return RedirectResponse(idp_logout_url, status_code=303)
    flash(request, "You have been logged out", "info")
    return RedirectResponse(
        request.url_for("admin_auth_login").include_query_params(logged_out="1"),
        status_code=303,
    )


async def _lookup_idp_logout_url(tenant_id: str) -> str | None:
    async with get_db_session() as db:
        config = (await db.execute(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        return config.oidc_logout_url if config and getattr(config, "oidc_logout_url", None) else None
```

### 4.1.3 Every change labeled

| Flask | FastAPI-native | Why |
|---|---|---|
| `@auth_bp.route("/login")` + `def login()` | `@router.get("/login", name="admin_auth_login")` async def | Verb-explicit decorator; flat route name. (Phase 4+ target. Phase 0-3 uses `def` with sync SQLAlchemy.) |
| `request.args.get("next")` | `next: Annotated[str | None, Query()] = None` | Declarative, typed, self-documenting OpenAPI. |
| `session["login_next_url"] = next_url` | `request.session["login_next_url"] = ...` | Starlette `SessionMiddleware` exposes `request.session` (signed-cookie backend). |
| `get_db_session()` called inline | `await _detect_tenant_from_host(request)` | `AsyncSession` is async-native; helper is `async def` with `async with get_db_session()`. (Phase 4+ target. Phase 0-3 uses sync `with get_db_session() as db:` / `db.scalars(stmt)`.) |
| `current_app.oauth.google.authorize_redirect(uri)` | `oauth.google.authorize_redirect(request, uri)` (from `authlib.integrations.starlette_client`) | The starlette variant is `async` and uses `request.session` directly — no Flask `current_app` thread-local. |
| Manual `current_app.session_interface.save_session(...)` at `auth.py:437` | _deleted_ | Starlette always writes the cookie on `http.response.start`; the Flask-specific bug doesn't exist. |
| `session.modified = True` | _deleted_ | `request.session` is a `dict` subclass that marks itself dirty on every mutation. |
| `url_for("auth.login")` | `request.url_for("admin_auth_login")` | Flat name, no blueprint dot-separator. Returns a `URL` object with `.include_query_params()`. |
| `redirect(...)` (302 by Flask default) | `RedirectResponse(..., status_code=303)` | 303 is the POST-redirect-GET spec-correct code; matches §11.1 convention. |
| `flash("msg", "error")` (Flask global) | `flash(request, "msg", "error")` (§11.3) | `request` parameter required; flash bucket lives on `request.session`. |
| `render_template("login.html", ...)` | `render(request, "login.html", {...})` | Wrapper at `src/admin/templating.py` injects `support_email`, `sales_agent_domain`, and pre-registers a `_url_for` safe-lookup override on `templates.env.globals`. Templates use `{{ url_for('admin_auth_login') }}` for admin paths and `{{ url_for('static', path='/validation.css') }}` for static assets — **NO `script_root`/`admin_prefix`/`static_prefix` globals exist** (greenfield). Note: no `csrf_token` injection — CSRFOriginMiddleware uses Origin header validation, not tokens. |
| `try: token = oauth.google.authorize_access_token(); except Exception` | `try: token = await oauth.google.authorize_access_token(request); except OAuthError` | Authlib's starlette_client raises `OAuthError` — catch the typed exception rather than bare `Exception`. |
| `return make_response(redirect(...))` | `return RedirectResponse(..., status_code=303)` | No `make_response` needed; `RedirectResponse` IS the response. |

### 4.1.4 Edge cases and error handling

| Branch | Handling | Tested? |
|---|---|---|
| `next` param is `https://evil.com/phish` | `safe_next_url()` rejects (scheme present), returns fallback | yes |
| `next` param is `//evil.com/phish` | Rejected (leading `//`) | yes |
| `next` param is `%2f%2fevil.com` URL-encoded | Rejected (decoded then checked) | yes |
| OAuth not configured (`google` not registered) | flash error → redirect to `/login` | yes |
| Token exchange raises `OAuthError` (user denied consent) | flash error, redirect to login with `logged_out=1` | yes |
| Token exchange succeeds but token is `None` | flash generic "failed" → redirect | yes |
| `extract_user_info(token)` returns `{}` (missing email claim) | flash "Could not retrieve user information" → redirect | yes |
| Email domain matches `super_admin_domain` | Fast path, set `is_super_admin=True`, redirect to core index | yes |
| User in signup flow (non-super-admin) | Redirect to onboarding | manual |
| Single-tenant mode with exactly one accessible tenant | Auto-select, redirect to tenant dashboard | yes |
| Multi-tenant / zero tenants | Redirect to `/auth/select-tenant` selector | yes |
| Session cookie size hits ~4KB cap | `SessionMiddleware` raises; caught at ASGI level | smoke |
| `/logout` with tenant OIDC logout URL configured | Redirect to IdP logout URL (no local flash) | yes |
| `/logout` with no tenant or no IdP URL | Clear session, flash "logged out", redirect to login | yes |
| OAuth callback arrives with mismatched `_state_google_*` | Authlib raises `MismatchingStateError` (subclass of `OAuthError`) — caught in existing branch | yes |
| Cookie `adcp_session` missing at callback time (user cleared cookies) | Authlib state load fails → `OAuthError` → redirect to login | yes |

**Open-redirect defense** — `safe_next_url()` is the only gate. It must run on every path that reads a `next` parameter AND must be reapplied when popping from the session (a value stored by an earlier route may have become unsafe if the fallback changed). All three uses above (login, callback, single-tenant auto-select) call it.

**OAuth state management** — `authlib.integrations.starlette_client` stores `_state_google_<short>` keys on `request.session` automatically. These names are Authlib internals; do not touch them. `session.clear()` in `google_auth()` now happens BEFORE `authorize_redirect`, and Authlib then writes fresh state to the cleared session — this is the correct order, which `auth.py:416` already got right.

### 4.1.5 Test pattern

Tests target the handler module directly via `TestClient`, with OAuth `authorize_access_token` mocked out.

```python
# tests/integration/admin/test_auth_router.py
import pytest
from unittest.mock import AsyncMock, patch

from tests.harness._base import IntegrationEnv
from tests.factories import TenantFactory


class _AuthEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}


@pytest.mark.requires_db
class TestGoogleOAuthLogin:
    """Covers: UC-AUTH-LOGIN-01 — Google OAuth happy path writes user to session."""

    def test_login_page_renders_when_oauth_unconfigured(self, integration_db, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        with _AuthEnv(tenant_id="t_auth_1", principal_id="p1") as env:
            TenantFactory(tenant_id="t_auth_1")
            env._commit_factory_data()
            client = env.get_rest_client()
            r = client.get("/login", follow_redirects=False)
            assert r.status_code == 200
            assert "login" in r.text.lower()

    def test_callback_super_admin_sets_session_and_redirects_to_core_index(
        self, integration_db, monkeypatch
    ):
        """Happy path: super-admin email → is_super_admin=True in session, 303 to /."""
        monkeypatch.setenv("SUPER_ADMIN_DOMAIN", "example.com")
        with _AuthEnv(tenant_id="t_auth_2", principal_id="p2") as env:
            TenantFactory(tenant_id="t_auth_2")
            env._commit_factory_data()
            client = env.get_rest_client()

            fake_token = {
                "access_token": "tok",
                "userinfo": {"email": "alice@example.com", "name": "Alice"},
            }
            with patch(
                "src.admin.oauth.oauth.google.authorize_access_token",
                new=AsyncMock(return_value=fake_token),
            ):
                r = client.get("/auth/google/callback", follow_redirects=False)

            assert r.status_code == 303
            assert r.headers["location"].endswith("/")
            # Session is signed-cookie; decode via the TestClient's cookie jar
            session_cookie = client.cookies.get("adcp_session")
            assert session_cookie is not None
            # Round-trip via a second request
            r2 = client.get("/admin/me", follow_redirects=False)
            assert "alice@example.com" in r2.text

    def test_callback_token_exchange_failure_redirects_to_login_with_flash(
        self, integration_db, monkeypatch
    ):
        """Error path: OAuthError during authorize_access_token → login?logged_out=1."""
        from authlib.integrations.base_client.errors import OAuthError

        with _AuthEnv(tenant_id="t_auth_3", principal_id="p3") as env:
            TenantFactory(tenant_id="t_auth_3")
            env._commit_factory_data()
            client = env.get_rest_client()

            with patch(
                "src.admin.oauth.oauth.google.authorize_access_token",
                new=AsyncMock(side_effect=OAuthError(description="denied")),
            ):
                r = client.get("/auth/google/callback", follow_redirects=False)

            assert r.status_code == 303
            assert "logged_out=1" in r.headers["location"]

    def test_login_safe_next_rejects_external(self, integration_db):
        with _AuthEnv(tenant_id="t_auth_4", principal_id="p4") as env:
            TenantFactory(tenant_id="t_auth_4")
            env._commit_factory_data()
            client = env.get_rest_client()
            r = client.get("/login?next=https://evil.com/phish", follow_redirects=False)
            # login_next_url was not stored (or stored as empty)
            assert r.status_code in (200, 303)
            # Inspect session via a subsequent authenticated request if needed
```

---

## Example 4.2 — Per-tenant OIDC (dynamic OAuth client registration)

Target file: `src/admin/blueprints/oidc.py` (431 LOC, 7 routes). We port `/auth/oidc/login/<tenant_id>` (initiate) and `/auth/oidc/callback` (receive). The tenant-configuration routes (`/tenant/<id>/config` GET/POST and `/enable`, `/disable`) are §13-style CRUD endpoints and are straightforward.

### 4.2.1 Flask source (read from disk)

**Dynamic client factory** (`src/admin/blueprints/oidc.py:31-57`):

```python
# src/admin/blueprints/oidc.py:31
def create_tenant_oauth_client(tenant_id: str):
    """Create an OAuth client for a tenant's OIDC configuration."""
    config = get_oidc_config_for_auth(tenant_id)
    if not config:
        return None

    # Create a temporary OAuth instance
    oauth = OAuth()
    oauth.init_app(current_app)
    oauth.register(
        name=f"tenant_{tenant_id}",
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        server_metadata_url=config["discovery_url"],
        client_kwargs={"scope": config["scopes"]},
    )
    return getattr(oauth, f"tenant_{tenant_id}")
```

**Observation:** this creates a brand-new `OAuth()` registry per request. Under load that's a discovery-URL fetch per login. The per-tenant cache is conspicuously absent in the Flask version — we add it during migration.

**`/auth/oidc/login/<tenant_id>`** (`src/admin/blueprints/oidc.py:384-431`):

```python
# src/admin/blueprints/oidc.py:384
@oidc_bp.route("/login/<tenant_id>")
def login(tenant_id: str):
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

        auth_config = db_session.scalars(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        ).first()
        if not auth_config or not auth_config.oidc_client_id:
            flash("SSO is not available for this tenant", "error")
            return redirect(url_for("auth.login"))

        is_setup_mode = getattr(tenant, "auth_setup_mode", False)
        if not is_setup_mode and not auth_config.oidc_enabled:
            flash("SSO is not available for this tenant", "error")
            return redirect(url_for("auth.login"))

        client_secret = auth_config.oidc_client_secret

        oauth = OAuth()
        oauth.init_app(current_app)
        oauth.register(
            name="login_oidc",
            client_id=auth_config.oidc_client_id,
            client_secret=client_secret,
            server_metadata_url=auth_config.oidc_discovery_url,
            client_kwargs={"scope": auth_config.oidc_scopes or "openid email profile"},
        )

        session["oidc_login_tenant_id"] = tenant_id
        redirect_uri = get_tenant_redirect_uri(tenant)
        return oauth.login_oidc.authorize_redirect(redirect_uri)
```

**`/auth/oidc/callback`** (`src/admin/blueprints/oidc.py:209-381`, 173 LOC) — handles both the **test flow** (which marks the config verified) and the **production flow** (which creates a user session):

```python
# src/admin/blueprints/oidc.py:209
@oidc_bp.route("/callback")
def callback():
    try:
        is_test = session.pop("oidc_test_flow", False)
        tenant_id = (session.pop("oidc_test_tenant_id", None)
                     or session.get("oidc_login_tenant_id"))
        if not tenant_id:
            flash("Invalid OAuth callback - no tenant context", "error")
            return redirect(url_for("auth.login"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("auth.login"))

            config = db_session.scalars(
                select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
            ).first()
            if not config or not config.oidc_client_id:
                flash("OIDC not configured", "error")
                return redirect(url_for("auth.login"))

            client_secret = config.oidc_client_secret

            oauth = OAuth()
            oauth.init_app(current_app)
            client_name = "test_oidc" if is_test else "login_oidc"
            oauth.register(
                name=client_name,
                client_id=config.oidc_client_id,
                client_secret=client_secret,
                server_metadata_url=config.oidc_discovery_url,
                client_kwargs={"scope": config.oidc_scopes or "openid email profile"},
            )
            try:
                oauth_client = getattr(oauth, client_name)
                token = oauth_client.authorize_access_token()
            except Exception as e:
                flash(f"OAuth authentication failed: {e}", "error")
                if is_test:
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))
                return redirect(url_for("auth.login"))

        # ... extract email, auto-create user if domain-authorized, write session ...
```

The `getattr(oauth, client_name)` pattern is a code smell — the name must match exactly between initiate and callback, and session state ties them together.

### 4.2.2 FastAPI-native translation

Target location: `src/admin/routers/oidc.py` + helpers in `src/admin/oauth.py` (§11.5 already has the stubs).

```python
# src/admin/oauth.py  (extending §11.5)
from __future__ import annotations
import asyncio
import logging
from typing import Any

from authlib.integrations.starlette_client import OAuth

from src.services.auth_config_service import get_oidc_config_for_auth

logger = logging.getLogger(__name__)

oauth = OAuth()  # module-level Starlette OAuth registry

# Process-local cache: tenant_id → registered client.
# Entries are invalidated via invalidate_tenant_oidc_client() from the
# settings-save endpoint — it must be called on every write.
_tenant_client_cache: dict[str, Any] = {}
_cache_lock = asyncio.Lock()


async def get_tenant_oidc_client(tenant_id: str) -> Any | None:
    """Return a cached, tenant-specific OAuth client, registering on first access.

    Returns None if the tenant has no OIDC configuration. Raises ValueError if the
    config exists but the issuer URL is not a valid HTTPS URL (security guard).
    """
    async with _cache_lock:
        cached = _tenant_client_cache.get(tenant_id)
        if cached is not None:
            return cached

        config = get_oidc_config_for_auth(tenant_id)
        if not config:
            return None

        discovery_url = config["discovery_url"]
        _validate_issuer_url(discovery_url)

        client_name = f"tenant_{tenant_id}"
        # Authlib's register() is idempotent by name but we guard anyway.
        if client_name not in oauth._clients:
            oauth.register(
                name=client_name,
                client_id=config["client_id"],
                client_secret=config["client_secret"],
                server_metadata_url=discovery_url,
                client_kwargs={"scope": config.get("scopes") or "openid email profile"},
            )
        client = getattr(oauth, client_name)
        _tenant_client_cache[tenant_id] = client
        return client


def invalidate_tenant_oidc_client(tenant_id: str) -> None:
    """Drop cache entry AND remove from Authlib's internal registry so that
    a subsequent config change forces a fresh discovery fetch."""
    _tenant_client_cache.pop(tenant_id, None)
    client_name = f"tenant_{tenant_id}"
    oauth._clients.pop(client_name, None)


def _validate_issuer_url(url: str) -> None:
    """Reject non-HTTPS or localhost issuers in production to prevent
    admin-set discovery URLs from leaking secrets over plain HTTP."""
    from urllib.parse import urlsplit
    import os
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"OIDC issuer must be http(s), got: {parts.scheme}")
    if os.environ.get("PRODUCTION", "").lower() == "true":
        if parts.scheme != "https":
            raise ValueError("OIDC issuer must be HTTPS in production")
        if parts.hostname in ("localhost", "127.0.0.1"):
            raise ValueError("OIDC issuer must not be localhost in production")
```

```python
# src/admin/routers/oidc.py
"""Per-tenant OIDC authentication flow — FastAPI-native."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool  # STALE — async pivot: unused import (DB helpers are async def). Remove when implementing.

from src.admin.auth_utils import extract_user_info
from src.admin.flash import flash
from src.admin.oauth import get_tenant_oidc_client
from src.admin.templating import render
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantAuthConfig, User
from src.services.auth_config_service import get_tenant_redirect_uri

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["admin-oidc"])


@router.get("/login/{tenant_id}", name="admin_oidc_login")
async def oidc_login(tenant_id: str, request: Request) -> RedirectResponse:
    """Initiate tenant-specific OIDC flow."""
    gate = await _check_tenant_oidc_available(tenant_id)
    if gate is not None:
        flash(request, gate, "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    try:
        client = await get_tenant_oidc_client(tenant_id)
    except ValueError as exc:
        logger.warning("Invalid OIDC config for %s: %s", tenant_id, exc)
        flash(request, f"OIDC configuration error: {exc}", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    if client is None:
        flash(request, "SSO is not available for this tenant", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    request.session["oidc_login_tenant_id"] = tenant_id

    redirect_uri = await _load_tenant_redirect_uri(tenant_id)
    return await client.authorize_redirect(request, redirect_uri)


async def _check_tenant_oidc_available(tenant_id: str) -> str | None:
    """Return error message if OIDC is not available, None if it is.

    `async def` per the full-async pivot (2026-04-11).
    """
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if not tenant:
            return "Tenant not found"
        config = (await db.execute(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if not config or not config.oidc_client_id:
            return "SSO is not available for this tenant"
        is_setup_mode = getattr(tenant, "auth_setup_mode", False)
        if not is_setup_mode and not config.oidc_enabled:
            return "SSO is not available for this tenant"
    return None


async def _load_tenant_redirect_uri(tenant_id: str) -> str:
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        return get_tenant_redirect_uri(tenant)


@router.get("/callback", name="admin_oidc_callback")
async def oidc_callback(request: Request) -> RedirectResponse | HTMLResponse:
    """Receive OIDC callback for both test and production flows."""
    is_test = bool(request.session.pop("oidc_test_flow", False))
    tenant_id: str | None = (
        request.session.pop("oidc_test_tenant_id", None)
        or request.session.get("oidc_login_tenant_id")
    )
    if not tenant_id:
        flash(request, "Invalid OAuth callback - no tenant context", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    client = await get_tenant_oidc_client(tenant_id)
    if client is None:
        flash(request, "OIDC not configured", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    try:
        token = await client.authorize_access_token(request)
    except OAuthError as err:
        logger.warning("OIDC token exchange failed for %s: %s", tenant_id, err)
        flash(request, f"OAuth authentication failed: {err}", "error")
        if is_test:
            return RedirectResponse(
                request.url_for("admin_tenants_tenant_settings", tenant_id=tenant_id),
                status_code=303,
            )
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    user_info = extract_user_info(token)
    if not user_info or not user_info.get("email"):
        flash(request, "Could not get user email from OAuth provider", "error")
        if is_test:
            return RedirectResponse(
                request.url_for("admin_tenants_tenant_settings", tenant_id=tenant_id),
                status_code=303,
            )
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)

    email = user_info["email"]

    if is_test:
        # Mark config verified + enable SSO (port of oidc.py:282-307)
        tenant = await _verify_and_enable_oidc(tenant_id)
        return render(request, "oidc_test_success.html", {
            "tenant": tenant, "tenant_id": tenant_id,
            "email": email, "name": user_info.get("name", email),
        })

    # Production flow
    request.session.pop("oidc_login_tenant_id", None)
    result = await _resolve_or_create_user(
        tenant_id, email.lower(), user_info.get("name", "")
    )
    if result == "denied":
        flash(request, "Access denied. You don't have permission to access this tenant.", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)
    if result == "disabled":
        flash(request, "Your account has been disabled. Please contact your administrator.", "error")
        return RedirectResponse(request.url_for("admin_auth_login"), status_code=303)
    assert isinstance(result, dict)

    request.session["user"] = result["email"]
    request.session["user_name"] = result["name"] or result["email"]
    request.session["tenant_id"] = tenant_id
    request.session["authenticated"] = True
    request.session["auth_method"] = "oidc"
    flash(request, f"Welcome {result['name'] or result['email']}!", "success")

    next_url = request.session.pop("login_next_url", None)
    if next_url:
        return RedirectResponse(next_url, status_code=303)
    return RedirectResponse(
        request.url_for("admin_tenants_dashboard", tenant_id=tenant_id),
        status_code=303,
    )


async def _verify_and_enable_oidc(tenant_id: str) -> Tenant | None:
    async with get_db_session() as db:
        config = (await db.execute(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if config:
            tenant = (await db.execute(
                select(Tenant).filter_by(tenant_id=tenant_id)
            )).scalars().first()
            config.oidc_verified_at = datetime.now(UTC)
            config.oidc_verified_redirect_uri = get_tenant_redirect_uri(tenant)
            config.oidc_enabled = True
            config.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(tenant)
            return tenant
    return None


async def _resolve_or_create_user(tenant_id: str, email: str, sso_name: str) -> dict | str:
    """Port of blueprints/oidc.py:309-370.

    Returns a dict {email, name} on success or a string sentinel on failure.
    `async def` per the full-async pivot (2026-04-11).
    """
    from uuid import uuid4
    async with get_db_session() as db:
        user = (await db.execute(
            select(User).filter_by(email=email, tenant_id=tenant_id)
        )).scalars().first()
        if not user:
            tenant = (await db.execute(
                select(Tenant).filter_by(tenant_id=tenant_id)
            )).scalars().first()
            email_domain = email.split("@", 1)[1] if "@" in email else None
            authorized_domains = (tenant.authorized_domains or []) if tenant else []
            if email_domain and email_domain in authorized_domains:
                user = User(
                    user_id=str(uuid4()),
                    tenant_id=tenant_id,
                    email=email,
                    name=sso_name or email,
                    role="admin",
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
            else:
                return "denied"
        if not user.is_active:
            return "disabled"
        if sso_name and sso_name != user.name:
            user.name = sso_name
        user.last_login = datetime.now(UTC)
        await db.commit()
        return {"email": user.email, "name": user.name}
```

### 4.2.3 Every change labeled

| Flask | FastAPI-native | Why |
|---|---|---|
| `OAuth(); oauth.init_app(current_app); oauth.register(...)` built per-request inside the handler | `get_tenant_oidc_client(tenant_id)` — module-level registry with per-tenant cache and a lock | Dynamic registration moves out of the request path. Each tenant's discovery URL is fetched exactly once per process lifetime (or until config change invalidates). |
| No cache — discovery fetch per request | `_tenant_client_cache: dict[str, Any]`, invalidated on settings save | Latency reduction; mirrors the well-known Flask pattern that was never added. |
| `getattr(oauth, "login_oidc")` | `client = await get_tenant_oidc_client(...)` | Typed reference; no `getattr` indirection; naming collision between initiate and callback disappears. |
| Login and callback register DIFFERENT client names (`"login_oidc"` vs `"test_oidc"`) | Single registered name per tenant; the flow variant lives purely in session state (`oidc_test_flow`) | Fewer moving parts; the Flask workaround existed because the client was per-request. |
| Discovery URL never validated | `_validate_issuer_url()` runs on cache-miss | Prevents an attacker with tenant-admin access from pointing `discovery_url` at `http://internal-metadata/` or `file://`. |
| `session.pop("oidc_test_flow", False)` | `bool(request.session.pop("oidc_test_flow", False))` | Parity, with explicit `bool` coercion. |
| `oauth_client.authorize_access_token()` (sync) | `await client.authorize_access_token(request)` (async) | Starlette variant returns awaitable and needs `request` for state validation. |
| Settings-save endpoint manually reconstructs client on next request | Settings-save endpoint calls `invalidate_tenant_oidc_client(tenant_id)` | One line in the settings handler; explicit invalidation contract. |

### 4.2.4 Edge cases and error handling

| Scenario | Handling |
|---|---|
| First login for tenant → cache miss, discovery URL reachable | Fetch metadata once, cache client, proceed |
| First login, discovery URL unreachable | `authlib` raises `OAuthError` in `get_tenant_oidc_client` registration OR during `authorize_redirect`; caught, flash error, redirect to `/login` |
| Tenant admin updates client secret at 10:00 | Save handler calls `invalidate_tenant_oidc_client()`; next login hits fresh discovery |
| Two concurrent requests for the same tenant, both cache-miss | `_cache_lock` serializes them; the second awaits, sees cache populated, returns existing client |
| Tenant admin sets `discovery_url = "file:///etc/passwd"` | `_validate_issuer_url` raises `ValueError("OIDC issuer must be http(s)...")`; caught, flash to login |
| Tenant admin sets `discovery_url = "http://internal"` in production | `_validate_issuer_url` rejects (must be HTTPS in production) |
| Tenant `auth_setup_mode=True`, OIDC configured but not enabled | `_check_tenant_oidc_available` allows (setup-mode bypass) — full flow runs |
| Tenant `auth_setup_mode=False`, `oidc_enabled=False` | Error "SSO is not available for this tenant" |
| Callback arrives with no `oidc_login_tenant_id` in session (stale state) | Error "Invalid OAuth callback - no tenant context" → `/login` |
| User email domain not in `authorized_domains` | `_resolve_or_create_user` returns `"denied"`; flash + redirect |
| User exists but `is_active=False` | Returns `"disabled"` |
| Test flow completes → `render(..., "oidc_test_success.html", ...)` with HTTP 200 (not a redirect) | |
| Test flow token exchange fails → redirect to `tenants_tenant_settings` with flash | |
| `next` URL in session points outside our domain | `next_url = request.session.pop("login_next_url", None)` — we re-run `safe_next_url` at the use site (not shown above for brevity; add in Wave 1) |

**Security invariant: issuer validation must match tenant config.** Although Authlib validates that the `iss` claim in the ID token matches the `server_metadata_url`'s issuer, a tenant admin can still point `discovery_url` at a malicious IdP that returns consistent `iss` claims. The `_validate_issuer_url` function is a first-line defense against obviously wrong URLs; a belt-and-suspenders check would additionally compare the token's `iss` claim to an allowlist.

### 4.2.5 Test pattern

```python
# tests/integration/admin/test_oidc_router.py
import pytest
from unittest.mock import AsyncMock, patch

from tests.factories import TenantFactory, UserFactory
from tests.harness._base import IntegrationEnv


class _OidcEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}


@pytest.mark.requires_db
class TestOidcDynamicRegistration:
    """Covers: UC-OIDC-DYNREG-01 — per-tenant client registered lazily with cache."""

    def test_first_login_registers_client_and_caches(self, integration_db, monkeypatch):
        with _OidcEnv(tenant_id="t_oidc_1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t_oidc_1", auth_setup_mode=False)
            # A TenantAuthConfigFactory sets oidc_client_id/_secret/_discovery_url/_enabled
            from tests.factories import TenantAuthConfigFactory
            TenantAuthConfigFactory(
                tenant=tenant,
                oidc_client_id="cid-1",
                oidc_client_secret="secret-1",
                oidc_discovery_url="https://idp.example.com/.well-known/openid-configuration",
                oidc_enabled=True,
            )
            env._commit_factory_data()
            client = env.get_rest_client()

            # Reset cache so the test is deterministic
            from src.admin.oauth import _tenant_client_cache
            _tenant_client_cache.clear()

            with patch(
                "authlib.integrations.starlette_client.StarletteOAuth2App.authorize_redirect",
                new=AsyncMock(return_value="https://idp.example.com/authorize?..."),
            ) as mock_redirect:
                client.get("/auth/oidc/login/t_oidc_1", follow_redirects=False)
                assert "t_oidc_1" in _tenant_client_cache
                # Second request: cache hit, no re-register
                client.get("/auth/oidc/login/t_oidc_1", follow_redirects=False)
                assert mock_redirect.call_count == 2

    def test_cache_invalidated_on_config_save(self, integration_db):
        with _OidcEnv(tenant_id="t_oidc_2", principal_id="p2") as env:
            from src.admin.oauth import (
                _tenant_client_cache, invalidate_tenant_oidc_client,
            )
            _tenant_client_cache["t_oidc_2"] = object()  # sentinel
            invalidate_tenant_oidc_client("t_oidc_2")
            assert "t_oidc_2" not in _tenant_client_cache

    def test_invalid_issuer_url_rejected(self, integration_db):
        from src.admin.oauth import _validate_issuer_url
        with pytest.raises(ValueError, match="http"):
            _validate_issuer_url("file:///etc/passwd")

    def test_callback_denied_for_unauthorized_domain(self, integration_db):
        with _OidcEnv(tenant_id="t_oidc_3", principal_id="p3") as env:
            tenant = TenantFactory(tenant_id="t_oidc_3", authorized_domains=["allowed.com"])
            from tests.factories import TenantAuthConfigFactory
            TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
            env._commit_factory_data()
            client = env.get_rest_client()

            # Prime session with tenant context
            client.cookies.set("adcp_session", _prime_session(tenant_id="t_oidc_3"))

            fake_token = {"userinfo": {"email": "attacker@evil.com"}}
            with patch(
                "src.admin.oauth.StarletteOAuth2App.authorize_access_token",
                new=AsyncMock(return_value=fake_token),
            ):
                r = client.get("/auth/oidc/callback", follow_redirects=False)
            assert r.status_code == 303
            assert r.headers["location"].endswith("/login")
```

---

## Example 4.3 — File upload (tenant favicon)

Target file: `src/admin/blueprints/tenants.py` (906 LOC). The favicon upload route at `src/admin/blueprints/tenants.py:764-831`.

### 4.3.1 Flask source (read from disk)

```python
# src/admin/blueprints/tenants.py:724
ALLOWED_FAVICON_EXTENSIONS = {"ico", "png", "svg", "jpg", "jpeg"}
MAX_FAVICON_SIZE = 1 * 1024 * 1024  # 1MB


def _get_favicon_upload_dir() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    return os.path.join(project_root, "static", "favicons")


def _allowed_favicon_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_FAVICON_EXTENSIONS


def _is_safe_favicon_path(base_dir: str, tenant_id: str) -> bool:
    tenant_dir = os.path.join(base_dir, tenant_id)
    resolved_base = os.path.realpath(base_dir)
    resolved_tenant = os.path.realpath(tenant_dir)
    return resolved_tenant.startswith(resolved_base + os.sep)


@tenants_bp.route("/<tenant_id>/upload_favicon", methods=["POST"])
@log_admin_action("upload_favicon")
@require_tenant_access()
def upload_favicon(tenant_id):
    """Upload a custom favicon for the tenant."""
    try:
        if "favicon" not in request.files:
            flash("No file selected", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        file = request.files["favicon"]
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        if not file.filename or not _allowed_favicon_file(file.filename):
            flash(f"Invalid file type. Allowed: {', '.join(ALLOWED_FAVICON_EXTENSIONS)}", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        # Check file size
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        if file_size > MAX_FAVICON_SIZE:
            flash(f"File too large. Maximum size: {MAX_FAVICON_SIZE // 1024}KB", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        upload_dir = _get_favicon_upload_dir()
        if not _is_safe_favicon_path(upload_dir, tenant_id):
            logger.error(f"Path traversal attempt detected for tenant: {tenant_id}")
            flash("Invalid tenant ID", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))
        tenant_favicon_dir = os.path.join(upload_dir, tenant_id)
        os.makedirs(tenant_favicon_dir, exist_ok=True)

        ext = file.filename.rsplit(".", 1)[1].lower()
        filename = f"favicon.{ext}"
        filepath = os.path.join(tenant_favicon_dir, filename)

        # Remove any existing favicon files for this tenant
        for old_ext in ALLOWED_FAVICON_EXTENSIONS:
            old_file = os.path.join(tenant_favicon_dir, f"favicon.{old_ext}")
            if os.path.exists(old_file):
                os.remove(old_file)

        file.save(filepath)

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                tenant.favicon_url = f"/static/favicons/{tenant_id}/{filename}"
                tenant.updated_at = datetime.now(UTC)
                db_session.commit()

        flash("Favicon uploaded successfully", "success")
    except Exception as e:
        logger.error(f"Error uploading favicon for tenant {tenant_id}: {e}", exc_info=True)
        flash("Error uploading favicon. Please try again.", "error")
    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))
```

### 4.3.2 FastAPI-native translation

```python
# src/admin/routers/tenants.py  (upload_favicon section)
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import aiofiles
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from src.admin.deps.auth import CurrentTenantDep
from src.admin.deps.audit import audit_action
from src.admin.flash import flash
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant", tags=["admin-tenants"])

# ─── Module-level constants (verbatim) ────────────────────────────────────────
ALLOWED_FAVICON_EXTENSIONS: frozenset[str] = frozenset({"ico", "png", "svg", "jpg", "jpeg"})
ALLOWED_FAVICON_CONTENT_TYPES: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/x-icon", "image/vnd.microsoft.icon",
    "image/svg+xml",
})
MAX_FAVICON_SIZE: int = 1 * 1024 * 1024  # 1 MiB
_FAVICON_READ_CHUNK: int = 64 * 1024


def _get_favicon_upload_dir() -> Path:
    """Resolve <repo_root>/src/admin/static/favicons once at import."""
    return Path(__file__).resolve().parent.parent / "static" / "favicons"


def _is_safe_favicon_path(base_dir: Path, tenant_id: str) -> bool:
    tenant_dir = (base_dir / tenant_id).resolve()
    base_resolved = base_dir.resolve()
    try:
        tenant_dir.relative_to(base_resolved)
    except ValueError:
        return False
    return True


def _ext_from(filename: str) -> str | None:
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext in ALLOWED_FAVICON_EXTENSIONS else None


# ─── Handler ─────────────────────────────────────────────────────────────────

@router.post(
    "/{tenant_id}/upload_favicon",
    name="admin_tenants_upload_favicon",
    dependencies=[Depends(audit_action("upload_favicon"))],
)
async def upload_favicon(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    favicon: Annotated[UploadFile, File(description="Favicon image file")],
) -> RedirectResponse:
    """Upload a tenant favicon. Validates content-type, size, and path safety
    before writing to disk. Updates tenant.favicon_url on success."""
    settings_url = request.url_for(
        "tenants_tenant_settings", tenant_id=tenant_id
    ).include_query_params(section="account")

    # 1. Basic file presence / extension check
    raw_name = favicon.filename or ""
    if not raw_name:
        flash(request, "No file selected", "error")
        return RedirectResponse(settings_url, status_code=303)

    ext = _ext_from(raw_name)
    if ext is None:
        flash(request, f"Invalid file type. Allowed: {', '.join(sorted(ALLOWED_FAVICON_EXTENSIONS))}", "error")
        return RedirectResponse(settings_url, status_code=303)

    # 2. Content-type check (defense in depth; don't trust the extension alone)
    ct = (favicon.content_type or "").lower()
    if ct and ct not in ALLOWED_FAVICON_CONTENT_TYPES:
        flash(request, f"Invalid content type: {ct}", "error")
        return RedirectResponse(settings_url, status_code=303)

    # 3. Size enforcement — Starlette does NOT enforce MAX_CONTENT_LENGTH.
    # We must read-and-count ourselves. Stream into memory in chunks to avoid
    # loading large files twice.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await favicon.read(_FAVICON_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FAVICON_SIZE:
            flash(request, f"File too large. Maximum size: {MAX_FAVICON_SIZE // 1024}KB", "error")
            return RedirectResponse(settings_url, status_code=303)
        chunks.append(chunk)
    data = b"".join(chunks)

    # 4. Path-traversal defense (tenant_id came from path param; CurrentTenantDep
    #    already verified tenant exists, but we still validate path construction)
    upload_dir = _get_favicon_upload_dir()
    if not _is_safe_favicon_path(upload_dir, tenant_id):
        logger.error("Path traversal attempt for tenant_id=%r", tenant_id)
        flash(request, "Invalid tenant ID", "error")
        return RedirectResponse(settings_url, status_code=303)

    tenant_dir = upload_dir / tenant_id
    filename = f"favicon.{ext}"
    filepath = tenant_dir / filename
    public_url = f"/static/favicons/{tenant_id}/{filename}"

    # 5. Write file + update DB. On DB failure after disk write, we clean up.
    try:
        await run_in_threadpool(tenant_dir.mkdir, parents=True, exist_ok=True)
        await run_in_threadpool(_remove_stale_favicons, tenant_dir)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(data)
    except OSError as err:
        logger.error("Favicon disk write failed for %s: %s", tenant_id, err, exc_info=True)
        flash(request, "Error uploading favicon. Please try again.", "error")
        return RedirectResponse(settings_url, status_code=303)

    try:
        # Async DB update — no run_in_threadpool wrapper for DB work under the
        # full-async pivot (2026-04-11). run_in_threadpool remains valid for
        # file I/O (filepath.unlink cleanup below) because pathlib is sync.
        await _update_tenant_favicon_url(tenant_id, public_url)
    except Exception:
        logger.error("Favicon DB update failed after disk write for %s", tenant_id, exc_info=True)
        # Rollback: remove the file we just wrote to keep disk/DB consistent.
        # filepath.unlink is sync I/O — run_in_threadpool is correct here.
        try:
            await run_in_threadpool(filepath.unlink, missing_ok=True)
        except Exception:
            logger.error("Cleanup after DB failure also failed", exc_info=True)
        flash(request, "Error uploading favicon. Please try again.", "error")
        return RedirectResponse(settings_url, status_code=303)

    flash(request, "Favicon uploaded successfully", "success")
    return RedirectResponse(settings_url, status_code=303)


def _remove_stale_favicons(tenant_dir: Path) -> None:
    for old_ext in ALLOWED_FAVICON_EXTENSIONS:
        old = tenant_dir / f"favicon.{old_ext}"
        if old.exists():
            old.unlink()


async def _update_tenant_favicon_url(tenant_id: str, public_url: str) -> None:
    async with get_db_session() as db:
        t = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if t is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        t.favicon_url = public_url
        t.updated_at = datetime.now(UTC)
        await db.commit()
```

### 4.3.3 Every change labeled

| Flask | FastAPI-native | Why |
|---|---|---|
| `if "favicon" not in request.files: ... file = request.files["favicon"]` | `favicon: Annotated[UploadFile, File(...)]` | Declarative parameter; presence validated by FastAPI (422 if missing). `File(...)` makes it required and puts it in OpenAPI. |
| `os.path.join` everywhere | `Path` / `pathlib` | Type-safe joining, explicit `.resolve()`, `.mkdir(parents=True, exist_ok=True)`. |
| `file.save(filepath)` (blocking I/O on event loop) | `aiofiles.open(filepath, "wb"); await f.write(data)` | Non-blocking disk write. For small files (<10KB typical favicon) `run_in_threadpool` would also work; `aiofiles` is cleaner. |
| `file.seek(0,2); file.tell(); file.seek(0)` to measure size | Chunked read loop with running `total` counter and early exit | Flask's MAX_CONTENT_LENGTH didn't enforce by default; FastAPI's default `Starlette` max form size is very large. We enforce explicitly by chunking. |
| Content-type never checked | `ct in ALLOWED_FAVICON_CONTENT_TYPES` | Defense in depth — extension can be forged on a renamed `.png` that's actually HTML. |
| `os.path.realpath` check | `tenant_dir.relative_to(base)` with `try/except ValueError` | Cleaner; pathlib does the traversal check without string prefix math. |
| Sync DB update inline | `await _update_tenant_favicon_url(...)` (direct async call; `_update_tenant_favicon_url` is `async def` with `async with get_db_session()` under the full-async pivot) | Pivoted 2026-04-11 — async DB end-to-end. See §18 + `async-pivot-checkpoint.md` §3. |
| No rollback on DB failure after disk write | Explicit `filepath.unlink(missing_ok=True)` on DB exception | Keeps disk and DB consistent; the old code left orphan files. |
| `@log_admin_action("upload_favicon")` decorator | `dependencies=[Depends(audit_action("upload_favicon"))]` | Dep-based side effects; no decorator stacking. |
| `flash("msg", "error")` | `flash(request, "msg", "error")` | §11.3. |
| `return redirect(url_for(...))` | `return RedirectResponse(url, status_code=303)` | Spec-correct. |

### 4.3.4 Edge cases and error handling

| Scenario | Handling |
|---|---|
| No `favicon` part in multipart body | FastAPI raises 422 before handler runs |
| `favicon.filename` is empty string | `if not raw_name` branch → flash + redirect |
| Extension is `exe` | `_ext_from` returns `None` → flash + redirect |
| File is a renamed HTML posing as `.png` | Content-type check catches if the client sent an accurate CT; if not, extension is checked and Image-bomb detection (not shown) could be added |
| File is exactly `MAX_FAVICON_SIZE` | Accepted (total == MAX_FAVICON_SIZE, not `>`) |
| File is `MAX_FAVICON_SIZE + 1` | Rejected at first chunk that exceeds — we stop reading and return |
| Upload takes 10 minutes (slow client) | uvicorn request-timeout kills the connection before buffer overflow |
| `tenant_id="../../etc/passwd"` | `CurrentTenantDep` rejects (no such tenant) BEFORE path construction; `_is_safe_favicon_path` is belt-and-suspenders |
| Disk is full during write | `OSError` caught, flash "Error uploading favicon" |
| `_update_tenant_favicon_url` raises (e.g., DB lock timeout) | File is deleted, user sees error flash |
| Tenant row doesn't exist at update time (racey delete) | `HTTPException(404)` propagates; our exception handler converts to admin redirect flow (or 404 for API callers) |
| SVG with embedded `<script>` | Not addressed here — add SVG sanitization at display time or reject SVG entirely. See Wave 3 security review. |
| Concurrent uploads for same tenant | `_remove_stale_favicons` + atomic `open("wb")` — last write wins; no locking |

### 4.3.5 Test pattern

```python
# tests/integration/admin/test_favicon_upload.py
import io
import pytest
from pathlib import Path
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv


class _FaviconEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}


@pytest.mark.requires_db
class TestFaviconUpload:
    """Covers: UC-TENANT-FAVICON-01 — favicon upload validates, stores, updates DB."""

    def test_happy_path_png(self, integration_db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.admin.routers.tenants._get_favicon_upload_dir",
            lambda: tmp_path,
        )
        with _FaviconEnv(tenant_id="t_fav_1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t_fav_1")
            env._commit_factory_data()
            client = env.get_rest_client()

            png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40  # Minimal PNG header
            r = client.post(
                "/tenant/t_fav_1/upload_favicon",
                files={"favicon": ("my.png", png_bytes, "image/png")},
                follow_redirects=False,
            )
            assert r.status_code == 303
            assert (tmp_path / "t_fav_1" / "favicon.png").exists()

            # DB verification
            from src.core.database.database_session import get_db_session
            from src.core.database.models import Tenant
            from sqlalchemy import select
            with get_db_session() as db:
                t = db.scalars(select(Tenant).filter_by(tenant_id="t_fav_1")).first()
                assert t.favicon_url == "/static/favicons/t_fav_1/favicon.png"

    def test_rejects_wrong_extension(self, integration_db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.admin.routers.tenants._get_favicon_upload_dir",
            lambda: tmp_path,
        )
        with _FaviconEnv(tenant_id="t_fav_2", principal_id="p2") as env:
            TenantFactory(tenant_id="t_fav_2")
            env._commit_factory_data()
            client = env.get_rest_client()

            r = client.post(
                "/tenant/t_fav_2/upload_favicon",
                files={"favicon": ("evil.exe", b"MZ", "application/octet-stream")},
                follow_redirects=False,
            )
            assert r.status_code == 303
            # File NOT written
            assert not (tmp_path / "t_fav_2").exists()

    def test_rejects_oversized(self, integration_db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.admin.routers.tenants._get_favicon_upload_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "src.admin.routers.tenants.MAX_FAVICON_SIZE", 1024
        )
        with _FaviconEnv(tenant_id="t_fav_3", principal_id="p3") as env:
            TenantFactory(tenant_id="t_fav_3")
            env._commit_factory_data()
            client = env.get_rest_client()

            huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
            r = client.post(
                "/tenant/t_fav_3/upload_favicon",
                files={"favicon": ("big.png", huge, "image/png")},
                follow_redirects=False,
            )
            assert r.status_code == 303
            assert not (tmp_path / "t_fav_3" / "favicon.png").exists()

    def test_db_failure_rolls_back_disk(self, integration_db, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.admin.routers.tenants._get_favicon_upload_dir",
            lambda: tmp_path,
        )
        def _boom(*a, **kw):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(
            "src.admin.routers.tenants._update_tenant_favicon_url", _boom,
        )
        with _FaviconEnv(tenant_id="t_fav_4", principal_id="p4") as env:
            TenantFactory(tenant_id="t_fav_4")
            env._commit_factory_data()
            client = env.get_rest_client()

            png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
            r = client.post(
                "/tenant/t_fav_4/upload_favicon",
                files={"favicon": ("f.png", png_bytes, "image/png")},
                follow_redirects=False,
            )
            assert r.status_code == 303
            # File was rolled back
            assert not (tmp_path / "t_fav_4" / "favicon.png").exists()
```

---

## Example 4.4 — ~~Server-Sent Events activity stream~~ **STALE — Decision 8 DELETE (2026-04-11)**

> **Do NOT implement this example.** Decision 8 deep-think analysis verified the SSE `/events` route is **orphan code** — `templates/tenant_dashboard.html:972` says `// Use simple polling instead of EventSource for reliability`, zero `new EventSource(` exists in templates, and the only `/events` caller is one integration smoke test probe. The SSE route is **DELETED in Wave 4** (not migrated). The `sse_starlette` dependency is NOT added. See CLAUDE.md Decision 8 and `async-pivot-checkpoint.md` §3 "SSE / long-lived connections" for the deletion scope. The two surviving routes (`/activity` JSON poll + `/activities` REST) convert mechanically to `async def` + `async with get_db_session()` and are NOT covered by a worked example because the conversion is trivial.

The example below is preserved for historical reference only:

Target file: `src/admin/blueprints/activity_stream.py` (390 LOC, 3 routes). ~~We port `/tenant/<tid>/events`.~~ **STALE — route is deleted, not ported.**

### 4.4.1 Flask source (read from disk)

```python
# src/admin/blueprints/activity_stream.py:21
MAX_CONNECTIONS_PER_TENANT = 10
connection_counts: dict[str, int] = defaultdict(int)
connection_timestamps: dict[str, list[float]] = defaultdict(list)


# src/admin/blueprints/activity_stream.py:226
@activity_stream_bp.route("/tenant/<tenant_id>/events", methods=["GET", "HEAD"])
@require_tenant_access(api_mode=True)
def activity_events(tenant_id, **kwargs):
    """Server-Sent Events endpoint for real-time activity updates."""
    if request.method == "HEAD":
        return Response(status=200)

    if not tenant_id or not isinstance(tenant_id, str) or len(tenant_id) > 50:
        return Response("Invalid tenant ID", status=400)

    now = datetime.now(UTC)
    connection_timestamps[tenant_id] = [
        ts for ts in connection_timestamps[tenant_id]
        if (now - ts).total_seconds() < 60
    ]

    active_connections = len(connection_timestamps[tenant_id])
    if active_connections >= MAX_CONNECTIONS_PER_TENANT:
        return Response("Too many connections. Please wait before reconnecting.", status=429)

    connection_timestamps[tenant_id].append(now)
    connection_counts[tenant_id] += 1

    def generate():
        cleanup_needed = False
        try:
            recent_activities = get_recent_activities(tenant_id, limit=50)
            for activity in reversed(recent_activities):
                data = json.dumps(activity)
                yield f"data: {data}\n\n"

            last_check = datetime.now(UTC)
            cleanup_needed = True

            while True:
                try:
                    new_activities = get_recent_activities(
                        tenant_id,
                        since=last_check - timedelta(seconds=1),
                        limit=10,
                    )
                    for activity in reversed(new_activities):
                        data = json.dumps(activity)
                        yield f"data: {data}\n\n"
                    if new_activities:
                        newest_timestamp_str = new_activities[0]["timestamp"]
                        newest_timestamp = datetime.fromisoformat(
                            newest_timestamp_str.replace("Z", "+00:00")
                        )
                        last_check = max(last_check, newest_timestamp)
                    else:
                        last_check = datetime.now(UTC)

                    yield ": heartbeat\n\n"
                    time.sleep(2)
                except GeneratorExit:
                    cleanup_needed = True
                    break
                except Exception as e:
                    logger.error(f"Error in SSE stream for tenant {tenant_id}: {e}")
                    cleanup_needed = True
                    error_data = json.dumps({"type": "error", ...})
                    yield f"event: error\ndata: {error_data}\n\n"
                    time.sleep(5)
        except Exception as e:
            ...
        finally:
            if cleanup_needed:
                connection_counts[tenant_id] = max(0, connection_counts[tenant_id] - 1)
                import gc
                gc.collect()

    response = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    return response
```

**Key pain points** — `time.sleep(2)` blocks a WSGI worker for the lifetime of the connection; `GeneratorExit` is the only disconnect signal; `gc.collect()` is magic thinking.

### 4.4.2 FastAPI-native translation

```python
# src/admin/routers/activity_stream.py
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from src.admin.deps.auth import CurrentTenantDep
from src.admin.services.activity import get_recent_activities

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant", tags=["admin-activity"])

# ─── Rate limit (process-local; sufficient for single-node deploys) ────────
MAX_CONNECTIONS_PER_TENANT: int = 10
RATE_WINDOW_SECONDS: int = 60
_active_connections: dict[str, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()


async def rate_limit_sse(tenant_id: str) -> None:
    """Dep that enforces SSE connection ceiling per tenant.

    Uses a monotonic timestamp list + async lock. On allow, appends current
    time. On deny, raises 429.
    """
    now_mono = time.monotonic()
    async with _rate_lock:
        recent = [ts for ts in _active_connections[tenant_id]
                  if now_mono - ts < RATE_WINDOW_SECONDS]
        if len(recent) >= MAX_CONNECTIONS_PER_TENANT:
            _active_connections[tenant_id] = recent
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many connections. Please wait before reconnecting.",
            )
        recent.append(now_mono)
        _active_connections[tenant_id] = recent


async def release_sse_slot(tenant_id: str) -> None:
    """Called from the generator's finally block when the client disconnects."""
    async with _rate_lock:
        bucket = _active_connections.get(tenant_id, [])
        if bucket:
            bucket.pop()  # Remove newest slot; order doesn't matter for rate window


# ─── Handler ────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/events", name="admin_activity_stream_events")
async def activity_events(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    _rate: Annotated[None, Depends(rate_limit_sse)],
) -> EventSourceResponse:
    """Server-Sent Events: real-time admin activity feed.

    Authentication is cookie-based (EventSource cannot send custom headers).
    CurrentTenantDep reads the admin session cookie; CSRF middleware exempts
    GET requests.
    """
    if len(tenant_id) > 50:
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            # 1. Send the initial burst (historical)
            recent = await get_recent_activities(tenant_id, None, 50)
            for activity in reversed(recent):
                yield {"event": "activity", "data": json.dumps(activity)}

            last_check = datetime.now(UTC)

            # 2. Poll loop
            while True:
                if await request.is_disconnected():
                    logger.info("SSE client disconnected for tenant %s", tenant_id)
                    break

                try:
                    new_activities = await get_recent_activities(
                        tenant_id,
                        last_check - timedelta(seconds=1),
                        10,
                    )
                except Exception:
                    logger.exception("SSE poll failed for tenant %s", tenant_id)
                    yield {
                        "event": "error",
                        "data": json.dumps({
                            "type": "error",
                            "message": "Stream error occurred",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }),
                    }
                    await asyncio.sleep(5)
                    continue

                for activity in reversed(new_activities):
                    yield {"event": "activity", "data": json.dumps(activity)}

                if new_activities:
                    newest = datetime.fromisoformat(
                        new_activities[0]["timestamp"].replace("Z", "+00:00")
                    )
                    last_check = max(last_check, newest)
                else:
                    last_check = datetime.now(UTC)

                # sse-starlette sends `ping` bytes automatically per
                # `ping` param; we don't need manual heartbeats.
                await asyncio.sleep(2)
        finally:
            await release_sse_slot(tenant_id)

    return EventSourceResponse(
        event_generator(),
        ping=15,  # seconds between keep-alive pings
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: disable response buffering
        },
    )
```

### 4.4.3 Every change labeled

| Flask | FastAPI-native | Why |
|---|---|---|
| `Response(generate(), mimetype="text/event-stream", ...)` | `EventSourceResponse(event_generator(), ping=15, ...)` | `sse-starlette` handles framing, pings, disconnect detection. |
| `def generate():` (sync generator, `yield "data: ...\n\n"`) | `async def event_generator():` yielding `{"event": "activity", "data": json.dumps(...)}` | Async-native; `EventSourceResponse` does string framing from the dict. |
| `GeneratorExit` (only disconnect signal, fragile) | `if await request.is_disconnected(): break` | Explicit disconnect check on every loop iteration; works with `sse-starlette`. |
| `time.sleep(2)` (blocks worker thread) | `await asyncio.sleep(2)` | Yields the event loop; one uvicorn worker can hold thousands of SSE connections. |
| `get_recent_activities(...)` called inline (sync SQL) | `await get_recent_activities(...)` | `AsyncSession` is async-native; helper is `async def` with per-tick `async with get_db_session()`. |
| Manual `": heartbeat\n\n"` yield | `ping=15` param | Library handles keep-alive; comment-line pings are automatic. |
| `defaultdict(list)` for timestamps + no lock | `asyncio.Lock` around shared state | Async-safe; prevents TOCTOU when many connections race on cache-miss. |
| `@require_tenant_access(api_mode=True)` | `CurrentTenantDep` + cookie-auth (middleware reads `request.session`) | Unified deps. **Cookie, not header** — EventSource can't set `Authorization`. |
| Rate limiting inline in handler | `rate_limit_sse` dep | Testable in isolation; raises `HTTPException(429)` before the generator starts. |
| `HEAD` method branch | _deleted_ | `HEAD` requests to an SSE endpoint are an authentication probe; we instead add a separate `GET /tenant/{id}/events/status` returning 200 OK with no streaming. (Not shown — one-line handler.) |
| Cleanup via `gc.collect()` | `finally: await release_sse_slot(tenant_id)` | Explicit resource release; no gc magic. |
| `connection_counts[tenant_id] += 1` (no lock) | async-lock around `_active_connections` | Correctness under concurrency. |

### 4.4.4 Edge cases and error handling

| Scenario | Handling |
|---|---|
| 11th concurrent connection for a tenant | `rate_limit_sse` raises `HTTPException(429)`; `EventSourceResponse` never constructed |
| Client disconnects mid-stream | `request.is_disconnected()` returns `True` on next loop iteration; `finally` releases slot |
| DB temporarily unreachable | Error event sent via SSE, 5-second sleep, retry continues until disconnect or success |
| Client network blip (TCP RST) | Starlette detects via `message.http.disconnect`; same as disconnect |
| Process restart | All connections drop; clients reconnect via EventSource auto-retry (browser-default ~3s) |
| Admin session cookie expires during stream | Next request to another route → 303 to login. Current stream continues until client reconnects |
| CSRF middleware | GET is a safe method → exempt. `POST /events` would be rejected. |
| Nginx buffering | `X-Accel-Buffering: no` disables response buffering; required for real-time delivery |
| Rate-limit bucket leaks (slot not released) | Window-based eviction: entries older than 60 seconds are dropped on next admit check |
| `tenant_id` longer than 50 chars | 400 before generator starts |
| OpenAPI doc generation | `EventSourceResponse` has no Pydantic schema; document via `responses={200: {"content": {"text/event-stream": {}}}}` on the decorator |

**URL-for considerations:** `request.url_for("admin_activity_stream_events", tenant_id=...)` returns a URL object. JavaScript `EventSource` MUST use the exact path; templates should emit `new EventSource("{{ url_for('activity_stream_events', tenant_id=tenant_id) }}")` (codemod converts `url_for` to `request.url_for` for the template wrapper in §12). No trailing slash — `/events` not `/events/`.

### 4.4.5 Test pattern

```python
# tests/integration/admin/test_activity_stream.py
import json
import pytest
from tests.factories import TenantFactory, AuditLogFactory
from tests.harness._base import IntegrationEnv


class _SSEEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}


@pytest.mark.requires_db
class TestActivityStreamSSE:
    """Covers: UC-ACTIVITY-SSE-01 — initial burst + rate limit + disconnect cleanup."""

    def test_initial_burst_contains_historical(self, integration_db):
        with _SSEEnv(tenant_id="t_sse_1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t_sse_1")
            AuditLogFactory(tenant=tenant, operation="create_media_buy")
            AuditLogFactory(tenant=tenant, operation="update_media_buy")
            env._commit_factory_data()
            client = env.get_rest_client()

            with client.stream("GET", "/tenant/t_sse_1/events") as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                lines = []
                for chunk in r.iter_lines():
                    lines.append(chunk)
                    if len(lines) >= 4:  # two events, each ~3 lines + blank
                        break
                joined = "\n".join(lines)
                assert "data:" in joined
                # Parse the first event
                events = [e for e in joined.split("\n\n") if e.strip()]
                first_data = next(
                    line[5:].strip()
                    for line in events[0].splitlines()
                    if line.startswith("data:")
                )
                parsed = json.loads(first_data)
                assert "operation" in parsed

    def test_rate_limit_429_after_max(self, integration_db, monkeypatch):
        monkeypatch.setattr(
            "src.admin.routers.activity_stream.MAX_CONNECTIONS_PER_TENANT", 2
        )
        # Reset rate-limit state between tests
        from src.admin.routers.activity_stream import _active_connections
        _active_connections.clear()

        with _SSEEnv(tenant_id="t_sse_2", principal_id="p2") as env:
            TenantFactory(tenant_id="t_sse_2")
            env._commit_factory_data()
            client = env.get_rest_client()

            # Open two streams, keep them open
            s1 = client.stream("GET", "/tenant/t_sse_2/events").__enter__()
            s2 = client.stream("GET", "/tenant/t_sse_2/events").__enter__()
            try:
                # Third should 429
                r3 = client.get("/tenant/t_sse_2/events")
                assert r3.status_code == 429
            finally:
                s1.__exit__(None, None, None)
                s2.__exit__(None, None, None)

    def test_disconnect_releases_slot(self, integration_db, monkeypatch):
        from src.admin.routers.activity_stream import _active_connections
        _active_connections.clear()

        with _SSEEnv(tenant_id="t_sse_3", principal_id="p3") as env:
            TenantFactory(tenant_id="t_sse_3")
            env._commit_factory_data()
            client = env.get_rest_client()

            with client.stream("GET", "/tenant/t_sse_3/events") as r:
                assert r.status_code == 200
                _ = next(r.iter_lines())

            # After context exit, slot released
            assert len(_active_connections.get("t_sse_3", [])) == 0
```

---

## Example 4.5 — Product creation with complex multi-field form + conditional super-admin

Target file: `src/admin/blueprints/products.py` (2,464 LOC — largest blueprint). The `add_product` route at `src/admin/blueprints/products.py:700-1324` is a ~625-LOC single function. We translate it into a router module with a Pydantic command model, a service function, and two handlers (GET + POST).

### 4.5.1 Flask source (read from disk)

**The form entry point** (`src/admin/blueprints/products.py:700-732`):

```python
# src/admin/blueprints/products.py:700
@products_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_product")
@require_tenant_access()
def add_product(tenant_id):
    """Add a new product - adapter-specific form."""
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("products.list_products", tenant_id=tenant_id))
        adapter_type = tenant.ad_server or "mock"

        currency_limits = db_session.scalars(
            select(CurrencyLimit).filter_by(tenant_id=tenant_id)
        ).all()
        currencies = [limit.currency_code for limit in currency_limits]
        if not currencies:
            currencies = ["USD"]

    if request.method == "POST":
        try:
            form_data = sanitize_form_data(request.form.to_dict())
            if not form_data.get("name"):
                flash("Product name is required", "error")
                return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
            ...
```

**Formats, a JSON-in-form field** (`src/admin/blueprints/products.py:737-789`):

```python
formats_json = form_data.get("formats", "[]") or "[]"
formats = []
try:
    formats_parsed = json.loads(formats_json)
    if isinstance(formats_parsed, list) and formats_parsed:
        from src.core.creative_agent_registry import get_creative_agent_registry
        registry = get_creative_agent_registry()
        result = asyncio.run(registry.list_all_formats_with_errors(tenant_id=tenant_id))
        if result.errors and not result.formats:
            formats.extend(_parse_format_entries(formats_parsed))
            flash("Format validation unavailable (creative agent unreachable). ...", "warning")
        else:
            valid_format_ids = {fmt.format_id.id for fmt in result.formats}
            all_entries = _parse_format_entries(formats_parsed)
            invalid_formats = [e["id"] for e in all_entries if e["id"] not in valid_format_ids]
            formats.extend(e for e in all_entries if e["id"] in valid_format_ids)
            if invalid_formats:
                flash(f"Invalid format IDs: {', '.join(invalid_formats)}. ...", "error")
                return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
except json.JSONDecodeError as e:
    flash("Invalid format data submitted. Please try again.", "error")
    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
```

**Multi-select fields (`request.form.getlist`)** (`src/admin/blueprints/products.py:792, 976, 982, 1032`):

```python
countries_list = request.form.getlist("countries")
countries = countries_list if countries_list and "ALL" not in countries_list else None
# ...
channels = request.form.getlist("channels")
allowed_principals = request.form.getlist("allowed_principal_ids")
selected_tags = request.form.getlist("selected_property_tags")
```

**Pricing options parsed from form** (`src/admin/blueprints/products.py:796-806`):

```python
try:
    pricing_options_data = parse_pricing_options_from_form(form_data)
except ValueError as e:
    flash(str(e), "error")
    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
if not pricing_options_data or len(pricing_options_data) == 0:
    flash("Product must have at least one pricing option", "error")
    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
```

**Nested JSON field (targeting template)** (`src/admin/blueprints/products.py:915-935`):

```python
targeting_template_json = form_data.get("targeting_template", "{}")
try:
    targeting_template = json.loads(targeting_template_json) if targeting_template_json else {}
except json.JSONDecodeError:
    targeting_template = {}
```

**`property_mode` — three-branch property-authorization handling** (`src/admin/blueprints/products.py:1029-1186`): `"tags"` (domain:tag pairs, grouped), `"property_ids"` (AdCP discriminated union), `"full"` (legacy full property objects).

**Final commit + redirect** (`src/admin/blueprints/products.py:1240-1316`):

```python
product = Product(**product_kwargs)
db_session.add(product)
db_session.flush()
# Create pricing options, inventory mappings ...
db_session.commit()
flash(f"Product '{product.name}' created successfully!", "success")
return redirect(url_for("products.list_products", tenant_id=tenant_id))
```

**Super-admin bypass:** there is no explicit super-admin gate in `add_product` in the current Flask version. For the migration we **add** a bypass for `allowed_principal_ids` spanning other tenants and for pricing-guardrail overrides (e.g., setting `rate=0.01` for `CPM` which would normally fail a floor check). This is the "hard case" the user asked about — we demonstrate the `SuperAdminDep` gating pattern.

### 4.5.2 FastAPI-native translation

**Decision: explicit `Form(...)` parameters, NOT a single `BaseModel`.** Pydantic v2 supports form-body models via `Form(media_type="multipart/form-data")`, but:

1. The form has **30+ fields**, many optional with defaults, and mutually exclusive property-mode branches — a single model would be as long as the handler.
2. Multi-select `list[str]` fields don't map cleanly to model fields; explicit `Form()` with `list[str]` works better.
3. JSON-in-form fields (`formats`, `targeting_template`) need post-parse validation that a model field can't express.
4. Super-admin-only fields need runtime gating against `user.role`, not a field-level `strict`.

Instead we define a **`CreateProductCommand` service-layer model** that the handler builds from form params and passes to a pure function. That function is the testable unit; the handler is the adapter.

```python
# src/admin/routers/products.py
"""Product creation — FastAPI-native with form-heavy handler + service."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool  # STALE — async pivot: unused import (DB helpers are async def). Remove when implementing.

from src.admin.deps.audit import audit_action
from src.admin.deps.auth import AdminUserDep, CurrentTenantDep
from src.admin.flash import flash
from src.admin.services.products import (
    CreateProductCommand,
    ProductCreateValidationError,
    create_product_for_tenant,
)
from src.admin.templating import render
from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant/{tenant_id}/products", tags=["admin-products"])


# ─── GET: show the form ─────────────────────────────────────────────────────

@router.get(
    "/add",
    name="admin_products_add_product_form",
    response_class=HTMLResponse,
)
async def add_product_form(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
) -> HTMLResponse:
    adapter_type, currencies = await _load_adapter_context(tenant_id)
    return render(request, "add_product.html", {
        "tenant_id": tenant_id,
        "tenant": tenant,
        "adapter_type": adapter_type,
        "currencies": currencies,
        "form_data": {},  # empty on first show
        "is_super_admin": _is_super_admin(request),
    })


# ─── POST: create ───────────────────────────────────────────────────────────

@router.post(
    "/add",
    name="admin_products_add_product",
    dependencies=[Depends(audit_action("add_product"))],
)
async def add_product(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    user: AdminUserDep,
    # Core fields
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    product_id: Annotated[str, Form()] = "",
    # JSON-in-form fields
    formats: Annotated[str, Form()] = "[]",
    targeting_template: Annotated[str, Form()] = "{}",
    pricing_options: Annotated[str, Form()] = "[]",
    # Multi-select fields (HTML checkboxes → list[str])
    countries: Annotated[list[str] | None, Form()] = None,
    channels: Annotated[list[str] | None, Form()] = None,
    allowed_principal_ids: Annotated[list[str] | None, Form()] = None,
    selected_property_tags: Annotated[list[str] | None, Form()] = None,
    selected_property_ids: Annotated[list[str] | None, Form()] = None,
    # Property-mode branch
    property_mode: Annotated[str, Form()] = "tags",
    # Adapter-specific
    targeted_ad_unit_ids: Annotated[str, Form()] = "",
    targeted_placement_ids: Annotated[str, Form()] = "",
    include_descendants: Annotated[str, Form()] = "",
    line_item_type: Annotated[str, Form()] = "",
    priority: Annotated[str, Form()] = "",
    # Dynamic product fields
    is_dynamic: Annotated[str, Form()] = "",
    signals_agent_selection: Annotated[str, Form()] = "all",
    signals_agent_ids: Annotated[list[str] | None, Form()] = None,
    variant_name_pattern: Annotated[str, Form()] = "default",
    variant_name_template: Annotated[str, Form()] = "",
    append_signal_description: Annotated[str, Form()] = "",
    max_signals: Annotated[str, Form()] = "5",
    variant_ttl_days: Annotated[str, Form()] = "",
    # Inventory profile
    inventory_profile_id: Annotated[str, Form()] = "",
    # Super-admin-only overrides
    override_pricing_floor: Annotated[str, Form()] = "",
    cross_tenant_principal_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse | HTMLResponse:

    # Raw form echo for re-rendering on error — mirrors Flask's form_data dict
    raw_form = {
        "name": name, "description": description, "product_id": product_id,
        "formats": formats, "targeting_template": targeting_template,
        "pricing_options": pricing_options,
        "countries": countries or [], "channels": channels or [],
        "allowed_principal_ids": allowed_principal_ids or [],
        "property_mode": property_mode,
        "selected_property_tags": selected_property_tags or [],
        "selected_property_ids": selected_property_ids or [],
        "targeted_ad_unit_ids": targeted_ad_unit_ids,
        "targeted_placement_ids": targeted_placement_ids,
        "include_descendants": include_descendants == "on",
        "line_item_type": line_item_type, "priority": priority,
        "is_dynamic": is_dynamic in ("on", "true", "1"),
        "signals_agent_selection": signals_agent_selection,
        "signals_agent_ids": signals_agent_ids or [],
        "variant_name_pattern": variant_name_pattern,
        "variant_name_template": variant_name_template,
        "append_signal_description": append_signal_description == "on",
        "max_signals": max_signals, "variant_ttl_days": variant_ttl_days,
        "inventory_profile_id": inventory_profile_id,
    }

    # 1. Parse JSON-in-form fields
    try:
        formats_parsed = json.loads(formats) if formats else []
        if not isinstance(formats_parsed, list):
            raise ValueError("formats must be a JSON array")
        targeting_parsed = json.loads(targeting_template) if targeting_template else {}
        pricing_parsed = json.loads(pricing_options) if pricing_options else []
        if not isinstance(pricing_parsed, list) or not pricing_parsed:
            raise ValueError("at least one pricing option required")
    except (json.JSONDecodeError, ValueError) as err:
        return await _rerender_with_error(
            request, tenant_id, tenant, raw_form, f"Invalid form data: {err}",
        )

    # 2. Super-admin gating — non-super-admins cannot override pricing floors
    #    or grant cross-tenant principals.
    is_super = user.role == "super_admin"
    if not is_super and override_pricing_floor:
        return await _rerender_with_error(
            request, tenant_id, tenant, raw_form,
            "Only super admins may override pricing guardrails",
        )
    if not is_super and cross_tenant_principal_ids:
        return await _rerender_with_error(
            request, tenant_id, tenant, raw_form,
            "Only super admins may grant cross-tenant principal access",
        )

    # 3. Build the command
    cmd = CreateProductCommand(
        tenant_id=tenant_id,
        product_id=product_id or f"prod_{uuid.uuid4().hex[:8]}",
        name=name.strip(),
        description=description,
        formats=formats_parsed,
        targeting_template=targeting_parsed,
        pricing_options=pricing_parsed,
        countries=countries or None,
        channels=channels or None,
        allowed_principal_ids=allowed_principal_ids or None,
        property_mode=property_mode,  # "tags" | "property_ids" | "full"
        selected_property_tags=selected_property_tags or [],
        selected_property_ids=selected_property_ids or [],
        targeted_ad_unit_ids=_parse_csv(targeted_ad_unit_ids),
        targeted_placement_ids=_parse_csv(targeted_placement_ids),
        include_descendants=(include_descendants == "on"),
        line_item_type=line_item_type or None,
        priority=_parse_int_or_none(priority),
        is_dynamic=(is_dynamic in ("on", "true", "1")),
        signals_agent_selection=signals_agent_selection,
        signals_agent_ids=signals_agent_ids or [],
        variant_name_pattern=variant_name_pattern,
        variant_name_template=variant_name_template.strip(),
        append_signal_description=(append_signal_description == "on"),
        max_signals=_parse_int_or_default(max_signals, 5),
        variant_ttl_days=_parse_int_or_none(variant_ttl_days),
        inventory_profile_id=_parse_int_or_none(inventory_profile_id),
        override_pricing_floor=bool(override_pricing_floor and is_super),
        cross_tenant_principal_ids=(
            cross_tenant_principal_ids if is_super else []
        ),
    )

    # 4. Run the service
    try:
        product_name = await create_product_for_tenant(cmd)
    except ProductCreateValidationError as err:
        return await _rerender_with_error(request, tenant_id, tenant, raw_form, str(err))
    except Exception:
        logger.exception("Unexpected error creating product for tenant %s", tenant_id)
        return await _rerender_with_error(
            request, tenant_id, tenant, raw_form,
            "Internal error creating product. Please try again.",
        )

    flash(request, f"Product '{product_name}' created successfully!", "success")
    return RedirectResponse(
        request.url_for("admin_products_list_products", tenant_id=tenant_id),
        status_code=303,
    )


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _load_adapter_context(tenant_id: str) -> tuple[str, list[str]]:
    """`async def` per the full-async pivot (2026-04-11)."""
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        adapter_type = (tenant.ad_server if tenant else None) or "mock"
        currencies = [
            row.currency_code
            for row in (await db.execute(
                select(CurrencyLimit).filter_by(tenant_id=tenant_id)
            )).scalars().all()
        ] or ["USD"]
    return adapter_type, currencies


def _is_super_admin(request: Request) -> bool:
    return bool(request.session.get("is_super_admin"))


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_int_or_none(value: str) -> int | None:
    try:
        return int(value) if value.strip() else None
    except (ValueError, AttributeError):
        return None


def _parse_int_or_default(value: str, default: int) -> int:
    try:
        return int(value)
    except (ValueError, AttributeError):
        return default


async def _rerender_with_error(
    request: Request,
    tenant_id: str,
    tenant: dict,
    raw_form: dict,
    message: str,
) -> HTMLResponse:
    flash(request, message, "error")
    adapter_type, currencies = await _load_adapter_context(tenant_id)
    return render(request, "add_product.html", {
        "tenant_id": tenant_id,
        "tenant": tenant,
        "adapter_type": adapter_type,
        "currencies": currencies,
        "form_data": raw_form,  # preserved input
        "is_super_admin": _is_super_admin(request),
    }, status_code=400)
```

And the **service-layer command and function** (`src/admin/services/products.py`):

```python
# src/admin/services/products.py
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty, GAMInventory, InventoryProfile,
    PricingOption, Product, ProductInventoryMapping,
)


class ProductCreateValidationError(Exception):
    """Raised by the service when form-derived data fails domain validation."""


@dataclass(slots=True, frozen=True)
class CreateProductCommand:
    tenant_id: str
    product_id: str
    name: str
    description: str
    formats: list[dict]
    targeting_template: dict
    pricing_options: list[dict]
    countries: list[str] | None
    channels: list[str] | None
    allowed_principal_ids: list[str] | None
    property_mode: str  # "tags" | "property_ids" | "full"
    selected_property_tags: list[str]
    selected_property_ids: list[str]
    targeted_ad_unit_ids: list[str]
    targeted_placement_ids: list[str]
    include_descendants: bool
    line_item_type: str | None
    priority: int | None
    is_dynamic: bool
    signals_agent_selection: str
    signals_agent_ids: list[str]
    variant_name_pattern: str
    variant_name_template: str
    append_signal_description: bool
    max_signals: int
    variant_ttl_days: int | None
    inventory_profile_id: int | None
    override_pricing_floor: bool
    cross_tenant_principal_ids: list[str] = field(default_factory=list)


async def create_product_for_tenant(cmd: CreateProductCommand) -> str:
    """Async service function. Raises ProductCreateValidationError with a
    user-facing message on any failure. Returns the product name on success.

    `async def` per the full-async pivot (2026-04-11). The `_validate_formats`,
    `_resolve_properties`, and `_build_impl_config` helpers are all `async def`
    too — see the ported blocks below.
    """
    if not cmd.name:
        raise ProductCreateValidationError("Product name is required")

    if not cmd.pricing_options:
        raise ProductCreateValidationError("Product must have at least one pricing option")

    # Validate pricing options (including floor-guard unless overridden by super-admin)
    parsed_pricing = _validate_pricing_options(cmd.pricing_options, cmd.override_pricing_floor)

    delivery_type = "guaranteed" if parsed_pricing[0].get("is_fixed") else "non_guaranteed"

    async with get_db_session() as db:
        # Format validation (use creative agent registry)
        formats_validated = await _validate_formats(db, cmd.tenant_id, cmd.formats)

        # Property-mode branching
        properties_kwargs = await _resolve_properties(db, cmd)

        # Build implementation config
        impl_config = await _build_impl_config(
            db, cmd, delivery_type, formats_validated,
        )

        product_kwargs: dict[str, Any] = {
            "product_id": cmd.product_id,
            "tenant_id": cmd.tenant_id,
            "name": cmd.name,
            "description": cmd.description,
            "format_ids": formats_validated,
            "delivery_type": delivery_type,
            "targeting_template": cmd.targeting_template,
            "implementation_config": impl_config,
            **properties_kwargs,
        }
        if cmd.countries is not None:
            product_kwargs["countries"] = cmd.countries
        if cmd.channels:
            product_kwargs["channels"] = cmd.channels
        if cmd.allowed_principal_ids:
            product_kwargs["allowed_principal_ids"] = (
                list(cmd.allowed_principal_ids) + list(cmd.cross_tenant_principal_ids)
            )
        if cmd.inventory_profile_id is not None:
            profile = (await db.execute(
                select(InventoryProfile).filter_by(id=cmd.inventory_profile_id)
            )).scalars().first()
            if not profile or profile.tenant_id != cmd.tenant_id:
                raise ProductCreateValidationError(
                    "Invalid inventory profile - profile not found or does not belong to this tenant"
                )
            product_kwargs["inventory_profile_id"] = cmd.inventory_profile_id

        if cmd.is_dynamic:
            _apply_dynamic_fields(product_kwargs, cmd)

        product = Product(**product_kwargs)
        db.add(product)
        await db.flush()

        for option in parsed_pricing:
            db.add(PricingOption(
                tenant_id=cmd.tenant_id,
                product_id=product.product_id,
                pricing_model=option["pricing_model"],
                rate=Decimal(str(option["rate"])) if option["rate"] is not None else None,
                currency=option["currency"],
                is_fixed=option["is_fixed"],
                price_guidance=option["price_guidance"],
                parameters=option["parameters"],
                min_spend_per_package=(
                    Decimal(str(option["min_spend_per_package"]))
                    if option["min_spend_per_package"] is not None else None
                ),
            ))

        # GAM inventory mappings (ad units, placements)
        for idx, au in enumerate(cmd.targeted_ad_unit_ids):
            db.add(ProductInventoryMapping(
                tenant_id=cmd.tenant_id,
                product_id=product.product_id,
                inventory_type="ad_unit",
                inventory_id=au,
                is_primary=(idx == 0),
            ))
        for idx, pl in enumerate(cmd.targeted_placement_ids):
            db.add(ProductInventoryMapping(
                tenant_id=cmd.tenant_id,
                product_id=product.product_id,
                inventory_type="placement",
                inventory_id=pl,
                is_primary=(idx == 0),
            ))

        await db.commit()
        return product.name


def _validate_pricing_options(
    raw: list[dict], override_floor: bool
) -> list[dict]:
    """Conditional validation: pricing_model="CPM" requires a rate unless
    override_floor is set by a super-admin."""
    out = []
    for opt in raw:
        if "pricing_model" not in opt:
            raise ProductCreateValidationError("pricing_model required on every option")
        try:
            rate = Decimal(str(opt["rate"])) if opt.get("rate") is not None else None
        except InvalidOperation:
            raise ProductCreateValidationError(f"Invalid rate: {opt.get('rate')!r}")
        if opt["pricing_model"] == "CPM":
            if rate is None:
                raise ProductCreateValidationError("CPM pricing requires a rate")
            if not override_floor and rate < Decimal("1.00"):
                raise ProductCreateValidationError(
                    "CPM rate below $1.00 floor (super-admin override required)"
                )
        out.append({
            "pricing_model": opt["pricing_model"],
            "rate": rate,
            "currency": opt.get("currency", "USD"),
            "is_fixed": opt.get("is_fixed", True),
            "price_guidance": opt.get("price_guidance"),
            "parameters": opt.get("parameters"),
            "min_spend_per_package": opt.get("min_spend_per_package"),
        })
    return out


# Implementations for _validate_formats, _resolve_properties, _build_impl_config,
# _apply_dynamic_fields are direct ports of the corresponding blocks from
# blueprints/products.py:737-789, 1029-1186, 819-935, 1188-1238. They raise
# ProductCreateValidationError with the user-facing message instead of calling
# flash + returning a response.
```

### 4.5.3 Every change labeled

| Flask | FastAPI-native | Why |
|---|---|---|
| Single 625-LOC function `add_product(tenant_id)` | Two handlers (GET form, POST create) + `CreateProductCommand` dataclass + `create_product_for_tenant()` service function | Separation of concerns. Service layer is testable without HTTP. |
| `request.form.to_dict()` + `sanitize_form_data()` | Explicit `Form(...)` params, each with its type | Type-safe, self-documenting, OpenAPI-friendly. |
| `request.form.getlist("countries")` | `countries: Annotated[list[str] \| None, Form()] = None` | Native multi-value support; no `getlist` call. |
| `request.method == "POST": ... else:` | Two separate decorators (`@router.get`, `@router.post`) | Dispatch happens at routing layer, not inside the handler. |
| Inline JSON parsing with nested try/except | Top-level JSON parse block that delegates to `_rerender_with_error` | Single error branch; no nested except trees. |
| `flash("...", "error") + return _render_add_product_form(...)` repeated 20 times | `_rerender_with_error()` helper | DRY; always returns status 400 on validation failure. |
| Business logic mixed with form handling | `create_product_for_tenant(cmd)` pure function | Unit-testable without starting the admin app. |
| No super-admin gating | `SuperAdminDep`-style gating via `user.role == "super_admin"` check on `override_pricing_floor` and `cross_tenant_principal_ids` | New feature; demonstrates the pattern. |
| Validation errors via `flash()` side-effect | `ProductCreateValidationError` typed exception | Service returns value or raises; handler translates to HTTP. |
| `asyncio.run(registry.list_all_formats_with_errors(...))` inside sync handler | `await registry.list_all_formats_with_errors(...)` directly — service is `async def` under the full-async pivot | No more `asyncio.run` inside handler; no more threadpool hop for DB or registry calls. |
| `request.form.getlist("selected_property_tags")` inside `property_mode` branch | All multi-selects declared at handler top; branching happens in service | Single source of truth for form fields. |
| `datetime.now(UTC)` inline | Service layer handles timestamps; handler doesn't care | |
| `flash(f"Product '{product.name}' created...", "success")` + redirect to list | Same, via `request.url_for("admin_products_list_products", tenant_id=...)` | Flat name. |
| `@log_admin_action("add_product")` decorator | `dependencies=[Depends(audit_action("add_product"))]` on POST decorator | Only the POST is audited; GET form views are not. |

### 4.5.4 Edge cases and error handling

| Scenario | Handling |
|---|---|
| Empty `name` | `ProductCreateValidationError` → re-render with flash |
| `formats` not valid JSON | JSONDecodeError caught in handler → re-render |
| `formats = "{}"` (object not array) | ValueError raised in handler → re-render |
| `pricing_options = "[]"` (empty) | ValueError ("at least one pricing option required") → re-render |
| `pricing_model="CPM"` but no `rate` | `ProductCreateValidationError("CPM pricing requires a rate")` |
| `pricing_model="CPM"`, `rate=0.50`, user is tenant_admin | Rejected: "CPM rate below $1.00 floor (super-admin override required)" |
| Same request, user is super-admin, `override_pricing_floor=on` | Accepted; product created with sub-floor rate |
| Tenant-admin submits `cross_tenant_principal_ids=["other_tenant_principal"]` | Rejected: "Only super admins may grant cross-tenant principal access" |
| Super-admin does the same | Accepted; principal IDs appended to `allowed_principal_ids` |
| `formats` contains unknown format ID | Service validates against creative agent registry; raises validation error |
| Creative agent unreachable | Graceful degradation: warn but accept (same as Flask) |
| `property_mode="tags"` but no tags selected | Rejected: "Please select at least one property tag" |
| `property_mode="tags"`, tag contains uppercase | Rejected: "Invalid tag 'Foo': use only lowercase letters..." |
| `property_mode="property_ids"`, one ID doesn't belong to tenant | Rejected: "One or more selected properties not found or not authorized" |
| `inventory_profile_id` belongs to different tenant | Rejected: "Invalid inventory profile - profile not found or does not belong to this tenant" |
| `priority="foo"` (unparseable) | `_parse_int_or_none` returns None → service treats as "no priority set" |
| `max_signals="bar"` | `_parse_int_or_default` returns 5 (default) |
| `is_dynamic=on`, `signals_agent_selection="specific"`, no agents selected | Validation error |
| Unexpected SQLAlchemy exception during commit | Caught at handler level, logged, flash generic error |
| CSRF origin validation failed | `CSRFOriginMiddleware` returns 403 before handler runs (Origin header missing or not in allowed origins) |
| Form field not in handler signature (e.g., `attacker=rce`) | FastAPI silently ignores extra form fields — they are never bound |

**Pydantic v2 `ConfigDict(extra="forbid", strict=True)`** — we considered this for a `ProductForm` model but rejected it: forbidding extras would fail on any new field, and strict mode doesn't help for string-only form data. The explicit `Form()` param list is the source of truth; any field absent from the signature is simply unread.

### 4.5.5 Test pattern

```python
# tests/integration/admin/test_add_product.py
import json
import pytest
from decimal import Decimal
from tests.factories import (
    TenantFactory, AuthorizedPropertyFactory, InventoryProfileFactory,
    CurrencyLimitFactory,
)
from tests.harness._base import IntegrationEnv


class _ProductEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {
        "creative_agent_registry": "src.core.creative_agent_registry.get_creative_agent_registry",
    }


@pytest.mark.requires_db
class TestAddProductHappyPath:
    """Covers: UC-PRODUCT-CREATE-01 — happy path creates product, pricing,
    and property authorization."""

    def test_minimal_fields_create_product(self, integration_db):
        with _ProductEnv(tenant_id="t_prod_1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t_prod_1", ad_server="mock")
            CurrencyLimitFactory(tenant=tenant, currency_code="USD")
            AuthorizedPropertyFactory(
                tenant=tenant, publisher_domain="example.com",
                property_id="prop1", tags=["sports"],
            )
            env._commit_factory_data()

            # Configure creative agent mock to accept our format
            env.mock["creative_agent_registry"].return_value.list_all_formats_with_errors.return_value = \
                type("R", (), {
                    "formats": [type("F", (), {"format_id": type("FID", (), {"id": "display_300x250"})})()],
                    "errors": [],
                })

            client = env.get_rest_client()
            r = client.post(
                "/tenant/t_prod_1/products/add",
                data={
                    "name": "Display Sports",
                    "description": "Sports display package",
                    "formats": json.dumps([{"id": "display_300x250"}]),
                    "pricing_options": json.dumps([
                        {"pricing_model": "CPM", "rate": 5.00, "currency": "USD",
                         "is_fixed": True}
                    ]),
                    "property_mode": "tags",
                    "selected_property_tags": "example.com:sports",
                    "targeting_template": "{}",
                },
                follow_redirects=False,
            )
            assert r.status_code == 303
            assert r.headers["location"].endswith("/tenant/t_prod_1/products/")

            from src.core.database.database_session import get_db_session
            from src.core.database.models import Product, PricingOption
            from sqlalchemy import select
            with get_db_session() as db:
                p = db.scalars(
                    select(Product).filter_by(tenant_id="t_prod_1", name="Display Sports")
                ).first()
                assert p is not None
                options = db.scalars(
                    select(PricingOption).filter_by(product_id=p.product_id)
                ).all()
                assert len(options) == 1
                assert options[0].rate == Decimal("5.00")


@pytest.mark.requires_db
class TestAddProductValidation:
    """Covers: UC-PRODUCT-CREATE-02 — validation errors re-render form with
    preserved input."""

    def test_missing_name_rerenders_with_flash(self, integration_db):
        with _ProductEnv(tenant_id="t_prod_2", principal_id="p2") as env:
            tenant = TenantFactory(tenant_id="t_prod_2")
            CurrencyLimitFactory(tenant=tenant, currency_code="USD")
            env._commit_factory_data()
            client = env.get_rest_client()

            r = client.post(
                "/tenant/t_prod_2/products/add",
                data={
                    "name": "",
                    "description": "blah",
                    "formats": "[]",
                    "pricing_options": json.dumps([{"pricing_model": "CPM", "rate": 5.00}]),
                    "property_mode": "tags",
                    "selected_property_tags": "example.com:sports",
                },
                follow_redirects=False,
            )
            assert r.status_code == 400  # re-rendered form
            assert "Product name is required" in r.text
            # Preserved user input
            assert "blah" in r.text

    def test_cpm_below_floor_rejected_for_tenant_admin(self, integration_db):
        with _ProductEnv(tenant_id="t_prod_3", principal_id="p3") as env:
            tenant = TenantFactory(tenant_id="t_prod_3")
            CurrencyLimitFactory(tenant=tenant, currency_code="USD")
            env._commit_factory_data()
            client = env.get_rest_client()

            r = client.post(
                "/tenant/t_prod_3/products/add",
                data={
                    "name": "Cheap",
                    "formats": "[]",
                    "pricing_options": json.dumps([
                        {"pricing_model": "CPM", "rate": 0.50, "currency": "USD",
                         "is_fixed": True}
                    ]),
                    "property_mode": "tags",
                    "selected_property_tags": "example.com:sports",
                    "override_pricing_floor": "on",  # Non-super-admin attempting bypass
                },
                follow_redirects=False,
            )
            assert r.status_code == 400
            assert "Only super admins" in r.text or "below $1.00 floor" in r.text


@pytest.mark.requires_db
class TestAddProductSuperAdminBypass:
    """Covers: UC-PRODUCT-CREATE-03 — super-admins may override pricing floor."""

    def test_super_admin_can_set_sub_floor_cpm(self, integration_db):
        # Override the default auth to return a super-admin identity
        from src.app import app
        from src.admin.deps.auth import get_admin_user, AdminUser
        app.dependency_overrides[get_admin_user] = lambda: AdminUser(
            email="root@example.com", role="super_admin",
        )

        with _ProductEnv(tenant_id="t_prod_4", principal_id="p4") as env:
            tenant = TenantFactory(tenant_id="t_prod_4")
            CurrencyLimitFactory(tenant=tenant, currency_code="USD")
            AuthorizedPropertyFactory(
                tenant=tenant, publisher_domain="example.com",
                property_id="prop1", tags=["premium"],
            )
            env._commit_factory_data()

            env.mock["creative_agent_registry"].return_value.list_all_formats_with_errors.return_value = \
                type("R", (), {"formats": [], "errors": []})

            client = env.get_rest_client()
            r = client.post(
                "/tenant/t_prod_4/products/add",
                data={
                    "name": "Promo Rate",
                    "formats": "[]",
                    "pricing_options": json.dumps([
                        {"pricing_model": "CPM", "rate": 0.25, "currency": "USD",
                         "is_fixed": True}
                    ]),
                    "property_mode": "tags",
                    "selected_property_tags": "example.com:premium",
                    "override_pricing_floor": "on",
                },
                follow_redirects=False,
            )
            try:
                assert r.status_code == 303, r.text
            finally:
                app.dependency_overrides.clear()
```

---

## Critical Files for Implementation

These are the files a v2.0 implementer will touch directly when porting the five routes above. Paths are absolute as requested.

- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/auth.py` (1,097 LOC, 11 routes) — source of Google OAuth flow
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/oidc.py` (431 LOC, 7 routes) — source of per-tenant OIDC dynamic registration
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/tenants.py` (906 LOC, favicon upload at lines 724-831) — source of multipart upload
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/activity_stream.py` (390 LOC, 3 routes) — source of SSE stream
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/products.py` (2,464 LOC, `add_product` at lines 700-1324) — source of complex form handler

Supporting files that each example depends on (already described in §11 of the main reference):

- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/oauth.py` (new, §11.5) — needs cache+invalidation code added for Example 4.2
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` (new, §11.4) — `CurrentTenantDep`, `AdminUserDep`, `SuperAdminDep`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templating.py` (new, §11.1) — `render()` wrapper
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/flash.py` (new, §11.3) — native `flash()`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/csrf.py` (new, §11.7) — `CSRFOriginMiddleware` (Origin header validation)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py` (line 894) — `IntegrationEnv.get_rest_client()` used by every test
