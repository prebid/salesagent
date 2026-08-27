# Signing key runbook

Operator-facing. Signing introduces the first cryptographic key material this agent
operates, and an undocumented rotation is how a deployment ends up with every signature
failing at once.

Counterparty-facing companions: [Signing posture and key discovery](../signing/posture-and-discovery.md)
and [Verifying our outbound webhooks](../signing/verifying-our-webhooks.md).

## Provisioning a key

Keys are minted by `src.core.signing.keys.provision_signing_key`. There are two
transports over that one function and no third: the ops script and the admin route.

```bash
uv run python scripts/ops/provision_signing_key.py --tenant-id publisher_1
uv run python scripts/ops/provision_signing_key.py --tenant-id publisher_1 \
    --ref-scheme env --env-var-name ADCP_SIGNING_KEY_PEM
```

Admin UI: the signing-keys route, under the tenant.

**Nothing is written to a filesystem.** There is no key file to `chmod`, no `O_EXCL`
handling rule, and no plaintext key at rest. A `db:` mint — the default, and the only
scheme this agent mints for ordinary use — stores the private PEM **encrypted on the
key's own row** under a deployment-wide key encryption key (KEK).

### The KEK is required, and there is no fallback

The KEK is named by `SigningConfig.key_passphrase_env` (`ADCP_SIGNING_KEY_PASSPHRASE_ENV`),
which holds the **name of the environment variable** carrying the passphrase — the
passphrase itself is never a configuration value.

With no KEK configured, a `db:` mint **refuses** with `AdCPConfigurationError` naming the
knob to set. It does not fall back to storing plaintext. The script exits non-zero with
that message rather than a traceback, because the operator's next action is a setting,
not a bug report.

Provisioning validates in this order, and each step is a precondition for the next:

1. the requested scheme is one this deployment will resolve, checked **before** any key
   material exists;
2. a `db:` mint requires the KEK;
3. the keypair is minted;
4. the private half is loaded back and re-derives the public JWK about to be stored —
   for `db:` this also proves the KEK round-trips;
5. only then is the row created.

So a key that exists is a key that was proven openable at the moment it was created.

### Schemes

| Scheme | Mintable | Where the private half lives |
|---|---|---|
| `db` | yes (default) | Encrypted on the `signing_keys` row, under the deployment KEK |
| `env` | yes | Handed back **once** for the operator to export |
| `file` | no | Names material provisioned by something else, onto a mounted secret |

An `env:` mint prints the PEM to stderr once and stores it nowhere. Until the signing
process has it exported under the name you gave, the published JWK has no resolvable
private half.

### Provisioning does not make a host route

The public half is published at the tenant's `/.well-known/jwks.json` the moment the row
exists. A tenant with no routable `virtual_host` or `subdomain` therefore has a key
nobody can fetch. Check routing before treating provisioning as done.

## Rotation

The model is **overlap**: publish before you sign, and keep publishing after you stop.
Both halves have a derivation, not a vibe.

1. **Provision the incoming key.** It is minted open-ended (`not_after` NULL) and becomes
   publishable immediately. It does not yet sign anything: when two keys are both active,
   the newest signs, and the tie-break is total (`created_at DESC, kid ASC`), so two rows
   created in one transaction cannot produce a nondeterministic signer.
2. **Wait for verifier caches to pick it up, before it signs.** We publish
   `Cache-Control: max-age=300` on every trust-root response, so a well-behaved verifier
   refreshes within **5 minutes**. Verifiers you do not control may hold a resolution
   longer — our own counterparty resolution cache is `agent_resolution_ttl_seconds`,
   default **3600 s**, which is a reasonable upper bound to assume of someone else's.
   **Wait 5 minutes at minimum; wait 1 hour if you cannot tolerate a single counterparty
   failing a verification.**
3. **Start signing with the new key.** Close the outgoing key's window (`not_after`) so
   the incoming one is the newest active key.
4. **Keep the outgoing key PUBLISHED until every signature it made has expired.** The
   longest signature lifetime our profile permits is `max_window_seconds` (**300 s**) and
   the tolerated clock skew is `max_skew_seconds` (**60 s**), so a signature made an
   instant before the switch can still be presented as valid for **360 s**. Wait at least
   that.
5. **Retire it — see the imperative below.**

### Retirement MUST set `revoked_at`

Not `not_after`. The intuitive action is the wrong one, and this is the paragraph that
exists to stop someone rediscovering it during an incident.

Two queries govern a key's life and they are deliberately asymmetric:

- `active_at` — which key we **sign** with. Governed by the half-open window
  `[not_before, not_after)`, with `revoked_at` beating the window.
- `publishable_at` — which keys we **publish**. Ignores **both** bounds. Its only exit is
  `revoked_at`, delayed by a grace period.

`publishable_at` ignores `not_after` on purpose: un-publishing a key whose window has
closed strands every signature it made that is still inside its verification window —
the exact gap the overlap exists to prevent. It ignores `not_before` on purpose too, so a
rotation can publish the incoming key before it signs.

The consequence is a requirement on you, not a caveat: **closing the window retires a
SIGNER and never a PUBLICATION.** A key retired only by `not_after` stays published
forever.

Revoke through the admin route (`/<kid>/revoke`) or the repository's `revoke()`.

## Revocation

### What revoking does

`revoked_at` immediately removes the key from `active_at` — it stops signing at once,
even if its window is open — and starts a grace period before it leaves the published
documents. `SigningConfig.grace_seconds` defaults to **600 s**, which is 2× the
`max-age=300` we publish on the trust root: long enough for a cache that just missed a
refresh to make the next one.

During grace the key stays in brand.json and the JWKS **carrying its revocation marker**,
so a verifier whose cache has not refreshed still finds it and can evaluate the marker. A
JWKS entry without the marker is indistinguishable from a live key, which is why we carry
it rather than dropping the key.

### How counterparties learn

We publish a combined revocation list at our brand.json origin
(`/.well-known/governance-revocations.json`, salesagent-z6nr.27) — a JWS general-JSON
document signed by the tenant's currently ACTIVE request-signing key, whose payload
carries `revoked_kids`: the PERMANENT record of every key we have ever revoked, unbounded
by the publication grace window. A counterparty learns that one of our keys is revoked
through any of:

- the revocation marker on the key, while it is still inside the grace window;
- the key's absence from our JWKS, after the grace window has elapsed;
- the `revoked_kids` entry in the governance-revocations list, which never ages out.

The list itself is withdrawn (404) once no active request-signing key remains to sign it
— a tenant with no live key cannot vouch for its own revocation history, so it fails
closed rather than serving a list signed by a dead key. Plan rotations on the grace
window, and do not assume a counterparty can be told about a compromised key faster than
their cache refresh.

### Propagation latency for keys we CONSUME

The other direction — how quickly *we* learn that a counterparty's key was revoked — is
governed by the issuer, not by us. The SDK's `CachingRevocationChecker` refetches the
list when `now >= next_update` on the list itself, so **propagation is the issuer's
publishing interval**. Our `revocation_grace_multiplier` (default **4.0**, the spec floor)
sets how far past `next_update` a cached list may go stale before we start rejecting with
`request_signature_revocation_stale` — it is a **staleness ceiling, not a speed**.

The checker cache is process-level and keyed by issuer origin, with the SDK owning
freshness inside it. Restarting the process discards it.

If a counterparty publishes no readable revocation list, `require_revocation_list`
(default `false`) decides whether they are served or rejected. When it is false and a
list cannot be read, the fail-open path increments `request_revocation_unavailable_total`
— watch that series rather than assuming silence means health.

## Rollout ladder

Promote one operation at a time, per tenant.

`supported_for` → `warn_for` → `required_for`

- **`supported_for`** — we verify a signature if one arrives and accept the request if
  none does. Nothing can break.
- **`warn_for`** (shadow mode) — same acceptance, but the traffic is graded. Watch
  `request_unsigned_total{operation,reason}`. `reason="absent"` means the request carried
  no signature headers at all; `reason="ignored"` means headers were present but the
  posture put the operation in the `none` bucket. Watch `request_signature_failed_total`
  alongside it: a counterparty who is signing but signing *wrongly* shows up there, not
  in the unsigned series, and promoting on the unsigned rate alone will break them.
- **Promotion threshold** — `request_unsigned_total{reason="absent"}` at zero for the
  operation across a full traffic cycle for that counterparty (a week covers weekly
  batch integrations), **and** `request_signature_failed_total` flat. Both, not either.
- **`required_for`** — unsigned requests to the operation are rejected.

## Rollback

There are two mechanisms. **Choose by blast radius, not by convenience** — reaching for
the wrong one during an incident turns one tenant's rollback into everyone's.

| Reach for | When | Blast radius |
|---|---|---|
| **Per-tenant declaration edit** | One counterparty broke after a promotion | That tenant only |
| **`verifier_enabled = false`** | The verifier itself is the problem | Every tenant, every operation |

**Per-tenant** — the posture lives in the tenant's `capability_declarations`. Remove the
operation from `required_for` (drop it to `warn_for` to keep grading it). This is a data
change; it takes effect on the next request.

**Deployment-wide** — `SigningConfig.verifier_enabled = false` makes every request pass
through the middleware untouched. It is a kill switch and it is deliberately a flag, so
that a rollback is a flag flip and not a deploy.

**Neither is a deploy.** If you find yourself preparing a release to roll back a signing
posture, stop — you are using the wrong mechanism.

## Two failure modes that will be misread

### A keyless tenant cannot activate notification subscribers

Activating a notification subscriber sends a proof-of-control challenge to the candidate
webhook URL, and that challenge **must** be signed with the seller's RFC 9421 webhook
profile key — even when the candidate registration selects legacy delivery auth. A tenant
with no usable signing key cannot produce one, so the activation **fails closed** rather
than sending an unsigned challenge.

This is deliberate, and it will be reported to you as a network fault. The distinguishing
evidence is a WARNING logged **once per tenant per process**, naming
`scripts/ops/provision_signing_key.py`. The fix is to provision a key, not to retry the
activation.

### A key minted under a KEK that was later unset

The private PEM on a `db:` row is ciphertext. Remove the KEK from the environment and it
becomes a key nobody can open — while its public half is still published.

The agent handles this honestly rather than optimistically: the tenant reports
`webhook_signing.supported: false` rather than advertising `true` and raising on every
delivery. The WARNING for "a key I cannot open" is deliberately **distinct** from the one
for "no key at all", so you can tell a deployment misconfiguration from a provisioning
gap.

**Recovery: restore the KEK. Do not re-mint.** Re-minting publishes a second JWK and
strands the first — you will have two published keys, one of which nobody can use, and
the one already trusted by counterparty caches is the stranded one.

## Not covered yet

**Compliance grading.** Pointing the `@adcp/sdk` runner at a sandbox grading endpoint is
not documented here because that endpoint does not exist yet; it is tracked separately.
When it lands, its runbook section belongs with it rather than here.
