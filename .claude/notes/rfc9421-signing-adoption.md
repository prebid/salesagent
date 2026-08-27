# RFC 9421 message signing — spec grounding and configuration surface design

Issue **#1291**. Branch `feat/rfc9421-request-signing`, stacked on `feature/spec-gaps-1210`.
Produced by A1 (`salesagent-z6nr.7`). **Design only — this note changes no production behavior.**

This is the artifact CLAUDE.md's spec-grounding gate requires *before code is written*. Every
child issue of #1291 cites this note instead of re-deriving citations; §13 is the block to paste
into each child PR.

---

## 1. Authority, and how to read every citation here

**Pinned version: AdCP 3.1.1**, via `adcp==6.6.0`. The pin is guarded by
`tests/unit/test_adcp_spec_version.py`.

Spec source is the `adcontextprotocol/adcp` repo at **tag `v3.1.1`**. Every path below is read as:

```bash
git -C ~/projects/adcp show v3.1.1:<path>
```

Read it that way and nothing else. The `~/projects/adcp` working tree is checked out at a
different version, so reading files from disk gives you the wrong text — a mistake that has
already produced wrong conclusions on this codebase.

### Authority hierarchy

1. **The 3.1.1 JSON schemas** — `dist/schemas/3.1.1/bundled/protocol/*.json`. The contract.
2. **The compliance artifacts** — `dist/compliance/3.1.1/{universal,test-kits,test-vectors}/`.
   What is actually graded, and the enumerated bodies (the vector directory, the checklist
   listing) beat any summary sentence that counts them.
3. **The prose** — `dist/docs/3.1.0/building/by-layer/L1/security.mdx`, read at tag `v3.1.1`.
4. **The `adcp` SDK** (`.venv/lib/python3.12/site-packages/adcp/signing/`) — a **cross-check, not
   the authority**. Where the SDK and the schema/prose disagree, the schema wins, we implement the
   schema, and the divergence is filed upstream.

### Path corrections this note makes to the epic's own citations

- **There is no `dist/docs/3.1.1/`.** `dist/docs/` at tag `v3.1.1` tops out at `3.1.0/`.
  `dist/docs/<version>/building/implementation/*.mdx` — cited by the epic body — does not resolve
  either at any version present (checked: `dist/docs/3.1.0/building/implementation/security.mdx`).
  The prose path that resolves is
  `dist/docs/3.1.0/building/by-layer/L1/security.mdx`. The schemas' own
  `x-adcp-validation.spec` pointers (`docs/building/implementation/security.mdx`) are stale
  in-schema pointers; do not copy them.
- **The vector count is 40, not 28** — see §6.
- **The verifier checklist is 15 checks, not 14** — see §8.

### The eight SDK divergences already found

Concrete instances, not a boilerplate disclaimer. All get filed upstream; all get implemented per
the schema on our side. (#4 and #5 were found by A3, `salesagent-z6nr.9`; #6 and #7 by B3,
`salesagent-z6nr.14`, by RUNNING the shipped conformance data against `adcp==6.6.0`; #8 by
`salesagent-z6nr.31`.)

| # | Divergence | Consequence |
|---|---|---|
| 1 | `adcp.signing.verifier.VerifierCapability` (verifier.py:88) carries **4 of `request_signing`'s 8 properties** — i.e. **2 of its 6 operation buckets** (`supported_for`, `required_for`). Missing: `warn_for`, `protocol_methods_supported_for`, `protocol_methods_warn_for`, `protocol_methods_required_for`. | Shadow mode (§10) and the JSON-RPC method namespace are **ours to implement**. Passing `warn_for` into `VerifierCapability` silently drops it. |
| 2 | `request_target_uri_malformed` (security.mdx step 10) and `request_body_malformed` (step 14) exist in the prose and in **no** SDK constant. `request_target_uri_malformed` IS graded by data (see #7); `request_body_malformed` is graded by no vector. | We emit both from our own layer. Do not "resolve" the divergence by dropping the codes. |
| 3 | `verify_starlette_request`'s docstring (middleware.py:38-44) claims Starlette caches the body so downstream handlers re-reading it get the same bytes. The cache lives on the `Request` **instance** the middleware constructs, not on the one the downstream app builds from the same scope — the receive channel **is** drained. | The `_receive` replay shim already in `src/app.py:455-462` is required. The docstring is wrong. |
| 4 | `adcp.signing.agent_resolver._fetch_capabilities` (agent_resolver.py:179-305) performs a raw `GET <agent_url>` and requires a JSON **object** body. security.mdx:1142 says the opposite verbatim: "This is a **protocol-level** call — invoke `get_adcp_capabilities` via the agent's declared transport (MCP `tools/call` or A2A skill invocation), **not a raw HTTP `GET`** against `A`. The agent URL is the protocol endpoint, not a JSON capabilities document." | `resolve_agent(<our agent url>)` cannot reach hop 2 against us no matter what we publish: `/mcp` answers GET with a redirect to an SSE stream and `/a2a` is JSON-RPC POST. Any test driving the resolver must seed hop 1 through `_capabilities_client_factory` (`tests/e2e/test_trust_root_e2e.py` does, and leaves hops 2 and 3 live). |
| 5 | `adcp.signing.brand_jwks._pick_agent` (brand_jwks.py:824-869) selects the `agents[]` entry by `type` plus an optional `agent_id` and **never compares `url` to the agent URL `A`** — so it raises `agent_ambiguous` for the shape the schema explicitly blesses (`#/definitions/agents`: "Multiple entries with the same type are permitted when they have distinct url values, such as one endpoint URL per tenant or property scope"), while security.mdx:1104 step 5 defines the match as byte-equality on `url`. | We publish one `agents[]` entry PER ENDPOINT we serve (`/mcp/`, `/a2a`) with distinct `id`s, per the schema — an origin-only `url` would byte-equal nothing any counterparty ever invoked. Our own resolver calls must pass `agent_id`. Do not "resolve" this by collapsing to one entry. |
| 6 | **Canonicalization.** `adcp.signing.canonical._canon_authority` (canonical.py:128-150) never calls the SDK's OWN `adcp.signing._idna_canonicalize.canonicalize_host` (four sibling SDK modules do), performs **no** malformed-authority rejection, and `canonicalize_target_uri` drops a trailing empty query. MEASURED: **8 of the 31 shipped `canonicalization.json` cases fail** against `adcp==6.6.0` — the 2 IDN cases, `trailing-empty-query-preserved`, and all 6 `reject: true` cases (5 accepted outright, `malformed-ipv6-missing-closing-bracket` refused with a bare `ValueError` carrying no code). The same root cause makes the SDK answer request vector `negative/026` with `request_signature_invalid` instead of `request_signature_header_malformed`. | `src/core/signing/canonical.py` is the thin seam: it DELEGATES every canonical form to the SDK and adds ONLY the spec's rejection set (url-canonicalization.mdx steps 2-3), so we never carry a second canonicalizer. 28 of 31 cases run as conformance through it; the 2 IDN mapping cases and `trailing-empty-query-preserved` are **not implementable at a verifier boundary** (the first are signer-side — a comparer MUST reject, not re-normalize; the third is destroyed by ASGI, which hands `query_string=b""` for both `/p` and `/p?`) and run as named our-obligation tests. **0 skipped, 0 xfailed.** |
| 7 | **`request_target_uri_malformed` is graded by data, and no SDK constant exists for it.** `canonicalization.json`'s 6 `reject: true` cases expect exactly this string, grounded at url-canonicalization.mdx ("Malformed authorities are rejected with `request_target_uri_malformed` on the signing path"). NOTE the vector README's worked example is **stale** and shows `request_signature_header_malformed`; the shipped DATA wins. | Defined in our layer as `src.core.signing.canonical.REQUEST_TARGET_URI_MALFORMED`, per #2's own instruction. **Keep it apart from `request_signature_header_malformed`**: request vector `negative/026` legitimately expects the latter (a checklist step-1 wire rejection), the canonicalization reject set expects the former. Collapsing the two loses a graded artifact in each direction. |
| 8 | **`adcp.signing.webhook_verifier`'s `VerifyOptions.expected_adcp_use` is pinned to `ADCP_USE_WEBHOOK` (webhook_verifier.py:152), so `_check_key_purpose` (verifier.py:607-621) accepts ONLY the deprecated `"webhook-signing"` JWK purpose and rejects `"request-signing"` — the exact inverse of security.mdx step 8 / the taxonomy row, which mandate the accept-set `{"request-signing", "webhook-signing"}` in EITHER direction (`:1438`: `"webhook-signing"` is deprecated, new signers SHOULD use `"request-signing"` only, but verifiers MUST still accept both). Found by B4/#1291 z6nr.31 widening `tests/helpers/signing.py::verify_as_conformant_receiver` to grade our own request-signing-purpose keys correctly. | Our test-side conformant-receiver shim attempts `"request-signing"` first and retries once with `"webhook-signing"` only on `REQUEST_SIGNATURE_KEY_PURPOSE_INVALID`, rather than mirroring the SDK's single-value pin. Production is unaffected (we sign with and publish `request-signing` keys only), but any grader importing the SDK's webhook verifier directly inherits the bug. |

**Upstream filing status (B3, `salesagent-z6nr.14`) — FILED, as four issues, not one.**

#6 and #7 are filed against `adcontextprotocol/adcp-client-python`, decomposed by root cause rather
than bundled. All four are OPEN and UNFIXED:

| Upstream | Covers |
|---|---|
| [#976](https://github.com/adcontextprotocol/adcp-client-python/issues/976) | Verifier does not reject malformed structured-field input at step 1 (wrong code; covered-component smuggling) — what our `_strict_header_precheck` closes |
| [#977](https://github.com/adcontextprotocol/adcp-client-python/issues/977) | No IDNA A-label conversion; raw U-labels not rejected — #6's 2 IDN cases |
| [#978](https://github.com/adcontextprotocol/adcp-client-python/issues/978) | Five malformed authority shapes accepted; `request_target_uri_malformed` unimplemented — #6's reject set **and** #7 |
| [#979](https://github.com/adcontextprotocol/adcp-client-python/issues/979) | Trailing empty query dropped, so `/p?` and `/p` produce the same signature base — #6 |
| [#1018](https://github.com/adcontextprotocol/adcp-client-python/issues/1018) | Webhook verifier's `expected_adcp_use` pin accepts only the deprecated `"webhook-signing"` purpose, rejecting the spec-mandated `"request-signing"` — #8 |

Related, same repo: [#975](https://github.com/adcontextprotocol/adcp-client-python/issues/975)
(vendored vectors incomplete and the loaders cannot detect it) with PR #980 approved — the SAME
defect class our `MANIFEST.json` + `test_adcp_conformance_vectors_pin.py` close locally.

And in `adcontextprotocol/adcp`: [#6071](https://github.com/adcontextprotocol/adcp/issues/6071)
(stale vector counts — the 28-vs-40 correction A1 made, now PRs #6073/#6074),
[#6076](https://github.com/adcontextprotocol/adcp/issues/6076) (seven documented signing error codes
that do not exist, PR #6078), and
[#6075](https://github.com/adcontextprotocol/adcp/issues/6075) (docs restate machine-readable spec
facts by hand with nothing checking they agree).

**Consequence for this repo: none of it changes what we vendor or how we grade, and that is by
design.** We vendor byte-verbatim from tag `v3.1.1` with a sha256 manifest; every upstream fix above
targets `main`, not the tag. When a new version is cut and the `adcp` pin moves, the drift guard
fires — which is exactly its job — and the vectors get re-vendored then.

**What it does change: `src/core/signing/canonical.py` is load-bearing for longer than a workaround
should be.** All four SDK bugs are open and unfixed, so the seam IS the implementation, not a
stopgap. When #976–#979 land and we bump the SDK, the seam must SHRINK to whatever remains
un-implemented upstream — otherwise it silently becomes a permanent second canonicalizer, which is
the exact thing divergence #6's consequence column forbids. That shrink is a condition of the next
`adcp` bump, not optional cleanup.

**Corroboration worth recording** (from PR #6078, which fixed #6076): the invented doc table was not
merely wrong on seven names — it was silently incomplete on a whole discovery path, omitting the
`request_signature_brand_*` and `request_signature_key_origin_*` families entirely. Two lessons land
on us. First, the mechanism the PR states — *"a rejection that is correct in substance still fails
its vector if the code string differs; take the code from the taxonomy rather than from memory"* —
is precisely why B3 took `request_target_uri_malformed` from the shipped vector DATA over the
README's stale worked example (#7). Second, those eight brand/key-origin constants DO exist in
`adcp.signing` and our middleware wires the resolvers that raise them, but **no conformance vector
exercises them**, so B3 grades none of that path. Filed as a gap, not a bug.

---

## 2. What the schema says — `request_signing`

`dist/schemas/3.1.1/bundled/protocol/get-adcp-capabilities-response.json`
`#/properties/request_signing`.

> RFC 9421 HTTP Signatures support for incoming requests. Optional in 3.0 — capability-advertised
> so counterparties can opt into signing selectively. Required for spend-committing operations in
> 4.0 (the next breaking-changes accumulation window).

Eight properties: `supported`, `covers_content_digest`, `required_for`, `warn_for`,
`supported_for`, `protocol_methods_supported_for`, `protocol_methods_warn_for`,
`protocol_methods_required_for`.

### `x-adcp-validation` rules, verbatim

| Property | Rule |
|---|---|
| `required_for` | `subset_of: request_signing.supported_for` |
| `warn_for` | `disjoint_with: request_signing.required_for`, `subset_of: request_signing.supported_for` |
| `protocol_methods_required_for` | `subset_of: request_signing.protocol_methods_supported_for` |
| `protocol_methods_warn_for` | `disjoint_with: request_signing.protocol_methods_required_for`, `subset_of: request_signing.protocol_methods_supported_for` |

Operation names in the three non-`protocol_methods_` buckets MUST be **AdCP protocol operation
names** — never MCP tool names, never A2A skill renames. JSON-RPC methods such as `tasks/cancel`
belong in the `protocol_methods_*` buckets.

`covers_content_digest` — enum `required | forbidden | either` (default `either`):

> 'required': signers MUST cover content-digest (body is bound to the signature); body-unbound
> signatures rejected with `request_signature_components_incomplete`. 'forbidden': signers MUST NOT
> cover content-digest; body-bound signatures rejected with `request_signature_components_unexpected`.

---

## 3. What the schema says — `webhook_signing`

`#/properties/webhook_signing`. Four properties:

| Property | Values | Note |
|---|---|---|
| `supported` | boolean | Forced `true` by `must_equal_when` — see below |
| `profile` | enum `["adcp/webhook-signing/v1"]` | MUST match the `tag=` parameter we emit in `Signature-Input` |
| `algorithms` | enum `["ed25519", "ecdsa-p256-sha256"]` | Closed. **No RSA.** |
| `legacy_hmac_fallback` | boolean (default false) | HMAC-SHA256 fallback on the legacy `push_notification_config.authentication`; removed in 4.0 |

`#/properties/webhook_signing/properties/supported`
`x-adcp-validation.verifier_constraints.must_equal_when` — `value: true`, `any_of`:

1. `media_buy.reporting_delivery_methods` **contains** `"webhook"`
2. `media_buy.content_standards.supports_webhook_delivery` **equals** `true`
3. `wholesale_feed_webhooks.supported` **equals** `true`

Exactly three triggers. `media_buy.offline_delivery_protocols` is **not** one of them — the
`_UNBACKED_BLOCKS` comment that said otherwise was corrected under #1729 (D3).

Rationale, from the schema itself: *"emitting state-changing webhooks unsigned is a downgrade
vector that lets an on-path attacker forge delivery callbacks."*

---

## 4. What the schema says — `identity`

`#/properties/identity`. Four properties: `brand_json_url`, `per_principal_key_isolation`,
`key_origins`, `compromise_notification`. The last three are advisory posture.

`brand_json_url` is the load-bearing one:

> `brand_json_url` is **load-bearing** for signature verification: when the agent declares any
> signing posture (`request_signing.supported_for`/`required_for` non-empty,
> `webhook_signing.supported === true`, or any `key_origins` subfield), `brand_json_url` MUST be
> present (storyboard-enforced in 3.x; schema-required in 4.0).

`x-adcp-validation` on `brand_json_url`:

- `trust_root: true`
- `required_when.any_of`: `request_signing.supported_for` non-empty · `request_signing.required_for`
  non-empty · `request_signing.protocol_methods_supported_for` non-empty ·
  `request_signing.protocol_methods_required_for` non-empty · `webhook_signing.supported == true` ·
  `identity.key_origins` any subfield present
- `schema_required_when`: `adcp.supported_versions` matches `^4\.`
- `verifier_constraints`: `agent_url_match: byte_equal` · `origin_binding:
  etld1_or_authorized_operators` · `key_origins_consistency: mandatory_when_signing`
- `distinct_from: sponsored_intelligence.brand_url`

**Consequence for us: even an inbound-only posture forces us to publish a trust root** — brand.json,
`adagents.json` `signing_keys`, and a JWKS. That is A3, and it is not optional.

`agent_url_match: byte_equal` is why §12 pins one canonical agent URL per tenant.

---

## 5. What is graded — the `signed_requests` storyboard

`dist/compliance/3.1.1/universal/signed-requests.yaml`.

**Gating** (verbatim):

> This storyboard runs for any agent advertising `request_signing.supported: true` in
> `get_adcp_capabilities`. Agents that do not advertise support are not tested against this
> storyboard — absence of advertisement is not a failure, it is a declaration that the agent does
> not offer verified signed requests.

Not advertising is conformant. Advertising is what buys us the grading.

**Grading** (verbatim):

> Observable-behavior only. The runner constructs signed HTTP requests exactly as documented in the
> conformance vectors at `/compliance/{version}/test-vectors/request-signing/` and sends them to
> the agent. The agent's responses are compared against the vectors' `expected_outcome`:
>
> - Positive vectors MUST produce a non-4xx response — the agent accepted the signed request.
> - Negative vectors MUST produce `401` with `WWW-Authenticate: Signature error="<code>"`, where the
>   `<code>` matches the vector's `expected_outcome.error_code` byte-for-byte. The checklist step
>   number is informational; grading is on the stable error code only.

Two things follow. The error code is the graded artifact, so it must be produced byte-for-byte —
`adcp.signing.middleware.unauthorized_response_headers(exc)` (middleware.py:20) already returns
exactly `{"WWW-Authenticate": 'Signature error="<code>"'}`, so B1 writes no header formatting. And
the checklist step numbers are *informational*: never grade on them, never let a test assert them.

Also from the storyboard: a seller advertising both `request_signing.supported: true` and a
specialism is graded on both, independently. The deprecated `signed-requests` specialism enum
still exists for back-compat but SHOULD NOT be claimed — `request_signing.supported: true` is the
declaration.

---

## 6. The 40 conformance vectors

`dist/compliance/3.1.1/test-vectors/request-signing/`.

**The count is 40 — 12 positive + 28 negative** — plus `canonicalization.json` (a separate flat,
crypto-free case set) and `keys.json`. Verified twice by directory listing at tag `v3.1.1`.

The epic body, its DoD checkbox, its child-index tree line, and `salesagent-z6nr.14`'s title all
said **28**. 28 is the *negative* count, propagated from the test-kit's own header comment
(`signed-requests-runner.yaml` says "28 conformance vectors") and from the storyboard's stale
narrative — whose positive-phase enumeration stops at `008` (the directory has 009-012) and whose
negative list jumps 020 → 028 with "Vectors 021-027 … cover later additions". **The vectors are the
graded artifact; the narrative is not.** B3 must run 40 or it can pass its own acceptance while
skipping 12 cases. A1 corrects all four ticket sites.

### Positive (12) — all MUST produce a non-4xx

| Vector | What it exercises | Our owning component |
|---|---|---|
| `001-basic-post` | Ed25519, no content-digest coverage | B1 middleware → SDK verifier |
| `002-post-with-content-digest` | content-digest covered | SDK verifier (step 11) |
| `003-es256-post` | ES256 (edge-runtime profile) | SDK verifier |
| `004-multiple-signature-labels` | multiple `Signature-Input` labels — verifier MUST process exactly one | SDK verifier |
| `005-default-port-stripped` | explicit `:443` stripped in canonicalization | SDK canonicalization |
| `006-dot-segment-path` | `/./` collapsed | SDK canonicalization |
| `007-query-byte-preserved` | query byte order preserved, not alphabetized | SDK canonicalization |
| `008-percent-encoded-path` | percent-encoded bytes normalized to uppercase hex | SDK canonicalization |
| `009-percent-encoded-unreserved-decoded` | unreserved bytes decoded per RFC 3986 §6.2.2 | SDK canonicalization |
| `010-percent-encoded-slash-preserved` | `%2F` preserved literally through dot-segment removal | SDK canonicalization |
| `011-ipv6-authority` | IPv6 literal, brackets preserved in `@target-uri`/`@authority` | SDK canonicalization + B1 `@authority` derivation |
| `012-ipv6-authority-default-port-stripped` | IPv6 + explicit `:443` | same |

The eight canonicalization vectors are the strongest argument for adopting the SDK wholesale: each
is a distinct way a hand-rolled canonicalizer silently diverges.

### Negative (28) — all MUST produce `401` + `WWW-Authenticate: Signature error="<code>"`

| Vector | `expected_outcome.error_code` | Our owning component |
|---|---|---|
| `001-no-signature-header` | `request_signature_required` | **B2** operation resolution (op ∈ `required_for`) |
| `002-wrong-tag` | `request_signature_tag_invalid` | SDK verifier (step 3) |
| `003-expired-signature` | `request_signature_window_invalid` | SDK verifier (step 5) |
| `004-window-too-long` | `request_signature_window_invalid` | SDK verifier (step 5) |
| `005-alg-not-allowed` | `request_signature_alg_not_allowed` | SDK verifier (step 4) |
| `006-missing-covered-component` | `request_signature_components_incomplete` | SDK verifier (step 6) |
| `007-missing-content-digest` | `request_signature_components_incomplete` | SDK verifier (step 6) — needs `covers_content_digest: required` |
| `008-unknown-keyid` | `request_signature_key_unknown` | **A5/B1** kid→JWK resolution (step 7) |
| `009-key-ops-missing-verify` | `request_signature_key_purpose_invalid` | SDK verifier (step 8) |
| `010-content-digest-mismatch` | `request_signature_digest_mismatch` | SDK verifier (step 11) |
| `011-malformed-header` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `012-missing-expires-param` | `request_signature_params_incomplete` | SDK verifier (step 2) |
| `013-expires-le-created` | `request_signature_window_invalid` | SDK verifier (step 5) |
| `014-missing-nonce-param` | `request_signature_params_incomplete` | SDK verifier (step 2) |
| `015-signature-invalid` | `request_signature_invalid` | SDK verifier (step 10) |
| `016-replayed-nonce` | `request_signature_replayed` | **A4** replay store (step 12) — stateful, see §7 |
| `017-key-revoked` | `request_signature_key_revoked` | **A5** revocation (step 9) — stateful, see §7 |
| `018-digest-covered-when-forbidden` | `request_signature_components_unexpected` | SDK verifier (step 6) — needs `covers_content_digest: forbidden` |
| `019-signature-without-signature-input` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `020-rate-abuse` | `request_signature_rate_abuse` | **A4** `at_capacity` (step 9a) — stateful, see §7 |
| `021-duplicate-signature-input-label` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `022-multi-valued-content-type` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `023-multi-valued-content-digest` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `024-unquoted-string-param` | `request_signature_header_malformed` | SDK verifier (step 1) |
| `025-jwk-alg-crv-mismatch` | `request_signature_key_purpose_invalid` | SDK verifier (step 8) |
| `026-non-ascii-host` | `request_signature_header_malformed` | SDK verifier (step 1) / B1 `@authority` |
| `027-webhook-registration-authentication-unsigned` | `request_signature_required` | **B2** — webhook registration carrying `push_notification_config.authentication` |
| `028-unsigned-protocol-method-required` | `request_signature_required` | **B2** — JSON-RPC `tasks/cancel`, bound off `protocol_methods_required_for` |

Three vectors are ours end to end because the SDK cannot express their inputs: 001, 027 and 028 all
turn on **which operation this request is**, and `VerifierCapability` has nowhere to put a
`protocol_methods_*` list (divergence #1). B2 binds `tasks/cancel` off the JSON-RPC **`method`**
field, not `params.name`.

---

## 7. The stateful-vector contract

`dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml`. Values verbatim.

- `endpoint_scope: sandbox` — *"The replay-window contract sends a live, validly-signed mutating
  request as its first step… Running this against a production endpoint would create a real media
  buy. Graders MUST target a sandbox/staging endpoint… Agents advertising `request_signing.supported:
  true` SHOULD expose a dedicated grading endpoint rather than grading in prod."* This is why B4
  exists and is not polish.
- `harness_mode: black_box` — *"AdCP Verified grading runs in black_box mode only."* White-box state
  injection via a vector's `test_harness_state` block does not count.
- `runner_signing_keys` — `test-ed25519-2026` (`ed25519`) and `test-es256-2026`
  (`ecdsa-p256-sha256`), JWKS at the vectors' `keys.json`. *"The agent's verifier MUST treat these
  keyids as a registered test counterparty whose JWKS contains the corresponding public keys with
  `adcp_use: "request-signing"`."*

| Vector | Contract field | Value |
|---|---|---|
| `016-replayed-nonce` | `black_box_behavior` | `repeat_request` |
| | `max_interval_seconds` | `5` |
| | `min_replay_ttl_seconds` | `10` |
| `017-key-revoked` | `pre_revoked_keyid` | `test-revoked-2026` |
| `020-rate-abuse` | `grading_target_per_keyid_cap_requests` | `100` |
| | `production_min_per_keyid_cap_requests` | `1000000` |
| | `window_seconds` | `60` |

On 016 the contract warns of a **silent false green**: *"Otherwise the cache entry for the first
request may evict before the second arrives and the vector will pass spuriously (i.e., both requests
accepted = no replay rejection)."* A TTL under 10s makes the vector pass while replay protection is
broken.

On 020, the sentence that makes the two-tier cap legitimate rather than a backdoor smuggled past
`harness_mode: black_box`:

> `grading_target_per_keyid_cap_requests` is the cap the runner will target during grading — NOT a
> production recommendation. Agents MAY configure a lower cap for the test-kit counterparty only so
> grading finishes in a reasonable time. Production caps MUST follow the spec recommendation at
> …§per-keyid cap (at least 1,000,000 entries per keyid). Implementers copying a value from this
> file into production code SHOULD use `production_min_per_keyid_cap_requests` below as the floor.

and its `scope.in_scope` line naming it in-contract:

> Grading-time cap the runner will target for rate-abuse grading (NOT a production recommendation;
> see rate_abuse block).

**Explicitly out of contract** (`scope.out_of_scope`): error-code strings (they live in the
vectors, graded byte-for-byte), checklist step numbers ("informational only"), our internal TTL/cap
storage mechanism, and production verifier configuration. So the cap must be resolvable
**per counterparty keyid** — one global constant cannot be both 100 and 1,000,000.

---

## 8. The verifier checklist — 15 checks, and two ordering invariants

`security.mdx` §"Verifier checklist (requests)", read at tag `v3.1.1`:

> Otherwise, verifiers MUST apply these **15 checks (14 numbered steps plus sub-step 9a)** in order,
> short-circuiting on the first failure.

The same release contradicts itself twice — the quickstart at :932 says *"all 14 checks (13
numbered steps plus sub-step 9a)"*, and `signed-requests.yaml` says the same. **The enumerated body
is the authority**: steps 1-14 plus 9a. Step 14 decomposes into 14a (strict-parse) and 14b (logging
discipline), which the spec says are *"elaborations of one check, not separate checks in the count"*.

Steps, abridged to code-emitting decisions:

| Step | Check | Code on failure |
|---|---|---|
| 1 | Parse `Signature-Input`/`Signature` per RFC 9421 §4 | `header_malformed` |
| 2 | `created`,`expires`,`nonce`,`keyid`,`alg`,`tag` all present | `params_incomplete` |
| 3 | `tag` is exactly `adcp/request-signing/v1` | `tag_invalid` |
| 4 | `alg` ∈ {`ed25519`,`ecdsa-p256-sha256`} — *"Library defaults MUST NOT be relied upon"* | `alg_not_allowed` |
| 5 | `expires > created`, `created ≤ now+60s`, `expires ≥ now−60s`, `expires−created ≤ 300s` | `window_invalid` |
| 6 | covered components ⊇ `@method`,`@target-uri`,`@authority`; `content-type` when a body exists; `content-digest` per `covers_content_digest` | `components_incomplete` / `components_unexpected` |
| 7 | resolve `keyid` → JWK; run the brand_json_url discovery preamble on cache miss; on `kid` miss **refetch once, subject to the 30-second cooldown** | `key_unknown`, `brand_*`, `key_origin_*` |
| 8 | JWK `use == "sig"`, `key_ops` ∋ `"verify"`, `adcp_use == "request-signing"` — *absent `adcp_use` MUST be treated as non-conforming* | `key_purpose_invalid` |
| 9 | revocation list; staleness beyond grace also rejects | `key_revoked` / `revocation_stale` |
| **9a** | per-keyid replay-cache cap | `rate_abuse` |
| 10 | canonical signature base + `@authority` derivation, then crypto verify | `invalid` / `request_target_uri_malformed` |
| 11 | recompute content-digest when covered | `digest_mismatch` |
| 12 | `(keyid, nonce)` against the replay cache | `replayed` |
| 13 | **insert** `(keyid, nonce)` with TTL `(expires − now) + 60s` | — |
| 14 | body well-formedness — reject duplicate object keys | `request_body_malformed` |

### Two ordering invariants the spec calls out as future-edit hazards — preserve both

1. **Steps 9 and 9a run BEFORE crypto verify (step 10).** *"a compromised or misconfigured signer
   exhausting its cap MUST NOT force amplified Ed25519/ECDSA work on the verifier."* And 9a runs
   *after* keyid resolution (step 7) *"so the cap-state oracle only responds for keys the verifier
   has already committed to recognizing — running 9a earlier would let an attacker probe
   verifier-internal rate-limit state across the full keyid space."*
2. **The replay insert is step 13 — after 10-12, before 14.** *"so that a captured frame carrying a
   valid signature over a malformed body cannot be replayed to burn crypto-verify CPU on each
   retry — the nonce is burned on first sighting of a cryptographically-valid frame."*

B1 is reviewed against both.

### The `@authority` rule (step 10) is load-bearing for us specifically

> verifiers MUST derive `@authority` from the HTTP/2+ `:authority` pseudo-header when present,
> otherwise from the as-received HTTP/1.1 `Host` header — **NOT from reverse-proxy routing state,
> load-balancer metadata, or any `Host` value a forward proxy may have rewritten in transit.**

Skipping it *"silently accepts a cross-vhost replay vector"*. We run behind nginx and route tenants
by `Host`/`Apx-Incoming-Host` — see §12, where the same sentence forces a decision about our agent
card.

Step 14 has **no conformance vector** and **no SDK constant** (divergence #2). It is a MUST the
storyboard does not grade, so it is graded by our own BDD or it is not graded at all.

---

## 9. The configuration surface

### 9.1 The split is settled by the graded feature file, not by taste

`tests/bdd/features/BR-UC-010-discover-seller-capabilities.feature` (generated, authoritative)
drives every signing scenario through **the tenant declaration**:

- `:1043` `Given the tenant declares request_signing posture <posture>` → asserts `supported`,
  `covers_content_digest`
- `:1064` `… supported_for=[…] required_for=[…] protocol_methods_supported_for=["tasks/cancel"]
  protocol_methods_required_for=["tasks/cancel"]`
- `:1081` `… supported_for=[…] required_for=[…] warn_for=[…]` → subset + disjoint assertions
- `:1094` `Given the tenant declares webhook_signing posture <posture>` → `supported`, `profile`,
  `algorithms`, `legacy_hmac_fallback`
- `:1521` `the tenant identity and signing posture are configured for <boundary_point>` → the
  `required_when` rejection, `CONFIGURATION_ERROR`, recovery `terminal`, naming `brand_json_url`

Every "invalid" row grades a `CONFIGURATION_ERROR` raised from the **declaration** path. So the
declarable posture is tenant-scoped by construction, and the relation validators belong next to the
existing `_UNBACKED_BLOCKS` loop in
`src/core/schemas/capability_declarations.py` (`:194-205`, `validate_backing()` at `:218`).

**Do not invent a third store.** Two stores exist and both are extended:
`CapabilityDeclarations` (tenant) and `src/core/config.py` (agent).

### 9.2 Tenant-level — typed fields on `CapabilityDeclarations`

Stored inside the existing `tenants.capability_declarations` `JSONType` column
(`src/core/database/models.py`). **No migration.**

Two of the three signing blocks are DECLARED; one is DERIVED. That split is the Core
Invariant of the family, not a convenience — see §9.4.

| Field | Why it varies per tenant |
|---|---|
| `request_signing.supported` | a seller may run the verifier but not offer it to this counterparty |
| `request_signing.covers_content_digest` | per-counterparty digest policy; graded by `@T-UC-010-v31-request-signing-posture` |
| `request_signing.supported_for` / `warn_for` / `required_for` | **the rollout dial**; graded by `@T-UC-010-v31-request-signing-subset` and `@T-UC-010-v31-request-signing-monotonicity` |
| `request_signing.protocol_methods_{supported,warn,required}_for` | same dial, JSON-RPC namespace; graded by `@T-UC-010-v31-request-signing-namespace-split` |
| `identity.brand_json_url` | per-tenant `virtual_host` ⇒ per-tenant trust root; graded by `@T-UC-010-v31-identity-brand-json-url` |
| `identity.{per_principal_key_isolation,key_origins,compromise_notification}` | advisory posture, per operator |
| `media_buy.reporting_delivery_methods` | `[webhook]` is backed and declarable; any list containing `offline` is refused under **#1729** |

The field type for `request_signing` is `RequestSigningPosture` — the EXISTING posture class
from `src/core/signing/posture.py`, not a parallel declaration model. That is what makes the
block the wire carries and the `VerifierCapability` the middleware enforces two views of one
object, and it inherits the namespace split, the protocol-method pattern and the
`covers_content_digest` enum from the SDK. `identity` is `IdentityDeclaration`, a
`Library*`-alias subclass that RESTATES `extra="forbid"` because the SDK's `Identity` is
`extra="allow"` and would otherwise swallow an operator's typo.

`webhook_signing` has **no field**. Its four properties are platform state:
`supported` from `KeyBacking.signs` AND trust-root publishability, `algorithms` from the
ACTIVE key row's `alg`, `profile` from the SDK's `WEBHOOK_TAG`, `legacy_hmac_fallback` from
the legacy arm's own reachability in `webhook_sender_factory`. `webhook_signing_posture()` is
the one producer, and C1's `_rfc9421_sender` consumes that same object — so the wire and the
socket cannot disagree about whether we sign or with what. Graded by
`@T-UC-010-v31-webhook-signing`.

`identity` is declared for VALIDATION and derived for EMISSION, and neither substitutes for
the other. The store carries the block so a declaration naming a posture without
`brand_json_url` is rejectable at all; the value put on the wire comes from
`src/core/agent_identity.py`, so an emitted posture always carries a conformant pointer. A
declared value that differs from the derived one is a `CONFIGURATION_ERROR` naming the field.

`media_buy.content_standards` and `wholesale_feed_webhooks` stay refused, with reasons that
outlive #1291: nothing in `src/` implements a content-standards surface or a wholesale feed of
any kind. `media_buy.offline_delivery_protocols` is **#1729 only**. The
`@T-UC-010-v31-reporting-delivery-methods` `offline_only` row does not graduate under #1291.

**The publishability gate.** `webhook_signing.supported` additionally requires
`canonical_agent_url(tenant)` to start `https://`, and the gate sits on the SHARED posture
object — so a keyed tenant on a non-https origin both advertises `false` AND has its sender
take the unauthenticated arm. This is a deliberate change to C1's landed behaviour for such
tenants, and it is the honest one: key discovery for our outbound signatures runs through
`identity.brand_json_url`, which the pin fixes to `^https://` and which security.mdx rejects
otherwise (`request_signature_brand_json_url_missing`). On such a host every signature we
emitted would be one no conformant receiver can resolve a key for, so stopping removes an
unverifiable signature rather than withdrawing a capability. Production tenants with a
`virtual_host` or `SALES_AGENT_DOMAIN` are dotted and unaffected; the consequence for the test
stacks is that any suite needing a live declared posture sets a dotted `virtual_host`.

`request_signing.supported` is NOT gated: it declares that we VERIFY with the COUNTERPARTY's
keys and needs no trust root of ours, so the conservative default stays emittable on
`http://localhost:8080`. Its BUCKETS are effectively gated by the `identity` rules instead — a
non-empty bucket fires `required_when`, and on a non-https host no conformant `brand_json_url`
exists, so the declaration is REFUSED rather than silently narrowed.

**The no-tenant response** declares `request_signing.supported = SigningConfig.verifier_enabled`
with every bucket empty, `webhook_signing.supported: false`, and no `identity`. The pin defines
`request_signing.supported` as *"whether this agent VERIFIES RFC 9421 signatures on incoming
requests"* — an AGENT-level fact, and `RequestSignatureMiddleware` is mounted process-wide, so a
literal `false` there under-declares something true. `webhook_signing` is the opposite case: no
tenant means no key.

**The default posture** is `supported: true` with `required_for` EMPTY and every other bucket
empty. The schema says `required_for` is *"empty in 3.0 by default; sellers populate selectively
during per-counterparty pilots"*, and per the `required_when` trigger list an all-empty posture
fires nothing — so it stays emittable for a keyless tenant with no trust root at all. Promotion
runs `supported_for → warn_for → required_for` (§10).

### 9.3 Agent-level — one `SigningConfig(BaseSettings)` on `AppConfig`

Composed in `src/core/config.py` following the existing `BaseSettings` sub-config precedent
(`:93-116`, `get_config()` at `:118`). Never hand-rolled `os.getenv`.

**Group A — verifier enforcement facts**

| Field | Why it cannot vary per tenant |
|---|---|
| verifier mounted (on/off) | one ASGI middleware instance serves every tenant |
| allowed algorithms | a profile constant; narrowing per tenant is not a schema-expressible posture |
| `max_skew_seconds` (60) / `max_window_seconds` (300) | RFC profile constants; a tenant override would be non-conformant |
| replay TTL floor, per-keyid cap default | properties of the shared replay store |

**Group B — our own key material and publication**

| Field | Why it cannot vary per tenant |
|---|---|
| `SigningProvider` selection + key **store kind** | one process, one key store |
| revocation list issuer origin + poll interval | one fetcher per process |
| brand.json / `adagents.json` / JWKS publication origin | one deployment origin |

The store **kind** is agent-level (`SigningConfig.provider`, plus `allowed_key_ref_schemes`, which
is what lets a deployment forbid a scheme outright); each key's **location** is per-tenant and lives
on the `signing_keys` row's scheme-prefixed `private_key_ref`. There is no single agent-level key
location, because each tenant is a distinct seller identity with its own brand domain and therefore
its own key material. A3 and C1 inherit this split; do not re-derive it.

### How key material is stored

**The application never writes key material to a filesystem.** The private PEM lives in the
`signing_keys` row, **encrypted**, opened with one deployment-wide KEK read from one environment
variable. `private_key_ref` is `db:<row-id>` — a pointer, never **plaintext** key material.

This is not new crypto. `generate_signing_keypair(passphrase=...)` returns PKCS#8
`BEGIN ENCRYPTED PRIVATE KEY`, so the stored ciphertext is the PEM itself; the passphrase path
(`SigningConfig.key_passphrase_env` → `key_passphrase` → `load_private_key_pem(password=)`) resolves
the KEK from the environment on every use rather than pinning it in process memory.

**Minting refuses when no KEK is configured**, naming the variable to set. There is no plaintext
fallback — that fallback is what would degrade "encrypted PEM in Postgres" into "private keys in the
database".

| scheme | role |
|---|---|
| `db:` | **the default.** Encrypted PEM in the row, KEK from one env var |
| `env:` | single-tenant deployments that prefer it |
| `file:` | **read-only**, for an orchestrator-mounted secret (k8s `Secret`, Docker secret) |
| `kms` | reserved in `SigningConfig.provider` and refused by `validate_provider`; `salesagent-z6nr.30` |

`db:` is the default because keys are **per tenant** and minted **at runtime**. `env:` would need one
variable per tenant per key, and onboarding a tenant would require a redeploy — the
"write a secret, read it into an env var" pattern fits one application-level secret, not N
runtime-minted per-tenant keys. A secret manager cannot be assumed to exist in every deployment.

**Threat model.** A database dump, backup or read replica alone yields useless ciphertext; the
environment alone yields nothing. An attacker holding **both** gets the key — identical to
`file:`+passphrase and to the AWS secret→env pattern, where a plaintext PEM sits in the environment.
Only a KMS/HSM improves on it, because there the private key never enters the process at all. `db:`
is envelope encryption already, so a KMS-managed data key later replaces the KEK with no change to
the row format.

Multi-replica deployment is **unsupported**. `db:` happens to work across replicas, since nothing is
on local disk, but that is a side effect and not a tested property.

**The public half.** `public_jwk` is a `JSONType` column on `signing_keys` carrying `kid`, `alg`,
`adcp_use`, `kty`, `crv` and `x`; `build_jwks` renders `/.well-known/jwks.json` from those rows via
`publishable_at`. `kid` is the wire selector: the signer emits it as the `keyid=` parameter of
`Signature-Input`, and a verifier matches that against the published JWK's `kid`.

**Group C — counterparty key resolution** (the inbound discovery chain: checklist step 7,
`get_adcp_capabilities → identity.brand_json_url → brand.json → agents[] → jwks_uri`). This group
owns **12 of the 27 error codes** and had no config home until this note; without it A5 and B1 each
invent one ad hoc.

| Field | Why it cannot vary per tenant |
|---|---|
| counterparty JWKS cache TTL | one fetcher, one cache, shared across tenants |
| refetch cooldown (spec: 30s) | a profile constant, not a posture |
| brand.json snapshot lifetime / `max_age` | property of the shared cache |
| `allow_private_destinations` | deployment-scoped SSRF policy — **MUST be false in prod**, true only for the B4 sandbox grading endpoint |
| counterparty capabilities fetch timeout/retry | one HTTP client per process |

Note the asymmetry that makes this its own group: `identity.brand_json_url` in §9.2 is what **we
publish** (A3); Group C is about **the counterparty's**, which we fetch.

**Neither tenant nor agent: the per-keyid cap override.** §7 requires 100 for the test counterparty
and ≥1,000,000 in production. Model it as an agent-level default plus a **per-keyid counterparty
override**, never a tenant field and never a global lowering.

### 9.4 The two refusal tables, and why there are two

`is_block_declarable(block)` is the ONE predicate every consumer reads — the middleware's body
buffer and operation resolve, and the two `*_is_declarable()` wrappers. It answers False off
either of two tables, because the reason differs and so does the fix the operator needs:

- **`_UNBACKED_BLOCKS`** — the schema defines it, this deployment does not IMPLEMENT it. Holds
  `content_standards` and `wholesale_feed_webhooks` (no surface of any kind exists for either;
  both were re-homed off #1291 because signing landing does not make them declarable and an
  entry citing #1291 would point at a closed issue) and `offline_delivery_protocols` (#1729).
  `wholesale_feed_webhooks` has no model field either; it is listed so the operator gets a
  reason rather than pydantic's generic extra-field error, and because it is the third
  `must_equal_when` trigger.
- **`_DERIVED_BLOCKS`** — we DO implement it, but the value is platform state, so there is
  nothing for a tenant to declare. Holds `webhook_signing` alone. Same philosophy the
  `_DERIVATION_ONLY_BUILDERS` guard already encodes for `account.*` and `adcp.*`.

`request_signing` and `identity` are in NEITHER: they are declarable fields (§9.2).
`reporting_delivery_methods` is MEMBER-gated in `validate_backing` rather than block-gated,
which is what lets the `webhook_only` row be graded on its own terms while `offline_only` stays
refused.

### 9.5 Three paths, three failure modes — all decided

`CapabilityDeclarations.from_tenant()` RAISES. It has three callers, and each needs a different
answer:

- **The capabilities READ path** (`src/core/tools/capabilities.py`) — raises. A malformed or
  relation-violating declaration surfaces as a terminal `CONFIGURATION_ERROR` naming the
  offending field, because the graded observable in every rejection scenario is the
  `get_adcp_capabilities` response. The parse happens BEFORE the degradation blocks, and the
  signing reads get their own `try` with their own advisory label, so a signing-key failure is
  never reported to the buyer as "could not resolve publisher domains".
- **The write/config path** — raises, for the same reason and more strongly: STRICT exists so an
  operator cannot *declare* an unbacked posture.
- **The ASGI verifier MIDDLEWARE** (`src/core/signing/request_verifier_middleware.py`) —
  degrades. `_resolve_request_context` runs in an `asyncio.to_thread` hop with nothing above it
  that translates `AdCPError`: `src/app.py` mounts the verifier under
  `UnifiedAuthMiddleware`/CORS only, and `_reject` emits a signature-specific 401 rather than a
  general envelope. Uncaught, ONE relation-violating declaration would answer EVERY AdCP request
  — get_products, create_media_buy, all of them — with a bare 500. So `posture_for_tenant`
  catches `AdCPError` (never a bare `Exception`), logs WARNING naming the tenant, and returns
  `UNSUPPORTED_POSTURE`. The same misconfiguration is loud on the surface whose job is to report
  it, and the fallback does not INVENT enforcement the tenant never declared. The rejected
  alternative — promoting an unreadable posture to `required` — turns a config typo into a 401
  on every AdCP surface. `_fail_closed_bucket` is unchanged: it covers the different case of a
  READABLE posture plus an unnameable request.

  `_detect_tenant_for_posture` extends the same trade one step out: the tenant READ itself can
  fail (a Postgres blip), on the same line, with the same absent envelope. It degrades to no
  tenant, hence to the agent-level posture. The security consequence is stated rather than
  hidden: for the interval of the failure, a tenant that declared `required_for` stops having it
  required.

**Agent-level backing degrades rather than raising, on every path.** A DECLARED posture with
`SigningConfig.verifier_enabled` false resolves to `supported=false` with a WARNING. Rolling the
verifier back is then a flag flip that makes the wire honest, not one that turns discovery into
`CONFIGURATION_ERROR` for every tenant that changed nothing. Because it is ONE object, the
middleware then also declines to enforce — which is correct, the verifier is not running.

**Schema-RELATION violations raise** (`required_for ⊄ supported_for`, `warn_for ∩ required_for ≠
∅`, `required_when`, `purpose_anchoring`, the declared-vs-derived `brand_json_url` mismatch).
Those are bad declarations, not absent backing, and the graded "invalid" rows depend on it.

**The cost this turns on, paid on purpose.** `request_signing` leaving the refusal tables flips
`request_signing_is_declarable()` True, which enables per-request body buffering, operation
resolution and a tenant read for ALL AdCP traffic — the expense R-H3 gated deliberately while B1
shipped alone. A declared posture is the thing being enforced, so the read is now worth its
price.

---

## 10. The shadow-mode ladder

The schema defines `warn_for` as the bridge: *"verifies signatures when present and logs failures
but does NOT reject… a shadow-mode bridge between `supported_for` and `required_for`."*

**Operator progression, one operation at a time:**

```
supported_for   verify when present, never reject          ← counterparties may start signing
      ↓
warn_for        verify when present, log + emit metric,    ← we learn whether they actually do,
                still never reject                            and whether they do it right
      ↓
required_for    reject unsigned with request_signature_required
```

Precedence is `required_for > warn_for > supported_for`. Relations: `required_for ⊆ supported_for`,
`warn_for ⊆ supported_for`, `warn_for ∩ required_for = ∅`.

**The metric an operator watches before promoting.** Per `(operation, keyid)` counters emitted from
the B1 middleware — the only layer that sees the outcome before it is either swallowed (warn) or
turned into a 401:

- `signed_ok`
- `signed_failed{code}`
- `unsigned`

**Promotion criterion:** over a full traffic cycle, `unsigned == 0` **and** `signed_failed == 0` for
that operation across every active counterparty. The breakdown by code is what distinguishes
*"counterparty is not signing yet"* (promote later) from *"counterparty is signing wrong"* (fix
before promoting) — a single aggregate number cannot tell those apart, and promoting on the wrong
one 401s live traffic.

**Implementation consequence — the trap.** The SDK verifier cannot be told about `warn_for`
(divergence #1). B1 implements warn by calling the verifier, catching `SignatureVerificationError`,
emitting the metric, and continuing. Passing `warn_for` into `VerifierCapability` would be silently
dropped, degrading the operation to plain `supported_for` semantics — **which produces the identical
non-rejecting response**, so the bug is invisible at the wire level and shows up only as a missing
metric. **B1 owes a test that fails if warn degrades to `supported_for`.**

---

## 11. `SigningProvider` selection and the replay-store backend

### `SigningProvider`

`adcp.signing.provider.SigningProvider` (provider.py:85) is a 3-method Protocol: `async
sign(signature_base) -> bytes`, `key_id()`, `algorithm()`. `InMemorySigningProvider` (:163) ships.

**Config key `signing.provider` on the agent-level `SigningConfig`, enum `in_memory` (default) |
`kms`.** `in_memory` constructs `InMemorySigningProvider` from a PEM loaded via
`adcp.signing.load_private_key_pem`.

**A KMS/HSM provider is OUT of scope for this epic.** It is a 3-method Protocol, so a follow-up adds
one without touching a single caller. Two things the follow-up inherits, recorded here so they are
not rediscovered: the ECDSA `DigestSign`-not-double-hash requirement (provider.py:85-108) is a
KMS-integration concern with no in-memory analogue, and **selecting `kms` before that lands must
fail at config validation, not at first signature.**

### Replay-store backend (feeds A4)

**Decision: implement `adcp.signing.replay.ReplayStore` ourselves** — three methods (`seen`,
`remember`, `at_capacity`) over the existing SQLAlchemy session in a repository, with
`adcp/signing/pg/replay_store.sql` translated into an Alembic migration so the table is versioned
like every other.

**Not `adcp.signing.pg.PgReplayStore`**, which hard-requires `psycopg` + `psycopg_pool`
(`pg/replay_store.py:85-91`) while we pin `psycopg2-binary` (`pyproject.toml:20`, with
`types-psycopg2`) — `import psycopg` raises `ModuleNotFoundError` in this venv — **and** opens its
own `ConnectionPool`, which `test_architecture_repository_pattern.py` forbids and whose allowlist
may only shrink. Adding a second driver and a second pool to satisfy a three-method Protocol is not
a close call. A4's own ticket text reaches option (b) independently.

**The mitigation A4 must carry.** `seen`/`remember`/`at_capacity` (replay.py:21-28) are **sync**
`def`, and the verifier calls them **inline** at verifier.py:304 (step 9a), :348 (step 12), :358
(step 13) — from a sync function that `verify_starlette_request` invokes inside an async request.
A session-backed store therefore does 2-3 **blocking** Postgres round-trips on the event loop per
signed request. `PgReplayStore` would be no better (psycopg3 sync pool), so this is not an argument
between the options — it is a property of the SDK's Protocol. It is also the same class as the
owner-confirmed *"adapters must not run in the HTTP request cycle"* direction. Wrap the store calls
— or the whole `verify_request_signature` call — in `anyio.to_thread.run_sync`, with a dedicated
short-lived session per call, **never** the request-scoped UoW session (not thread-safe to share).

### The one place two named reuse targets do not compose

`verify_request_signature` (verifier.py:170) is **sync**, and `VerifyOptions.jwks_resolver`
(verifier.py:138) types the **sync** `JwksResolver` (jwks.py:91). But `BrandJsonJwksResolver`
(brand_jwks.py:320) — which implements the entire step-7 discovery chain with SSRF validation and
IP pinning — implements `AsyncJwksResolver` **only** (`async def resolve`, :405), and
`as_async_resolver` (jwks.py:508) converts sync→async, the wrong direction. `jwks.py` ships no sync
brand.json resolver: `CachingJwksResolver` and `StaticJwksResolver` take a bare `jwks_uri` and skip
discovery entirely.

**Decision:** the ASGI middleware **awaits** `BrandJsonJwksResolver.resolve(kid)` first, then passes
a `StaticJwksResolver` seeded with that key into the sync verify. Discovery I/O stays off the sync
path and the SDK's SSRF/IP-pinning chain stays intact.

**Rejected:** writing our own sync brand.json resolver — ~600 lines of SSRF and IP-pinning logic
duplicated, and ours to keep correct forever.

**Consequence:** A5 is *"wire + own the kid→JWK resolution step"*, not *"wire"*. B1 owns the
await-then-seed ordering.

---

## 12. The canonical per-tenant agent URL — FYI to the owner

`identity.brand_json_url`'s `verifier_constraints` demand `agent_url_match: byte_equal` against the
`agents[].agent_url` **we publish**. So what must be pinned is one published string, not a
per-request derivation.

Today `_create_dynamic_agent_card` (`src/app.py:346-388`) derives the agent card URL per request
from `Apx-Incoming-Host`, then `Host`, then `X-Forwarded-Proto` — **exactly the reverse-proxy
routing state security.mdx step 10 forbids relying on.** The same tenant can therefore publish
several different `agent_url` strings depending on which hostname the caller used, so a byte-equal
compare is a coin flip.

**Decision:** one canonical stored agent URL per tenant — `Tenant.virtual_host` when set, else
`https://{subdomain}.{base_domain}` — computed by a single function and emitted **byte-identically**
by the agent card, brand.json `agents[].agent_url`, and the JWKS pointer. `ADCP_AGENT_URL`
(`src/admin/blueprints/authorized_properties.py:224`) becomes the deployment-level base, not a
second source of truth.

**A3's acceptance becomes:** *the agent card and brand.json emit a byte-identical `agent_url` for
the same tenant.*

Flagged for the owner because it changes existing agent-card behavior. It does not block A1.

### Corrections from A3 (`salesagent-z6nr.9`), which implemented this

1. **The field is `agents[].url`, not `agents[].agent_url`.** `dist/schemas/3.1.1/brand.json`
   `#/definitions/brand_agent_entry` has `required: [type, url, id]` and no `agent_url` anywhere.
   Grep for the wrong name and you find nothing to match against.
2. **It is one entry PER ENDPOINT, not one origin.** security.mdx:1104 step 5 byte-equals the URL
   whose `get_adcp_capabilities` the counterparty invoked, and that is an endpoint — the schema
   calls `url` "Agent endpoint URL (MCP or A2A)" and :1104's own worked failure example is
   `https://x.com/mcp` vs `https://x.com/mcp/`. A bare origin byte-equals nothing anybody ever
   called. We publish `{origin}/mcp/` and `{origin}/a2a` (paths verified against the running app:
   `GET /mcp` 307s to `/mcp/`; `GET /a2a` does not redirect), each with a distinct `id`. The
   schema blesses this explicitly under `#/definitions/agents`. See SDK divergence #5 for why
   the SDK's `_pick_agent` is not evidence against it.
3. **The origin binding is eTLD+1 EQUALITY** (security.mdx:1102 step 3), not same-host. We serve
   brand.json on the tenant's own host, which is sufficient and strictly stricter — but nobody
   should later read "same host" as the normative rule.
4. **`ADCP_AGENT_URL` is ranked BELOW the tenant's own host**, not above it. As a top-priority
   override it would collapse every tenant onto one URL, which is the defect this section exists
   to remove. It is the base for deployments that have no per-tenant host at all.
5. Implemented in `src/core/agent_identity.py`. Two call sites migrated
   (`_create_dynamic_agent_card`, `_construct_agent_url`); `create_agent_card`
   (`adcp_a2a_server.py:2214`) is a static default that is always overridden per request and was
   deliberately left alone.

---

## 13. PR-body citation block

Every child PR of #1291 pastes this, filled in. It discharges CLAUDE.md's spec-grounding gate
per-PR without re-deriving anything.

```markdown
### Spec grounding

- **Pinned version:** AdCP 3.1.1 (`adcp==6.6.0`), guarded by `tests/unit/test_adcp_spec_version.py`
- **Spec source:** `adcontextprotocol/adcp` @ tag `v3.1.1` — read via `git show v3.1.1:<path>`
- **Mandating section:** `<dist/schemas/3.1.1/... #/pointer>` or
  `<dist/docs/3.1.0/building/by-layer/L1/security.mdx §section>` (read at tag v3.1.1)
- **Graded by:** `dist/compliance/3.1.1/universal/signed-requests.yaml` — vectors `<ids>` /
  BR-UC-010 tags `<tags>`   ·   or the literal word **ungraded**, with the reason
- **Design note:** `.claude/notes/rfc9421-signing-adoption.md` §`<n>`
- **SDK divergence touched:** none | #1 warn_for/protocol_methods | #2 spec-only codes | #3 body cache
```

Filled example, from B3:

```markdown
### Spec grounding

- **Pinned version:** AdCP 3.1.1 (`adcp==6.6.0`), guarded by `tests/unit/test_adcp_spec_version.py`
- **Spec source:** `adcontextprotocol/adcp` @ tag `v3.1.1` — read via `git show v3.1.1:<path>`
- **Mandating section:** `dist/docs/3.1.0/building/by-layer/L1/security.mdx` §"Verifier checklist
  (requests)" — the 15 checks, applied in order, short-circuiting on first failure
- **Graded by:** `dist/compliance/3.1.1/universal/signed-requests.yaml` against all 40 vectors at
  `dist/compliance/3.1.1/test-vectors/request-signing/{positive,negative}/` plus
  `canonicalization.json`; stateful vectors 016/017/020 additionally gated on
  `dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml`
- **Design note:** `.claude/notes/rfc9421-signing-adoption.md` §6, §7, §8
- **SDK divergence touched:** #1 (vectors 001/027/028 need operation and protocol-method binding
  that `VerifierCapability` cannot express)
```

---

## 14. Corrections this note makes to #1291's own tickets

Recorded so the next reader knows the epic text was wrong before it was right.

| Where | Was | Is |
|---|---|---|
| Epic body, DoD checkbox, child-index tree line, `salesagent-z6nr.14` title + description | "28 conformance vectors" | **40** (12 positive + 28 negative) + `canonicalization.json` |
| Epic body, this note's own brief | `dist/docs/<version>/building/implementation/*.mdx` | `dist/docs/3.1.0/building/by-layer/L1/security.mdx`, read at tag `v3.1.1` |
| Epic body | "27 codes … mapped to the verifier checklist step" | 27 confirmed; **15 graded** by the negative vectors, **12 integration-only** by the vectors' own README, **2 in prose with no SDK constant** |
| Everywhere | "the 14-check verifier checklist" | **15 checks** (14 numbered + 9a); the "14" in the quickstart and in `signed-requests.yaml` contradicts the enumerated body |
| `_UNBACKED_BLOCKS`, conftest park (fixed by D3 / #1729) | `offline_delivery_protocols` gated on #1291 | gated on **#1729**; `must_equal_when` has exactly three triggers and offline delivery is not one |
| A5 (`salesagent-z6nr.11`) scope | "wire `CachingRevocationChecker`" | wire **+ own the kid→JWK resolution step** (§11, sync/async) |

### The 27 `request_signature_*` codes

**15 graded** by the negative vectors: `alg_not_allowed`, `components_incomplete`,
`components_unexpected`, `digest_mismatch`, `header_malformed`, `invalid`, `key_purpose_invalid`,
`key_revoked`, `key_unknown`, `params_incomplete`, `rate_abuse`, `replayed`, `required`,
`tag_invalid`, `window_invalid`.

**12 integration-only** — the vectors' README: they *"do not exercise live JWKS fetch, brand.json
discovery, or revocation-list polling … those require live endpoints and belong in integration
suites"*: `revocation_stale`, `jwks_unavailable`, `jwks_untrusted`, `brand_json_url_missing`,
`capabilities_unreachable`, `brand_json_unreachable`, `brand_json_malformed`,
`brand_origin_mismatch`, `agent_not_in_brand_json`, `brand_json_ambiguous`, `key_origin_mismatch`,
`key_origin_missing`. All twelve are emitted by the §9.3 Group C subsystem.

**2 in the prose, in no SDK constant and no vector**: `request_target_uri_malformed` (step 10),
`request_body_malformed` (step 14). We emit both from our own layer and file the gap upstream.
