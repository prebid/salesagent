"""Admin helper utilities (Flask → FastAPI v2.0 L0 foundation).

Shared helpers that multiple admin routers depend on, landed at L0 so the
L1a/L1b/L1c/L1d router ports have their DRY building blocks ready.

Each module in this package has a single narrow purpose (e.g., ``redirects``
exposes the ``admin_redirect()`` wrapper that defaults to HTTP 302 to
preserve Flask's GET-after-POST semantics under FastAPI's 307-by-default
``RedirectResponse``).
"""
