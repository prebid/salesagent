# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# Reproduces salesagent-og9k.3: the reserved-TLD policy has a single owner
# (``is_reserved_tld_host``, src/core/security/url_validator.py:53) that the
# provisioning path does not call. ``_check_domain_validity``
# (src/core/tools/accounts.py:896-899) re-implements the match as a raw
# ``brand_domain.endswith(tld)``, which — unlike the owner — does not lowercase,
# does not strip a trailing root dot, and does not match a bare TLD label. The
# two therefore disagree, and the disagreement is buyer-visible: a brand domain
# the proof service will later refuse as unprovable is ACCEPTED at provisioning.
#
# Upstream gap: BR-UC-011-manage-accounts.feature grades the reserved-TLD
# refusal with exactly ONE domain (``invalid-brand.test``, the
# @T-UC-011-ext-b-partial partial-failure scenario) — the single spelling on
# which the owner and the re-implementation happen to agree. One lowercase
# example cannot distinguish "the policy is applied" from "a substring test that
# coincides with it on this input", which is why the divergence survived.
#
# Spec grounding for the SET of names (not the normalization, which is ours):
# @source repo=adcp ref=v3.1.1 path=docs/creative/canonical-formats.mdx line=222
#   "resolved hostname MUST NOT land on ... RFC 6761 special-use names
#    (`.local`, `.localhost`, `.internal`, `.test`, `.example`, `.invalid`)"
# RESERVED_TLDS (url_validator.py:50) carries four of those six: `.local` and
# `.internal` are absent. NOTE the pinned normative "Webhook URL validation
# (SSRF)" section (building/by-layer/L1/security.mdx:104-119), which
# sync_accounts.mdx:205 binds the provisioning path to, enumerates reserved IP
# RANGES only and does NOT restate the RFC 6761 name list — so the name-set
# obligation is cited to canonical-formats.mdx above, which does state it
# normatively, rather than to the sync_accounts cross-reference.
#
# Reconcile upstream in adcp-req (a reserved-TLD partition with more than one
# spelling), then retire this file in favour of the regenerated one.

Feature: Reserved-TLD brand domains are refused under one normalized policy
  As a seller
  I want a brand domain under a reserved TLD refused at provisioning
  So that an account is never created for a domain whose endpoint can never be proven

  @T-UC-011-local-reserved-tld-normalized @sync @validation @partition @boundary
  Scenario Outline: A reserved-TLD brand domain is refused however it is spelled
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    | <domain>        | <domain>      | operator |
    Then the response is a success variant with accounts array
    And the account for brand domain "acme-corp.com" has action "created"
    And the account for brand domain "<domain>" has action "failed"
    And the failed account includes a per-account errors array
    And the per-account errors array contains an error with code "VALIDATION_ERROR"

    # NOT an example row: a mixed-case spelling (``Invalid-Brand.TEST``). The
    # owner/re-implementation divergence on CASE is real at the function level
    # but unreachable on this surface — the pinned schema constrains
    # ``brand.domain`` to ``^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$``
    # (dist/schemas/3.1.1/core/brand.json), so an uppercase or trailing-dot
    # domain is refused by request validation before any gate runs. Measured:
    # such a row fails at SyncAccountsRequest construction, grading the SDK
    # pattern rather than our policy. A bare reserved LABEL is the divergence
    # the pattern DOES admit — one label matches the pattern, and
    # ``endswith(".test")`` is False for it while the owner returns True.
    Examples: spellings of a reserved TLD the owning predicate refuses
      | domain                 |
      | invalid-brand.test     |
      | test                   |
      | invalid-brand.internal |
      | invalid-brand.local    |
