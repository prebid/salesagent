# RETIRED: tests/unit/test_webhook_auth_resolver.py

Deleted in Epic D lane C4 (salesagent-fo99.4), together with its subject.

## Why it is gone rather than rewritten

Its subject was `webhook_auth_for(scheme, credentials) -> WebhookAuth` and the
five-variant hand-rolled union it returned. Owner ruling #4 deleted both:

> "why there is anything besides just a clear branching based on the inner enum
> ... and if the enum is not in the list of supported enums then just throw"

The union existed to stop three senders each answering "how is this
authenticated?" their own way. That goal is unchanged and is now met by a
different carrier: the pinned AdCP `Authentication` itself, imported as
`LibraryAuthentication` in `src/core/security/webhook_egress.py` and matched
once inside `deliver_webhook`/`adeliver_webhook`, so constructing it enforces
the scheme enum,
`maxItems: 1`, and `credentials` minLength 32 in one call — and the egress seam
matches on that once, returning a `WebhookDeliveryOutcome`. No sender sees an
auth decision at all, so none can get it wrong.

This file's own docstring recorded the resolver's justification: "The return
type is the point of the ticket". That reasoning was right for the union and is
superseded by a type derived from the spec rather than maintained by us.

## Where its obligations live now

Every case it graded has a successor, so nothing was dropped:

| retired case | successor |
|---|---|
| scheme absent → unauthenticated | `test_webhook_delivery_outcome_contract.py` — the plain-delivery row of the match table |
| scheme present, credential missing → refuse | same file, `no_credentials` row |
| unknown scheme → unauthenticated | same file, `scheme_not_in_spec` row — **reversed by owner ruling #2**: it now refuses rather than silently delivering plain |
| lowercase spelling still resolves | same file, the canonicalisation row; and the pinned `Authentication`'s before-validator |
| HMAC + credential → sign | same file, the signed-delivery row, asserted over the exact wire bytes |

The seam-contract test is stronger than what it replaces: it asserts the
observable outcome (bytes on the wire, headers, attempts, refusal reason)
rather than the shape of an intermediate value.
