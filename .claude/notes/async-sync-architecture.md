# Outbound I/O in the request cycle — the rule, and the one carve-out

## The rule

Outbound I/O does **not** belong in the HTTP request cycle. The direction for
adapter work is: accept → validate against Postgres → return `201 pending` →
a background worker calls GAM/Kevel/etc → update status → notify.

The current synchronous adapter I/O is why `run_async_in_sync_context` exists.
That helper is a band-aid, not a pattern to copy. See
[issue-draft-async-adapter-architecture.md](issue-draft-async-adapter-architecture.md).

## The carve-out: notification activation proof (#1592 T2)

**Granted by KonstantinMirin, 2026-07-27.** Narrow, and not a precedent.

`sync_accounts` performs a proof-of-control challenge — one outbound HTTPS POST —
before it will persist or echo an account notification subscriber as
`active: true`. That POST happens inside the request cycle.

### Why the async shape is not available here

This is the unusual case where the spec forbids the accept-now-verify-later
pattern:

- There is no per-subscriber pending state. `NotificationConfig.active` is a
  boolean, and the per-entry action enum is `created` / `updated` / `unchanged` /
  `failed` — there is no `pending`.
- The response echoes subscriber state *after* activation-proof checks.

So every async variant is non-conformant: echoing `active: true` before the proof
violates a MUST; echoing `active: false` misstates the applied state; and flipping
the flag in a worker behind a synchronous "updated" tells the buyer something that
was not true when it was said. If the response is synchronous, the proof outcome
has to be in it.

(The fully-conformant alternative — return the whole operation as `submitted` and
deliver per-entry results through `push_notification_config` — is real, but
`sync_accounts` is 100% synchronous today, no storyboard grades that path, and the
machinery is large. It stays available if activation ever needs to scale.)

### What the carve-out permits, exactly

`src/services/notification_proof_service.py`:

- **one** POST per subscriber, no retries;
- a hard per-challenge timeout (`CHALLENGE_TIMEOUT_SECONDS = 2.0`) and a
  per-request budget (`_PROOF_BUDGET_SECONDS` in `src/core/tools/accounts.py`), so
  a buyer cannot hold a worker with 16 configs × N accounts;
- fired **only** for an entry declaring `active: true` whose proof tuple is not
  already persisted (the spec's proof-reuse allowance);
- **fail-closed** — anything that is not a clear 2xx is "not proven";
- injected through `get_notification_proof_service()`, so tests override the
  getter and production always holds a real prover.

### What it does NOT permit

- **Not inside a transaction.** `_resolve_activation_proofs` runs *before* the
  write `AccountUoW` opens. The read-only lookup it needs for proof-reuse happens
  in its own short transaction that closes before any socket is opened. Holding a
  Postgres transaction across a network round trip is exactly what this carve-out
  does not cover.
- **Not a template for adapter calls.** An adapter provisioning call is unbounded,
  retryable, and has a conformant async shape available. This is a single bounded
  handshake whose result the response is required to contain.
- **Not an auto-pass prover.** A prover that returns True without asking would
  persist `active: true` without proof — the precise violation the service exists
  to prevent. The test seam is the getter, and the default is exercised by real
  seeding rather than assumed.

### Known gap

The challenge POST is **not** RFC 9421-signed — signing is not implemented
(`FIXME(#1291)`). Under the STRICT capability policy the seller therefore declares
no signing capability. A buyer cannot yet verify that a challenge came from us.
