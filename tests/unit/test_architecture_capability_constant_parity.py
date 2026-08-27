"""Guard: an advertised capability value must be DERIVED from its enforced constant.

Regression guard for the idempotency replay-TTL drift: ``get_adcp_capabilities``
once advertised ``replay_ttl_seconds=86400`` as a bare literal while the *enforced*
TTL lived in ``DEFAULT_REPLAY_TTL``. A literal and a constant are two sources for one
value and drift independently — the buyer is then told a window the server does not
enforce. Every ``replay_ttl_seconds=`` site MUST reference the constant
(``int(DEFAULT_REPLAY_TTL.total_seconds())``), never a hardcoded number.

This is the AST-detectable slice of semantic single-source-of-truth. A value-counting
"duplicate literal" guard is deliberately NOT used: 86400 ("seconds per day") and 3600
("seconds per hour") have many legitimate unrelated uses in src/, so counting literals
would be almost all false positives. Keying on the capability KEYWORD instead is exact.
The non-AST-detectable slice (one invariant computed two ways) stays a review concern.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import iter_call_expressions

# Capability keywords whose value must be derived from an enforced constant, not a literal.
_DERIVED_CAPABILITY_KEYWORDS = {"replay_ttl_seconds"}

# Builders that must NOT READ THE DECLARATION STORE. That is the property this guard
# checks, stated precisely -- it is NOT "these blocks can never be tenant-declared",
# which would be false of ``request_signing`` (#1291 D1 makes it declarable) and would
# make the guard assert the opposite of that design.
#
# Two different reasons a builder belongs here:
#
# * ``_build_account_block`` / ``_build_adcp_block`` -- their output must stay derived
#   from its ENFORCED source and must never become tenant-declarable at all (#1592 T1a).
#   ``account.sandbox`` comes from the ``account_sandbox`` column and
#   ``account.supported_billing`` from ``resolve_supported_billing``, the same function
#   the sync_accounts billing gate calls, so the advertised policy and the enforced one
#   cannot diverge. The ``adcp.*`` block derives from SUPPORTED_ADCP_MAJORS/VERSIONS plus
#   ``get_idempotency_posture``. A tenant that could declare ``supported_billing`` would
#   advertise a billing policy sync_accounts then refuses to honour.
# * ``_build_signing_blocks`` -- ``request_signing`` IS tenant-declared, but the store is
#   read in exactly one place (``posture_for_tenant``) and the builder receives the
#   resolved posture object. Listing it here pins that shape: a second parse of the
#   declaration inside the builder is the two-source bug the deleted
#   ``_REQUEST_SIGNING_UNSUPPORTED`` / ``_WEBHOOK_SIGNING_UNSUPPORTED`` literals were.
#
# A structural check, not a data-driven one, precisely because a future migration would
# pick a store key no fixture could predict -- and because CapabilityDeclarations'
# extra="forbid" rejects an unknown key at parse time, so a "declare an override and
# assert it is ignored" test would never reach its assertion.
_DERIVATION_ONLY_BUILDERS = ("_build_account_block", "_build_adcp_block", "_build_signing_blocks")
_DECLARATION_STORE_NAMES = ("capability_declarations", "CapabilityDeclarations")


def _literal_capability_sites_in(source: str) -> list[str]:
    """Return ``keyword@line`` for capability keywords assigned a bare numeric literal."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in iter_call_expressions(tree):
        for kw in node.keywords:
            if (
                kw.arg in _DERIVED_CAPABILITY_KEYWORDS
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, (int, float))
                and not isinstance(kw.value.value, bool)
            ):
                out.append(f"{kw.arg}@{kw.value.lineno}")
    return out


def test_capability_values_are_derived_not_literal():
    """No advertised capability keyword may be a hardcoded number anywhere in src/.

    Core invariant: one source of truth for the replay window — the enforced
    constant. A literal in the capability response drifts from enforcement
    silently (the #1b bug class).
    """
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        try:
            for site in _literal_capability_sites_in(path.read_text()):
                offenders.append(f"{path}:{site.split('@')[1]} ({site.split('@')[0]})")
        except SyntaxError:
            continue
    assert not offenders, (
        "Advertised capability values must derive from their enforced constant "
        "(e.g. replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds())), not a "
        f"bare literal, at: {offenders}"
    )


def _declaration_reads_in_builders(source: str) -> list[str]:
    """Return ``builder@line`` for any read of the declaration store inside a
    derivation-only builder."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in _DERIVATION_ONLY_BUILDERS):
            continue
        for sub in ast.walk(node):
            name = None
            if isinstance(sub, ast.Name):
                name = sub.id
            elif isinstance(sub, ast.Attribute):
                name = sub.attr
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                name = sub.value
            if name in _DECLARATION_STORE_NAMES:
                out.append(f"{node.name}@{sub.lineno}")
    return out


def test_derivation_only_builders_do_not_read_the_declaration_store():
    """The listed builders must take resolved values, never read the store themselves.

    Core invariant: the capabilities response may only advertise what some other
    part of the system ENFORCES. `supported_billing` is shared with the
    sync_accounts gate; `sandbox` is the provisioning gate's own column. Letting a
    tenant declare either would let config advertise a policy production refuses to
    honour -- the exact honesty inversion #1592 T1a exists to prevent.

    For ``_build_signing_blocks`` the invariant is one step removed and just as binding:
    ``request_signing`` IS declared, but by ONE reader upstream, so the builder that puts
    it on the wire must see a posture object and not a declaration document.
    """
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        try:
            for site in _declaration_reads_in_builders(path.read_text()):
                builder, line = site.split("@")
                offenders.append(f"{path}:{line} ({builder})")
        except SyntaxError:
            continue
    assert not offenders, (
        "these builders must receive resolved values and derive from their enforced "
        f"sources, never read the capability declaration store, at: {offenders}"
    )


class TestDerivationOnlyMatcherModelsTheForm:
    """Self-tests for the derivation-only matcher."""

    def test_declaration_read_in_builder_is_flagged(self):
        src = "def _build_account_block(tenant):\n    return tenant.get('capability_declarations')\n"
        assert _declaration_reads_in_builders(src)

    def test_declaration_read_outside_those_builders_is_ignored(self):
        src = "def _get_adcp_capabilities_impl(tenant):\n    return tenant.get('capability_declarations')\n"
        assert not _declaration_reads_in_builders(src)

    def test_attribute_access_form_is_also_flagged(self):
        # A future refactor to TenantContext attribute access must not slip past
        # a string-literal-only matcher.
        src = "def _build_adcp_block(tenant):\n    return tenant.capability_declarations\n"
        assert _declaration_reads_in_builders(src)

    def test_derived_builder_passes(self):
        src = "def _build_account_block(tenant):\n    return tenant.get('account_sandbox', True)\n"
        assert not _declaration_reads_in_builders(src)


class TestMatcherModelsTheForm:
    """Self-tests: the matcher flags a literal and passes a derived expression."""

    def test_bare_literal_is_flagged(self):
        assert _literal_capability_sites_in("Idempotency(supported=True, replay_ttl_seconds=86400)")

    def test_derived_expression_passes(self):
        src = "Idempotency(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds()))"
        assert not _literal_capability_sites_in(src)

    def test_unrelated_literal_keyword_ignored(self):
        # A bare number on an UNREGISTERED keyword is not this guard's concern.
        assert not _literal_capability_sites_in("Foo(timeout_seconds=86400)")


# ── D1: declaration-driven fields must not regress to literals ────────────────
#
# The complement of test_account_and_adcp_blocks_are_not_declaration_driven above.
# That check pins fields that must STAY derived; this one pins fields that must
# STAY declaration-driven.
#
# Without it, salesagent-3xmz's D1-13/D1-14 MIGRATE rows (supported_protocols and
# specialisms, moved from bare literals to the capability-declaration store) have
# no class-level protection: a future edit could revert either to
# `[SupportedProtocol.media_buy]` and only the BDD scenarios would notice. The
# disease scan called for exactly this ("extended to ban NEW literal capability
# emissions"); it is that half.

# Response fields the tenant-resolved path must build from the declaration store.
_DECLARATION_DRIVEN_FIELDS = {"supported_protocols", "specialisms"}

# The impl whose tenant-resolved response construction is in scope. The NO-TENANT
# response is deliberately excluded: it has no tenant, so it cannot read a
# tenant-scoped declaration, and it must keep emitting the defaults byte-for-byte.
_TENANT_RESPONSE_IMPL = "_get_adcp_capabilities_impl"


def _fields_never_declaration_driven(source: str) -> list[str]:
    """Return declaration-driven fields the impl NEVER builds from the store.

    Per-EMISSION checking is impossible here and it matters why: the no-tenant
    minimal response is an early ``return`` INSIDE ``_get_adcp_capabilities_impl``
    (capabilities.py :265-274), and it legitimately keeps
    ``list(_DEFAULT_SUPPORTED_PROTOCOLS)`` -- there is no tenant to read a
    tenant-scoped declaration from, and that response must stay byte-identical.
    So "every emission reads the store" would be false for correct code.

    The enforceable invariant is therefore: for each field, AT LEAST ONE emission
    in the impl reads the declarations. That catches the regression that actually
    matters -- the store silently stops reaching the wire -- while leaving the
    tenant-less path alone. It does NOT catch a partial regression where one of
    several tenant-path emissions is reverted; the BDD accept scenarios
    (local-uc010-declaration-backing.feature) grade that, and they fail on a
    mutation that ignores the declaration (verified).
    """
    tree = ast.parse(source)
    out: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if func.name != _TENANT_RESPONSE_IMPL:
            continue
        emitted: set[str] = set()
        store_driven: set[str] = set()
        for node in iter_call_expressions(func):
            for kw in node.keywords:
                if kw.arg not in _DECLARATION_DRIVEN_FIELDS or kw.value is None:
                    continue
                emitted.add(kw.arg)
                if any(isinstance(n, ast.Name) and "declaration" in n.id.lower() for n in ast.walk(kw.value)):
                    store_driven.add(kw.arg)
        out.extend(sorted(emitted - store_driven))
    return out


def test_declaration_driven_fields_are_not_literal():
    """``supported_protocols``/``specialisms`` must reach the wire from the store.

    They were literals until salesagent-3xmz moved them behind
    ``CapabilityDeclarations``, which is what lets a tenant with a backed
    measurement catalog or a backed specialism advertise it -- and what makes the
    backing validator reachable at all. Reverting them to literals would silently
    disconnect the store from the wire while every unrelated test stayed green.
    """
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        try:
            offenders.extend(f"{path} ({field})" for field in _fields_never_declaration_driven(path.read_text()))
        except SyntaxError:
            continue
    assert not offenders, (
        "supported_protocols/specialisms must be built from the capability declaration "
        f"store somewhere in {_TENANT_RESPONSE_IMPL}, not emitted only as literals, at: {offenders}"
    )


class TestDeclarationDrivenMatcherModelsTheForm:
    """Self-tests: models the REAL two-emission shape, not an idealized one."""

    _NO_TENANT = (
        "    if not tenant:\n"
        "        return Response(\n"
        "            supported_protocols=list(_DEFAULT_SUPPORTED_PROTOCOLS),\n"
        "            specialisms=list(_DEFAULT_SPECIALISMS),\n"
        "        )\n"
    )

    def _impl(self, tenant_path: str) -> str:
        return "def _get_adcp_capabilities_impl(req):\n" + self._NO_TENANT + tenant_path

    def test_store_driven_tenant_path_passes(self):
        """Both emissions present; the tenant one reads the store -> clean."""
        src = self._impl(
            "    return Response(\n"
            "        supported_protocols=(\n"
            "            declarations.emitted_supported_protocols(_DEFAULT_SUPPORTED_PROTOCOLS)\n"
            "            if declarations else list(_DEFAULT_SUPPORTED_PROTOCOLS)\n"
            "        ),\n"
            "        specialisms=declarations.emitted_specialisms(_DEFAULT_SPECIALISMS),\n"
            "    )\n"
        )
        assert not _fields_never_declaration_driven(src)

    def test_reverted_tenant_path_is_flagged(self):
        """The regression this guard exists for: tenant path back to a literal."""
        src = self._impl(
            "    return Response(\n"
            "        supported_protocols=list(_DEFAULT_SUPPORTED_PROTOCOLS),\n"
            "        specialisms=list(_DEFAULT_SPECIALISMS),\n"
            "    )\n"
        )
        assert sorted(_fields_never_declaration_driven(src)) == ["specialisms", "supported_protocols"]

    def test_partial_revert_is_flagged(self):
        """One field reverted, one still store-driven -> only the reverted one."""
        src = self._impl(
            "    return Response(\n"
            "        supported_protocols=declarations.emitted_supported_protocols(D),\n"
            "        specialisms=list(_DEFAULT_SPECIALISMS),\n"
            "    )\n"
        )
        assert _fields_never_declaration_driven(src) == ["specialisms"]

    def test_no_tenant_literal_alone_is_not_penalized(self):
        """WOULD-BE-MISSED inverse: a function with ONLY the tenant-less emission
        (no tenant path at all) must not be flagged -- otherwise the guard would
        demand a store read where there is no tenant to read one from."""
        src = "def _get_adcp_capabilities_impl(req):\n" + self._NO_TENANT
        assert sorted(_fields_never_declaration_driven(src)) == ["specialisms", "supported_protocols"]

    def test_unrelated_field_ignored(self):
        src = "def _get_adcp_capabilities_impl(req):\n    return Response(last_updated=now())\n"
        assert not _fields_never_declaration_driven(src)
