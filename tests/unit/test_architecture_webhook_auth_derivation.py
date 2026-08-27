"""Guard: the webhook auth mode is derived in ONE place (#1291 C2).

The disease, in the words of the scan that found it
(``.claude/code-review/salesagent-z6nr.19/scan.sh``): *the webhook "auth mode" is derived
ad hoc at each call site instead of from one shared scheme->mode derivation, so the same
concept is spelled three different ways and the spellings can disagree*.

It had already cost a live bug twice. Before C1 the three senders spelled the legacy
scheme ``"HMAC-SHA256"`` / ``"Bearer"`` / ``"bearer"``, so a bearer-registered receiver
reached a different arm depending on which sender fired. C2 found the fourth spelling:
the proof-of-control challenge derived ``delivery_auth.mode`` itself, so the mode we
TOLD a receiver we would use and the arm :func:`build_webhook_sender` actually took were
two independent readings of the same registration.

The canonical home is ``src/core/signing/webhook_sender_factory.py`` — :func:`declared_auth`
(the one pluck), :func:`legacy_auth_mode` (which arm we take) and :func:`delivery_auth_mode`
(which arm we announce), all off one ``_HMAC_SCHEMES`` table. Everywhere else, reading a
scheme in order to decide a mode is a second source.

Both detected shapes are inherited from the scan's own sub-scans rather than defined a
second time here:

* the PLURAL pluck ``authentication.get("schemes")`` and the SINGULAR drift
  ``getattr(auth, "scheme", ...)`` / ``x.authentication.scheme`` / ``AuthenticationInfo(scheme=...)``
  — sub-scans S1 and S2;
* a bare mode literal (``"HMAC-SHA256"``, ``"hmac_sha256"``, ``"bearer"``, ``"rfc9421"``)
  inside a scope that ALSO reads a scheme/auth field — sub-scan S5, narrowed by that
  co-occurrence so it catches a scheme->mode MAPPING rather than every use of the word
  "bearer" in the tree.

What is deliberately NOT flagged, because each is the sanctioned path rather than the
disease:

* ``DeclaredAuth.scheme`` read off the shared derivation's OUTPUT (a bare ``x.scheme``);
* the transport-boundary singular->plural hop in the A2A server, which WRITES
  ``auth["schemes"]`` — a store, not a derivation;
* the canonical home itself.
"""

import ast

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root

#: The ONE module that owns the derivation.
_CANONICAL_HOME = "src/core/signing/webhook_sender_factory.py"

#: The plural field every AdCP type spells the receiver's declared schemes with.
_PLURAL_FIELDS = frozenset({"schemes"})

#: The singular spelling: A2A's protobuf ``AuthenticationInfo``. Legitimate at that
#: transport's own boundary, a drift anywhere else.
_SINGULAR_FIELDS = frozenset({"scheme"})

#: The fourth spelling, from the admin UI's webhook form (sub-scan S4). Only used to
#: recognize a mode-deriving scope; it is not itself a pluck.
_FORM_FIELDS = frozenset({"auth_type", "auth_config"})

_SCHEME_FIELDS = _PLURAL_FIELDS | _SINGULAR_FIELDS | _FORM_FIELDS

#: The wire spellings of a delivery auth mode, lower-cased.
_MODE_LITERALS = frozenset({"hmac-sha256", "hmac_sha256", "hmac", "bearer", "rfc9421"})

#: Pre-existing derivations, keyed ``(file, enclosing function)`` so line drift cannot
#: silence the guard. Each entry names the work that removes it. Allowlist SHRINKS ONLY.
#:
#: FIXME(#1291): every entry below is a second reading of a registration's auth. They are
#: deferred, not accepted — see the disposition table on the C2 review for the ticket that
#: owns each group.
ALLOWLIST = {
    # --- The persist-the-scheme sites: they pluck schemes[0] to write it to the
    # PushNotificationConfig row. A different operation from deriving a mode, which is why
    # C2 did not widen to cover them; they should terminate in the parameterized upsert.
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
    ("src/services/delivery_webhook_scheduler.py", "_send_report_for_media_buy"),
    ("src/admin/blueprints/creatives.py", "_call_webhook_for_creative_status"),
    ("src/core/context_manager.py", "_send_push_notifications"),
    # --- The singular read that started this: binding on `scheme` alone leaves the
    # credential-rotation half stale, so it cannot adopt the shared derivation as-is.
    ("src/core/tools/accounts.py", "_proof_tuple"),
    # --- A2A protobuf singular `scheme`, at and around that transport's own boundary.
    ("src/a2a_server/adcp_a2a_server.py", "on_get_task_push_notification_config"),
    ("src/a2a_server/adcp_a2a_server.py", "on_create_task_push_notification_config"),
    ("src/a2a_server/adcp_a2a_server.py", "on_list_task_push_notification_configs"),
    # --- The admin webhook form's own scheme->mode branch (the fourth spelling).
    ("src/admin/blueprints/principals.py", "register_webhook"),
}


def _read_field(node: ast.AST) -> str | None:
    """The auth field *node* READS, or ``None`` if it reads none.

    Reads only — a store such as ``auth["schemes"] = [...]`` is the A2A boundary's
    normalization hop, not a derivation, and must not be flagged.
    """
    if isinstance(node, ast.Call):
        func = node.func
        # S1: `x.get("schemes")` / `x.get("schemes", default)`. Restricted to the plural
        # and form fields: a bare `.get("scheme")` is also how ASGI reads a URL scheme.
        if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in _PLURAL_FIELDS | _FORM_FIELDS:
                return str(first.value)
        # S1/S2: `getattr(x, "schemes", ...)` / `getattr(x, "scheme", ...)`.
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
        # S2: `AuthenticationInfo(scheme=...)`.
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "AuthenticationInfo":
            for keyword in node.keywords:
                if keyword.arg in _SINGULAR_FIELDS:
                    return keyword.arg
    # S1: `x["schemes"]`, load only.
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        index = node.slice
        if isinstance(index, ast.Constant) and index.value in _PLURAL_FIELDS:
            return str(index.value)
    # S2: `x.authentication.scheme`. A bare `x.scheme` is NOT matched — that is also how a
    # consumer reads `DeclaredAuth.scheme`, the sanctioned OUTPUT of the shared derivation.
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in _SINGULAR_FIELDS:
        parent = node.value
        parent_name = getattr(parent, "attr", None) or getattr(parent, "id", None)
        if parent_name == "authentication":
            return node.attr
    return None


def find_scheme_plucks(tree: ast.AST) -> list[int]:
    """Lines that read a scheme off a registration in order to act on it."""
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if _read_field(node) in _PLURAL_FIELDS | _SINGULAR_FIELDS and hasattr(node, "lineno")
        }
    )


def _scopes(tree: ast.Module) -> list[list[ast.AST]]:
    """Every function body, plus the module body with functions and classes removed."""
    out: list[list[ast.AST]] = [
        list(ast.walk(node)) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    module_level: list[ast.AST] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        module_level.extend(ast.walk(stmt))
    out.append(module_level)
    return out


def find_mode_literals(tree: ast.Module) -> list[int]:
    """Lines where a mode literal sits in a scope that also reads a scheme field.

    The co-occurrence is what makes this a scheme->mode MAPPING rather than any use of the
    string. It is why ``mcp_client._build_auth_headers`` — which maps its own ``"type"``
    field to a header and never touches a scheme — is not swept in.
    """
    hits: set[int] = set()
    for scope in _scopes(tree):
        if not any(_read_field(node) in _SCHEME_FIELDS for node in scope):
            continue
        hits.update(
            node.lineno
            for node in scope
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in _MODE_LITERALS
        )
    return sorted(hits)


def _enclosing_function(tree: ast.Module, lineno: int) -> str:
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    if not candidates:
        return "<module>"
    return min(candidates, key=lambda n: (n.end_lineno or n.lineno) - n.lineno).name


def _derivations() -> set[tuple[str, str]]:
    """``(file, function)`` for every ad hoc auth-mode derivation in ``src/``."""
    root = repo_root()
    found: set[tuple[str, str]] = set()
    for path in sorted((root / "src").rglob("*.py")):
        relative = str(path.relative_to(root))
        if relative == _CANONICAL_HOME:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):  # pragma: no cover - matches the sibling guards
            continue
        for lineno in find_scheme_plucks(tree) + find_mode_literals(tree):
            found.add((relative, _enclosing_function(tree, lineno)))
    return found


@pytest.mark.arch_guard
def test_no_new_ad_hoc_webhook_auth_derivation():
    """A new scheme->mode derivation fails immediately."""
    new = _derivations() - ALLOWLIST
    assert not new, (
        "a webhook auth mode is derived outside "
        f"{_CANONICAL_HOME}: {sorted(new)}. Call declared_auth() for the pluck and "
        "legacy_auth_mode() / delivery_auth_mode() for the mode, so the arm we take and "
        "the arm we announce cannot disagree — three spellings of the same scheme is how "
        "a bearer-registered receiver reached a different sender than the one it "
        "registered for (#1291 C1/C2)."
    )


@pytest.mark.arch_guard
def test_allowlist_entries_still_exist():
    """Stale-entry detection: a fixed site must leave the allowlist."""
    assert_violations_match_allowlist(
        _derivations(),
        ALLOWLIST,
        fix_hint="Remove fixed entries from ALLOWLIST — it may only shrink.",
    )


class TestMatcherModelsTheForm:
    """Self-tests: the matcher flags the real disease shapes and passes the intended ones."""

    def test_the_plural_pluck_is_flagged(self):
        assert find_scheme_plucks(ast.parse('s = authentication.get("schemes", [])')) == [1]

    def test_the_plural_pluck_via_subscript_is_flagged(self):
        assert find_scheme_plucks(ast.parse('s = authentication["schemes"]')) == [1]

    def test_the_singular_getattr_drift_is_flagged(self):
        # WOULD-BE-MISSED by an attribute-only matcher, and it is the exact shape of the
        # `_proof_tuple` read that started this.
        assert find_scheme_plucks(ast.parse('x = getattr(auth, "scheme", None)')) == [1]

    def test_schemes_reached_via_getattr_is_flagged(self):
        assert find_scheme_plucks(ast.parse('x = getattr(auth, "schemes", [])')) == [1]

    def test_the_nested_authentication_scheme_is_flagged(self):
        assert find_scheme_plucks(ast.parse("x = params.authentication.scheme or None")) == [1]

    def test_the_protobuf_construction_is_flagged(self):
        assert find_scheme_plucks(ast.parse("x = AuthenticationInfo(scheme=s, credentials=c)")) == [1]

    def test_reading_the_shared_derivations_output_passes(self):
        """`DeclaredAuth.scheme` is the sanctioned path, not a second reading."""
        assert find_scheme_plucks(ast.parse("auth = declared_auth(a)\nx = auth.scheme is not None\n")) == []

    def test_the_transport_boundary_store_passes(self):
        """The A2A singular->plural hop WRITES schemes; a store is not a derivation."""
        source = 'if "scheme" in auth and "schemes" not in auth:\n    auth["schemes"] = [auth.pop("scheme")]\n'
        assert find_scheme_plucks(ast.parse(source)) == []

    def test_the_asgi_url_scheme_passes(self):
        """`scope.get("scheme", "http")` is a URL scheme, not an auth scheme."""
        assert find_scheme_plucks(ast.parse('s = scope.get("scheme", "http")')) == []


class TestLiteralMatcherModelsTheForm:
    """Self-tests for the mode-literal half, which is string-matched and so needs them."""

    def test_a_mode_branch_beside_a_scheme_read_is_flagged(self):
        source = (
            'def f(form):\n    auth_type = form.get("auth_type")\n    if auth_type == "hmac_sha256":\n        pass\n'
        )
        assert find_mode_literals(ast.parse(source)) == [3]

    def test_a_case_variant_literal_is_flagged(self):
        # WOULD-BE-MISSED by a case-sensitive match: the pre-C1 spellings differed only
        # in case, which is the whole reason the three senders disagreed.
        source = 'def f(a):\n    s = a.get("schemes")\n    return "HMAC_SHA256" if s else "Bearer"\n'
        assert find_mode_literals(ast.parse(source)) == [3]

    def test_a_mode_literal_with_no_scheme_read_passes(self):
        """Header construction from an unrelated `type` field is a different concept."""
        source = 'def f(auth):\n    if auth.get("type") == "bearer":\n        return "Authorization"\n'
        assert find_mode_literals(ast.parse(source)) == []

    def test_the_module_scope_is_checked_too(self):
        source = 'CFG = auth.get("schemes")\nMODE = "rfc9421"\n'
        assert find_mode_literals(ast.parse(source)) == [2]
