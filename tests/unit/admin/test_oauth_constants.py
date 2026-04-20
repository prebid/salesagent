"""Unit tests for L0-11 ``src/admin/oauth.py`` — OAuth callback constants.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.6.

Per Critical Invariant #6 (``CLAUDE.md`` §Critical Invariants), the
following OAuth redirect URIs are **byte-immutable contracts** with
Google Cloud Console and per-tenant OIDC provider configs. Any drift —
trailing slash, case, prefix, or ``{tenant_id}`` path-parameter — yields
``redirect_uri_mismatch`` at login time and kills auth:

    /admin/auth/google/callback
    /admin/auth/oidc/callback          (NO {tenant_id} — tenant is in session)
    /admin/auth/gam/callback           (WITH /admin prefix)

This test is the Red/Green gate for L0-11 Pattern (b): the module's
absence IS the obligation at L0. L1a tightens to assert the constants
match the registered route paths; L0 asserts the constants exist with
byte-exact values.

Also verified here: ``GOOGLE_CLIENT_NAME = "google"`` (Authlib client
name used at registration), and that the module exposes an
``authlib.integrations.starlette_client.OAuth`` instance as ``oauth``.
"""

from __future__ import annotations


def test_oauth_module_exports_required_callback_path_constants():
    """The three OAuth callback paths match Invariant #6 verbatim."""
    from src.admin import oauth as mod

    assert mod.OAUTH_GOOGLE_CALLBACK_PATH == "/admin/auth/google/callback"
    assert mod.OAUTH_OIDC_CALLBACK_PATH == "/admin/auth/oidc/callback"
    assert mod.OAUTH_GAM_CALLBACK_PATH == "/admin/auth/gam/callback"


def test_oauth_module_exports_google_client_name():
    """``GOOGLE_CLIENT_NAME`` is the Authlib registration key — MUST be 'google'."""
    from src.admin import oauth as mod

    assert mod.GOOGLE_CLIENT_NAME == "google"


def test_oauth_module_exports_oauth_instance():
    """Module provides an Authlib ``OAuth`` instance for starlette."""
    from authlib.integrations.starlette_client import OAuth

    from src.admin import oauth as mod

    assert isinstance(mod.oauth, OAuth)


def test_google_client_name_constant_matches_registration_behavior(monkeypatch):
    """``init_oauth()`` with env vars set registers a client under GOOGLE_CLIENT_NAME."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid-test")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec-test")

    from src.admin import oauth as mod

    # Reset to known state — other tests may have already registered.
    if hasattr(mod.oauth, "_clients"):
        mod.oauth._clients.pop(mod.GOOGLE_CLIENT_NAME, None)

    mod.init_oauth()

    # Authlib exposes registered clients by attribute name matching register(name=...).
    client = getattr(mod.oauth, mod.GOOGLE_CLIENT_NAME, None)
    assert client is not None, f"OAuth client registered under {mod.GOOGLE_CLIENT_NAME!r} is missing after init_oauth()"


def test_invariant_6_paths_have_no_tenant_id_in_oidc_nor_missing_admin_prefix_in_gam():
    """Explicit regression of the two Invariant #6 FE-3 audit corrections.

    * ``/admin/auth/oidc/callback`` — NO ``{tenant_id}`` segment
      (tenant context lives in the session, not the URL).
    * ``/admin/auth/gam/callback`` — WITH the ``/admin/`` prefix
      (the prefix is part of the registered URI).
    """
    from src.admin import oauth as mod

    assert "{tenant_id}" not in mod.OAUTH_OIDC_CALLBACK_PATH
    assert mod.OAUTH_OIDC_CALLBACK_PATH.count("/callback") == 1
    assert mod.OAUTH_GAM_CALLBACK_PATH.startswith("/admin/")
    assert mod.OAUTH_GAM_CALLBACK_PATH.endswith("/callback")
