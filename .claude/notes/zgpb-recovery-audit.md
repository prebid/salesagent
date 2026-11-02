# Audit: per-raise recovery overrides and hand-built advisory errors

Deliverable of `salesagent-zgpb`, executed as the migration-audit molecule
`salesagent-4vbv9`. The same text is on the ticket; it is mirrored here because a
disposition table that only exists in the tracker cannot be reviewed in a diff.

## AUDIT DISPOSITION (salesagent-4vbv9.3) — 2026-08-27, HEAD 151759604

Acceptance 1 asks for a table listing EVERY per-raise recovery= override and EVERY
hand-built advisory Error, each marked MIGRATE / ALLOWLIST / DEFER. Here it is. The
short version: there is nothing left to migrate, because the two constructors that
could express the defect were rebuilt to make it unconstructible.

### Acceptance 2 — the scan command, verbatim and reproducible

    grep -rn 'recovery="' src/ --include='*.py'

  when this ticket was filed : 23 hits / 8 files
  2026-08-27                 : 1 hit / 1 file, and it is a COMMENT
                               (src/core/tools/products.py:422, prose explaining why
                               a former override was wrong)

Widened to 'recovery=' (any spelling): 17 hits. 10 are CODE_TABLE's own construction
in src/core/errors/codes.py, 1 is exceptions.py:1057 recovery=exc.recovery (a READ off
the exception), and the remaining 6 are comments and docstrings.

### Table A — per-raise recovery= overrides

  file (as filed)                     n    disposition
  ---------------------------------------------------------------------------
  creative_agent_registry.py          7    MIGRATED — site gone
  media_buy_create.py                 4    MIGRATED — site gone
  creatives/_processing.py            4    MIGRATED — site gone
  signals_agent_registry.py           3    MIGRATED — site gone
  products.py                         2    MIGRATED — one gone, one now a comment
  _assignments.py                     1    MIGRATED — site gone
  accounts.py                         1    MIGRATED — site gone
  context_manager.py                  1    MIGRATED — site gone
  ---------------------------------------------------------------------------
  TOTAL                              23    0 remain. Nothing to ALLOWLIST, nothing
                                           to DEFER, so no follow-up ticket is owed.

Migrated BY WHAT: AdCPSalesAgentError.__init__ no longer has a recovery parameter. Probed, not
assumed: AdCPValidationError(recovery='terminal') raises
TypeError: AdCPSalesAgentError.__init__() got an unexpected keyword argument 'recovery'.
recovery is a read-only property resolving from CODE_TABLE per read.

### Table B — hand-built advisory Error objects (13 live sites)

  src/core/creative_agent_registry.py:867      AGENT_UNREACHABLE
  src/core/tools/media_buy_delivery.py:232/363/554   MEDIA_BUY_NOT_FOUND
  src/core/tools/accounts.py:643/1205          code from _FAILURE_CLASS_TO_CODE
  src/core/tools/media_buy_list.py:250         SERVICE_UNAVAILABLE
  src/core/tools/capabilities.py:142           SERVICE_UNAVAILABLE
  src/core/tools/creative_formats.py:580       REFERENCE_NOT_FOUND
  src/core/tools/creatives/listing.py:395      CONFIGURATION_ERROR
  src/services/targeting_capabilities.py:248   UNSUPPORTED_FEATURE
  src/core/tools/creatives/_processing.py:156  from_exception
  src/admin/blueprints/operations.py:611       from_exception

  disposition: ALL 13 -> NO ACTION. Not one of them picks a recovery, because neither
  Error.of(code, *, field, details, retry_after) nor Error.from_exception(exc) has a
  recovery parameter. _derive_graded_fields overwrites message, suggestion and
  recovery from CODE_TABLE on every validation, and the mutation routes are closed
  (model_copy, model_construct and direct model_validate all re-derive; setattr is
  refused by frozen=True). Probe recorded on salesagent-4vbv9.

### Acceptance 3 — divergences from the pinned per-code classification

ZERO. The example this ticket cites, AdCPValidationError(msg, recovery='terminal') at
media_buy_create.py:2029, is no longer expressible: the message parameter is gone too
(CODE_TABLE owns the sentence) and so is recovery. VALIDATION_ERROR resolves to the
pinned 'correctable' on both lanes, verified by probe.

### Acceptance 4 — no new guard

Satisfied, and not merely by abstention. This ticket asked to extend
test_architecture_error_recovery_enum_conformance.py with an AST oracle at raise-site
granularity. That file does not exist and should not be written: an AST guard DETECTS
a defect that can still be written, and the defect can no longer be written. That is
the parent epic's own stated preference — a loaded table cannot drift, so it needs no
guard — applied to recovery.

### The BILLING_NOT_SUPPORTED item — DONE

This ticket asks to fold in the promotion flagged at exceptions.py:41-42, giving
BILLING_NOT_SUPPORTED the same treatment CONFIGURATION_ERROR gets. It already has it:
both sit in _AUTHORED_SPEC_MESSAGES (src/core/errors/codes.py:303 and :304), which
overrides the MESSAGE only — recovery and suggestion come from the pinned schema,
which is authoritative for them. _build_code_table raises at import if the SDK ever
ships a message for an overridden code, so the override deletes itself rather than
drifting.

### One finding, LOW, recorded not acted

src/core/schemas/_base.py's Error docstring lists five mutation routes and says each
is redefined as a re-validation. Four are. A raw obj.__dict__['recovery'] poke is not,
and cannot be — pydantic has no hook below __setattr__. Probed: it yields 'terminal'.
No production site does this. Fixing it means rewording a docstring, which is not this
audit's deliverable and would need its own change.

### Where the remaining risk actually lives

Recovery is now a pure function of the CODE. So the only way to reach a buyer with the
wrong recovery is to emit the wrong CODE — which is salesagent-tay20, not this ticket.
Two Table B sites are worth that ticket's attention as input: capabilities.py:142 and
media_buy_list.py:250 both emit SERVICE_UNAVAILABLE (pinned transient) for conditions
that read as non-transient degradation. Handed over, not fixed here.
