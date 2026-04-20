"""Admin redirect helper — 302-default wrapper (Red stub).

L0-32 Red state: this stub intentionally returns 307 (FastAPI's default)
to demonstrate that the behavioral tests fail until the Green impl lands
with the 302 default.
"""

from __future__ import annotations

from starlette.responses import RedirectResponse

# Stub value — intentionally wrong so the Red tests fail at 302 != 307.
DEFAULT_REDIRECT_STATUS = 307


def admin_redirect(url: str, status_code: int = DEFAULT_REDIRECT_STATUS) -> RedirectResponse:
    """Stub — defaults to 307 (wrong); replaced by Green impl."""
    return RedirectResponse(url, status_code=status_code)
