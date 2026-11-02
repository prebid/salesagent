# Hand-authored feature — not compiled from adcp-req
# Grades what salesagent-02rgd centralised: one resolver decides which tenant and which
# principal a request runs as. Until this feature existed nothing graded that decision --
# every other scenario seeds ONE tenant, so an unscoped query returns the right rows by
# accident and a broken filter is invisible.

@security
Feature: A credential reaches exactly one tenant's data
  As a publisher whose inventory sits in a shared database beside other publishers',
  I want a buyer's credential to resolve to exactly one tenant and one principal,
  so that no buyer can read, or be served, another tenant's data.

  Background:
    Given two tenants each own a product the other does not
    And tenant "A" serves discovery to anyone while tenant "B" requires a credential

  # POSITIVE, and it is only half a test on its own. "A sees A's product" also passes
  # when A sees EVERY tenant's products, so the absence assertion below it is what
  # separates a scoped query from an unscoped one.
  @T-SECURITY-002-own-tenant-only
  Scenario Outline: A buyer sees its own tenant's products and no other tenant's
    When the buyer requests products with tenant "<tenant>" credentials
    Then the response contains tenant "<tenant>" products
    And the response contains no tenant "<other>" products

    Examples:
      | tenant | other |
      | A      | B     |
      | B      | A     |

  # NEGATIVE. A token is minted per (tenant, principal) and the lookup filters on both
  # columns, so a credential is meaningless outside the tenant it belongs to. The failure
  # this guards against is the request being served ANYWAY -- either as the addressed
  # tenant (contamination) or as the token's own tenant (the address silently ignored).
  #
  # The error code IS pinned: AUTH_INVALID, recovery terminal. The pinned enum
  # (3.1/enums/error-code.json) says "Sellers MUST return this code when an `Authorization`
  # header was present but verification failed", and names no task, so the resolver refuses
  # a presented-and-rejected credential on every row -- get_products, a public tool, included.
  # The tool's credential policy (identity: PublicIdentity, plus the tenant's
  # brand_manifest_policy) decides only what an ABSENT credential gets: AUTH_MISSING on a
  # protected row, anonymous service on a public one. A's token addressed to B is presented
  # and verifies as nobody in B, so the answer is AUTH_INVALID whichever policy B carries.
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID
  @T-SECURITY-002-credential-does-not-cross-tenants
  Scenario Outline: A credential minted for one tenant is refused by the other
    When the buyer presents tenant "<holder>" credentials addressed to tenant "<target>"
    Then the request is refused with AUTH_INVALID and no products are returned

    Examples:
      | holder | target |
      | A      | B      |
      | B      | A      |

  # NEGATIVE, public tool, bad token. The case the Examples above cannot separate from
  # tenant-crossing: a token NO tenant issued, addressed to a tenant that serves discovery
  # to anyone (A) and to one that requires a credential (B). Before this scenario, A served
  # the caller anonymously (200): the resolver took a rejected credential as absent on a
  # public row, citing security.yaml's "public tasks like get_adcp_capabilities return 200
  # without credentials by design" -- a sentence about the ABSENT credential. The storyboard
  # never sends a bad credential to a public task (both its probes target the protected
  # probe task), so this is ungraded upstream and graded here, on every transport alike.
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID
  # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/security.yaml pointer=/prerequisites/description
  @T-SECURITY-002-rejected-credential-is-refused-on-a-public-tool
  Scenario Outline: A credential that verifies as nobody is refused with AUTH_INVALID whatever the tenant's policy
    When the buyer presents a credential no tenant issued, addressed to tenant "<target>"
    Then the request is refused with AUTH_INVALID and no products are returned

    Examples:
      | target |
      | A      |
      | B      |
