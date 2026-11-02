# Hand-authored feature — not compiled from adcp-req
# Cross-cutting wire obligation for salesagent-3dawm.8 (resolve the suggestion from the table)

@codes
Feature: A buyer-facing suggestion is resolved from the code table, not authored
  As a buyer agent,
  I want every error I receive to carry the suggestion its code defines,
  identical no matter which seller code path produced it,
  so that a recovery hint is a property of the code and not of the raise site.

  # AdCP 3.1.1 enums/error-code.json carries `enumMetadata.suggestion` per code, and the
  # pinned bundle is normative. Before salesagent-3dawm.8 a suggestion reached the buyer
  # only when some raise site or class happened to author one: measured, 220 of 294
  # construction sites passed none at all, so the field was simply ABSENT from those
  # envelopes. It now resolves from CODE_TABLE.
  #
  # WHAT THESE SCENARIOS ASSERT, AND WHY NOT MORE: presence and non-emptiness only. The
  # text is deliberately NOT asserted. Every available oracle for it is degenerate —
  # comparing it to CODE_TABLE grades the table against itself, comparing it to the string
  # it replaced checks for the absence of a defect, and writing the pin's sentence into a
  # feature file transcribes the pinned file into a test. Presence is the honest claim: on
  # these paths the field did not exist before and does now.

  @T-CODES-002-suggestion-appears-on-a-bare-raise
  Scenario: A rejection raised without an authored suggestion still carries one
    # Deliberately reuses BR-CODES-001's rejection path: it is a BARE, non-auth raise site
    # (AdCPMediaBuyRejectedError with no suggestion= and no _default_suggestion) already
    # wired across mcp, a2a, rest and e2e_rest. Before .8 this envelope had no suggestion
    # field at all, so this scenario fails on every transport against the old code.
    Given the buyer requests a media buy the seller will reject
    When the Buyer Agent sends the create_media_buy request
    Then the error is compliant with the AdCP error spec
    And the response is an error carrying the seller's own code "MEDIA_BUY_REJECTED"
    And the wire error carries a non-empty suggestion

  # NO UNIFORMITY SCENARIO HERE, and the reason matters. A draft of this feature carried
  # one that built two envelopes straight from AdcpErrorResponse.of and compared
  # them. That is a TRANSPORT-BYPASS — it never touches the wire, so it would have reported
  # green on four transports while running the same in-process code four times — and the
  # harness correctly refused to run it (auto-xfail, "harness not wired"). It is not
  # re-expressed as a wire scenario because after this step there is exactly ONE source for
  # AUTH_MISSING's suggestion: the constant and both ClassVars are deleted, so comparing two
  # paths compares one resolution to itself. The fork it would have guarded returns only if a
  # raise site can author the field again, and that is closed by DELETING THE PARAMETER
  # (salesagent-3dawm.12) — a type change, not a test.
