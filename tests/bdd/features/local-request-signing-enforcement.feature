# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Upstream gap: the request-signing obligations are graded upstream ONLY by the
# 40 static conformance vectors (dist/compliance/3.1.1/test-vectors/request-signing/*),
# which are byte-level fixtures replayed against a verifier — there is no storyboard
# scenario that drives a REAL buyer request through a REAL seller deployment and grades
# whether it was refused or verified. Every one of the three obligations below was
# therefore asserted in this repo by code shape and by per-transport unit tests, which
# is precisely how the A2A credential-location bypass (SF-4) survived a review:
# the property "a signed request accepted, an unsigned one refused, IDENTICALLY on every
# transport" is a CROSS-transport property, and nothing cross-transport graded it.
#
# These scenarios are that grading. Each differs from its neighbour by exactly ONE
# variable, and all of them run on the same operation through the same env, so a
# difference in outcome is attributable to the variable and not to the setup.
#
# The last three add the BUCKET as that variable (salesagent-nx8jp.10). `warn_for` is
# this repo's extension — the SDK's VerifierCapability drops it, and the string appears
# zero times in the 40 conformance vectors — so the one rule that separates a pre-check
# failure (refused in EVERY bucket, warn included) from a checklist failure (suppressed
# by warn alone) is reachable from no upstream artifact at all.
#
# Reconcile upstream in adcp-req (a "seller enforces inbound request signatures"
# storyboard), then retire this file in favor of the regenerated one.
#
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1268-L1269
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1375
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1462-L1465
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/test-vectors/request-signing/negative/027-webhook-registration-authentication-unsigned.json pointer=/expected_outcome
Feature: Inbound request-signature enforcement on an AdCP operation (local)

  @T-UC-006-local-signing-required-unsigned @request-signing @error-path @invariant
  Scenario: an unsigned request to a required_for operation is refused
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_required"
    # The composition rule, security.mdx @ v3.1.1 :1268-1269: a seller MUST NOT refuse an
    # unsigned request for the missing signature when the caller "presents another
    # credential the agent accepts" (:1269) — so `required_for` alone does NOT make an
    # authenticated unsigned request a 401, and the caller here presents NO credential
    # (:1268), which is the only branch that reaches the refusal.
    # The oracle is the CHALLENGE, byte-exactly, never the status: a bare 401 is equally
    # produced by the auth middleware rejecting first, by a 404 wearing a 401, and by the
    # malformed-header precheck.

  @T-UC-006-local-signing-verified @request-signing @invariant
  Scenario: a request signed by a counterparty the seller can resolve is accepted
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller requires a request signature for "sync_creatives"
    And the Buyer Agent signs the request
    When the Buyer Agent syncs the creative
    Then the seller verified exactly 1 request under the Buyer Agent's published key
    # ONE variable apart from the scenario above: the same operation, the same posture,
    # the same seller — a signature, and a credential the seller accepts.
    # "Accepted" is NOT graded as a 2xx, and cannot be: a 200 is equally true of a
    # middleware that never looked at the request, and of an operation whose posture
    # bucket collapsed to `none`. The seller's own record of WHICH key it verified —
    # matching the key this buyer published, under a kid unique to this run — is what
    # separates "verified" from "waved through".

  @T-UC-006-local-signing-webhook-credentials @request-signing @error-path @invariant @boundary
  Scenario: a registration carrying webhook authentication is refused unless signed
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller supports request signatures but requires them for no operation
    And the request registers a webhook whose authentication carries credentials
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_required"
    # security.mdx @ v3.1.1 :1462-1465 — "sellers that support request signing MUST require
    # the inbound request to be 9421-signed ... when `authentication` is present", restated
    # at :1375 as a trigger that fires "regardless of `required_for` membership".
    # The posture declares `supported` and requires the operation NOWHERE, which is the
    # pinned vector's own `verifier_capability` ({supported: true, required_for: []}) and is
    # LOAD-BEARING: with the operation in `required_for` the refusal could equally come from
    # the composition rule, and the scenario would stop grading the escalation at all.
    # The buyer here IS authenticated — the opposite of the first scenario — because the
    # escalation is deliberately NOT subject to the composition rule's exemption: the
    # registering request is normally bearer-authed, and an on-path mutator injecting or
    # stripping the `authentication` block is exactly what the MUST exists to stop.
    # WHERE the credential travels is the TRANSPORT's business, not this scenario's, and it
    # is not the same place on every transport: the env puts it where each transport's
    # production code READS it. That difference is the point — see the a2a result.

  @T-UC-006-local-signing-malformed-every-bucket @request-signing @error-path @invariant @boundary
  Scenario Outline: a present-but-malformed signature is refused in every bucket the seller declares
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller places "sync_creatives" in the "<bucket>" request-signature bucket
    And the Buyer Agent sends a signature the seller cannot parse
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_header_malformed"
    # security.mdx @ v3.1.1 :1226 — a verifier MUST NOT fall back to bearer-only auth when a
    # malformed signature is present, "even for operations not in `required_for`". That "even
    # for" is a QUANTIFIER OVER BUCKETS, and a quantifier graded at one bucket is not graded:
    # the Examples table below is the quantifier, three rows on every wire transport.
    # The bucket is the ONLY variable between the rows — same operation, same key, same
    # malformed headers — so a row that answers differently is attributable to the bucket.
    # The WARN row is the sentinel. `warn_for` suppresses a CHECKLIST failure (:1273) and
    # must NOT suppress this one, which fails the pre-check at checklist step 1, above the
    # bucket; `required` and `supported` refuse signed-but-invalid requests anyway, so they
    # would keep answering this challenge even if the pre-check phase were removed.

    Examples:
      | bucket    |
      | required  |
      | warn      |
      | supported |

  @T-UC-006-local-signing-warn-suppresses-checklist @request-signing @invariant
  Scenario: a signed-but-invalid request completes under warn
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller places "sync_creatives" in the "warn" request-signature bucket
    And the Buyer Agent signs a different rendering of the request
    When the Buyer Agent syncs the creative
    Then the creative should be processed successfully
    And the seller recorded exactly 1 suppressed "request_signature_digest_mismatch" signature failure
    # security.mdx @ v3.1.1 :1273 scopes `warn_for` to signed-but-invalid requests — the
    # verifier runs its checklist, FAILS, and serves the request anyway. "Signed-but-invalid"
    # is a cryptographically REAL signature over a different rendering of the body, so the
    # verifier gets past the pre-check on its merits and reaches the digest mismatch INSIDE
    # the checklist, which is the arm `warn_for` governs.
    # THE PAIR IS THE ORACLE, and neither half grades this alone. A completion alone is
    # equally true of a middleware that never looked at the request; a recorded failure alone
    # is equally true of the 401 the `supported` scenario below asserts. Together they say the
    # middleware ran the checklist, recorded exactly one failure, and continued.
    # Body replay is what /mcp and /a2a add here: the verifier consumed the request body to
    # compute the digest, so a warn continuation has to hand the SAME bytes to the
    # application. The REST shadow-mode ladder cannot reach that — it runs on a bodyless
    # path — so this claim is graded on those two transports for the first time.

  @T-UC-006-local-signing-supported-refuses-checklist @request-signing @error-path @invariant
  Scenario: the same signed-but-invalid request is refused under supported
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller places "sync_creatives" in the "supported" request-signature bucket
    And the Buyer Agent signs a different rendering of the request
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_digest_mismatch"
    # ONE variable apart from the scenario above: the same operation, the same key, the same
    # tampered bytes — the bucket. This is the control that makes the warn completion mean
    # something: without it, "the request completed" is equally explained by a verifier that
    # never rejects a digest mismatch at all, and the warn scenario would grade nothing.

  @T-UC-006-local-signing-warn-credentials-escalate @request-signing @error-path @invariant @boundary
  Scenario: a signed-but-invalid registration carrying credentials is refused under warn
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller places "sync_creatives" in the "warn" request-signature bucket
    And the Buyer Agent signs a different rendering of the request
    And the request registers a webhook whose authentication carries credentials
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_digest_mismatch"
    # ONE variable apart from "a signed-but-invalid request completes under warn" above: the
    # same operation, the same key, the same tampered bytes, the same bucket — the request
    # now hands over webhook CREDENTIALS. That scenario COMPLETES and this one is REFUSED,
    # and the flip is the whole claim: security.mdx @ v3.1.1 :1462-1465 makes the credentials
    # force a signature and :1375 says the escalation fires "regardless of `required_for`
    # membership", so `warn_for` must NOT suppress the checklist failure the way it does for
    # the neighbour above. Same shape as the malformed row of the Outline, on the other trigger.
    # THE SIGNED PATH IS WHAT IS NEW. The credential escalation is graded elsewhere only on
    # UNSIGNED requests ("a registration carrying webhook authentication is refused unless
    # signed" above, and the five integration cases beside it), and the seller promotes the
    # bucket in TWO places — once for the whole request, once again inside the unsigned
    # branch. Disarm the first and every unsigned grader stays green, because the second still
    # answers. A request that CARRIES signature headers reaches only the first, which is why
    # this scenario has to be signed: the escalation exists precisely so that ATTACHING a junk
    # Signature cannot buy what omitting one is refused.
    # `warn` and not a narrowed `none`: `none` is the purer arm — no checklist runs there at
    # all — but it has no one-variable neighbour in this file, and every scenario here differs
    # from its neighbour by exactly one variable. The cost is recorded rather than hidden:
    # this file grades the WEAKER of the two un-promoted arms.
