"""Shared test contracts for admin-UI base-template context.

Extracted from two tests that independently declared the same 11-key
frozenset (DRY — see CLAUDE.md §DRY invariant):

- ``tests/unit/admin/test_templates_dep.py`` — unit test for
  ``get_base_context``.
- ``tests/unit/admin/test_template_context_completeness.py`` — integration
  test that exercises the dep via a mini FastAPI + TestClient.

The frozenset below is the canonical source. Both tests import it, and a
change to the contract touches exactly one file. The dep under test
(``src/admin/deps/templates.py::get_base_context``) is the production
source of truth; this frozenset mirrors its 11-key output for assertion
symmetry.

Per ``flask-to-fastapi-foundation-modules.md`` §11.4 (admin base context
contract across ~54 admin pages).
"""

from __future__ import annotations

ADMIN_BASE_CTX_KEYS: frozenset[str] = frozenset(
    {
        "messages",
        "support_email",
        "sales_agent_domain",
        "user_email",
        "user_authenticated",
        "user_role",
        "test_mode",
        "session",
        "g_test_mode",
        "csrf_token",
        "get_flashed_messages",
    }
)
"""11-key contract that ``get_base_context`` (and therefore every admin
template via ``base.html``) must return — no more, no less."""


__all__ = ["ADMIN_BASE_CTX_KEYS"]
