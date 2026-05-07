"""Guard: every tenant-scoped route in admin blueprints uses ``@require_tenant_access``.

The decorator is the single structural gate for:

- Tenant-isolation auth (test_user/super_admin/OAuth/header-auth resolution).
- Embedded mutation blocking (``_maybe_block_embedded_write`` rejects writes
  on platform-managed tenants — preview OR permanent — at the boundary).

A route under ``/tenant/<tenant_id>/...`` without the decorator silently
bypasses both gates: it would accept any authenticated session and let
header-auth callers POST against an embedded tenant. This test scans the
admin blueprint AST and fails CI if any new route lands without the
decorator.

Allowlist exists for pre-existing exceptions (none expected — flagged here
so future maintainers can opt out only with a comment justifying why).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Pre-existing routes that don't use ``require_tenant_access`` today.
# Two reasons a route is allowlisted:
#
# A. Public-by-design — authentication entry points that cannot require
#    auth (because they're how a user authenticates in the first place).
#    These are correct as-is; no follow-up needed.
#
# B. Uses a different auth pattern (``require_auth()``) — checks login
#    but not tenant scoping or embedded mutations. Pre-existing debt;
#    each entry should migrate to ``require_tenant_access()`` over time.
#    FIXME comments mark these; they should shrink, never grow.
#
# When you fix one, remove its entry — the stale-entry test will remind you.
ALLOWLIST: set[tuple[str, str]] = {
    # ── (A) Auth-flow entry points: must be reachable without auth ──
    ("src/admin/blueprints/auth.py", "tenant_login"),
    ("src/admin/blueprints/auth.py", "tenant_google_auth"),
    ("src/admin/blueprints/auth.py", "gam_authorize"),
    ("src/admin/blueprints/oidc.py", "test_initiate"),
    ("src/admin/blueprints/oidc.py", "login"),
    # ── (B) Pre-existing ``require_auth()`` users — should migrate ──
    # FIXME(embedded-mutation-audit): use require_tenant_access() so the
    # embedded write-block fires and tenant isolation is enforced.
    ("src/admin/blueprints/api.py", "revenue_chart_api"),
    ("src/admin/blueprints/api.py", "get_tenant_products"),
    ("src/admin/blueprints/api.py", "get_product_suggestions"),
    ("src/admin/blueprints/core.py", "reactivate_tenant"),
    ("src/admin/blueprints/inventory.py", "orders_browser"),
    ("src/admin/blueprints/inventory.py", "check_inventory_sync"),
    ("src/admin/blueprints/inventory.py", "analyze_ad_server_inventory"),
    # publisher_partners.py: fixed in #65 — all 5 routes now use
    # ``@require_tenant_access(api_mode=True)``.
}

BLUEPRINTS_DIR = Path(__file__).parent.parent.parent / "src" / "admin" / "blueprints"


def _route_decorator_target(node: ast.AST) -> str | None:
    """Return the dotted target of an ``@xx_bp.route(...)`` decorator, or None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "route":
        # ``foo_bp.route(...)`` — only flag tenant-scoped paths.
        return func.attr
    return None


def _decorator_uses_require_tenant_access(decorators: list[ast.expr]) -> bool:
    """True if any decorator on the function is ``require_tenant_access(...)``
    or ``@require_tenant_access`` (no-arg form)."""
    for d in decorators:
        if isinstance(d, ast.Call):
            f = d.func
            if isinstance(f, ast.Name) and f.id == "require_tenant_access":
                return True
            if isinstance(f, ast.Attribute) and f.attr == "require_tenant_access":
                return True
        elif isinstance(d, ast.Name) and d.id == "require_tenant_access":
            return True
        elif isinstance(d, ast.Attribute) and d.attr == "require_tenant_access":
            return True
    return False


def _route_path_arg(call: ast.Call) -> str | None:
    """Return the literal route path (first positional arg) of a route decorator."""
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _is_tenant_scoped(path: str, blueprint_url_prefix: str | None) -> bool:
    """Tenant-scoped routes have ``<tenant_id>`` somewhere in their final URL.

    Either the route literal contains ``<tenant_id>`` or the blueprint's
    ``url_prefix`` does (then every route in that blueprint inherits the
    scope).
    """
    if "<tenant_id>" in path:
        return True
    if blueprint_url_prefix and "<tenant_id>" in blueprint_url_prefix:
        return True
    return False


def _blueprint_url_prefix(tree: ast.Module) -> str | None:
    """Find the ``url_prefix=`` kwarg on the ``Blueprint(...)`` constructor."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "Blueprint":
            for kw in node.keywords:
                if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        return kw.value.value
    return None


def _scan_blueprint(path: Path) -> list[tuple[str, str, int]]:
    """Return list of (file, function, line) for tenant-scoped routes
    missing ``require_tenant_access``."""
    source = path.read_text()
    tree = ast.parse(source)
    blueprint_prefix = _blueprint_url_prefix(tree)
    findings: list[tuple[str, str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # The route decorator ``@xx_bp.route("...")`` carries the path; the
        # auth decorator is separate. Both live in the same decorator list.
        route_paths: list[str] = []
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and _route_decorator_target(d) == "route":
                p = _route_path_arg(d)
                if p is not None:
                    route_paths.append(p)
        if not route_paths:
            continue
        if not any(_is_tenant_scoped(p, blueprint_prefix) for p in route_paths):
            continue
        if _decorator_uses_require_tenant_access(node.decorator_list):
            continue
        rel = str(path.relative_to(path.parent.parent.parent.parent))
        findings.append((rel, node.name, node.lineno))
    return findings


class TestTenantRoutesDecorated:
    def test_all_tenant_routes_use_require_tenant_access(self):
        """No tenant-scoped admin route may skip ``@require_tenant_access``.

        Catches the failure mode where a future PR adds a route under
        ``/tenant/<tenant_id>/...`` without the auth+mutation gate, silently
        regressing both isolation and embedded-write protection.
        """
        all_findings: list[tuple[str, str, int]] = []
        for bp in sorted(BLUEPRINTS_DIR.glob("*.py")):
            if bp.name == "__init__.py":
                continue
            all_findings.extend(_scan_blueprint(bp))

        violations = [(f, fn, line) for f, fn, line in all_findings if (f, fn) not in ALLOWLIST]

        if violations:
            lines = [
                "Tenant-scoped routes missing @require_tenant_access:",
                "",
            ]
            for f, fn, line in violations:
                lines.append(f"  {f}:{line} in {fn}()")
            lines.append("")
            lines.append(
                "Add @require_tenant_access() (or @require_tenant_access(api_mode=True) "
                "for JSON endpoints) to gate tenant isolation + embedded mutations. "
                "If the route is genuinely public, document why and add to ALLOWLIST in "
                "tests/unit/test_architecture_tenant_routes_decorated.py."
            )
            raise AssertionError("\n".join(lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted entry must still be a real violation.

        If someone fixes a route by adding ``@require_tenant_access`` but
        forgets to remove the allowlist entry, this test catches the stale
        entry so the allowlist shrinks over time.
        """
        all_violations = set()
        for bp in sorted(BLUEPRINTS_DIR.glob("*.py")):
            if bp.name == "__init__.py":
                continue
            for f, fn, _line in _scan_blueprint(bp):
                all_violations.add((f, fn))

        stale = ALLOWLIST - all_violations
        if stale:
            lines = [
                "Stale allowlist entries (route now has @require_tenant_access — remove from allowlist):",
                "",
            ]
            for f, fn in sorted(stale):
                lines.append(f"  ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(lines))
