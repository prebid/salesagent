# Verifying our outbound webhooks

For counterparties receiving webhooks from this sales agent. It covers how our webhook
signatures are constructed, where to fetch the key that verifies them, and what the
deprecated HMAC scheme means for you.

Companion pages: [Signing posture and key discovery](posture-and-discovery.md) and the
operator-facing [signing key runbook](../operations/signing-key-runbook.md).

AdCP spec version: **3.1.1**.

## What we send

Every AdCP webhook leaves this agent through one outbound boundary, so there is exactly
one answer to "how is this signed" regardless of which subsystem raised the event —
delivery notifications, creative status changes, order approvals, and the
proof-of-control challenge all take the same path.

Signed deliveries carry an RFC 9421 `Signature` and `Signature-Input` header.

**`content-digest` is always covered** — the webhook profile pins it, so a signature of
ours that does not cover the body digest does not exist. Verify the digest against the
bytes you received, not just the signature.

`content-type` is covered too, because we always send it explicitly rather than leaving
it to the HTTP client. A verifier that rejects a signature whose covered components omit
`content-type` is behaving correctly, and we do not produce one.

The bytes we sign are the bytes we send. The payload is serialized once and handed to the
signer and the socket unchanged — the signature never covers a re-encoding you did not
receive.

### The profile tag

We declare `webhook_signing.profile` in `get_adcp_capabilities`, and its value is
`adcp/webhook-signing/v1`. It is the same constant the signer emits as the `tag=`
parameter, so you can statically validate the declared profile against what arrives on
the wire — that match is the whole point of the field.

**`profile` is emitted only when `supported` is true.** The spec makes `supported` the
only required member of the block, and `supported: false` is a promise that no
`Signature` header will be sent at all. A declared profile alongside that would be a
claim about a header we never send, so we omit it. If you see `webhook_signing` without a
`profile`, check `supported` — the absence is deliberate and it means "we do not sign",
not "we sign with an unstated profile".

### Algorithms

`ed25519` and `ecdsa-p256-sha256`. `ed25519` is what we provision by default.

## Where to fetch the key

Two documents answer two different questions. Use the right one.

**Operator-side webhook signatures** — the ones this agent sends as itself — are verified
through the same chain as request signatures: `identity.brand_json_url` → brand.json →
`agents[].jwks_uri` → JWKS. See
[the walk](posture-and-discovery.md#key-discovery), including why a bare
`.well-known/jwks.json` lookup is not the discovery mechanism.

**Sell-side webhook delivery** is pinned by the publisher: `authorized_agents[].signing_keys[]`
in `<publisher-domain>/.well-known/adagents.json`. That pin is authoritative for exactly
that tuple and is **not** part of the request-signing chain.

Both are built from one query against the same publishable key set, so they cannot
disagree about which keys exist.

A revoked key stays published for a grace period and carries a revocation marker into
both documents. **Do not trust a key that carries the marker**, even though you fetched it
successfully — see
[Revoked keys stay published](posture-and-discovery.md#revoked-keys-stay-published-and-carry-a-marker).

## Legacy HMAC-SHA256, and its removal

Before RFC 9421 signing, webhook authentication was a shared secret: a receiver
registered `HMAC-SHA256` with a credential, and we signed deliveries with it. That path
still exists and is reachable only when a receiver explicitly registers for it. RFC 9421
is the default for every registration that declares no `authentication` block at all —
legacy is never a silent fallback.

`webhook_signing.legacy_hmac_fallback` tells you whether this build still has that arm.

**It is a property of the build, not a declaration.** No operator of this agent can turn
it on or off; it is derived from whether the legacy sending arm is present in the running
code. This matters for how you plan: it is a migration path you **wait out, not one you
opt out of**. There is no configuration flag to ask an operator for, and no per-tenant
setting that will change the answer.

The AdCP-legacy HMAC scheme is the 3.x fallback and is **removed in AdCP 4.0**. Two
consequences worth planning around now:

- A `Bearer` or `Basic` registration is answered with a token-bearing header, because the
  legacy arm has one token constructor. If you registered `Basic` expecting a `Basic`
  header, you will receive `Authorization: Bearer <token>`. That is a deliberate
  consequence of routing every legacy scheme through one sender, and it is logged rather
  than silently absorbed.
- An HMAC registration with a credential shorter than 32 characters is not signed with a
  weak key — the delivery goes out unauthenticated with a loud log instead. A short shared
  secret is a password, not a signature.

### Migrating

Drop the `authentication` block from your webhook registration. That single change moves
you to RFC 9421 signing, because an absent `authentication` block IS the RFC 9421 arm.
Then verify against the JWKS reached by the walk above.

If you are looking for `docs/webhooks/migration-from-fragmented-senders.md` — the path
named in a deprecation warning emitted by the `adcp` SDK — that reference is to the SDK's
own documentation and does not resolve in this repository. This page is the migration
path for this agent.
