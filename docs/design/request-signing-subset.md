# The RFC 9421 subset this seller implements

AdCP 3.1.1's `request_signing` surface is over-specified for one deployment. This seller is
COMPATIBLE with it and is not an exhaustive implementation of it. Three narrowings were
decided; this document states what each one means in code, what it deletes, and — for the one
the pinned corpus refuses — the two storyboard cards that refuse it.

Spec grounding throughout: AdCP 3.1.1 via `adcp==6.6.0`,
`v3.1.1:docs/building/by-layer/L1/security.mdx` (there is no `dist/docs/3.1.1/`), the pinned
`get-adcp-capabilities-response.json`, and the graded corpus
`dist/compliance/3.1.1/universal/signed-requests.yaml` (12 positive / 28 negative vectors).

## 1. Key resolution is a pure function of the agent URL — LANDED

A counterparty's signing keys come from `<agent origin>/.well-known/jwks.json` and nowhere
else. security.mdx step 6 makes that the DEFAULT — "defaulting to `/.well-known/jwks.json` at
the origin of `A` when absent" — and permits `agents[].jwks_uri` to name somewhere else
instead. This seller declines the second half.

**The state.** `Principal.agent_url`, recorded at registration, is the only input.
`well_known_jwks_uri(agent_url)` is the only derivation, and it is the same function on both
paths that can produce an `AgentResolution`:

| path | how the location is established |
|---|---|
| the brand.json walk (`_resolution_for`) | the SDK resolves a `jwks_uri`; a resolution naming anything but the derived location is REFUSED with `request_signature_jwks_untrusted` and a cooldown, so the counterparty's operator gets a diagnosable answer rather than a key that never matches |
| the counterparty registry (`build_registry_resolution`) | the location is DERIVED, not declared, so a config entry cannot name one the walk would refuse |

**What follows, and is the whole point:** `identity.key_origins` is not consulted. The map
exists to declare where keys live when location can vary, and here it cannot — with one
possible location there is no origin to pin. So `VerifyOptions.expected_key_origins` is not
passed, `AgentResolution.key_origins` is not read, and the two codes the indirection exists
to produce (`request_signature_key_origin_mismatch`, `_missing`) are unreachable from this
verifier.

**Why that is safe, measured rather than assumed.**

* The graded corpus never exercises it: `signed-requests.yaml` at 3.1.1 contains ZERO
  occurrences of `jwks_uri` and ZERO of `key_origins` across all 40 vectors. The two codes
  are mandated by the prose and graded by nothing. (Re-measure with
  `grep -c key_origins tests/storyboard/runner/adcp-3.1.1/compliance/universal/signed-requests.yaml`;
  `grep -c signature` on the same file is the positive control that the pattern can match.)
* The spec couples two cache TTLs (step 4: a JWKS cache "MUST be bounded above by the JWKS
  revocation polling interval so a key rotation cannot be masked by a stale brand.json")
  purely because key location comes from a mutable document. A fixed location has no second
  document and no coupling.
* What the indirection buys is per-purpose origin separation, which is a SHOULD (:1084) and is
  already expressible in key metadata through `adcp_use`.

**The gate is the load-bearing half.** Dropping the key-origin pin WITHOUT fixing the location
would be strictly worse than the status quo: we would keep reading a location out of a
counterparty-controlled document and lose the shared-tenancy defence with nothing replacing
it. Fixing the location is what makes dropping the pin safe.

**Emission is unchanged, deliberately.** This narrowing is about what this seller CONSUMES. We
continue to publish `identity.key_origins.request_signing` under the existing anchoring gate:
a counterparty that DOES pin origins needs our published one, and `emitted_identity`'s gate is
what keeps the `required_when` trigger list from firing on itself.

**Deleted:** `_expected_key_origins`; the `expected_key_origins=` argument;
`_BrandJsonJwksResolver` (it existed only to engage the SDK's step-7 key-origin check, which
needs the map we no longer pass — keeping it bought a `UserWarning` on every verified
request); `CounterpartyRegistryEntry.jwks_uri` and `.key_origin` (both derivable);
`tests/unit/test_expected_key_origins.py`;
`test_request_signature_discovery.py::TestKeyOriginMissingIsGraded`.

**Cost to our own fixtures, found when the gate met the code.** The e2e counterparty origin
served each caller's keyset at a PER-SLOT path (`/.well-known/jwks-slot/<slot>.json`) while
every slot agent lives on one origin — so the slot mechanism named a location this narrowing
refuses. A well-known path is one per ORIGIN, so per-caller key isolation cannot be a path: the
slot keysets now merge into the single well-known document and are selected by `kid`, which is
what the SDK does within a JWKS anyway. Slots keep their own `agent_url` (their real purpose is
a per-caller `AGENT_RESOLUTION_CACHE` entry), and "nothing installed for this slot" is answered
at hop 2 — the slot's brand.json 404s — instead of at hop 3.

## 2. The enforcement state — LANDED

Two things a tenant could declare are gone. Neither was ever an AdCP obligation; both were
this seller's own surface, and each one advertised a promise it did not keep.

### `warn_for` — deleted, with the outcome it named

**Corpus coverage: zero.** `warn_for` appears in NO file under
`tests/storyboard/runner/adcp-3.1.1/compliance/` — not in `signed-requests.yaml`, not in any
vector. (`grep -rl warn_for` over that tree: 0 files. The positive control is `required_for`:
43 files.) AdCP grades two outcomes, and we implemented three.

What the third one did, from the truth table:

| request | `supported` | `warn` | `required` |
|---|---|---|---|
| unsigned | 200 | 200 | 401 |
| signed, valid | 200 | 200 | 200 |
| signed, **invalid** | 401 | **200** | 401 |

It differed from `supported` in one cell, and that cell told a counterparty whose signing was
BROKEN that it was working: the buyer cannot tell a verified signature from an ignored one, so
the mode's only real consumer was our own log — which the failure counter already serves. Not
merely ungraded, then, but undesirable: a posture whose sole observable effect is false
confidence in someone else's signing.

**Deleted:** the `warn` member of `PostureBucket`; the `bucket == "warn"` branch in
`_handle_rejection`; the precedence's middle term; `warn_for` and
`protocol_methods_warn_for` from every relation rule.

**What kept the claims it carried.** `warn` was the vehicle for three graded claims about
OTHER rules, and the narrowed `none` bucket (`supported: true`, this operation in no list) is
the same vehicle: it also serves a signed-but-invalid request, so a refusal there is still
attributable to whatever overrode the bucket. All three moved rather than going with it — the
BDD credential-escalation scenario, its integration sibling, and the over-application guard on
the `(code, step == 1)` pre-check predicate. `none` is the stronger arm for all three: no
checklist runs there at all.

### `protocol_methods_*` — declarability deleted

The MATCHING path went in the first half of this document. The three arrays stayed declarable
because BDD graded them; the corpus says what that grading was worth.

**One vector, and it gates itself.**
`test-vectors/request-signing/negative/028-unsigned-protocol-method-required.json` supplies its
own `verifier_capability` containing `protocol_methods_required_for: ["tasks/cancel"]`, and
`signed-requests.yaml` states the rule: *"Vector 028 is gated on
`request_signing.protocol_methods_required_for` being non-empty: the runner skips when the
agent does not declare the bucket, and FAILs (not SKIPs) when it declares it but doesn't
enforce."* Our storyboard tenant declares `required_for` and `covers_content_digest` and no
protocol bucket, so the vector skips — we were coherent by ACCIDENT, one config edit away from
a guaranteed conformance failure.

**And nothing is left for it to protect.** The spec's motivating case is A2A's
`tasks/pushNotificationConfig/set` registering a webhook and its credentials outside any tool
call. This agent declines that channel wholesale: the card advertises
`push_notifications=False`, all four `tasks/pushNotificationConfig/*` handlers raise
`PushNotificationNotSupportedError`, and `_refuse_envelope_push_config` refuses the
`message/send` envelope field too (`src/a2a_server/adcp_a2a_server.py`). Webhook configuration
reaches this seller only as a declared field of an AdCP request body, signed like any other
body.

**Deleted:** the three arrays from every relation rule; `_reject_mixed_namespaces` and
`_is_protocol_method` (with no second namespace there is no split to police);
`registry.is_adcp_operation` and its `vocabulary` re-export, whose only caller was that check;
the `RootModel` unwrap in `bucket_names`, which existed for the generated protocol-method item
types. This closes **#2280** by removing what could not be enforced, and makes vector 028
inapplicable BY CONSTRUCTION — graded as such by
`tests/unit/test_signing_conformance_plan.py`, which asserts the vector's own capability is a
declaration `from_tenant` refuses.

### How both deletions hold, given Pattern #1

The four fields are inherited from the pinned library type and are NOT redeclared away — a
redeclaration is the drift Pattern #1 exists to prevent, and it would owe the
schema-inheritance guard an allowlist row. What keeps them off the wire is that no tenant can
store one: `_reject_undeclarable_posture_fields` refuses a declaration naming any of them,
before pydantic types anything, with the reason LOGGED and the envelope carrying the field
names alone. A value that cannot be stored is a value no response can echo.

## 3. `covers_content_digest` — MEASURED twice, and NOT taken

The posture stays `either`. Requiring coverage is right on the security argument — every
request here is a POST to one path, so `@method`, `@target-uri` and `@authority` are constant
across all of them, and a signature that does not cover `content-digest` binds nothing that
varies (security.mdx :1057 spells out the body-swap consequence). The pinned corpus refuses it
anyway, and the refusal was measured on the wire rather than argued.

### What the runner's gate does with each digest vector

The runner gates each vector against the agent's DECLARED policy, in two halves —
`contentDigestDeclarationMismatch` and `contentDigestStructuralMismatch`
(`@adcp/sdk@14.0.0-rc.42`, `lib/testing/storyboard/request-signing/grader.mjs`), reached
through the `agentContentDigestPolicy` this repo's own bridge patch forwards:

| vector | declares | signs content-digest | under `either` | under `required` |
|---|---|---|---|---|
| `007-missing-content-digest` | `required` | no | SKIP (policy) | **SKIP (structural)** |
| `010-content-digest-mismatch` | `required` | yes | GRADED | GRADED |
| `023-multi-valued-content-digest` | `required` | yes | GRADED | GRADED |
| `018-digest-covered-when-forbidden` | `forbidden` | yes | SKIP (policy) | SKIP (policy) |

Declaring `required` adds NOTHING. 010 and 023 already grade us; 018 already skips; and 007
keeps skipping, because the structural half refuses any vector whose `Signature-Input` omits
`content-digest` when the agent requires it — which is 007's entire shape. The SDK will not
grade the one vector `required` exists for.

### What it costs, measured

**36 of the 40 corpus vectors sign without `content-digest`.** Only `positive/002`,
`negative/010`, `negative/018` and `negative/023` cover it. So the structural rule excludes 36
of them the moment the agent declares `required`.

Two runs of the storyboard on this tree, one variable apart (same box, same SDK, same corpus,
`completeness: complete` on both):

| declared | run | passed | failed | `signed_requests` graded | `signed_requests` failures |
|---|---|---|---|---|---|
| `either` | `innet_270926_1850` | **102** | 15 | 28 | 0 |
| `required` | `innet_280926_1234` | **83** | 15 | 9 | 0 |

Identical on MCP and A2A. **19 graded checks per protocol stop being graded** — 38 across the
two cards — and the set that goes is the checklist itself: every positive vector but 002, and
the negatives covering tag, expiry, window, algorithm, covered components, unknown keyid, key
purpose, missing params, crypto-invalid, replay, revocation and rate-abuse. Steps 2 through 12
would no longer be graded against the corpus at all, because every vector that reaches them
omits `content-digest` and would be refused at step 6 first.

The in-process suite says the same thing on the real wire: declaring `required` turned 16
`test_signing_conformance_vectors.py` rows red, 7 of them positive vectors this seller now
REFUSES with `request_signature_components_incomplete`, and 8 negatives whose own graded code
is pre-empted at step 6.

### Why that settles it

A verifier declaring `required` is spec-legal, and upstream's gate politely excludes the
vectors it would break rather than failing them. Underneath that politeness is the fact:
**this seller would refuse 11 of the 12 requests the pinned corpus says a conformant verifier
must ACCEPT.** `either` is not a compromise between the four digest vectors; it is the only
value under which the happy path is graded at all.

Landing it would additionally mean re-writing those 16 conformance expectations so the new
refusals read as correct, which is the one thing a measurement like this must not be used for.
Recorded here and beside the declaration (`scripts/setup/storyboard_signing.py`), and reported
upstream with the fixture PR.

The half that did land: nothing now accepts a posture we do not enforce, so
`to_verifier_capability` is no longer a lossy projection — every bucket a tenant can declare
is a field `VerifierCapability` carries, and `bucket_for` compensates for nothing.

## What did not move, and why it looks like it should have

`request_signature_key_origin_mismatch` and `_missing` keep their rows in
`src/core/errors/signature_codes.py`. That table is GENERATED from the SDK's
`REQUEST_TO_WEBHOOK_CODE`, so a code the SDK publishes has an entry whether or not this
verifier can reach it; deleting the rows would make the code table incomplete for a vocabulary
we do not own.
