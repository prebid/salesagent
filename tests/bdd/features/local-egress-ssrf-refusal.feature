# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Spec grounding — AdCP 3.1.1, the version this repo PINS (adcp==6.6.0, see
# docs/adcp-spec-version.md). Read the pinned prose with
# `git -C <adcp-checkout> show v3.1.1:docs/building/by-layer/L1/security.mdx`
# (`dist/docs/` stops at 3.1.0 at that tag), § "Webhook URL validation (SSRF)":
#   point 1 — reject non-HTTPS URLs;
#   point 2 — reject reserved-range addresses (169.254.169.254 named outright);
#   point 6 — "Do not echo fetch errors to the agent that supplied the URL …
#             a side-channel for probing internal network topology."
# "Any URL that a buyer, seller, or governance agent provides for another party
# to fetch is an SSRF vector" — property_list.agent_url is one of those URLs.
#
# The conformance storyboard does NOT grade this. Nothing in
# dist/compliance/3.1.1/ grades a seller refusing a counterparty-supplied URL;
# the three `ssrf` hits there are guardrails the RUNNER applies to its OWN
# outbound fetches. So there is no BR-UC-* scenario to inherit — hence a local
# feature. Reconcile upstream in adcp-req (a get_products scenario carrying a
# refused property_list), then retire this file for the regenerated one.
#
# The wire grading is VALIDATION_ERROR / correctable / field
# "property_list.agent_url": the refusal is a property of the URL the buyer
# sent, so it is buyer-fixable, and `field` is the only channel that can say
# WHICH input to fix without disclosing anything (the message must not).
# Grounded on docs/building/by-layer/L3/error-handling.mdx § "Request
# Validation" + § "Recovery Classification"; the code choice itself is
# grounded on the pinned enum's OWN descriptions (v3.1.1
# static/schemas/source/enums/error-code.json):
#   INVALID_REQUEST — "Request is malformed, missing required fields, or
#     violates schema constraints. Recovery: correctable (check request
#     parameters and fix)."
#   VALIDATION_ERROR — "Request contains invalid field values or violates
#     business rules beyond schema validation. Recovery: correctable (review
#     error details and fix field values)."
# A `format: "uri"`-valid https URL that lands in a policy-blocked range is
# not malformed and violates no schema constraint — it is a well-formed field
# VALUE that our egress business rule refuses. That is squarely the second
# description, so VALIDATION_ERROR is the reconciled answer.
#
# These scenarios USED to grade INVALID_REQUEST, and the ingest scenario below
# graded VALIDATION_ERROR, for the same buyer mistake — the wire code told the
# buyer which of our gates happened to notice, which is meaningless to them.
# The reconciliation is to the code the ingest twin already used, and the
# storyboard-adjacent extension scenario already reached independently:
# BR-UC-002-create-media-buy.feature:432 (@T-UC-002-ext-webhook-ssrf) grades a
# blocked webhook URL as VALIDATION_ERROR / correctable / suggestion, with the
# same v3.1.1 @source cite. Both codes are `correctable` in the pin, so no
# buyer's retry behavior changes — only a buyer branching on the code sees the
# unification.
#
# Why THESE causes: with the private-range escape hatch open — which is the
# posture of docker-compose.e2e.yml and of run_all_tests_host.sh, and the ONLY
# hatch left (GH #1757 deleted the scheme hatch: the seam now requires
# https unconditionally, no operator override) — a cloud-metadata address, an
# unresolvable host, and a plaintext http scheme are all still refused (the SDK
# checks BLOCKED_METADATA_IPS and raises on getaddrinfo failure upstream of the
# allow_private gate; the scheme is checked before that gate is read at all).
# All three grade the same production on every transport, so all three
# scenarios pin the hatch ON.
#
# 100.64.0.1 (RFC 6598 CGNAT) is the FOURTH such cause, and the first one this
# repo owns rather than inherits (GH #1802). adcp.signing does not
# classify the six #974 supplement ranges at all — every one evaluates False on
# every flag it tests — so nothing but this repo's own predicate in
# src/core/security/egress/policy.py can refuse them, and that predicate is
# unconditional: no posture, the open hatch included, reaches it. That is what
# makes the row realizable in the e2e compose stack, whose hatch is open
# permanently. It is graded here BECAUSE an immunity graded only in-process is
# graded on the transport least like production.
#
# What a REGRESSION looks like on this row, stated accurately: if the
# supplement check ever moves back behind the hatch, this row does not fail
# fast. The address is accepted, the seam dials 100.64.0.1 for real with its
# retry schedule, and the scenario fails as a delivery failure after connect
# timeouts rather than as VALIDATION_ERROR. The refusal is pre-connection only
# while the fix holds; a slow failure is still a failure, but do not read this
# row as a promise that no packet ever leaves the stack.
#
# The plaintext-http scenario used to pin it OFF instead, deliberately, to keep
# itself unrealizable over e2e_rest rather than silently xpassing there
# (GH #1417). GH #1802 REVERSES that: the posture cannot
# change this scenario's outcome, because EgressPolicy.resolve_for_dial raises
# on the scheme at src/core/security/egress/policy.py:322 — before
# allow_private is read at all, at :331 and :333. Pinning the hatch OPEN is
# therefore the honest declaration (it is the live stack's real posture) and it
# is never less discriminating: it disarms one gate that could otherwise emit
# the same VALIDATION_ERROR, so a green mark here can only be the scheme gate.
# What the reversal COSTS: no BDD scenario exercises the CLOSED posture any
# more — that is graded solely by
# tests/integration/test_url_provenance_wire.py:111.
#
# NOT here, on purpose: the redirect-not-followed obligation (proved by a
# second live origin's hit count in
# tests/integration/test_delivery_webhook_behavioral.py:823 — unobservable
# across the Docker boundary) and the delivery-time push_notification_config
# refusal (tests/integration/test_delivery_webhook_behavioral.py:523 — no
# request/response cycle, so no envelope to grade). The ingest-time
# push_notification_config twin (last scenario below) grades the same
# obligation at the one moment a request still exists to refuse into —
# create_media_buy ingest, before the URL is stored for later delivery. Its
# @egress_create tag routes it to the media-buy create harness; the sibling
# ingest paths (update_media_buy, sync_creatives, reporting_webhook.url) are
# graded at tests/integration/test_webhook_url_ingest_refusal.py.
#
# The ingest gate is not the seam, but it now returns the SAME wire code, BY
# CONSTRUCTION rather than by coincidence: both raise the one refusal class for
# a refused buyer-supplied URL, so what still differs between them is WHEN they
# refuse and on what evidence — not what the buyer is told.
# `reject_unsafe_webhook_registration_url` (src/core/webhook_validator.py)
# refuses as VALIDATION_ERROR / correctable / field / suggestion — and it is
# deliberately DNS-FREE: an unresolvable-but-public
# hostname is ACCEPTED at ingest and re-checked when the callback is dialled
# (gh-#1589 / gh-#1697). So the ingest scenario carries its own CAUSES even
# though it no longer carries its own code; an unresolvable host is not one of
# them. AdCPBlockedUrlError owns the message; no gate authors one.
Feature: Egress refusal of a buyer-supplied URL (local, L1 SSRF)

  A URL the buyer supplies for us to fetch is an SSRF vector. When the egress
  seam refuses one, the buyer must learn that their request is fixable and
  which field to fix — and nothing whatsoever about our network.

  @T-EGRESS-SSRF-refused-url-is-a-correctable-buyer-error @egress @invariant
  Scenario Outline: a refused agent_url is a correctable buyer error naming the field
    Given a tenant is configured for product discovery
    And the outbound private-range egress hatch is open
    When the buyer requests products with a property list agent at "<agent_url>"
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code VALIDATION_ERROR
    And the response error field is property_list.agent_url

    Examples:
      | agent_url                    |
      | https://169.254.169.254      |
      | https://no-such-host.invalid |
      | https://100.64.0.1           |

  @T-EGRESS-SSRF-refusal-discloses-nothing @egress @invariant
  Scenario Outline: the refusal discloses nothing and does not distinguish the cause
    Given a tenant is configured for product discovery
    And the outbound private-range egress hatch is open
    When the buyer requests products with a property list agent at "<agent_url>"
    Then the error is compliant with the AdCP error spec
    And the refusal is VALIDATION_ERROR / correctable on both envelope layers, and its error object carries the code's own message
    And the error envelope names neither the supplied host nor any IP address

    Examples:
      | agent_url                    |
      | https://169.254.169.254      |
      | https://no-such-host.invalid |
      | https://100.64.0.1           |

  # The host RESOLVES, and is public, on purpose: scheme policy runs before
  # address validation, so a host that NXDOMAINs would be refused either way and
  # this scenario could not tell a scheme refusal from an unresolvable-host one —
  # it would stay green with the scheme gate deleted. A resolvable public host
  # makes the scheme gate the only thing standing between the request and the
  # network, which is what the scenario claims to grade. Nothing is ever sent:
  # the refusal happens before DNS.
  @T-EGRESS-SSRF-plaintext-http-refused @egress
  Scenario: a plaintext http agent_url is refused
    Given a tenant is configured for product discovery
    And the outbound private-range egress hatch is open
    When the buyer requests products with a property list agent at "http://example.com"
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code VALIDATION_ERROR
    And the response error field is property_list.agent_url

  # The third buyer-supplied URL on the protocol surface, and the one with the
  # LOWEST privilege bar: creatives[].format_id.agent_url names the creative
  # agent a creative's format lives on, and sync_creatives dials it to check the
  # format exists. Any authenticated advertiser can send it (gh-#1790).
  #
  # Graded here rather than only in integration because the refusal's
  # classification — not merely its existence — is the obligation: before the
  # fix the connection WAS refused, but the registry reported it through its
  # OPERATOR branch as CONFIGURATION_ERROR / terminal, telling the buyer a seller
  # was misconfigured about a URL the buyer themselves chose, with no field.
  #
  # Hatches OPEN, and a cloud-metadata address, for the reason the header gives:
  # that is the only posture the e2e stack can realize, and metadata is refused
  # even with both hatches wide open, so this scenario grades ONE production on
  # every transport including e2e_rest.
  #
  # The field is INDEXED (creatives[0]...) per the pinned spec's JSONPath-lite
  # examples: a sync carries up to 100 creatives and the message says nothing,
  # so an unindexed path would leave the buyer unable to tell WHICH creative.
  @T-EGRESS-SSRF-sync-creatives-agent-url @egress_sync @invariant
  Scenario: a refused creative-agent agent_url is a correctable buyer error at sync ingest
    Given the outbound private-range egress hatch is open
    When the buyer syncs a creative whose format agent is at "https://169.254.169.254"
    Then the response is compliant with the sync_creatives success spec
    And the creative is rejected with VALIDATION_ERROR naming field "creatives[0].format_id.agent_url"

  # The ingest twin: the same obligation — a buyer-supplied URL we refuse comes
  # back as a correctable, non-disclosing error naming the field to fix —
  # graded at the one moment a request still exists to refuse into. A
  # push_notification_config.url is STORED at create_media_buy and dialled later
  # by a background worker; by then the buyer is gone, so an
  # accepted-then-undeliverable URL is a silent failure.
  #
  # Why THESE causes: the ingest gate is DNS-free, so an unresolvable host is
  # ACCEPTED here (correctly — send time re-checks it) and cannot be an example.
  # A reserved-range LITERAL needs no DNS and is refused via the shared address
  # predicate in src/core/security/egress/policy.py (EgressPolicy.check_
  # registration, not the dial-time egress hatches), so it grades one
  # production on every transport. Both rows are IPv6 reserved ranges spec
  # point 2 covers — unique-local (RFC 4193) and multicast. Loopback (::1) is
  # deliberately NOT an example here even though the fix below makes its
  # message non-disclosing too: ADCP_TESTING is ambient true in this harness,
  # and ::1 is loopback exactly like 127.0.0.1 (tests/unit/conftest.py's own
  # _LOOPBACK_PREFIXES treats it identically), so the registration gate's
  # testing-mode allowance (EgressPolicy.check_registration's allow_loopback
  # parameter) RESCUES it here — the request would succeed, not refuse. A
  # cloud-metadata literal and any IPv4 literal are not used here
  # only because the IPv6 set already exercises every address-cause branch
  # (named-network match and the loopback/private fallback) — not because
  # either would still leak: the message below is now identical for every
  # address cause.
  #
  # The message is now cause-blind, same as the seam scenarios above: the
  # registration gate used to report a per-cause reason (which CIDR, which
  # resolved address) — the bug fixed here — and now returns the SAME
  # non-disclosing sentence regardless of which reserved range matched. That
  # sentence is no longer an authored per-class string: under ADR-010 the
  # buyer-facing message is a pure function of the CODE through CODE_TABLE, so
  # cause-blindness is a property of the taxonomy rather than of one carefully
  # worded constant. The Then below grades exactly that, and the non-disclosure
  # Then after it holds the host/address line independently.
  @T-EGRESS-SSRF-ingest-refused-webhook-url @egress_create @invariant
  Scenario Outline: a refused push_notification_config.url is a correctable buyer error at ingest
    Given the outbound private-range egress hatch is open
    When the buyer creates a media buy with push notification url "<webhook_url>"
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code VALIDATION_ERROR
    And the response error field is push_notification_config.url
    And the refusal is VALIDATION_ERROR / correctable on both envelope layers, and its error object carries the code's own message
    And the error envelope names neither the supplied host nor any IP address

    Examples:
      | webhook_url            |
      | https://[fc00::1]/hook |
      | https://[ff02::1]/hook |

  # ── The CREDENTIAL half of the same registration ─────────────────────
  #
  # Everything above grades the URL half of a webhook registration. A
  # registration has a second half, and it is refusable for the same reason at
  # the same moment.
  #
  # Spec grounding — AdCP 3.1.1 (the pin: adcp==6.6.0, docs/adcp-spec-version.md),
  # `git -C <adcp-checkout> show
  # v3.1.1:dist/schemas/3.1.1/core/push-notification-config.json`. `authentication`
  # is OPTIONAL on the config (`required: ["url"]`), but when present it is
  # `required: ["schemes", "credentials"]`, and `credentials` carries
  # `minLength: 32` — "For HMAC-SHA256: shared secret used to generate
  # signature." The same block's own description closes the only escape:
  # "Precedence is a switch, not a fallback: presence of this block selects the
  # legacy scheme; absence selects 9421. A seller MUST NOT sign the same webhook
  # both ways." So a registration that names HMAC-SHA256 and supplies no secret
  # is unservable — we cannot sign it, and we cannot quietly fall back to the
  # RFC 9421 profile, because the block's PRESENCE already selected legacy.
  #
  # UNGRADED BY STORYBOARD: nothing in dist/compliance/3.1.1/ grades a seller
  # refusing an unservable webhook registration (the `credentials` hits in
  # universal/security.yaml are TRANSPORT auth — API keys, Basic, OAuth — not a
  # webhook registration). That silence is about WHETHER a seller must refuse,
  # not about which code a refusal carries — enums/error-code.json answers the
  # second directly, and against the schema facts stated just above:
  #
  #   INVALID_REQUEST  "Request is malformed, missing required fields, or
  #                     violates schema constraints."
  #   VALIDATION_ERROR "Request contains invalid field values or violates
  #                     business rules beyond schema validation."
  #
  # A `credentials` that is absent (`required`) or under `minLength: 32` is the
  # first, verbatim, so these scenarios grade INVALID_REQUEST / correctable /
  # field. This group previously copied the sibling URL gate's VALIDATION_ERROR
  # on the reasoning that the SHAPE was production-authoritative wherever the
  # storyboard was silent; that answered a question the pin had already
  # answered. The URL half KEEPS VALIDATION_ERROR and is not a divergence: a
  # deny-listed host is a schema-VALID `format: "uri"` string refused by seller
  # policy, which is the second sentence. `correctable` is from the pinned
  # enum's own metadata (enums/error-code.json), never STANDARD_ERROR_CODES, and
  # is carried by BOTH codes — so it pins that the buyer is the only party who
  # can supply the secret and that supplying it makes the identical request
  # succeed, while the CODE is what separates the two gates.
  #
  # Why ingest, like the URL half: accepting the registration and discovering it
  # later is a SILENT non-delivery. The senders fail closed inside a background
  # worker, where no request is left to refuse into and the buyer's only signal
  # is a log line nobody reads.
  #
  # Why EVERY transport: this document is schema-INVALID, and all three tool
  # surfaces refuse it ABOVE `_impl` — create and sync through the very
  # `to_push_notification_config` funnel REST uses (src/core/tools/
  # media_buy_create.py, sync_wrappers.py), update through the typed
  # `UpdateMediaBuyRequest` built inside `_build_update_request`, MCP through
  # FastMCP's TypeAdapter on the tool parameter. That shared funnel is WHY they
  # all report one ABSOLUTE path, `push_notification_config.authentication.
  # credentials`, which is the literal these scenarios assert.
  #
  # They were once believed to disagree — MCP and REST reporting a path relative
  # to the sub-model they validated — and the tool-surface scenarios below were
  # graded on A2A alone for that reason. Measured, they do not. Grading all four
  # proves the agreement instead of asserting it here: if any transport re-forks
  # the field path, a scenario reddens.
  #
  # Every URL below is public and passes the registration SSRF gate that runs
  # immediately before this one, so the ONLY thing that can refuse these requests
  # is the credential half — a green here cannot be the URL gate firing by luck.

  # GRADES THE REQUEST MODEL, NOT THE INGEST GATE — measured, not assumed.
  # A credential-less document never reaches the gate: `create_media_buy_raw`
  # coerces into the typed request for a2a/rest and FastMCP's TypeAdapter
  # refuses on mcp, both before `_impl` runs. Bypassing the
  # `accept_push_notification_config` call in `_create_media_buy_impl` leaves
  # this scenario GREEN on all three
  # transports (verified by mutation: 6 failed / 41 passed, and all six are the
  # URL scenario below). So the per-surface mutation check cannot bind here —
  # `credentials` is required AND `minLength: 32`, so no document that survives
  # the request model can reach the credential gate with a missing secret.
  # What this scenario does grade is still worth having: that the refusal is a
  # correctable INVALID_REQUEST naming the credentials field, on every
  # transport, rather than a 500 or a silent acceptance.
  @T-EGRESS-CREDS-create-media-buy @egress_create @invariant
  Scenario: a credential-less HMAC-SHA256 registration is refused at create ingest
    When the buyer creates a media buy registering HMAC-SHA256 with no credentials
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is push_notification_config.authentication.credentials
    And the refusal names the missing shared secret and not the URL

  # GRADES THE REQUEST MODEL, NOT THE INGEST GATE — measured, not assumed.
  # `UpdateMediaBuyRequest` inherits the library's TYPED
  # `push_notification_config`, so this document is refused by the request model
  # on every transport, absolute field path and all, before `_impl` runs. Deleting
  # the entire push-config gate block from `_update_media_buy_impl` leaves this
  # scenario GREEN (verified by mutation). Two consequences, both deliberate:
  # (1) the per-surface mutation check cannot bind at this surface with this
  # document — no document that survives the typed request model can reach the
  # credential gate here, because `credentials` is required AND `minLength: 32`,
  # so the egress seam can never resolve a missing secret from it; the update
  # surface's stake in the lane is structural (no URL-only path remains), not a
  # new refusal. (2) the scenario is still worth its keep as an OUTCOME guard: it
  # asserts the buyer is refused at this surface by SOME layer, and reddens exactly
  # when no layer refuses any more. Measured: relaxing the
  # annotation to `dict` alone leaves it GREEN, because this lane's ingest gate then
  # catches the document; removing the gate alone leaves it GREEN, because the typed
  # model still refuses. It reddens only when BOTH stop refusing — which is the
  # property worth pinning, and is stronger than guarding either layer alone.
  @T-EGRESS-CREDS-update-media-buy @egress_update @invariant
  Scenario: a credential-less HMAC-SHA256 registration is refused at update ingest
    Given the Buyer owns an existing media buy
    When the buyer updates the media buy registering HMAC-SHA256 with no credentials
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is push_notification_config.authentication.credentials
    And the refusal names the missing shared secret and not the URL

  @T-EGRESS-CREDS-sync-creatives @egress_sync_creds @invariant
  Scenario: a credential-less HMAC-SHA256 registration is refused at sync ingest
    When the buyer syncs a creative registering HMAC-SHA256 with no credentials
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is push_notification_config.authentication.credentials
    And the refusal names the missing shared secret and not the URL

  # The fourth registration surface, and the one that is not an AdCP tool
  # parameter at all: A2A `message/send` carries the webhook in the PROTOCOL
  # envelope (params.configuration.task_push_notification_config), read by
  # on_message_send before any skill routing. The buyer registering it is doing
  # the same thing as the three above — asking for signed callbacks — so it owes
  # the same answer, and it is the surface a buyer reaches without invoking any
  # tool. Its wire type is the protobuf AuthenticationInfo, whose `scheme` is
  # SINGULAR and free-form (no enum guards the value here), which is why the
  # scheme below is the exact pinned spelling and the refusal must still name the
  # credential.
  # RETIRED: the two "at A2A message/send" scenarios. Their surface was the A2A
  # protocol envelope's own push registration (`configuration.taskPushNotificationConfig`),
  # which this agent no longer implements -- it advertises `push_notifications=false` and
  # declines all four `tasks/pushNotificationConfig/*` methods, because AdCP 3.1.1
  # L3/webhooks.mdx :308 makes that a SEPARATE registration channel with a separate (A2A
  # `Task`) envelope. Both obligations they carried are graded above on the channel this
  # seller does implement: "refused at create ingest" / "at update ingest" / "at sync
  # ingest" for the credential-less case, and "refused at sync ingest" for the sub-32 one.


  # ── The CARDINALITY half, and the credential MINIMUM ───────────────
  #
  # Epic D lane C3 (GH #1299). The two scenarios below grade the two
  # documents the PINNED schema forbids outright but which reach `_impl`
  # unvalidated today, because the A2A skill handler pops the buyer's raw dict
  # (`adcp_a2a_server.py`) and `create_media_buy_raw` / `sync_creatives_raw`
  # forward it without coercion. Neither is a judgement call this repo makes —
  # both come from the pin, read at the tag:
  #
  #   git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/core/push-notification-config.json
  #     authentication.schemes:     {"type": "array", "minItems": 1, "maxItems": 1}
  #     authentication.credentials: {"type": "string", "minLength": 32}
  #     authentication.required:    ["schemes", "credentials"]
  #   git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/enums/auth-scheme.json
  #     enum: ["Bearer", "HMAC-SHA256"]
  #
  # So a two-entry `schemes` array is not "undefined precedence" the seller may
  # resolve as it likes — it is SCHEMA-INVALID, and the block's own description
  # ("Precedence is a switch, not a fallback ... A seller MUST NOT sign the same
  # webhook both ways") forbids honouring both anyway. `schemes[0]` is therefore
  # not a narrowing rule; it is a swallow, and the half it swallows is the half
  # that decides whether the callback can be signed at all.
  #
  # UNGRADED BY STORYBOARD, same standing as the credential group above: nothing
  # in dist/compliance/3.1.1/ grades a seller refusing a schema-invalid webhook
  # registration. The CODE is still not a free choice — a `schemes` array over
  # `maxItems: 1`, like an absent `credentials`, "violates schema constraints"
  # and is INVALID_REQUEST / correctable / field by enums/error-code.json (the
  # full quotation is in the credential group above). That is also what the
  # coercion funnel already emits on the transports that DO type this parameter.
  #
  # WHY EVERY WIRED TRANSPORT: "every transport names the same field" IS the
  # obligation here, so parametrizing on a single transport would grade exactly
  # the half that already works. The credential-less group above is graded the
  # same way and for the same reason — it was single-transport only while the
  # relative-field-path claim was believed, and that claim is measured false.
  #
  # THE EXPECTED FIELD IS PER-SCENARIO, NOT ONE STRING (measured against the
  # pinned model, 2026-08-18): a >1 array fails `too_long` at
  # `authentication.schemes`, while a bad enum MEMBER fails at
  # `authentication.schemes[0]` — WITH the index — and an absent-or-short secret
  # fails at `authentication.credentials`. Each scenario states its own.
  #
  # The URL is the same public host the group above uses, so a green here can
  # never be the SSRF gate firing by luck; and the exact-field assertion is what
  # rules out the A2A `_invalid_params_from_ssrf_error` funnel re-enveloping this
  # as a URL refusal (it manufactures `push_notification_config.url`).

  @T-EGRESS-SCHEMES-multi-create @egress_create @invariant
  Scenario: a two-scheme registration is refused at create ingest
    When the buyer creates a media buy registering two authentication schemes
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is push_notification_config.authentication.schemes

  @T-EGRESS-CREDS-short-sync @egress_sync_creds @invariant
  Scenario: a shared secret shorter than the pinned minimum is refused at sync ingest
    When the buyer syncs a creative registering HMAC-SHA256 with a 31-character secret
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is push_notification_config.authentication.credentials

  # The PRIMITIVES twin of the scenario above.
  #
  # This surface once exempted the pinned minimum: the coercion funnel's refusal
  # was caught and, when the only complaint was a short `credentials`, the
  # document was re-validated with a padded secret and the buyer's short one put
  # back — so the registration was ACCEPTED, STORED, and refused later inside the
  # sender, where no request was left to carry the answer and the buyer was not
  # on the call. The exemption was reachable from exactly TWO surfaces: this one
  # and the admin registration form (`src/admin/blueprints/principals.py`, graded
  # at tests/integration/test_admin_ingest_url_policy.py). Both now refuse at
  # ingest, which is what this scenario and its admin sibling hold in place.
  #
  # The pin is unconditional and admits no transport discriminator:
  #   git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/core/push-notification-config.json
  #     authentication.credentials: {"type": "string", "minLength": 32}
  #   git -C ~/projects/adcp show v3.1.1:docs/building/by-layer/L3/webhooks.mdx
  #     :62 "For A2A, the A2A protocol wraps it in a `configuration` envelope
  #          using camelCase — but the object's contents are identical."
  # UNGRADED BY STORYBOARD, same standing as every other scenario in this file:
  # nothing in dist/compliance/3.1.1/ sends a short credential (the runner emits
  # only schema-valid negative payloads — universal/webhook-emission.yaml:487).
  # Full ruling: .claude/notes/pldmk8-spec-grounding.md.
  #
  # WHY THIS SURFACE AND NOT ALL FOUR: MCP and REST reach the credential rule
  # through the TYPED request model, which already refuses 31 characters today,
  # so a grader written there would be green before the carve-out is deleted and
  # its green would camouflage the two surfaces that actually change. The
  # four-surface equivalence, once they agree, is pinned at
  # tests/integration/test_webhook_hmac_credentials_ingest_refusal.py.
  #
  # The URL is the same public host every credential scenario above uses, so a
  # green here can never be the SSRF gate firing by luck, and the exact-field
  # assertion is what rules out the A2A `_invalid_params_from_ssrf_error` funnel
  # re-enveloping this as a URL refusal.
