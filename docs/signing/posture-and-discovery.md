# Signing posture and key discovery

For counterparties integrating with this sales agent. It covers what we advertise about
RFC 9421 message signing, what each declaration means for your requests, and how to find
the public key that verifies our signatures.

Companion pages: [Verifying our outbound webhooks](verifying-our-webhooks.md) and the
operator-facing [signing key runbook](../operations/signing-key-runbook.md).

AdCP spec version: **3.1.1** (see [adcp-spec-version.md](../adcp-spec-version.md)).

## What we advertise

`get_adcp_capabilities` carries three signing-related blocks. Each is honest about the
key material actually present — a tenant with no usable signing key advertises that it
cannot sign, rather than advertising a posture it could not honour.

| Block | Meaning |
|---|---|
| `request_signing` | Whether we VERIFY signatures on requests you send us, and for which operations |
| `webhook_signing` | Whether we SIGN the webhooks we send you |
| `identity` | Where our trust root lives — `brand_json_url` is the anchor for the walk below |

### The enforcement ladder

`request_signing` grades every operation into exactly one bucket:

| Bucket | What happens to an unsigned request |
|---|---|
| not listed | Passes. The operation is outside the posture entirely. |
| `supported_for` | Passes. We verify a signature if you send one, and we accept the request if you do not. |
| `warn_for` | Passes, and we record it. Shadow mode — this is where an operation sits while both sides build confidence. |
| `required_for` | Rejected. |

Two more rules matter when you read a declaration:

- **`supported: false` collapses every bucket to "none".** If we advertise that we do not
  verify signatures, no operation is required or warned regardless of what the other
  fields say. Check `supported` first.
- **JSON-RPC methods are a separate namespace.** `protocol_methods_supported_for`,
  `protocol_methods_warn_for` and `protocol_methods_required_for` grade wire methods such
  as `tasks/cancel`. They are kept apart from AdCP tool names deliberately, so the two
  can never collide as bare strings. A method is graded against the
  `protocol_methods_*` trio when one is supplied, and against the tool-name trio
  otherwise.

### `covers_content_digest`

Declares whether our verifier requires the `content-digest` component to be covered by
your signature. The default is `either`: we accept a signature that covers it and one
that does not. If we advertise a stricter value, a signature that omits the component is
rejected even though it is otherwise valid.

### Signature freshness

Our verifier enforces two bounds on the `created` and `expires` parameters:

| Bound | Default | Meaning |
|---|---|---|
| clock skew | 60 s | How far your clock may differ from ours before a signature is refused |
| validity window | 300 s | The longest signature lifetime we accept, the spec ceiling |

A signature with a window longer than we accept is refused even if it has not expired.
Sign close to the moment you send.

### Algorithms

We mint and verify `ed25519` and `ecdsa-p256-sha256`. `ed25519` is the default for keys
we provision.

## Key discovery

**A bare `.well-known/jwks.json` lookup is NOT the discovery mechanism.** This is the
documented trap, and it fails in a way that looks like success: the URL usually exists
and usually returns a JWKS, so a verifier built on it appears to work right up until it
is pointed at an agent whose keys live elsewhere, or until a key rotation it has no way
to learn about. The JWKS is the last hop of a chain, not the entry point.

The chain is anchored on what we advertise, so it can never point at a document we do not
control:

1. **`get_adcp_capabilities` → `identity.brand_json_url`.** The spec pins this to
   `^https://`; an agent that cannot serve https cannot declare one, and therefore cannot
   participate in signed exchange at all.
2. **`brand_json_url` → brand.json.** Served at `<origin>/.well-known/brand.json`. The
   origin is our canonical agent origin — scheme and host, no path. brand.json and the
   agent always share an origin: the Brand Agent variant of the document has no
   `authorized_operators[]` escape hatch, so the two cannot legitimately diverge.
3. **brand.json → `agents[].jwks_uri` → JWKS.** Served at
   `<origin>/.well-known/jwks.json`. This is the document that is authoritative for
   request signatures and for operator-side webhook signatures.

Concretely, for an agent reachable at `https://seller.example.com`:

```
get_adcp_capabilities  ->  identity.brand_json_url = "https://seller.example.com/.well-known/brand.json"
                       ->  agents[].jwks_uri       = "https://seller.example.com/.well-known/jwks.json"
                       ->  {"keys": [{"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
                                      "use": "sig", "key_ops": ["verify"],
                                      "adcp_use": "request-signing", "kid": "...", "x": "..."}]}
```

`agents[].url` in brand.json is the same origin plus the transport's endpoint path
(`/mcp/` or `/a2a`) — the exact strings the running app resolves to after any redirect it
issues.

### adagents.json is a different document for a different question

`<publisher-domain>/.well-known/adagents.json` carries
`authorized_agents[].signing_keys[]`. It is a **publisher-side pin**, authoritative for
exactly one thing: sell-side webhook delivery. **It is not in the request-signing chain.**
Do not substitute it for the brand.json walk, and do not treat its absence as a signal
about request signing.

Both documents are built from one query against the same publishable key set, so the pin
and the JWKS cannot drift into disagreeing about which keys exist.

### Caching

Trust-root responses carry an explicit `Cache-Control: max-age=300`. Publishing it
ourselves stops an intermediate proxy inventing a TTL of its own and masking a rotation
from you. Honour it: caching longer means a rotation reaches you late, and caching not at
all means a request-rate of key fetches you do not need.

### Revoked keys stay published, and carry a marker

A revoked key remains in the JWKS for a grace period rather than disappearing
immediately, so a verifier whose cache has not yet refreshed still finds the key **and
can see that it was revoked**. The revocation marker travels with the key into both
brand.json and the JWKS.

This places an obligation on you: **a key carrying a revocation marker must not be
trusted**, even though it is present in a document you fetched successfully. A JWKS entry
without the marker is indistinguishable from a live key, which is precisely why we carry
it rather than omitting the key.

## Enrolling for a pilot

1. Publish your own trust root and tell us your agent URL. We resolve you by the same
   walk described above — your `brand_json_url`, your JWKS — so an agent that cannot be
   discovered cannot be enrolled.
2. We add your operations to `supported_for`. Send signed requests; unsigned ones still
   pass. Nothing about your integration is at risk in this stage.
3. We promote to `warn_for` (shadow mode) and watch the unsigned-request rate for your
   operations. Signatures are graded but nothing is rejected.
4. We promote to `required_for` once shadow mode is quiet. From this point unsigned
   requests to those operations are rejected.

Rollback from step 4 is a per-tenant configuration change on our side, not a deploy — see
the [runbook](../operations/signing-key-runbook.md#rollback). If your integration breaks
after promotion, tell us; the fix does not require a release.
