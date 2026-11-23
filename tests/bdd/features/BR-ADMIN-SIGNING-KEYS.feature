# Hand-authored feature — not compiled from adcp-req
# Admin UI: what the operator is told about the tenant's active signing key (#2288)

Feature: BR-ADMIN-SIGNING-KEYS The signing-keys page and the setup checklist agree on the active key
  As a Tenant Admin
  I want both admin pages to say whether this seller signs, and what to do when it cannot
  So that I fix the cause the deployment actually has, as the signing key runbook says

  # Both pages read the one derivation, signing_key_backed (src/core/signing/posture.py).
  # A key in its active window that this deployment cannot sign with gets the advice for
  # its cause, ActiveKeyFault.advice, on both pages:
  #   - a KEK that does not open the key: restore the KEK, do not re-mint, because a
  #     replacement strands the key counterparties already trust
  #     (docs/operations/signing-key-runbook.md, "A key minted under a KEK that was later
  #     unset");
  #   - key material that no longer derives the published JWK: rotate, because the KEK is
  #     fine ("A key whose private half no longer matches its published key").
  # A published key outside its active window (revoked, or not active yet) gets "provision
  # a replacement", and a healthy key gets no warning.
  #
  # Every state is seeded; the deployment KEK is never changed, so the scenarios run
  # unchanged against a live server. {kid} in an expected text is the seeded key's kid,
  # and {revoked_at} is when it was revoked.
  #
  # Transports: the admin form surface, in process (Flask test client) and over e2e
  # (requests against the live stack), through AdminSigningKeysEnv.

  @T-ADMIN-SIGNKEYS-001 @admin_signing_keys @requires_db
  Scenario Outline: both pages give the same answer for a key that <state>
    Given the tenant's only signing key <state>
    When the operator opens the signing-keys page and the setup checklist
    Then the key's Status cell reads "<status>"
    And the signing-keys page warns "<warning>"
    And the Message Signing Key task reads "<checklist>"

    Examples:
      | state | status | warning | checklist |
      | signs under the deployment KEK | signing | nothing | Signing key provisioned and published |
      | was provisioned under a KEK this deployment does not hold | active, cannot sign | Key {kid} is active, but this deployment cannot sign with it, so this seller is not signing. The key encryption key (KEK) this deployment holds is not the one the key was provisioned under, so it cannot decrypt the key. Restore the original KEK. Do not re-mint: a replacement publishes a second key and strands this one, which is the key counterparties already trust. Signing key runbook | Active key {kid} cannot sign. The key encryption key (KEK) this deployment holds is not the one the key was provisioned under, so it cannot decrypt the key. Restore the original KEK. Do not re-mint: a replacement publishes a second key and strands this one, which is the key counterparties already trust. |
      | publishes a public key its private half does not match | active, cannot sign | Key {kid} is active, but this deployment cannot sign with it, so this seller is not signing. The key decrypts, but its private half no longer matches the public key it publishes, so the stored key material has changed and counterparties would reject its signatures. Restoring the KEK will not help. Rotate: provision a replacement, then revoke this key. Signing key runbook | Active key {kid} cannot sign. The key decrypts, but its private half no longer matches the public key it publishes, so the stored key material has changed and counterparties would reject its signatures. Restoring the KEK will not help. Rotate: provision a replacement, then revoke this key. |
      | was revoked moments ago | revoked {revoked_at} | nothing | Published key is revoked or not yet active — provision a replacement |
      | becomes active tomorrow | published | nothing | Published key is revoked or not yet active — provision a replacement |

  @T-ADMIN-SIGNKEYS-002 @admin_signing_keys @requires_db
  Scenario Outline: the warning links the runbook section for the cause, a key that <state>
    Given the tenant's only signing key <state>
    When the operator opens the signing-keys page and the setup checklist
    Then the signing-keys warning links the runbook section "<section>"

    Examples:
      | state | section |
      | was provisioned under a KEK this deployment does not hold | a-key-minted-under-a-kek-that-was-later-unset |
      | publishes a public key its private half does not match | a-key-whose-private-half-no-longer-matches-its-published-key |
