"""CI guard: assert the adcp SDK pin targets the expected AdCP spec version."""

import adcp

from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION


def test_adcp_spec_version_matches_pin() -> None:
    """Verify SDK pin targets the spec version this codebase expects.

    Failure here means the adcp Python SDK pin in pyproject.toml has shifted
    to a version that targets a different AdCP spec version. Either revert
    the pin or follow docs/adcp-spec-version.md to update
    EXPECTED_SPEC_VERSION and the related references it lists.
    """
    actual = adcp.get_adcp_spec_version()
    assert actual == EXPECTED_SPEC_VERSION, (
        f"adcp SDK targets spec {actual}, but this codebase expects "
        f"{EXPECTED_SPEC_VERSION}. See docs/adcp-spec-version.md for "
        f"reconciliation steps."
    )


def test_the_vendored_schema_tree_matches_the_pin() -> None:
    """The vendored schema fixtures must be the version the SDK pin targets.

    ``tests/fixtures/adcp_schemas_pinned/<version>/`` carries the schema documents the
    trust-root tests validate against, and the version is a DIRECTORY NAME — a literal.
    The guard above pins the SDK against ``EXPECTED_SPEC_VERSION``; nothing pinned the
    neighbouring schema tree, so a bump could move the SDK and the served ``$schema``
    while the documents were still graded against the previous version's fixtures.

    That is the same mechanism the vector guard already applies, applied to the tree
    beside it (#1757). It fails LOUDLY at bump time — which is the point: the bump
    procedure in ``docs/adcp-spec-version.md`` then has something to tell you to do.
    """
    from pathlib import Path

    tree = Path(__file__).resolve().parents[1] / "fixtures" / "adcp_schemas_pinned"
    versions = {child.name for child in tree.iterdir() if child.is_dir() and child.name[0].isdigit()}

    assert versions == {EXPECTED_SPEC_VERSION}, (
        f"the vendored schema tree carries {sorted(versions)} but the codebase is pinned to "
        f"{EXPECTED_SPEC_VERSION}. Re-vendor the fixtures for the pinned version (see "
        f"tests/fixtures/adcp_schemas_pinned/_refresh.py) and update the references "
        f"docs/adcp-spec-version.md lists — a stale tree grades our documents against the "
        f"wrong spec while every version literal in production has already moved."
    )


def test_the_published_jwk_model_preserves_members_it_does_not_declare() -> None:
    """``AgentSigningKey`` must round-trip ``key_ops`` and ``adcp_use``.

    ``build_jwks`` routes every published key through this model so the JWKS is
    schema-valid BY CONSTRUCTION (#1757). That is only safe because the model sets
    ``extra: allow``: it DECLARES [kid, kty, alg, use, crv, x, y, n, e, revoked_at] and
    NOT ``key_ops`` or ``adcp_use``, both of which ``adcp.signing.keygen`` emits and which
    ``_published_jwk`` passes through verbatim from the stored ``public_jwk``.

    If a future SDK tightened this model to ``extra: forbid`` or ``ignore``, the
    conversion would either start rejecting our own keys or SILENTLY STOP PUBLISHING two
    members of every JWK — a published-key regression with no other test to catch it,
    because the members are ones our own builder never names.

    This is the pin for that assumption. It belongs beside the spec-version guards because
    it is the same kind of claim: something about the pinned SDK that our code depends on
    and that a bump could move.
    """
    from adcp.types.generated_poc.core.agent_signing_key import AgentSigningKey

    declared = set(AgentSigningKey.model_fields)
    assert {"key_ops", "adcp_use"}.isdisjoint(declared), (
        "AgentSigningKey now DECLARES key_ops/adcp_use — this pin can be simplified, but "
        f"check the emitted types first. Declared: {sorted(declared)}"
    )

    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "key_ops": ["verify"],
        "adcp_use": "request-signing",
        "kid": "adcp-pin-check",
        "x": "0" * 43,
    }
    served = AgentSigningKey.model_validate(jwk).model_dump(mode="json", exclude_none=True)

    assert served == jwk, (
        "the published JWK must survive the model round-trip byte-for-byte; "
        f"key_ops/adcp_use are undeclared and rely on extra='allow'. Got: {served}"
    )


def test_the_brand_agent_variant_is_still_the_one_we_bind() -> None:
    """``BrandDiscovery3`` must still be the BRAND-AGENT variant we serve.

    ``build_brand_json`` validates through it, but the name is POSITIONAL — generated as
    "the third oneOf variant in generation order", not "the brand-agent document". A
    regeneration that adds or reorders variants rebinds that name to a DIFFERENT SHAPE
    while the import keeps resolving.

    ``extra: forbid`` catches the common case loudly, but only when the new variant is not
    a SUPERSET of the three keys we emit ($schema, agents, last_updated). A superset binds
    silently, and we would start serving a document graded against the wrong variant.

    So the BINDING is pinned here by name, for the same reason
    ``_BRAND_AGENT_KEYS`` in tests/integration/test_trust_root_documents.py pins the
    document's keys — that constant's own docstring says a document drifting into another
    variant should be "caught by name rather than by a downstream validation failure".
    This is that argument applied one level up, to the model we bind rather than the dict
    we emit.

    MUTATION: point the alias at ``BrandDiscovery5`` and this goes RED.
    """
    from src.core.signing.trust_root import LibraryBrandAgentDocument

    assert set(LibraryBrandAgentDocument.model_fields) == {
        "field_schema",
        "version",
        "agents",
        "brand_agent",
        "contact",
        "data_subject_contestation",
        "last_updated",
    }, (
        "the variant bound as the brand-agent document no longer has that field set — a "
        "regeneration has renumbered the oneOf variants. Re-point the alias in "
        f"src/core/signing/trust_root.py. Got: {sorted(LibraryBrandAgentDocument.model_fields)}"
    )
    assert LibraryBrandAgentDocument.model_fields["field_schema"].alias == "$schema", (
        "$schema must be carried as field_schema with an alias — build_brand_json dumps "
        "by_alias, and without the alias the wire would carry 'field_schema'"
    )


def test_published_timestamps_render_one_spelling() -> None:
    """Every timestamp in every trust-root document renders through ONE formatter.

    ASSERTS RENDERED BYTES, deliberately. A "parses as a datetime" test sails straight past
    the failure this pins: ``2026-08-21T19:59:59.115782Z`` and
    ``...115782+00:00`` are the same instant, both valid RFC 3339, and the pinned schemas
    constrain neither (``adagents.json`` :110-113 says only
    ``{"type": "string", "format": "date-time"}``). Only a byte comparison can see the
    difference — which is how the regression was caught, by an assertion that compared
    bytes rather than parsing them.

    THE FAULT THIS EXISTS FOR (#1757). ``src/core/signing/_rfc3339.py`` was created because
    this codebase already shipped two formatters that "happen to agree today" — its own
    docstring says so. Routing two of the four documents through pinned SDK models
    re-created that from the other side: pydantic renders ``...Z``, ``rfc3339()`` renders
    ``...+00:00``. Because ``build_adagents_json`` embeds the same ``_published_jwk``
    output while adagents itself is not converted, ONE ORIGIN was serving both spellings.

    So the invariant is "exactly one spelling", not "the old spelling" — and it is held by
    construction: the serializers CALL ``rfc3339()`` rather than re-implementing
    ``isoformat()``, because a second implementation that agrees today is the fault itself.

    MUTATION: drop either ``field_serializer`` and this goes RED on a ``Z``.
    """
    from datetime import UTC, datetime

    from src.core.signing._rfc3339 import rfc3339
    from src.core.signing.trust_root import PublishedBrandDocument, PublishedSigningKey

    moment = datetime(2026, 8, 21, 19, 59, 59, 115782, tzinfo=UTC)
    expected = "2026-08-21T19:59:59.115782+00:00"
    assert rfc3339(moment) == expected, f"the one formatter changed shape: {rfc3339(moment)}"

    # JWKS revoked_at — also embedded in adagents.json's signing_keys[].
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "kid": "adcp-ts-pin",
        "x": "0" * 43,
        "revoked_at": rfc3339(moment),
    }
    served_jwk = PublishedSigningKey.model_validate(jwk).model_dump(mode="json", exclude_none=True)
    assert served_jwk["revoked_at"] == expected, (
        f"JWKS revoked_at must render through rfc3339; got {served_jwk['revoked_at']!r}"
    )

    # brand.json last_updated.
    brand = {
        "$schema": f"https://adcontextprotocol.org/schemas/{EXPECTED_SPEC_VERSION}/brand.json",
        "agents": [
            {
                "type": "sales",
                "url": "https://x.example.com/a2a",
                "id": "x_a2a",
                "jwks_uri": "https://x.example.com/.well-known/jwks.json",
                "description": "d",
            }
        ],
        "last_updated": rfc3339(moment),
    }
    served_brand = PublishedBrandDocument.model_validate(brand).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert served_brand["last_updated"] == expected, (
        f"brand.json last_updated must render through rfc3339; got {served_brand['last_updated']!r}"
    )
    assert "$schema" in served_brand, "the $schema alias must survive by_alias serialization"

    # The revocation list's two timestamps are unconverted and call rfc3339 directly
    # (revocation_list.py :104-105) — pinned here so a future conversion of THAT document
    # cannot fork the format either.
    assert rfc3339(moment) == expected
