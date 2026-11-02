# Hand-authored feature — not compiled from adcp-req
# Cross-cutting wire-safety obligation for #1587 (salesagent-prkv.18)

@security
Feature: Wire error safety for untyped exceptions
  As a buyer agent,
  I want an untyped exception raised inside a dispatched skill to never leak
  its raw exception text onto the wire,
  so that internal details (DSNs, stack fragments, upstream responses) never
  reach me, per AdCP 3.1.1 transport-errors.mdx Security Considerations.

  # CODE REVERSED by salesagent-3dawm.6, from SERVICE_UNAVAILABLE to INTERNAL_ERROR.
  # SERVICE_UNAVAILABLE was never what the raise site declared: adcp_error_for turns an
  # untyped exception into INTERNAL_ERROR, and a now-deleted table rewrote it at the boundary.
  # The obligation this scenario grades is UNCHANGED and is not about which code appears — it is
  # that no raw exception TEXT reaches the buyer, asserted by the marker scan and by pinning the
  # fault's identity in the boundary log. A more specific code with recovery=transient tells the
  # buyer more than the collapse did, and leaks nothing: the message is CODE_TABLE's sentence.
  @T-SECURITY-001-untyped-exception
  Scenario: Untyped exception inside a dispatched skill yields a safe wire envelope
    Given a tenant is configured for product discovery
    And an untyped exception is raised inside the dispatched skill's business logic
    When the Buyer Agent requests products
    Then the error is compliant with the AdCP error spec
    And the response is an error with code "INTERNAL_ERROR" and no raw exception text
