# src/core/tools — pointer

Every tool's `_impl` controller and its services live here, which makes this the directory
where almost every **raise site** in the codebase sits. Two contracts bind a change here, and
both are documented elsewhere; this file names them and points.

## Raising an error

**Raise an `AdCPSalesAgentError` subclass bound to the code, and pass the `ErrorDetails`
subclass its type parameter declares.** Nothing else.

```python
raise AdCPBudgetExceededError(details=BudgetDetails(requested_budget="500", budget_limit="100"))
```

The base is generic in its details type, so **mypy refuses a dict and a foreign details class
at every raise site** — that is the enforcement, and it is why there is no runtime check to
add. Three failure modes, each of which has actually happened:

- **A helper that takes the class as a parameter must parameterize it.**
  `exc_type: type[AdCPSalesAgentError]` erases the type variable to `Any`, and `Any` accepts a
  dict. One such annotation in `financial_validation.py` sent a buyer
  `500 INTERNAL_ERROR / transient` for a budget breach that raised
  `BUDGET_EXCEEDED / correctable / 422`, because the dict had no `to_wire` and the boundary's
  failure builder died mid-render. Write `type[AdCPSalesAgentError[BudgetDetails]]`.
- **No authored text, anywhere.** `message`, `recovery`, `suggestion` and `status_code` come
  from `CODE_TABLE`. A validator's diagnostic sentence gets LOGGED; `internal_detail` takes an
  exception, not a string, and no details class has a free-text field.
- **A code with no subclass is a gap to fill, not to work around.** Naming a code on the base
  (`AdCPSalesAgentError(error_code="...")`) puts it on the wire with nothing bound to it.

**Full rules:** [docs/design/error-architecture.md](../../../docs/design/error-architecture.md).
Root [CLAUDE.md](../../../CLAUDE.md) pattern 10 summarizes.

## Never branch on a test flag

A production path here must not read `adcp_testing`, and
`.ast-grep/rules/test-flag-never-reaches-production.yml` fails the build on it. A behavior
that needs to differ under test needs a **real input** a deployment can set — a tenant
column, an `AdapterConfig` row, a settings field — which the test then seeds like any other
state. Root [CLAUDE.md](../../../CLAUDE.md) pattern 11 carries the reasoning.

## The seam

An `_impl` takes `(req, identity)` and nothing else, imports no transport, and performs no
auth, account resolution, idempotency or context echo — `_boundary.py` does each of those
once for every transport. Root [CLAUDE.md](../../../CLAUDE.md) pattern 5 and
[docs/development/building-tools.md](../../../docs/development/building-tools.md) are the
contract.
