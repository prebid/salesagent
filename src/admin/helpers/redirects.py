"""Admin redirect helper — 302-default wrapper around ``RedirectResponse``.

Canonical spec: ``L0-implementation-plan-v2.md`` §L0-32
(new in v2; see also ``async-audit/frontend-deep-audit.md`` F3 and
``async-audit/testing-strategy.md`` Tier 1 ``admin_redirect`` row).

Flask's ``flask.redirect()`` defaulted to HTTP 302 ("Found") — the
status that triggers GET-after-POST in every browser and is the bedrock
of the Post-Redirect-Get (PRG) idiom. FastAPI's
``starlette.responses.RedirectResponse`` defaults to HTTP 307
("Temporary Redirect") which PRESERVES the original method and body.
At L1+, 338 call sites of ``redirect(...)`` port from Flask to FastAPI.
Porting each one to bare ``RedirectResponse`` would silently flip the
semantics from 302 to 307 — POSTs would replay after a redirect and
break form submissions that rely on PRG to prevent double-submits.

``admin_redirect()`` is the mechanical replacement — same call shape as
Flask's ``redirect()``, 302 by default, 307 overrideable when a handler
genuinely needs POST-body preservation (rare — the
``ApproximatedExternalDomainMiddleware`` is the current example).

At L0 this module is scaffold-only — nothing imports it yet. L1c/L1d
routers pick it up as their mechanical translation of ``redirect(...)``.

Obligations asserted at the test layer
--------------------------------------
1. Default status code is 302 (not 307 — the ``RedirectResponse``
   default must NOT leak through).
2. ``status_code=307`` overrideable explicitly for POST-body preservation.
3. Query string preserved verbatim through the redirect target.
4. Absolute URLs (``https://...``) pass through unchanged in ``Location``.
5. Relative URLs produced by ``url_for(...)`` pass through unchanged in
   ``Location`` (the helper does NOT rewrite or validate paths).

.. warning::

    **Open-redirect footgun — caller responsibility.**

    ``admin_redirect(url, ...)`` performs **NO URL validation**. Passing a
    caller-controlled value straight into ``url`` opens a classic
    open-redirect hole: an attacker crafts ``?next=https://evil.example``,
    the handler echoes ``next`` into ``admin_redirect``, and the user is
    bounced off-site with the admin session cookie already sent.

    Callers that accept ``?next=``, ``?return_to=``, ``?redirect_to=`` or
    any other user-controlled redirect target MUST validate the value
    before passing it in:

    - **Reject absolute/external URLs** (``http://``, ``https://``, any
      scheme — keep only relative paths starting with ``/``).
    - **Reject protocol-relative URLs** (values starting with ``//`` —
      browsers treat ``//evil.example`` as ``https://evil.example``).
    - **Reject path-traversal inputs** (``..``, backslashes, CRLF) that
      could break out of the intended path space.
    - **Reject non-admin-prefixed paths** in admin contexts — if the
      caller is an admin route, refuse to redirect outside ``/admin/`` or
      ``/tenant/...`` unless the route has an explicit cross-surface
      reason to do so.

    For L0 the helper stays permissive because its only L0-wired callers
    produce their ``url`` from ``url_for(...)`` (a server-side route
    name — not user input). A later layer that introduces ``?next=``
    handling MUST add a validator (e.g. ``is_safe_admin_redirect(url)``)
    and call it before ``admin_redirect``.
"""

from __future__ import annotations

from starlette.responses import RedirectResponse

# 302 "Found" — the HTTP status Flask's ``redirect()`` emitted by default
# and that every browser implements as a method-rewriting redirect
# (POST→GET). PRG idiom depends on this. Do NOT change without a full
# audit of every caller.
DEFAULT_REDIRECT_STATUS = 302


def admin_redirect(url: str, status_code: int = DEFAULT_REDIRECT_STATUS) -> RedirectResponse:
    """Return a ``RedirectResponse`` defaulting to HTTP 302.

    Args:
        url: target location. Passed verbatim to ``RedirectResponse`` —
            the helper performs NO path rewriting and NO validation.
            Callers are expected to build the target via ``url_for(...)``
            (for named admin routes) or to supply a trusted absolute URL.
        status_code: HTTP status to emit. Defaults to 302 (GET-after-POST
            semantics, matching Flask's ``redirect()``). Override to 307
            only when POST-body preservation is explicitly required
            (e.g., external-domain proxies — see
            ``ApproximatedExternalDomainMiddleware``).

    Returns:
        A ``RedirectResponse`` whose ``Location`` header is ``url`` and
        whose status code is ``status_code``.
    """
    return RedirectResponse(url, status_code=status_code)
