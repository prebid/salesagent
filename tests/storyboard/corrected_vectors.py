"""Request-signing conformance vectors whose bodies REACH a verifier.

Filed upstream as adcontextprotocol/adcp#7567. The pinned corpus at
``test-vectors/request-signing/`` ships stub bodies — ``{"plan_id":"plan_001"}`` for
``create_media_buy``, ``{}`` for ``sync_creatives`` — that no conformant AdCP seller can
parse. A seller validates the payload before it authenticates the caller, so a stub body
is answered ``INVALID_REQUEST`` and the RFC 9421 checklist never runs. Measured against
this agent: all 27 graded ``signed_requests`` checks failed with ``got 200
(error="(none)")`` — a JSON-RPC error inside an HTTP 200, not a verifier verdict.

That is a defect in the FIXTURES, not in the storyboard: every vector's
``expected_outcome`` is about the signature, and a body the seller cannot parse decides
the response before the signature is read. This module produces the corrected corpus —
same vectors, same mutations, same expected outcomes, bodies the seller's schema
accepts — and :func:`corrected_compliance_tree` materializes it as a compliance
directory the runner is pointed at with ``--compliance-dir``.

THE PINNED TREE IS NEVER EDITED. It is byte-pinned by the drift guard over
``MANIFEST.json``; this writes a separate tree beside it.

What is corrected, and nothing else
-----------------------------------
``request.body`` alone. Not a header, not a URL, not a ``verifier_capability``, not an
``expected_outcome``. The single fault each vector grades lives in its Signature
headers or its ``verifier_capability``, and every one of those goes through untouched —
so ``negative/002``'s wrong tag is still wrong, ``negative/005``'s ``rsa-pss-sha512`` is
still disallowed, and ``negative/010``'s falsified ``Content-Digest`` is still false.

The body is safe to change for two reasons the runner's own builder establishes
(``@adcp/sdk`` ``lib/testing/storyboard/request-signing/builder.mjs``):

* every mutation that re-signs does so over the request it is ABOUT to send, and
  ``stripSignatureHeaders`` drops the fixture's ``Signature``, ``Signature-Input`` and
  ``Content-Digest`` first — so a re-signed row's shipped header bytes are discarded
  anyway and cannot be invalidated by a body edit;
* every mutation that passes the fixture headers through verbatim (021-028) is graded
  at checklist step 1 or step 0, both of which are decided before any byte of the body
  is read.

Bodies come from :func:`tests.helpers.signing_vectors.transplant_body`, which is the
repo's existing answer to this same problem one layer down (the L2 suite transplants for
the identical reason). Reusing it means there is ONE conformant ``create_media_buy``
payload in this repo, graded against the pinned schema by ``tests/factories/request.py``,
rather than a second one typed out here that could drift from the pin.

Timestamps are PINNED rather than taken from the factory's ``now``-relative defaults: a
fixture file is read by the runner in a different process from the one that wrote it, and
a corpus proposed upstream has to be reproducible from its own bytes. The ceiling is
stated where they are defined.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tests.helpers.signing_vectors import create_media_buy_body

#: Vendored-corpus ids keyed the way ``load_signing_vectors`` keys them
#: (``"negative/001-no-signature-header"``), derived from a vector file's own path so the
#: corrected tree and the vendored snapshot cannot disagree about which row is which.
_BUCKETS = ("positive", "negative")

#: The window every corrected ``create_media_buy`` body declares.
#:
#: FIXED, not ``now``-relative. Two reasons: the runner reads these files in a different
#: process from the one that generated them, and a corpus offered upstream must be
#: reproducible from its own bytes rather than from the clock of whoever ran the
#: generator.
#:
#: KNOWN CEILING: a seller that refuses a flight starting in the past will, after
#: 2027-01-04, answer these bodies on a business rule rather than on the signature. That
#: does not change what any vector grades HERE — the storyboard grades the HTTP status and
#: the ``WWW-Authenticate`` code, and an MCP seller answers a business rejection inside an
#: HTTP 200 exactly as it answers a success — but an upstream corpus should carry a
#: convention (a relative offset, or a per-release bump) rather than a literal. Raised in
#: the upstream issue.
_FLIGHT_START = "2027-01-04T00:00:00Z"
_FLIGHT_END = "2027-02-01T00:00:00Z"

#: The advertiser every corrected body names.
#:
#: RESERVED, never registrable. ``.example`` is reserved by RFC 2606 §2 and is therefore
#: guaranteed unreachable, so no agent running these vectors can send a payload naming a
#: host under a stranger's control. It matches the style the rest of the payload already
#: uses (``acmeoutdoor.example``, ``pinnacle-agency.example``).
#:
#: Still named HERE rather than inherited from ``SAMPLE_BRAND``, even though that constant
#: is now reserved too: this value is published in a corpus proposed to the spec, where
#: ``brand.example`` says what the field IS to a reader with no knowledge of this repo's
#: fixtures. The repo-side default is free to change without moving what upstream reviews.
_BRAND = {"domain": "brand.example"}

#: ``request.url``'s final segment names the AdCP operation the runner dispatches: in MCP
#: mode it becomes ``params.name`` of the ``tools/call`` envelope
#: (``builder.mjs`` ``extractOperationFromVectorUrl``). A vector's body therefore has to
#: satisfy THAT operation's schema, not ``create_media_buy``'s — which is why this is a
#: dispatch and not one builder.
#:
#: Rows whose operation is absent from this table keep their body VERBATIM. That covers
#: ``negative/028`` (a JSON-RPC ``tasks/cancel`` envelope posted to ``/mcp``, whose body IS
#: the thing that puts it in the protocol-method namespace) and the canonicalization
#: positives whose URL names no operation at all (``.../resource/%e2%98%83/item``).
_CORRECTED_OPERATIONS = ("create_media_buy", "update_media_buy", "sync_creatives")


def _operation(vector: dict[str, Any]) -> str:
    """The AdCP operation a vector's URL names — its final path segment."""
    return vector["request"]["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _create_media_buy_body(vector_id: str) -> dict[str, Any]:
    """The L2 transplant's payload, with its two ``now``-relative fields pinned.

    :func:`~tests.helpers.signing_vectors.create_media_buy_body` already carries the three
    clauses this corpus needs — the conformant baseline from the factory, the vector's own
    ``plan_id``, and ``negative/027``'s webhook-credential block re-spelled to the pinned
    schema — so it is reused whole rather than reproduced.
    """
    payload: dict[str, Any] = dict(create_media_buy_body(vector_id))
    payload["start_time"] = _FLIGHT_START
    payload["end_time"] = _FLIGHT_END
    payload["brand"] = dict(_BRAND)
    return payload


def _update_media_buy_body(vector_id: str, vector: dict[str, Any]) -> dict[str, Any]:
    """``negative/027``'s body, on ``update_media_buy`` where its URL puts it.

    The vector grades one thing: a request REGISTERING webhook credentials must be signed
    even though ``required_for`` is empty (security.mdx @ v3.1.1 :1462-1465). What the
    seller reads is the PRESENCE of ``push_notification_config.authentication``, so the
    block survives — re-spelled through the same factory ``transplant_body`` uses, because
    the fixture writes the pre-3.1 singular ``authentication.scheme`` and a ``credentials``
    shorter than the pin's ``minLength: 32``. Dropping it would invert the vector into one
    that must NOT be rejected; keeping the unparseable spelling would leave the request
    answered ``INVALID_REQUEST``, which is the defect being fixed.

    The media buy it names survives too — it is the request's subject, the body's
    counterpart of the URL bytes the canonicalization vectors preserve.
    """
    from tests.factories.request import UpdateMediaBuyRequestFactory
    from tests.factories.webhook import PushNotificationConfigRequestFactory, hmac_authentication

    sent = json.loads(vector["request"].get("body") or "{}")
    payload: dict[str, Any] = UpdateMediaBuyRequestFactory.payload(
        idempotency_key=_idempotency_key(vector_id),
        media_buy_id=sent.get("media_buy_id", "mb_001"),
        brand=dict(_BRAND),
    )
    registered = sent.get("push_notification_config") or {}
    if registered.get("authentication") is not None:
        payload["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
            url=registered["url"], authentication=hmac_authentication()
        )
    return payload


def _sync_creatives_body(vector_id: str) -> dict[str, Any]:
    """``negative/011``'s body, on ``sync_creatives`` where its URL puts it.

    The vector grades a syntactically invalid ``Signature-Input`` at checklist step 1,
    which is decided before the operation matters at all — but the request still has to
    reach the verifier, and a seller answers ``{}`` with ``INVALID_REQUEST`` first.
    """
    from tests.factories import SyncCreativesRequestFactory

    return SyncCreativesRequestFactory.payload(idempotency_key=_idempotency_key(vector_id))


def _idempotency_key(vector_id: str) -> str:
    """The same key ``transplant_body`` mints, for the rows it does not build.

    Imported rather than re-derived: ``negative/016`` sends its request TWICE and the two
    submissions must be byte-identical, which is a property of THAT spelling.
    """
    from tests.helpers.signing_vectors import _idempotency_key as mint

    return mint(vector_id)


def corrected_body(vector_id: str, vector: dict[str, Any]) -> str | None:
    """The body *vector* must carry to reach a verifier, or None to keep it verbatim."""
    operation = _operation(vector)
    if operation not in _CORRECTED_OPERATIONS:
        return None
    if operation == "create_media_buy":
        payload = _create_media_buy_body(vector_id)
    elif operation == "update_media_buy":
        payload = _update_media_buy_body(vector_id, vector)
    else:
        payload = _sync_creatives_body(vector_id)
    return json.dumps(payload, separators=(",", ":"))


#: ``"body": "<json string>"`` — the ONE member :func:`correct_vector_text` rewrites.
#: ``(?:[^"\\]|\\.)*`` is a JSON string body: any character that is neither a quote nor a
#: backslash, or any backslash-escape, so an escaped quote inside the payload does not end
#: the match.
_BODY_MEMBER = re.compile(r'("body"\s*:\s*)"(?:[^"\\]|\\.)*"')


def correct_vector_text(vector_id: str, source: str) -> str:
    """*source* with its ``request.body`` replaced — every other byte untouched.

    A TEXT edit, not a re-serialization, and that is the whole point. Round-tripping
    through ``json.dumps`` reformats members this change does not touch: the corpus writes
    short arrays inline (``"jwks_ref": ["test-ed25519-2026"]``) and a dumper expands them,
    so a body-only correction arrived as a 326-line diff across 37 files with the actual
    edit buried in it. Upstream cannot review that, and neither can a reader asking whether
    a vector's graded fault survived.

    Exactly one member is rewritten, and a corpus where that stops being true is an ERROR
    rather than a silent partial edit — a second ``"body"`` key would mean the vector shape
    changed and this transformation no longer describes it.
    """
    vector = json.loads(source)
    body = corrected_body(vector_id, vector)
    if body is None:
        return source
    replaced, count = _BODY_MEMBER.subn(lambda m: m.group(1) + json.dumps(body), source, count=2)
    if count != 1:
        raise ValueError(f"{vector_id}: expected exactly one 'body' member, found {count}")
    return replaced


def _discard(tree: Path) -> None:
    """Get *tree* out of the way, WITHOUT needing permission to delete its contents.

    The in-network runner builds this tree from inside the container, where the process is
    root; a later host-side rebuild then runs as an ordinary user and ``shutil.rmtree``
    dies on the first root-owned file. Invisible in CI, where the uid matches both times,
    and reliably confusing locally — a raw ``PermissionError`` several frames inside
    ``shutil`` says nothing about containers.

    RENAMING is the fix, because it needs write permission on the PARENT directory rather
    than on the files: the parent is created by whoever ran first, but the rebuild only
    ever has to move a name within it. The renamed-aside copy is then deleted
    best-effort — it is garbage at that point, and failing to remove garbage must not fail
    a rebuild that has already succeeded.

    If even the rename is refused the parent itself is unwritable, which no amount of
    cleverness here fixes, so it is reported with the remedy instead of a stack trace.
    """
    # Sweep what earlier calls could not delete. A discard whose contents are root-owned
    # survives its own run's best-effort removal, so it is retried here — the first run
    # that HAS permission clears it, and the directory cannot grow without bound.
    for leftover in tree.parent.glob(f"{tree.name}.discarded-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    if not tree.exists():
        return
    # A FRESH empty directory per call, not a name derived from the pid: two calls in one
    # process share a pid, so a pid-suffixed name collides with the caller's OWN
    # undeletable leftover and the rename fails ENOTEMPTY — which the handler below would
    # then report as an unwritable parent, blaming the wrong thing. Renaming ONTO an empty
    # directory is permitted, so mkdtemp gives a target that cannot collide.
    discarded = Path(tempfile.mkdtemp(prefix=f"{tree.name}.discarded-", dir=tree.parent))
    try:
        tree.rename(discarded)
    except OSError as exc:
        raise RuntimeError(
            f"cannot rebuild {tree}: its parent is not writable by this user ({exc}). "
            f"It was most likely written from inside a container as root — remove it with "
            f"`sudo rm -rf {tree}` and re-run."
        ) from exc
    shutil.rmtree(discarded, ignore_errors=True)


def corrected_compliance_tree(source: Path, dest: Path) -> Path:
    """Write *source* to *dest* with the request-signing bodies corrected.

    The whole compliance tree is copied, not just the vectors: ``--compliance-dir`` is
    the runner's root for storyboards, schemas and test-kits alike, so pointing it at a
    vectors-only directory would take every OTHER storyboard out of the run — which is
    the same false green, arriving as "fewer checks" instead of "wrong checks".

    Rebuilt from scratch on every call. A stale tree left by an earlier revision of this
    module would be graded as if it were current, which is exactly the
    measured-not-inferred rule the storyboard suite exists to hold.
    """
    _discard(dest)
    shutil.copytree(source, dest)

    vectors = dest / "test-vectors" / "request-signing"
    if not vectors.is_dir():
        raise FileNotFoundError(f"no request-signing vectors under {vectors}")
    for bucket in _BUCKETS:
        for path in sorted((vectors / bucket).glob("*.json")):
            source_text = path.read_text()
            corrected = correct_vector_text(f"{bucket}/{path.stem}", source_text)
            if corrected != source_text:
                path.write_text(corrected)
    return dest
