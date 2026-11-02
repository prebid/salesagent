# Request signature architecture

This document describes how the sales agent mints, stores, publishes, resolves,
and verifies RFC 9421 signing keys. It covers the implementation, not the
specification. For what the AdCP specification requires and which parts this
seller implements, see
[Request signing: the implemented subset](request-signing-subset.md).

A signature is a credential. It answers two questions — who sent this request,
and did anyone change it in transit — and the boundary treats it the way it
treats a bearer token.

## What the seller stores

Each tenant owns its signing keys. A `signing_keys` row holds one keypair:

| Column | Holds |
|--------|-------|
| `kid` | The key identifier a signature names, minted server-side |
| `public_jwk` | The public half, as the JSON Web Key the seller publishes |
| `private_key_pem_encrypted` | The private half, as PKCS#8 ciphertext |
| `not_before`, `not_after` | The window in which the key signs |
| `revoked_at` | When an operator retired the key, if ever |

The database is the only place a private key lives. `private_key_pem_encrypted`
is `NOT NULL`, so a published key whose private half is missing is a state the
schema forbids rather than one the resolver refuses at read time.

The deployment supplies one key encryption key (KEK). `SigningSettings.key_passphrase_env`
names the environment variable that holds the passphrase, and the variable holds
the passphrase itself. This indirection lets an orchestrator inject the secret
under whatever name its secret store uses.

## Mint a signing key

`provision_signing_key` in `src/core/signing/keys.py` is the only function that
writes a `signing_keys` row. Two transports call it: the admin blueprint at
`src/admin/blueprints/signing_keys.py` and the operations script at
`scripts/ops/provision_signing_key.py`. Neither contains provisioning logic.

The function returns the `kid` as a string. It cannot return private key
material, because a string has nowhere to put any.

Minting performs these steps in order, and each one is a precondition for the
next:

1. Confirms that the deployment configures a KEK. Without one, minting refuses
   and names the variable to set. There is no plaintext fallback.
2. Generates the keypair through `adcp.signing.keygen.generate_signing_keypair`,
   which returns a PKCS#8 `BEGIN ENCRYPTED PRIVATE KEY` PEM and a JSON Web Key
   whose members already match the publication shape.
3. Loads the private half back and re-derives the public JWK the row is about to
   publish. This runs the same check the resolver runs, so minting and resolving
   cannot disagree about what matches.
4. Writes the row.

Because step 3 precedes step 4, the seller never publishes a key it cannot sign
with.

## Publish the public half

Four documents bootstrap the trust chain, and `src/routes/well_known.py` serves
all four per Host:

- `/.well-known/jwks.json` carries the tenant's public keys.
- `/.well-known/brand.json` names the tenant's agents and where their keys live.
- `/.well-known/adagents.json` carries authorized-property records.
- `/.well-known/governance-revocations.json` is itself a signed JWS.

These are plain `GET` requests with no authentication and no signature
requirement. A counterparty fetches them to obtain the material that lets it
check a signature, so requiring a signature to read them would deadlock every
counterparty.

Nothing exempts these paths. Inbound verification runs inside `_resolve_identity`
behind the tool boundary, and a plain router `GET` never reaches that boundary.

### Two selectors, because rotation needs both

`SigningKeyRepository` answers two different questions about the same tenant:

- `publishable_at(now, grace_seconds)` returns every key a counterparty may see
  in the JWKS. It ignores `not_before` and `not_after`, so a rotation publishes
  the incoming key before it starts signing, and a retired key lingers for the
  grace period while counterparty caches expire.
- `active_at(now)` returns the single key the tenant signs with. The window is
  half-open, and `revoked_at` beats the window, so revoking a key stops it
  signing immediately.

The two disagree during a rotation, which is the point. A counterparty that
cached the JWKS an hour ago still verifies signatures made with the key that
rotation retired.

## Resolve a counterparty's public key

Key discovery is a pure function of the agent URL. The seller reads a
counterparty's keys from `<agent origin>/.well-known/jwks.json` and nowhere
else, and the signature's `keyid` parameter selects within that document.

`_jwks_is_well_known` in `src/core/signing/verifier.py` enforces this. A
resolution pointing anywhere else is refused with
`request_signature_jwks_untrusted`, and the log names the location the seller
looked at, so the counterparty's operator can correct the publication.

Because the location never varies, the seller consults no
`identity.key_origins` map. That map exists to declare where keys live when the
location can vary.

`AGENT_RESOLUTION_CACHE` in `src/core/signing/agent_cache.py` holds the whole
resolution per agent URL, and entries expire against
`SigningSettings.agent_resolution_ttl_seconds`. It sits in its own module because
two readers need it: the verifier fills it from the discovery walk, and the
revocation checker reads it to find the key a revocation list was signed with.
When a
counterparty's discovery walk fails, `_RESOLUTION_FAILURES` records the failure
and its spec code, so a request arriving inside the refetch cooldown receives the
same answer as the request that triggered the failure.

## Verify an inbound signature

`_resolve_identity` in `src/core/resolved_identity.py` reads the request headers
once, loads the tenant, and resolves the principal. It then calls
`verify_inbound_signature`, passing what it already holds. The verifier never
re-reads headers, never resolves its own tenant, and never sends a response.

The verifier decides three things and delegates the rest:

1. **Which bucket grades this operation.** The tenant's `request_signing`
   declaration names operations in `required_for` and `supported_for`.
   `posture_for_tenant` reads the declaration off the tenant row, and
   `bucket_for` maps the operation to its bucket. An operation in neither bucket
   is not graded.
2. **Whether to call the SDK's checklist.** `adcp.signing.verifier.verify_request_signature`
   performs every RFC 9421 step. The seller reimplements no step and reorders
   none.
3. **Whether to swallow the SDK's exception.** Under `supported`, a refusal
   propagates. Under `required`, an unsigned request from an unauthenticated
   caller also refuses.

A refusal leaves the verifier as a typed `AdCPSalesAgentError` carrying a
signature error code. `invoke_tool` catches it, the boundary renders it, and
`AuthChallengeResponder` lifts it to a `401` carrying
`WWW-Authenticate: Signature error="<code>"`. See
[Error architecture](error-architecture.md) for how the code reaches the status
line.

### The protocol namespace is declined, not verified

MCP and A2A carry their own JSON-RPC methods beside the AdCP tools. Every AdCP
tool arrives as `tools/call` with the operation in `params.name`; the transports
also define `tasks/get`, `tasks/cancel`, and the four `tasks/pushNotificationConfig/*`
methods. Those are answered by the a2a-sdk's handlers and by FastMCP's session
machinery, which never call the boundary, so the verifier never sees them.

The seller keeps that surface as small as the transports allow rather than
verifying into it:

- **It declines every `tasks/pushNotificationConfig/*` method** and advertises
  `push_notifications=False` on the agent card
  (`src/a2a_server/adcp_a2a_server.py`). This is the method that would otherwise
  register a webhook and its credentials without invoking any skill. Declining it
  means webhook configuration reaches the seller only as a field in an AdCP
  request body, where the signature covers it like any other body.
- **It declares no signing posture over protocol methods, and refuses a tenant's
  attempt to declare one.** A posture the boundary cannot apply would advertise
  enforcement that does not happen, so the declaration is refused at the point a
  tenant writes it.

What remains is task status and cancellation, which carry no credential and
create nothing.

## Sign an outbound request

Every outbound request travels through the one egress seam,
`src/core/security/outbound_http.py`, which takes a `sign=` strategy and applies
it **per attempt** rather than once. An RFC 9421 signature covers a nonce, so a
signature computed above a retry loop arrives replayed and a conformant receiver
refuses it.

`src/core/signing/outbound.py` resolves a tenant's signer:

- `delivery_signer_for_tenant` signs webhook deliveries.
- `adcp_challenge_signer` signs a notification-proof challenge.
- `webhook_delivery_signer` builds the strategy a delivery uses.

The signer sees `request.content` — the exact bytes on the wire — so the signed
bytes and the sent bytes are one object. Re-serializing between signing and
sending produces a digest mismatch at the receiver.

For which egress paths exist and what the seam refuses, see
[Outbound egress and SSRF](../security/outbound-egress.md).

## Multi-tenancy

Every question in this document is scoped to a tenant:

- A `signing_keys` row belongs to one tenant, and `SigningKeyRepository` takes
  the tenant identifier at construction.
- The `/.well-known/` routes resolve the tenant from the Host, then re-read that
  tenant through the repository layer inside one `TrustRootUoW`.
- `posture_for_tenant` reads the declaration from the tenant the resolver loaded,
  not from a second lookup.
- `delivery_signer_for_tenant` resolves the signing material for the tenant that
  owns the outbound delivery.

One deployment KEK opens every tenant's stored key. The KEK protects the
database at rest; it does not separate tenants from each other. Tenant isolation
comes from the repository scoping above.

## Where the rules live

- [Request signing: the implemented subset](request-signing-subset.md) records
  which parts of the specification this seller implements, with the measurement
  behind each decision.
- [Signing versus the request boundary](signing-vs-request-boundary.md) divides
  ownership between the signing layer and the request boundary.
- [Request lifecycle](../development/request-lifecycle.md) places verification in
  the path a request takes.
- [Posture and discovery](../signing/posture-and-discovery.md) covers the
  declaration a tenant writes.
- [Signing key runbook](../operations/signing-key-runbook.md) covers rotation,
  revocation, and KEK recovery.
