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
# The last three add the BUCKET as that variable (salesagent-nx8jp.10): `supported` and
# `required` are the two a tenant can declare, and the narrowed `none` bucket — a seller
# that verifies, for no operation — is the third arm the credential escalation has to
# override.
#
# Reconcile upstream in adcp-req (a "seller enforces inbound request signatures"
# storyboard), then retire this file in favor of the regenerated one.
#
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1268-L1269
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1375
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1462-L1465
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/test-vectors/request-signing/negative/027-webhook-registration-authentication-unsigned.json pointer=/expected_outcome
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1028
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1088-L1094
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1100
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1120
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1212
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1216
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml pointer=L39-L60
# @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml pointer=L108-L126
# @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L1240
Feature: Inbound request-signature enforcement on an AdCP operation (local)

  @T-UC-006-local-signing-required-unsigned @request-signing @error-path @invariant
  Scenario: an unsigned request to a required_for operation is refused
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_required" and recovery "correctable"
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
    Then the seller answers with the request-signature challenge "request_signature_required" and recovery "correctable"
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
    Then the seller answers with the request-signature challenge "request_signature_header_malformed" and recovery "correctable"
    # security.mdx @ v3.1.1 :1226 — a verifier MUST NOT fall back to bearer-only auth when a
    # malformed signature is present, "even for operations not in `required_for`". That "even
    # for" is a QUANTIFIER OVER BUCKETS, and a quantifier graded at one bucket is not graded:
    # the Examples table below is the quantifier, on every wire transport.
    # The bucket is the ONLY variable between the rows — same operation, same key, same
    # malformed headers — so a row that answers differently is attributable to the bucket.
    # `supported` is the row that makes the pre-check claim non-trivial in the narrow sense
    # the spec means: an operation NOT in `required_for` still refuses.

    Examples:
      | bucket    |
      | required  |
      | supported |

  @T-UC-006-local-signing-unbucketed-waves-through @request-signing @invariant
  Scenario: a signed-but-invalid request completes when the seller grades no operation
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller verifies request signatures but grades the creative sync in no bucket
    And the Buyer Agent signs a different rendering of the request
    When the Buyer Agent syncs the creative
    Then the creative should be processed successfully
    And the seller recorded exactly 0 "request_signature_digest_mismatch" signature failure
    # The narrowed `none` bucket: this seller VERIFIES (`supported: true`) and grades this
    # operation in no bucket, so a signature it would otherwise reject is ignored and the
    # request is served on its bearer alone. security.mdx @ v3.1.1 :1226 binds the PRE-CHECK
    # here — a malformed header still refuses, which the Outline above grades — and nothing
    # else, so a cryptographically real signature over different bytes is waved through.
    # THE PAIR IS THE ORACLE. The completion alone is equally true of a seller that never
    # looked at the request; the zero count is what says no failure was INVENTED for it —
    # the narrowed bucket verifies against an empty key resolver, so its step-7
    # `key_unknown` is engineered by this seller and must not reach the series an operator
    # reads for real refusals. Together they establish that the refusal below is
    # attributable to the CREDENTIALS and not to the posture.

  @T-UC-006-local-signing-supported-refuses-checklist @request-signing @error-path @invariant
  Scenario: the same signed-but-invalid request is refused under supported
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller places "sync_creatives" in the "supported" request-signature bucket
    And the Buyer Agent signs a different rendering of the request
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_digest_mismatch" and recovery "terminal"
    # ONE variable apart from the scenario above: the same operation, the same key, the same
    # tampered bytes — the bucket. This is the control that makes that completion mean
    # something: without it, "the request completed" is equally explained by a verifier that
    # never rejects a digest mismatch at all, and the unbucketed scenario would grade nothing.

  @T-UC-006-local-signing-credentials-escalate-signed @request-signing @error-path @invariant @boundary
  Scenario: a signed-but-invalid registration carrying credentials is refused where the same request without them completes
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller verifies request signatures but grades the creative sync in no bucket
    And the Buyer Agent signs a different rendering of the request
    And the request registers a webhook whose authentication carries credentials
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_digest_mismatch" and recovery "terminal"
    # ONE variable apart from "a signed-but-invalid request completes when the seller grades
    # no operation" above: the same operation, the same key, the same tampered bytes, the same
    # posture — the request now hands over webhook CREDENTIALS. That scenario COMPLETES and
    # this one is REFUSED, and the flip is the whole claim: security.mdx @ v3.1.1 :1462-1465
    # makes the credentials force a signature and :1375 says the escalation fires "regardless
    # of `required_for` membership", so a bucket that grades nothing must still refuse here.
    # THE SIGNED PATH IS WHAT IS NEW. The credential escalation is graded elsewhere only on
    # UNSIGNED requests ("a registration carrying webhook authentication is refused unless
    # signed" above, and the five integration cases beside it), and the seller promotes the
    # bucket in TWO places — once for the whole request, once again inside the unsigned
    # branch. Disarm the first and every unsigned grader stays green, because the second still
    # answers. A request that CARRIES signature headers reaches only the first, which is why
    # this scenario has to be signed: the escalation exists precisely so that ATTACHING a junk
    # Signature cannot buy what omitting one is refused.
    # `none` is also the arm where the promotion does the MOST work: without it this request
    # reaches no checklist at all, so the refusal cannot be explained by anything the posture
    # says.

  @T-UC-006-local-signing-onboarded-no-bearer @request-signing @invariant
  Scenario: a request signed by an onboarded counterparty is served without a bearer
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller onboarded the Buyer Agent's signing key
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    And the Buyer Agent signs the request
    When the Buyer Agent syncs the creative
    Then the creative should be processed successfully
    And the seller verified exactly 1 request under the Buyer Agent's published key
    # security.mdx @ v3.1.1 L1088-1090: given a keyid, the verifier resolves the signing
    # agent's agents[] entry, and "Discovery MAY come from prior onboarding". The seller
    # recorded the keyid against the Buyer's agent URL; the keys come from the agent's own
    # origin. The signature is the only credential (L1216, L631-632).

  @T-UC-006-local-signing-onboarded-other-tenant @request-signing @error-path @invariant
  Scenario: a keyid onboarded only by another seller is not resolved
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the Buyer Agent's signing key was onboarded only by another seller
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    And the Buyer Agent signs the request
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_key_unknown" and recovery "terminal"
    # ONE variable apart from the scenario above: the onboarding record belongs to another
    # tenant. A seller resolves a keyid against its own onboarding records; L1094 forbids
    # accepting a keyid it cannot resolve to a specific agents[] entry.

  @T-UC-006-local-signing-bearer-never-borrows-a-key @request-signing @error-path @invariant
  Scenario: a bearer-authenticated caller is never verified against another counterparty's onboarded key
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the Buyer's onboarding record names no agent URL
    And the Buyer Agent's signing key was onboarded for another counterparty of this seller
    And the seller requires a request signature for "sync_creatives"
    And the Buyer Agent signs the request
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_key_unknown" and recovery "terminal"
    # The Buyer keeps its bearer. On the bearer transport identity comes from the
    # credential-to-agent mapping in the onboarding record (L1216), and the Buyer's names no
    # agent URL, so no key resolves for it. The other counterparty sits at the Buyer
    # Agent's published origin: a verifier that looked the keyid up for a bearer caller
    # would verify and serve this request.

  @T-UC-006-local-signing-test-counterparty @request-signing @invariant
  Scenario: the signed-requests test counterparty is served from its accepted JWKS without a bearer
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the Buyer is the signed-requests test counterparty, accepting the Buyer Agent's keys
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    And the Buyer Agent signs the request
    When the Buyer Agent syncs the creative
    Then the creative should be processed successfully
    And the seller verified exactly 1 request under the Buyer Agent's published key
    # signed-requests-runner.yaml @ v3.1.1 L55-60: the agent "MUST treat these keyids as a
    # registered test counterparty whose JWKS contains the corresponding public keys".
    # The Buyer is that counterparty: its keyid is onboarded for it, its agent URL publishes
    # nothing, so only the accepted JWKS on its own record can verify the signature.

  @T-UC-006-local-signing-test-counterparty-replay-cap @request-signing @error-path @invariant
  Scenario: the signed-requests test counterparty meets its record's replay cap
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the Buyer is the signed-requests test counterparty, accepting the Buyer Agent's keys, with a replay cap of 1
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    And the Buyer Agent signs the request
    And that creative was already synced with idempotency_key "sig-replay-cap-0001"
    When the Buyer Agent syncs the creative
    Then the seller answers with the request-signature challenge "request_signature_rate_abuse" and recovery "terminal"
    And the seller verified exactly 1 request under the Buyer Agent's published key
    # runner.yaml @ v3.1.1 L108-126: the agent MUST reject the (N+1)th distinct-nonce request
    # with request_signature_rate_abuse once the per-keyid cap is hit, and MAY lower the cap
    # for the test-kit counterparty only. Each signature carries a fresh nonce, and the shared
    # idempotency_key only makes the second sync a retry that the verifier runs ahead of. The
    # first sync leaves one live replay entry, so the second meets the record's cap of 1 at
    # checklist step 9a (security.mdx L1240; "No (block new)" at L1392). A verifier that kept
    # the deployment's cap for the test counterparty would serve it.

  @T-UC-006-local-signing-verified-label-identifies @request-signing @invariant
  Scenario: an onboarded counterparty is identified by the signature label the seller verifies
    Given a creative with a known format_id
    And the Buyer Agent has published a signing key the seller can resolve
    And the seller onboarded the Buyer Agent's signing key
    And the seller requires a request signature for "sync_creatives"
    And the Buyer has no authentication credentials
    And the Buyer Agent signs the request with an additional signature label ahead of it naming a key nobody onboarded
    When the Buyer Agent syncs the creative
    Then the creative should be processed successfully
    And the seller verified exactly 1 request under the Buyer Agent's published key
    # ONE variable apart from "a request signed by an onboarded counterparty is served
    # without a bearer": an unverified label placed first. L1028: verifiers MUST process
    # exactly one label (conventionally sig1) and MUST ignore any additional labels, so the
    # keyid that selects the counterparty is the verified label's (L1212).
