"""Guard: the three signing-family wire types are constructed in ONE module (#1291 D1).

The disease this is the AST-detectable form of, in its own words from the code it
replaced (``src/core/tools/capabilities.py``)::

    _WEBHOOK_SIGNING_UNSUPPORTED = WebhookSigning(supported=False)
    _REQUEST_SIGNING_UNSUPPORTED = RequestSigning(supported=False)

Two module-level literals, emitted on both construction sites, declaring
``webhook_signing.supported: false`` for every tenant — including tenants whose
outbound webhooks #1291 C1 had already been RFC 9421-SIGNING for weeks. The wire said
"receivers MUST NOT expect a Signature header" while the socket carried one, and
nothing failed.

A literal is only the cheapest form of the bug. The general form is *any* construction
of ``RequestSigning`` / ``WebhookSigning`` / ``Identity`` outside the module that owns
their derivation: each one is a second source for a field whose whole contract is that
the side ADVERTISING it and the side ENFORCING it read one object.

So: ``src/core/signing/posture.py`` may construct them (it defines the subclasses
``RequestSigningPosture`` / ``WebhookSigningPosture`` / ``IdentityDeclaration``, derives
every field from real backing, and is what both the capabilities builder and the
outbound webhook sender read). Nowhere else in ``src/`` may.

Constructing the SUBCLASSES elsewhere is fine and is the intended path — they carry the
derivation. This guard is about reaching past them to the raw library type.
"""

import ast
from pathlib import Path

from tests.unit._architecture_helpers import iter_call_expressions

#: The library types whose construction is confined. Matched on the NAME at the call
#: site, which also catches the ``Library*`` alias convention (``LibraryIdentity(...)``)
#: and an ``as``-renamed import, because the alias is what appears in the call.
_CONFINED_TYPES = {
    "RequestSigning",
    "WebhookSigning",
    "Identity",
    "LibraryRequestSigning",
    "LibraryWebhookSigning",
    "LibraryIdentity",
}

#: The ONE module that owns their derivation.
_OWNER = Path("src/core/signing/posture.py")


def _confined_constructions_in(source: str) -> list[str]:
    """Return ``Type@line`` for every call constructing a confined library type."""
    out: list[str] = []
    for node in iter_call_expressions(ast.parse(source)):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in _CONFINED_TYPES:
            out.append(f"{name}@{node.lineno}")
    return out


def test_signing_wire_types_are_constructed_only_in_the_posture_module():
    """No second source for request_signing / webhook_signing / identity.

    There is deliberately NO allowlist parameter here. An allowlist would be the
    invitation the two deleted literals accepted: the point is that a signing block on
    the wire has exactly one producer, and a new construction site is the drift, not an
    exception to it.
    """
    offenders: list[str] = []
    for path in sorted(Path("src").rglob("*.py")):
        if path == _OWNER:
            continue
        try:
            source = path.read_text()
        except OSError:  # pragma: no cover - unreadable file is not this guard's concern
            continue
        try:
            sites = _confined_constructions_in(source)
        except SyntaxError:  # pragma: no cover - matches the sibling guards' posture
            continue
        offenders.extend(f"{path}:{site.split('@')[1]} ({site.split('@')[0]})" for site in sites)

    assert not offenders, (
        "RequestSigning/WebhookSigning/Identity must be constructed only in "
        f"{_OWNER}, which derives every field from real backing and is read by BOTH the "
        "capabilities builder and the outbound webhook sender. A second construction is a "
        "second source, and the last two were a wire that declared "
        f"webhook_signing.supported=false while production signed. Found at: {offenders}"
    )


class TestMatcherModelsTheForm:
    """Self-tests: the matcher flags the real disease shapes and passes the intended ones."""

    def test_bare_literal_construction_is_flagged(self):
        assert _confined_constructions_in("X = WebhookSigning(supported=False)") == ["WebhookSigning@1"]

    def test_library_alias_construction_is_flagged(self):
        assert _confined_constructions_in("x = LibraryIdentity(brand_json_url=u)") == ["LibraryIdentity@1"]

    def test_module_qualified_construction_is_flagged(self):
        # WOULD-BE-MISSED by a Name-only matcher: the same literal reached through the
        # generated module instead of a direct import.
        assert _confined_constructions_in("x = gen.RequestSigning(supported=False)") == ["RequestSigning@1"]

    def test_constructing_the_derived_subclass_passes(self):
        """The intended path: the subclass carries the derivation, so it is not confined."""
        assert not _confined_constructions_in("x = WebhookSigningPosture(supported=True, algorithms=[a])")

    def test_annotation_only_reference_passes(self):
        """Naming the type in a signature is not constructing one."""
        assert not _confined_constructions_in("def f(x: Identity | None) -> Identity | None:\n    return x\n")


# ---------------------------------------------------------------------------
# The hole the sweep verification found (#1291 D1)
# ---------------------------------------------------------------------------
# The guard above confines the LIBRARY types, and deliberately permits constructing
# the derived posture subclasses — that is the sanctioned shape. But the two literals
# D1 deleted (`_WEBHOOK_SIGNING_UNSUPPORTED = WebhookSigning(supported=False)` and its
# request_signing twin) would come back as SUBCLASS constructions and slip it, and the
# parity guard only detects declaration-store reads. So the exact reintroduction of the
# defect D1 fixed was pinned by nothing.
#
# The precise property: a posture fixed at IMPORT time cannot reflect key material or
# trust-root publishability, so it can only ever be a second, stale source. A
# function-local construction from real inputs is fine and is how derivation works.

#: Our derived posture types — the ones a module-level constant would freeze.
_POSTURE_TYPES = {"WebhookSigningPosture", "RequestSigningPosture", "SigningIdentity"}


def _module_level_posture_constants(source: str) -> list[str]:
    """``Name@line`` for every MODULE-LEVEL binding whose value constructs a posture."""
    out: list[str] = []
    for node in ast.parse(source).body:  # module body only — not nested scopes
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _POSTURE_TYPES:
            out.append(f"{name}@{node.lineno}")
    return out


def test_no_module_level_posture_constant_outside_the_posture_module():
    """A posture frozen at import time is a second source by construction.

    ``posture.py`` itself keeps exactly one (``UNSUPPORTED_POSTURE``), which is the
    agent-level "we do not verify" answer and depends on nothing. Everywhere else, a
    posture must be built from what is actually backing it at the time of the request.
    """
    offenders: list[str] = []
    for path in sorted(Path("src").rglob("*.py")):
        if path == _OWNER:
            continue
        try:
            source = path.read_text()
        except OSError:  # pragma: no cover - unreadable file is not this guard's concern
            continue
        try:
            sites = _module_level_posture_constants(source)
        except SyntaxError:  # pragma: no cover - matches the sibling guards' posture
            continue
        offenders.extend(f"{path}:{site.split('@')[1]} ({site.split('@')[0]})" for site in sites)

    assert not offenders, (
        "a signing posture is bound at MODULE level outside "
        f"{_OWNER}: {offenders}. Frozen at import, it cannot reflect key material or "
        "trust-root publishability — which is exactly how the deleted "
        "_WEBHOOK_SIGNING_UNSUPPORTED / _REQUEST_SIGNING_UNSUPPORTED literals declared "
        "webhook_signing.supported=false while production signed. Build the posture per "
        "request from its real backing instead."
    )


class TestModuleLevelMatcherModelsTheForm:
    """Self-tests, including the shape that slipped the two existing guards."""

    def test_the_exact_deleted_literal_is_flagged(self):
        source = "_WEBHOOK_SIGNING_UNSUPPORTED = WebhookSigningPosture(supported=False)"
        assert _module_level_posture_constants(source) == ["WebhookSigningPosture@1"]

    def test_annotated_module_constant_is_flagged(self):
        source = "X: WebhookSigningPosture = WebhookSigningPosture(supported=False)"
        assert _module_level_posture_constants(source) == ["WebhookSigningPosture@1"]

    def test_module_qualified_construction_is_flagged(self):
        source = "X = posture.RequestSigningPosture(supported=False)"
        assert _module_level_posture_constants(source) == ["RequestSigningPosture@1"]

    def test_function_local_construction_passes(self):
        """Derivation itself — built per call from real inputs, not frozen."""
        source = "def f(row):\n    return WebhookSigningPosture(supported=bool(row))\n"
        assert _module_level_posture_constants(source) == []

    def test_annotation_only_reference_passes(self):
        assert _module_level_posture_constants("X: WebhookSigningPosture | None = None") == []
