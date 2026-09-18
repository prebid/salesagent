# RCA: the literal error path, and what the adcp SDK already supplies

Scope: `adcp==6.6.0` installed at
`.venv/lib/python3.12/site-packages/adcp`,
repo at `.` (branch `feature/spec-gaps-1210`).
Everything below is quoted from those two trees. Anything I could not verify is
marked **unverified**.

---

## Section 1 — What the SDK supplies on the error path

### 1.1 Two disjoint exception hierarchies

**Client-side** — `adcp/exceptions.py` (487 lines). `ADCPError(Exception)` with
`message / agent_id / agent_uri / suggestion`:

```python
# adcp/exceptions.py:8
class ADCPError(Exception):
    """Base exception for all AdCP client errors."""
    def __init__(self, message: str, agent_id: str | None = None,
                 agent_uri: str | None = None, suggestion: str | None = None):
```
Subclasses: `ADCPConnectionError`, `ADCPAuthenticationError`, `ADCPTimeoutError`,
`ADCPProtocolError`, `ADCPToolNotFoundError`, `ADCPWebhookError`,
`ADCPWebhookSignatureError`, `ADCPSimpleAPIError`, `RegistryError`,
`ADCPFeatureUnsupportedError`, `ADCPSigningRequiredError`, `Adagents*`,
`ADCPTaskError`, `IdempotencyConflictError`, `IdempotencyExpiredError`,
`IdempotencyUnsupportedError`, `ConfigurationError`. These are for a *buyer*
calling out; each one hardcodes its own `suggestion` text in `__init__`.

**Server-side** — `adcp/decisioning/types.py:62`. This is the one a seller raises:

```python
# adcp/decisioning/types.py:62
class AdcpError(Exception):
    """Wire-shaped structured error raised by platform methods.

    Distinct from :class:`adcp.exceptions.ADCPError` (the client-side
    connection-failure exception). This is the *server-side* structured
    error the framework's dispatcher catches and projects to the wire
    ``adcp_error`` envelope.
    ...
    Adopters raise this from inside Protocol method bodies for any
    buyer-fixable rejection. The framework catches at the dispatch
    seam, serializes to the structured-error envelope, and returns
    the wire response. Adopters do NOT serialize themselves.
    """

    def __init__(self, code: str, *, message: str = "",
                 recovery: Literal["retry_with_changes","correctable","transient","terminal"] = "terminal",
                 field: str | None = None, suggestion: str | None = None,
                 retry_after: int | None = None,
                 details: dict[str, Any] | None = None) -> None:
```

with its own wire projection:

```python
# adcp/decisioning/types.py:135
    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code,
                               "message": self.args[0] if self.args else "",
                               "recovery": self.recovery}
        if self.field is not None: out["field"] = self.field
        if self.suggestion is not None: out["suggestion"] = self.suggestion
        if self.retry_after is not None: out["retry_after"] = self.retry_after
        if self.details:
            details = sanitize_error_details(self.code, self.details)
            if details: out["details"] = details
        return out
```

### 1.2 Typed code→recovery→default-message subclasses

`adcp/decisioning/errors.py` — module docstring states the authorship model
outright:

```python
# adcp/decisioning/errors.py:1
"""Typed exception subclasses for the AdCP error code vocabulary.
...
These subclasses bind each spec code to its canonical recovery classification
(per ``schemas/cache/enums/error-code.json#enumMetadata``) and offer a
sensible default message adopters can override.

Recovery values are normative — they MUST match the ``enumMetadata``
block in the error-code schema. Adopters MUST NOT override
``recovery`` on these subclasses; if a different recovery is needed
for a vendor variant, raise the base :class:`AdcpError` directly.
"""
__all__ = ["AccountNotFoundError","AuthRequiredError","BillingNotPermittedForAgentError",
           "MediaBuyNotFoundError","PermissionDeniedError","RateLimitedError",
           "ServiceUnavailableError","UnsupportedFeatureError","ValidationError"]
```

Shape of every one of the nine (example):

```python
# adcp/decisioning/errors.py:~255
class ValidationError(AdcpError):
    def __init__(self, *, message: str | None = None, field: str | None = None,
                 suggestion: str | None = None, **details: Any) -> None:
        super().__init__("VALIDATION_ERROR",
                         message=message or "Request failed validation.",
                         recovery="correctable", field=field,
                         suggestion=suggestion, details=dict(details) or None)
```

### 1.3 The code table + envelope builder

`adcp/server/helpers.py:29` — `STANDARD_ERROR_CODES: dict[str, dict[str,str]]`,
39 entries, each `{"recovery": ..., "message": ...}`. Derived sets at
`helpers.py:96-105`: `TRANSIENT_CODES`, `CORRECTABLE_CODES`, `TERMINAL_CODES`.

```python
# adcp/server/helpers.py:108
def adcp_error(code: str, message: str | None = None, *, field=None, suggestion=None,
               recovery=None, retry_after=None, details=None) -> dict[str, Any]:
    """Build a structured ADCP error response with auto-recovery.

    Standard codes get recovery auto-populated from the code table.
    Custom codes default to "terminal".
    ...
        details: Server-generated debugging data (constraint names, limits,
            thresholds). Use only server-generated values here. NEVER pass
            request params or user-supplied strings -- they flow to the
            caller's LLM context and could enable prompt injection.
    """
    std = STANDARD_ERROR_CODES.get(code, {})
    err: dict[str, Any] = {"code": code,
                           "message": message or std.get("message", code),
                           "recovery": recovery or std.get("recovery", "terminal")}
    ...
    return {"errors": [err]}
```

### 1.4 The transport boundary the SDK ships

`adcp/server/translate.py` — one extractor feeding three projections.

```python
# adcp/server/translate.py:110
def _extract_structured_fields(exc: ADCPError | Error | Any) -> tuple[str,str,str,str|None,str|None,dict|None,list|None]:
    """Extract (code, message, recovery, field, suggestion, details, errors).

    Handles three input shapes:
    - ``adcp.types.Error`` (Pydantic model)
    - ``adcp.decisioning.types.AdcpError`` (decisioning-layer exception)
    - ``adcp.exceptions.ADCPError`` (client-side exception, including ADCPTaskError)
    """
```
It normalizes recovery from the code when unset (`_recovery_for_code`,
`translate.py:76`) and sanitizes details (`translate.py:169`:
`details = sanitize_error_details(code, details)`).

```python
# adcp/server/translate.py:176
def build_mcp_error_result(exc, *, params=None, method_name="",
                           response_enhancer=None, context=None) -> CallToolResult:
    ...
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structuredContent=structured,   # {"adcp_error": {...}} (+ echoed context)
                          isError=True)

# adcp/server/translate.py:255
def translate_error(exc, protocol: Literal["mcp","a2a"] | Protocol) -> ToolError | A2AError:
    ...
    if proto == "mcp":
        return _to_mcp(code, message, suggestion=suggestion, field=field, details=details)
    return _to_a2a(code, message, recovery=recovery, suggestion=suggestion,
                   details=details, errors=errors)
```
`_to_a2a` (`translate.py:454`) picks `InvalidParamsError` when
`effective_recovery == "correctable"`, else `InternalError`, and packs
`{"error_code","recovery","suggestion","details","errors"}` into `data`.

Also shipped and unused by us: `adcp/error_sanitization.py`
(`sanitize_error_details`, `sanitize_authorization_required_details` with the
`AUTHORIZATION_REQUIRED_DETAIL_KEYS` allowlist), and
`adcp/protocols/_adcp_errors.py`:

```python
# adcp/protocols/_adcp_errors.py:16
MAX_ERROR_CODE_LEN = 64
MAX_ERROR_SIZE_BYTES = 4096
def validate_adcp_error(err: Any) -> dict[str, Any] | None:
    """Return ``err`` if it's a spec-shaped ``adcp_error`` envelope, else ``None``."""
```

### 1.5 The thing the SDK ships that decides this whole question

The pinned spec's own enum is **inside the installed wheel**:
`adcp/_schemas/3.1/enums/error-code.json`, 92 codes, and its `enumMetadata`
block is `{recovery, suggestion}` for **all 92** — verified by execution:

```
$comment: "Structured recovery classification and remediation hints for each error code.
 SDKs MUST consume this block instead of parsing 'Recovery: X' from enumDescriptions prose.
 Each entry is { recovery, suggestion }."
INVALID_REQUEST  {"recovery": "correctable", "suggestion": "check request parameters and fix"}
AUTH_MISSING     {"recovery": "correctable", "suggestion": "provide credentials via the auth header and retry"}
AUTH_INVALID     {"recovery": "terminal",    "suggestion": "do NOT auto-retry — credentials were rejected; rotate keys, refresh OAuth t…"}
CREATIVE_NOT_FOUND {"recovery":"correctable","suggestion":"verify creative_id via list_creatives, or sync_creatives to register it"}
...
codes with a suggestion in enumMetadata: 92 / total enum codes: 92
```
There is a loader for it too — `adcp/validation/schema_loader.py:106`
`_resolve_schema_root()`, which resolves `files("adcp") / "_schemas" / key`.

### 1.6 The SDK's intended authorship model, stated by the SDK

- **Code**: adopter picks it — either as a string on `AdcpError(code, ...)` or by
  choosing one of the nine `decisioning/errors.py` subclasses.
- **Recovery**: **not** adopter-authored. `errors.py` docstring: *"Recovery values
  are normative — they MUST match the enumMetadata block… Adopters MUST NOT
  override recovery on these subclasses."* `adcp_error()` auto-fills from the
  code table.
- **Message**: SDK authors a default per code (`STANDARD_ERROR_CODES[code]["message"]`,
  and each `errors.py` subclass's `message or "<default>"`). Adopter MAY override.
  `message` is required by the wire schema (`_schemas/3.1/core/error.json`:
  `"required": ["code","message"]`).
- **Suggestion**: adopter-authored *in code*, but the canonical text per code is
  the `enumMetadata.suggestion` string the wheel ships.
- **Serialization**: explicitly not the adopter's job — *"Adopters do NOT
  serialize themselves"* (`decisioning/types.py:87`).

---

## Section 2 — What we USE, RE-IMPLEMENT, IGNORE

`src/core/exceptions.py` is 1340 lines. Its total SDK surface is one import:

```python
# src/core/exceptions.py:16
from adcp.server.helpers import STANDARD_ERROR_CODES, adcp_error
```
Verified repo-wide: the only other `adcp.server.*` imports in `src/` are
`MEDIA_BUY_STATE_MACHINE / is_terminal_status / valid_actions_for_status`
(media_buy_update/list/create), `ADCP_TOOL_DEFINITIONS` (`main.py:331`), and
`adcp.server.idempotency` (`idempotency_canonical.py:34-36`).

| SDK facility | Status | Evidence |
|---|---|---|
| `adcp.server.helpers.STANDARD_ERROR_CODES` | **USED**, then rewritten | `exceptions.py:16`, rebuilt into `WIRE_STANDARD_CODES` at `exceptions.py:119` |
| `adcp.server.helpers.adcp_error()` | **USED** (2 call sites) | `exceptions.py:597` (`to_adcp_error`), `exceptions.py:1219` (`build_two_layer_error_envelope`) |
| `_schemas/3.1/enums/error-code.json` `enumMetadata` | **IGNORED**, hand-copied | see 2.1 |
| `adcp.decisioning.types.AdcpError` | **IGNORED / RE-IMPLEMENTED** | zero imports in `src/` |
| `adcp.decisioning.errors.*` (9 typed subclasses) | **IGNORED / RE-IMPLEMENTED** as 42 `AdCPSalesAgentError` subclasses | `exceptions.py:608-1180` (42 `class AdCP*` declarations) |
| `AdcpError.to_wire()` | **RE-IMPLEMENTED** as `build_two_layer_error_envelope` | `exceptions.py:1197` |
| `adcp.server.translate._extract_structured_fields` | **RE-IMPLEMENTED** as `normalize_to_adcp_error` | `exceptions.py:1306` |
| `adcp.server.translate.build_mcp_error_result` | **IGNORED / RE-IMPLEMENTED** as `AdCPToolError` | `tool_error_logging.py:32` |
| `adcp.server.translate.translate_error(..., "a2a")` | **IGNORED / RE-IMPLEMENTED** | `adcp_a2a_server.py:143,312,342,403,426,434,705,1597` |
| `adcp.error_sanitization.sanitize_error_details` | **IGNORED** | 0 hits in `src/` |
| `adcp.protocols._adcp_errors.validate_adcp_error` (64-char / 4 KB caps) | **IGNORED** | 0 hits in `src/` |
| `adcp.server.helpers.TRANSIENT/CORRECTABLE/TERMINAL_CODES` | **IGNORED / RE-IMPLEMENTED** as `advisory_recovery_for` | `exceptions.py:259` |

### 2.1 The code table: hand-maintained, and provably derivable from the wheel

```python
# src/core/exceptions.py:50
_SPEC_SUPPLEMENT_CODES: dict[str, dict[str, str]] = {
    "CREATIVE_NOT_FOUND": {"recovery": "correctable", "message": "Creative not found"},
    "CONFIGURATION_ERROR": {"recovery": "terminal", "message": "Configuration error"},
    "AUTH_MISSING": {"recovery": "correctable", "message": "No credentials were presented"},
    "AUTH_INVALID": {"recovery": "terminal", "message": "Credentials were presented but rejected"},
    "PERMISSION_DENIED": {...}, "VERSION_UNSUPPORTED": {...},
    "UNSUPPORTED_PROVISIONING": {...}, "BILLING_NOT_SUPPORTED": {...},
}
# src/core/exceptions.py:109
_SPEC_RECOVERY_OVERRIDES: dict[str, str] = {
    "ACCOUNT_PAYMENT_REQUIRED": "terminal", "AUTHORIZATION_REQUIRED": "correctable",
    "BUDGET_EXHAUSTED": "terminal", "CONFLICT": "transient",
    "IDEMPOTENCY_CONFLICT": "correctable", "IDEMPOTENCY_EXPIRED": "correctable",
    "UNSUPPORTED_FEATURE": "correctable",
}
```
The header comment above `_SPEC_SUPPLEMENT_CODES` justifies the hand-copy as
*"the SDK is a cross-check, not the authority"*. Executed check against
`adcp/_schemas/3.1/enums/error-code.json` shipped in adcp 6.6.0:

```
_SPEC_SUPPLEMENT_CODES present in shipped 3.1 enum?
  CREATIVE_NOT_FOUND True | CONFIGURATION_ERROR True | AUTH_MISSING True | AUTH_INVALID True
  PERMISSION_DENIED True | VERSION_UNSUPPORTED True | UNSUPPORTED_PROVISIONING True | BILLING_NOT_SUPPORTED True
_SPEC_RECOVERY_OVERRIDES vs shipped enumMetadata:
  ACCOUNT_PAYMENT_REQUIRED ours=terminal shipped=terminal MATCH
  AUTHORIZATION_REQUIRED   ours=correctable shipped=correctable MATCH
  BUDGET_EXHAUSTED         ours=terminal shipped=terminal MATCH
  CONFLICT                 ours=transient shipped=transient MATCH
  IDEMPOTENCY_CONFLICT     ours=correctable shipped=correctable MATCH
  IDEMPOTENCY_EXPIRED      ours=correctable shipped=correctable MATCH
  UNSUPPORTED_FEATURE      ours=correctable shipped=correctable MATCH
NOT_SUPPORTED in 3.1 enum? False   (matches our _SPEC_DEMOTED_CODES)
```
15/15 hand-written entries reproduce the wheel's own data file exactly. The
authority we cite (the pinned spec enum) is on disk in the dependency we already
import from; the divergence being corrected is `adcp.server.helpers`'s Python
table vs `adcp/_schemas`'s JSON, not spec vs SDK.

And the suggestions:

```python
# src/core/exceptions.py:1245
# Canonical buyer-facing suggestions from error-code.json enumMetadata (AdCP 3.1.1):
INVALID_REQUEST_SUGGESTION = "check request parameters and fix"
VALIDATION_ERROR_SUGGESTION = "review error details and fix field values"
```
`"check request parameters and fix"` is byte-identical to
`enumMetadata.INVALID_REQUEST.suggestion` in the shipped JSON. Two of 92
transcribed by hand; the other 90 are unused. `grep -rn "error-code.json"
src/**/*.py` returns only comments — nothing in `src/` reads the file.

### 2.2 `normalize_to_adcp_error` — our re-implementation of `_extract_structured_fields`

```python
# src/core/exceptions.py:1306
def normalize_to_adcp_error(exc: Exception) -> AdCPSalesAgentError:
    if isinstance(exc, AdCPSalesAgentError):
        _log_internal_detail(exc)
        return exc                                              # (A) no-op
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        return AdCPValidationError(
            errors[0].get("msg") if errors else "Request failed schema validation",
            field=first_validation_error_field(exc),
            suggestion=VALIDATION_ERROR_SUGGESTION,
            details=build_validation_error_details(errors))     # (B) authors message from pydantic
    if isinstance(exc, ValueError):
        return AdCPValidationError(str(exc))                    # (C) authors message = raw str(exc)
    if isinstance(exc, PermissionError):
        return AdCPAuthorizationError(str(exc))                 # (D) same
    # Deliberately NOT str(exc): an arbitrary/untyped exception's text has no
    # provenance guarantee ...
    return AdCPSalesAgentError(type(exc).__name__)                        # (E)
```
Branch (A) is the load-bearing one and is a **no-op**: for a typed error there
is no downstream sanitization point at all. The class docstring says so:

```python
# src/core/exceptions.py:403 (AdCPSalesAgentError docstring; class at :362)
    ``normalize_to_adcp_error()`` returns an already-typed ``AdCPSalesAgentError``
    unchanged, so for a typed error THE RAISE SITE IS THE WIRE — there is no
    downstream sanitization point. The trust decision therefore has to be
    taken at construction time, per raise site, and it is opt-in
```

Branch (C)/(D) contradict the reasoning of branch (E): `str(exc)` on an
arbitrary `ValueError` has exactly the same provenance problem the (E) comment
describes. Trace 2 below is a live instance.

### 2.3 `internal_detail` — the sanctioned escape hatch, at 12 use sites

```python
# src/core/exceptions.py:464 (AdCPSalesAgentError.__init__, def at :436)
        # NON-WIRE. Deliberately absent from to_dict()/to_adcp_error()/
        # build_two_layer_error_envelope(); emitted only to the server-side log
        # by normalize_to_adcp_error(). Never add it to a serializer.
        self.internal_detail = internal_detail
```
`grep -c "internal_detail=" src` → **12**, against 292 `AdCP*Error(...)`
construction sites (Section 4). The SDK has no equivalent slot; this one is ours
and is not the default path.

### 2.4 The `ValueError -> AdCPValidationError(str(exc))` branch, concretely

`AdCPValidationError` is `VALIDATION_ERROR` / 400 / correctable
(`exceptions.py:608`). So **any** `ValueError` anywhere under an `_impl`
becomes a buyer-facing `VALIDATION_ERROR` whose `message` is the raw
`str(exc)` — including `ValueError`s raised deep in adapter code about GAM SOAP
faults. That is Trace 2.

---

## Section 3 — Four traces, hop by hop

### Trace 1 — `src/core/tools/media_buy_create.py:4336`

```python
# src/core/tools/media_buy_create.py:4266
    except Exception as e:
        # Untyped exception — same workflow audit treatment, plus Slack
        ctx_manager.audit_workflow_step_failure_if_present(step, e)
        ... slack ... audit ...
# src/core/tools/media_buy_create.py:4336
        raise AdCPAdapterError(f"Failed to create media buy: {str(e)}")
```

| # | Hop | Object | who set `message` | `code` | `suggestion` | no-op? |
|---|---|---|---|---|---|---|
| 1 | any untyped `e` inside `_create_media_buy_impl` (line 2008) | arbitrary `Exception` | its own raiser | — | — | — |
| 2 | `context_manager.audit_workflow_step_failure_if_present` → `audit_workflow_step_failure` (`context_manager.py:370-391`) | `AdCPSalesAgentError` (via `normalize_to_adcp_error`) persisted to `workflow_step.response_data` | branch (E) `type(exc).__name__`, unless `wire_code not in WIRE_STANDARD_CODES` → `AdCPSalesAgentError.synthesize(..., error_code="SERVICE_UNAVAILABLE", recovery="terminal")` at `context_manager.py:381-390` | rewritten here | carried forward | no — it authors a *second, different* envelope for webhook subscribers |
| 3 | `media_buy_create.py:4336` | `AdCPAdapterError` | **this line**, `f"Failed to create media buy: {str(e)}"` | class default `SERVICE_UNAVAILABLE` (`exceptions.py:835`, 502, transient) | `None` (`_default_suggestion` unset) | no |
| 4a | MCP: `with_error_logging` → `_handle_tool_exception` (`tool_error_logging.py:307`) → `_translate_to_tool_error` (`tool_error_logging.py:294`) | `normalize_to_adcp_error` **branch (A)** | unchanged | unchanged | unchanged | **yes, pure no-op** |
| 5a | `AdCPToolError(build_two_layer_error_envelope(typed), status_code=502)` | dict `{"adcp_error":…, "errors":[…]}` | copied | `exc.wire_error_code` = `translate_error_code("SERVICE_UNAVAILABLE")` = unchanged | copied (`None` → key omitted by `adcp_error()`) | no |
| 6a | FastMCP serializes `raise AdCPToolError`; `__str__` = `json.dumps(self.envelope)` (`tool_error_logging.py:58`) | `CallToolResult(isError=True, content=[TextContent(text=<json>)])` — **no `structuredContent`** (0 hits for `structuredContent=` in `src/`, cf. SDK `build_mcp_error_result` which sets it) | — | — | — | no |
| 4b | A2A: `adcp_a2a_server.py:1655-1659` `except (AdCPSalesAgentError, ValueError, PermissionError)` → `normalize_to_adcp_error(e)` | branch (A) | unchanged | unchanged | unchanged | **yes** |
| 5b | `_build_failed_skill_result` (`adcp_a2a_server.py:354`) → `_build_error_envelope` (`:342`) = `build_two_layer_error_envelope(normalize_to_adcp_error(exc))` — **normalizes a third time** | `{"skill":…, "error_envelope": {...}, "success": False}` in an artifact DataPart | copied | copied | copied | the third normalize is a no-op |
| 4c | REST: `routes/api_v1.py:373` `create_media_buy_raw` raises up to `@app.exception_handler(AdCPSalesAgentError)` (`app.py:192`) → `_envelope_response` (`app.py:135`) | `JSONResponse(status_code=exc.status_code=502, content=build_two_layer_error_envelope(exc))` | copied | copied | copied | no |

Wire result, all three transports: `code="SERVICE_UNAVAILABLE"`,
`recovery="transient"`, `message="Failed to create media buy: <str of an
arbitrary exception>"`, no `suggestion`. Three separate envelope constructions
(`tool_error_logging.py:295`, `adcp_a2a_server.py:342`, `app.py:171`) plus a
fourth for webhooks (`context_manager.py:391`).

### Trace 2 — `src/adapters/gam/managers/targeting.py:330`

```python
# src/adapters/gam/managers/targeting.py:328-330
        except Exception as e:
            logger.error(f"Failed to get/create custom targeting value '{value_name}': {e}", exc_info=True)
            raise ValueError(f"Custom targeting value lookup/creation failed for '{value_name}': {e}")
```
`e` here is whatever `custom_targeting_service.createCustomTargetingValues([...])`
(a zeep SOAP call, `targeting.py:323`) raised — a GAM API fault string.

| # | Hop | Object | `message` author | `code` | `suggestion` | no-op? |
|---|---|---|---|---|---|---|
| 1 | `targeting.py:330` | `ValueError` | this line; interpolates the raw GAM exception | none | none | — |
| 2 | `targeting.py:401 / 456 / 481 / 555` `_get_or_create_custom_targeting_value(...)` | uncaught | — | — | — | yes |
| 3 | `targeting.py:876` `_build_custom_targeting_structure(...)` ← `build_targeting` (`targeting.py:652`) | uncaught (the only `except ValueError` in this file is `targeting.py:~866`, the AXE-exclude-key arm, a different call) | — | — | — | yes |
| 4 | `google_ad_manager.py:285` `_build_targeting` → `:682` inside `create_media_buy` | uncaught | — | — | — | yes |
| 5 | `media_buy_create.py:594` `_execute_adapter_create_media_buy`'s `except Exception as adapter_error:` | logs `traceback.format_exc()` then bare `raise` (`media_buy_create.py:598`) | unchanged | — | — | **yes, deliberately** |
| 6 | `media_buy_create.py:4266` `except Exception as e:` | — | — | — | — | — |
| 7 | `media_buy_create.py:4336` `raise AdCPAdapterError(f"Failed to create media buy: {str(e)}")` | **this line**; `str(e)` is hop 1's string, which contains the GAM fault text | `SERVICE_UNAVAILABLE` | none | no |
| 8+ | identical to Trace 1 hops 4–6 | | | | | |

Note what did **not** happen: `normalize_to_adcp_error`'s
`isinstance(exc, ValueError) -> AdCPValidationError(str(exc))` branch never fires,
because hop 7 got there first. Had hop 7 not existed, the same GAM fault text
would have reached the wire as `VALIDATION_ERROR` / 400 / correctable instead of
`SERVICE_UNAVAILABLE` / 502 / transient. The wire code for this failure is decided
by which `except` clause happens to be nearest, not by the failure.

Final wire text contains, nested three deep: GAM SOAP fault → `"Custom targeting
value lookup/creation failed for '<value>': <fault>"` → `"Failed to create media
buy: <that>"`. Measured against the constraint the `AdCPSalesAgentError` docstring itself
quotes (`transport-errors.mdx` § Security Considerations: *"MUST NOT include…
upstream API responses from internal services"*).

### Trace 3 — `src/core/creative_agent_registry.py:580`

```python
# src/core/creative_agent_registry.py:567-589 (inside _fetch_formats_from_agent, line 371)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.error(f"Creative agent fallback HTTP error: {exc.response.status_code} from {mcp_url}")
                # ``mcp_url`` stays off the wire ... The status code is kept: a numeric HTTP
                # status is structured first-party data, not an identifier.
                if exc.response.status_code == 429:
                    raise AdCPRateLimitError("Creative agent rate-limited",
                                             details={"retry_after": exc.response.headers.get("Retry-After")}) from exc
                if exc.response.status_code >= 500:
                    raise AdCPServiceUnavailableError(
                        f"Creative agent unavailable (HTTP {exc.response.status_code})") from exc
                raise AdCPAdapterError(f"Creative agent HTTP error: {exc.response.status_code}",
                                       recovery="terminal") from exc
```

Two distinct downstream fates, decided by the caller:

**3a — `list_all_formats` (`creative_agent_registry.py:826`):**

| # | Hop | Object | `message` author | `code` | no-op? |
|---|---|---|---|---|---|
| 1 | `:587` | `AdCPAdapterError`, `recovery="terminal"` overriding the class `transient` | this line | `SERVICE_UNAVAILABLE` | — |
| 2 | `:848` `except Exception as e:` | **discarded** | — | — | no |
| 3 | `:857-861` | `AdCPResponseError(code="AGENT_UNREACHABLE", message=CREATIVE_AGENT_UNREACHABLE_MESSAGE)` — module constant at `:81` | a *constant*, third author | `"AGENT_UNREACHABLE"` — not in the pinned enum, not in `WIRE_STANDARD_CODES` | no |
| 4 | `normalize_advisory_errors` (`exceptions.py:285`) → `to_wire_error_code` (`exceptions.py:242`) | `AGENT_UNREACHABLE` has no `ERROR_CODE_MAPPING` entry → collapses to `SERVICE_UNAVAILABLE`; `recovery` filled by `advisory_recovery_for` → `transient` | — | rewritten a 2nd time | no |
| 5 | rides in `errors[]` on a **success** payload of `list_creative_formats`; never touches `build_two_layer_error_envelope` | | | | |

So on this path the raise-site message, code and `recovery="terminal"` are all
destroyed and re-authored. The typed exception carried nothing forward.

**3b — `get_formats_for_agent` (`creative_agent_registry.py:707`):** no catch;
the `AdCPAdapterError` propagates as in Trace 1 hops 4–6.

Separately, `src/core/tools/creative_formats.py:206-213` re-authors again for a
*different* failure in the same tool:

```python
    except Exception as e:
        logger.error(f"Failed to create creative agent registry: {e}", exc_info=True)
        raise AdCPServiceUnavailableError(f"Creative agent registry initialization failed: {e}", context=req.context) from e
```
— an f-string carrying `str(e)` of an arbitrary exception, i.e. the thing the
neighbouring `:575-580` comment block argues against.

### Trace 4 — `src/core/schemas/capability_declarations.py:278`

```python
# src/core/schemas/capability_declarations.py:274-281
        try:
            parsed = cls.model_validate(declared)
        except ValidationError as exc:
            raise AdCPConfigurationError(
                f"capability_declarations is not a valid declaration document: {exc}",
                details={"capability_declarations": sorted(declared)},
            ) from exc
```

| # | Hop | Object | `message` author | `code` | `suggestion` | no-op? |
|---|---|---|---|---|---|---|
| 1 | pydantic | `ValidationError` whose `str()` renders every failing field **with its input value** | pydantic | — | — | — |
| 2 | `:278` | `AdCPConfigurationError` | this line, embedding hop 1's full render | `CONFIGURATION_ERROR` — a `_SPEC_SUPPLEMENT_CODES` pass-through (`exceptions.py:50`), so **no translation**; class at `exceptions.py:843`, 500, terminal | `None` | — |
| 3 | `capabilities.py:550` `declarations = CapabilityDeclarations.from_tenant(tenant.get("capability_declarations"))` — **no try/except** | unchanged | — | — | — | yes |
| 4a | MCP `_translate_to_tool_error` → `normalize_to_adcp_error` branch (A) | unchanged | unchanged | unchanged | unchanged | **yes** |
| 5a | `build_two_layer_error_envelope` | `{"adcp_error":{"code":"CONFIGURATION_ERROR","message":"capability_declarations is not a valid declaration document: 3 validation errors for CapabilityDeclarations …","recovery":"terminal","details":{...}}, "errors":[…]}`, HTTP 500 | | | | |
| 4b | A2A: `get_adcp_capabilities` ∈ `DISCOVERY_SKILLS` (`adcp_a2a_server.py:263`), so this reaches an **unauthenticated** caller | same | | | | |
| 4c | REST: `app.py:192` handler, status 500 | same | | | | |

Nothing between line 278 and the wire inspects the message. The 4 KB envelope cap
in `adcp/protocols/_adcp_errors.py:MAX_ERROR_SIZE_BYTES` is not applied anywhere
in `src/`; a multi-field pydantic render can exceed it — **unverified** whether
any real declaration document does.

---

## Section 4 — The authorship-point census

AST scan of every `.py` under `src/` (script:
`scratchpad/census.py`, `scratchpad/census2.py`), counting call expressions that
supply an error `message` or `code`.

### 4.1 Message

**308 construction sites** can decide buyer-facing message text:

| kind | count |
|---|---|
| `AdCP*Error(...)` constructions with a message arg | **292** |
| advisory `Error(...)` / `AdCPResponseError(...)` with `message=` | **12** |
| direct `adcp_error(...)` | 2 |
| `AdCPSalesAgentError.synthesize(...)` | 2 |

Of the 292 `AdCP*Error` sites: **82** pass a bare string literal, **210** pass a
non-literal (f-string, `str(...)`, variable, conditional).

Plus **7 non-construction functions** that author or rewrite message text:

1. `src/core/validation_helpers.py:162` `format_validation_error` — writes a
   multi-line message including `json.dumps(input_val, indent=2)` of the caller's
   rejected value.
2. `src/core/validation_helpers.py:225` `suggest_validation_fix` — writes 5
   distinct suggestion strings.
3. `src/core/validation_helpers.py:28` `adcp_validation_boundary` — composes 1+2
   into `AdCPValidationError`.
4. `src/core/exceptions.py:1306` `normalize_to_adcp_error` — branches (B)(C)(D)(E)
   each author a message.
5. `src/core/tool_error_logging.py:104` `extract_error_info` — synthesizes code
   `"TOOL_ERROR"` and message `str(error)` for plain `ToolError`.
6. `src/core/context_manager.py:370-391` `audit_workflow_step_failure` — rewrites
   code to `SERVICE_UNAVAILABLE` and re-emits the envelope for webhooks.
7. `src/a2a_server/adcp_a2a_server.py:309-313` — prefixes `f"{operation} failed: {typed.message}"`.

Plus **74 explicit `suggestion=` arguments** (48 literal, 12 f-string, 10
variable, 3 call, 1 boolop) and 3 class-level `_default_suggestion` values
(`AUTH_MISSING_SUGGESTION`, `AUTH_INVALID_SUGGESTION`, plus the two module
constants `INVALID_REQUEST_SUGGESTION` / `VALIDATION_ERROR_SUGGESTION`), against
92 `suggestion` strings shipped unused in `adcp/_schemas/3.1/enums/error-code.json`.

**Message-authorship total: 308 construction sites + 7 rewriting functions = 315 places.**

### 4.2 Code

| kind | count | where |
|---|---|---|
| `AdCPSalesAgentError` subclasses declaring `_default_error_code` | 42 | `exceptions.py:608-1180` (42 `class AdCP*` declarations) |
| `error_code=` runtime override | 1 | `context_manager.py:383` |
| `AdCPSalesAgentError.synthesize(...)` | 2 | `context_manager.py:381`, `tool_error_logging.py:455` |
| advisory `Error(code="...")` literal | 11 | `creative_agent_registry.py:859`, `media_buy_delivery.py:235,366,564`, `accounts.py:1230`, `capabilities.py:140`, `creatives/listing.py:389`, `media_buy_list.py:123,138,257`, `targeting_capabilities.py:261` |
| `ERROR_CODE_MAPPING` rewrite table | 42 entries | `exceptions.py:125-198` |
| `to_wire_error_code` collapse-to-`SERVICE_UNAVAILABLE` | 1 | `exceptions.py:242` |
| `normalize_to_adcp_error` type→code branches | 4 | `exceptions.py:1316,1319,1327,1329,1340` |
| `extract_error_info` → `"TOOL_ERROR"` | 2 | `tool_error_logging.py:142,143` |
| `app.py:243` picks `AdCPValidationError` vs `AdCPInvalidRequestError` by `field.startswith("attribution_window")` | 1 | `app.py:266-268` |
| `context_manager.py:381` non-standard→`SERVICE_UNAVAILABLE` | 1 | |

**Code-authorship total: ~65 places**, of which 42 are the typed-class
declarations (the shape the thesis wants) and ~25 are runtime rewrites.

`"TOOL_ERROR"` has no `ERROR_CODE_MAPPING` entry and is not in
`WIRE_STANDARD_CODES`, so `handle_tool_error` (`tool_error_logging.py:449-461`)
can emit it verbatim on the REST wire via
`AdCPSalesAgentError.synthesize(error_message, error_code="TOOL_ERROR", …)` →
`build_two_layer_error_envelope` → `wire_error_code` → unchanged. Reachable when
a plain `fastmcp` `ToolError` (not `AdCPToolError`) hits `app.py`'s
`@app.exception_handler(ToolError)`. **Unverified** whether any live path raises
a bare `ToolError`; `tests/harness/_base.py:216` special-cases the value, which
implies the harness sees it.

### 4.3 Full per-file list (message-authoring construction sites)

```
src/a2a_server/adcp_a2a_server.py (12): 135,404,435,706,828,1598,1776,1828,1913,1949,2117,2314
src/adapters/base.py (1): 253
src/adapters/broadstreet/adapter.py (14): 169,350,641,659,678,699,708,720,740,742,749,768,772,782
src/adapters/gam/managers/creatives.py (5): 692,708,715,734,827
src/adapters/gam/managers/targeting.py (15): 674,683,688,727,765,777,783,789,796,802,832,852,889,901,922
src/adapters/gam/pricing_compatibility.py (1): 151
src/adapters/google_ad_manager.py (27): 155,172,312,321,434,452,551,572,585,617,787,1144,1335,1352,1373,1387,1403,1414,1427,1448,1484,1495,1502,1515,1546,1571,1604
src/adapters/kevel.py (5): 237,619,706,744,773
src/adapters/mock_ad_server.py (18): 350,571,575,618,624,630,636,642,653,739,809,812,818,1050,1094,1139,1205,1265
src/adapters/triton_digital.py (5): 164,553,639,677,705
src/admin/blueprints/operations.py (2): 593,595
src/core/auth.py (6): 266,333,360,396,400,422
src/core/auth_context.py (2): 113,129
src/core/context_manager.py (1): 381
src/core/creative_agent_registry.py (14): 95,433,455,500,503,575,580,587,599,610,612,627,644,858
src/core/database/models.py (3): 209,674,1396
src/core/database/repositories/media_buy.py (2): 81,205
src/core/database/repositories/workflow.py (1): 234
src/core/exceptions.py (6): 597,1219,1321,1328,1330,1340
src/core/format_resolver.py (1): 98
src/core/helpers/account_helpers.py (7): 83,89,94,111,125,169,176
src/core/helpers/adapter_helpers.py (1): 280
src/core/helpers/context_helpers.py (2): 80,81
src/core/helpers/creative_helpers.py (2): 586,628
src/core/idempotency_canonical.py (1): 64
src/core/idempotency_policy.py (2): 61,67
src/core/main.py (4): 306,307,315,316
src/core/property_list_resolver.py (4): 49,106,111,113
src/core/resolved_identity.py (1): 176
src/core/schema_helpers.py (4): 121,128,165,173
src/core/schemas/_base.py (5): 1980,1988,2015,2021,2027
src/core/schemas/capability_declarations.py (5): 148,254,264,278,327
src/core/signals_agent_registry.py (3): 178,202,214
src/core/tool_error_logging.py (2): 295,455
src/core/tools/accounts.py (9): 623,1070,1229,1262,1285,1516,1616,1649,1805
src/core/tools/capabilities.py (2): 139,439
src/core/tools/creative_formats.py (1): 212
src/core/tools/creatives/_assignments.py (3): 137,168,241
src/core/tools/creatives/_processing.py (2): 344,669
src/core/tools/creatives/_validation.py (4): 95,98,104,130
src/core/tools/creatives/_workflow.py (2): 45,54
src/core/tools/creatives/listing.py (3): 108,117,388
src/core/tools/media_buy_create.py (46): 381,497,1411,1472,1480,1494,1501,1514,1571,1582,1589,1598,1608,1617,1671,1691,1707,1723,2083,2198,2211,2227,2238,2247,2260,2283,2290,2301,2330,2438,2456,2651,3173,3267,3280,3360,3522,3542,3700,3811,3873,3906,3948,3983,4079,4336
src/core/tools/media_buy_delivery.py (5): 50,198,234,365,556
src/core/tools/media_buy_list.py (5): 107,122,137,256,469
src/core/tools/media_buy_update.py (21): 233,247,287,337,400,413,430,457,500,642,793,874,975,997,1024,1088,1100,1205,1259,1355,1504
src/core/tools/products.py (7): 176,202,206,313,341,373,868
src/core/tools/properties.py (1): 191
src/core/tools/signals.py (3): 296,301,323
src/core/tools/task_management.py (2): 210,221
src/core/validation_helpers.py (1): 54
src/core/version_negotiation.py (1): 49
src/core/webhook_validator.py (1): 115
src/services/ai/factory.py (1): 154
src/services/idempotency_policy.py (2): 89,101
src/services/targeting_capabilities.py (2): 260,333
TOTAL 308
```

---

## Section 5 — Blockers to "raise a typed error with no message argument"

Answering the four candidate blockers with counts.

### (a) "The SDK doesn't supply enough" — **FALSE**, refuted

The SDK supplies a message default per code
(`STANDARD_ERROR_CODES[code]["message"]`, `helpers.py:29`), a raisable
structured server exception with `to_wire()`
(`decisioning/types.py:62,135`), nine code-bound subclasses with default
messages (`decisioning/errors.py`), a boundary translator for both transports
(`server/translate.py:176,255`), a details sanitizer
(`error_sanitization.py:120`), an envelope validator with the spec's 64-char /
4 KB caps (`protocols/_adcp_errors.py:16`), and — decisive — the pinned spec's
own `enumMetadata` with `{recovery, suggestion}` for **92/92** codes at
`adcp/_schemas/3.1/enums/error-code.json`, plus the loader
(`validation/schema_loader.py:106`) to read it. We import two names from all of
that: `STANDARD_ERROR_CODES` and `adcp_error`.

### (b) "Our error types don't carry enough" — **PARTLY TRUE**, 2 gaps

`AdCPSalesAgentError.__init__` (`exceptions.py:436`) already carries `details, field, suggestion, retry_after,
context, internal_detail` (`exceptions.py:438-464`). What it lacks:

1. **No class-level default `message`.** Every subclass declares
   `_default_status_code`, `_default_error_code`, `_default_recovery`,
   `_default_suggestion` — but there is no `_default_message`. `message: str = ""`
   is the first positional parameter, and `to_dict`/`to_adcp_error`/
   `build_two_layer_error_envelope` pass it straight through. Since `message` is
   `"required"` in `_schemas/3.1/core/error.json`, an omitted message ships an
   empty string — so a raise site *must* pass one today. This is the single
   mechanical blocker, and the fix is 42 `_default_message` ClassVars, or one
   lookup into `WIRE_STANDARD_CODES[code]["message"]` (already in memory).
2. **`_default_suggestion` is set on 3 of 42 subclasses** (`AdCPAuthenticationError`,
   `AdCPAuthRequiredError`, plus inheritance), so 74 raise sites pass
   `suggestion=` by hand while 92 canonical suggestions sit unread in the wheel.

### (c) "The transport boundary isn't a single place" — **TRUE**, 4 boundaries + 3 rewriters

Envelope construction sites (`build_two_layer_error_envelope` callers):

- `src/core/tool_error_logging.py:295` (MCP), `:461` (REST-catching-ToolError)
- `src/a2a_server/adcp_a2a_server.py:143, 312, 342, 403, 426, 434, 705, 1597` (A2A — **8** sites)
- `src/app.py:171` (REST)
- `src/core/context_manager.py:391` (webhook/`workflow_step.response_data`)

`normalize_to_adcp_error` callers: `app.py:228,287,315`,
`context_manager.py:370`, `mcp_compat_middleware.py:124`,
`tool_error_logging.py:294`, `adcp_a2a_server.py:309,342,1659` — **9**, and at
least one path calls it three times on the same exception (Trace 1, hops 4b/5b).
Every one of those calls is a no-op for an already-typed error.

Three of the boundaries also *rewrite*, not just serialize:
`context_manager.py:381` (code → `SERVICE_UNAVAILABLE`),
`app.py:266-268` (code chosen by `field.startswith("attribution_window")`),
`tool_error_logging.py:455` (`AdCPSalesAgentError.synthesize` from a parsed `ToolError`).

The wire shapes also differ per transport by construction: MCP emits the
envelope as `json.dumps` inside `content[0].text` with **no**
`structuredContent` (`tool_error_logging.py:58`; 0 occurrences of
`structuredContent=` in `src/`), where the SDK's `build_mcp_error_result`
(`translate.py:176`) sets `structuredContent={"adcp_error": …}` + `isError=True`
and echoes `context`. A2A puts it in `error.data` **or** in an artifact DataPart
under `error_envelope` depending on which of the 8 sites fires.

### (d) "Call sites want to say things the enumeration can't express" — **TRUE for a minority**

Of the **210** non-literal `AdCP*Error` message sites:

- **11 interpolate an exception object** (verified by AST + regex over the
  message source segment) — these carry text whose provenance we do not own, and
  every one of them has a sanctioned destination already (`internal_detail=`,
  used at only 12 sites repo-wide):

```
src/core/creative_agent_registry.py:580  AdCPServiceUnavailableError  f"Creative agent unavailable (HTTP {exc.response.status_code})"
src/core/creative_agent_registry.py:587  AdCPAdapterError             f"Creative agent HTTP error: {exc.response.status_code}"
src/core/property_list_resolver.py:106   AdCPAdapterError             f"Failed to fetch property list from {url}: HTTP {exc.response.status_code}"
src/core/schemas/capability_declarations.py:278 AdCPConfigurationError f"capability_declarations is not a valid declaration document: {exc}"
src/core/tools/creative_formats.py:212   AdCPServiceUnavailableError  f"Creative agent registry initialization failed: {e}"
src/core/tools/media_buy_create.py:1582  AdCPValidationError          f"Package {package_idx+1}, format_ids[{idx}]: Invalid format_id structure: {e}"
src/core/tools/media_buy_create.py:1617  AdCPAdapterError             f"...Failed to verify format on agent. agent_url={agent_url}, format_id={format_id!r}. Error: {e}"
src/core/tools/media_buy_create.py:4336  AdCPAdapterError             f"Failed to create media buy: {str(e)}"
src/core/tools/media_buy_list.py:469     AdCPValidationError          f"Invalid status_filter value: {e}"
src/core/tools/products.py:868           AdCPValidationError          f"Invalid get_products request: {e}"
src/core/tools/properties.py:191         AdCPAdapterError             f"Failed to list authorized properties: {str(e)}"
```
  (plus 3 non-f-string equivalents: `exceptions.py:1328` `AdCPValidationError(str(exc))`,
  `exceptions.py:1330` `AdCPAuthorizationError(str(exc))`,
  `adcp_a2a_server.py:135` `AdCPValidationError(str(exc))` — 14 total.)

- **54 interpolate an identifier** (`{media_buy_id}`, `{format_id}`,
  `{agent_url}`, `{package_id}`, …). These are expressible without message text:
  `field=` for the request path, `details=` for the value —
  `error.json`'s `details` description names the canonical shape
  (`rejected_value` / `accepted_values`), and `adcp_error()` already forwards
  both.

- **~155 interpolate a computed value** (limits, thresholds, supported sets,
  counts). Same answer: `details` is `additionalProperties: true` and the spec
  explicitly blesses `accepted_values` for exactly this. Whether the *rendered
  sentence* matters is a graded question I did not measure — **unverified**
  which of these are asserted on by a storyboard step. One that is: the
  `AdCPSalesAgentError` docstring claims *"the spec also POSITIVELY requires specific
  first-party message content in places (version negotiation must name the
  buyer's requested version and the seller's supported set)"* — the candidate
  site is `src/core/version_negotiation.py:49`. I did not open the storyboard to
  confirm the assertion is on `message` rather than on `details.supported_versions`.

### 5.1 What would have to be true for `raise SomeTypedError(field=..., details=...)` with no message

1. `AdCPSalesAgentError` gains `_default_message: ClassVar[str]`, or `__init__` falls back
   to `WIRE_STANDARD_CODES[self.error_code]["message"]` — currently
   `message: str = ""` with no fallback (`exceptions.py:438-464`).
2. `_default_suggestion` populated for all 42 subclasses from
   `adcp/_schemas/3.1/enums/error-code.json` `enumMetadata[code].suggestion`
   instead of 2 hand-copied module constants — currently 3 of 42 set it.
3. The 14 exception-interpolating sites move their text to `internal_detail=`
   (already built, already logged by `_log_internal_detail`, `exceptions.py:1284`).
4. The 209 identifier/value-interpolating sites move to `field=` + `details=`.
5. One envelope constructor instead of 12 `build_two_layer_error_envelope` call
   sites across 5 modules, and one normalize call instead of 9.
6. The three code-rewriting boundaries (`context_manager.py:381`,
   `app.py:266-268`, `tool_error_logging.py:455`) either disappear or become the
   single boundary.
7. `ERROR_CODE_MAPPING`'s 42 entries and `INTERNAL_CODES`' 16 entries stop being
   needed — they exist because ~14 subclasses declare a `_default_error_code`
   that is *not* a wire code (`WORKFLOW_CREATION_FAILED`, `LINE_ITEM_CREATION_FAILED`,
   `PARTIAL_FAILURE`, `GAM_UPDATE_FAILED`, `ACTIVATION_WORKFLOW_FAILED`,
   `MEDIA_BUY_REJECTED`, `INVENTORY_UNAVAILABLE`, `FORMAT_NOT_FOUND`,
   `TASK_NOT_FOUND`, `NOT_FOUND`, `INTERNAL_ERROR`, …). Under the thesis, that
   internal taxonomy is `internal_detail` / `details`, not `error_code`.

### 5.2 Blocker verdict

- (a) SDK insufficiency — **not a blocker.** Refuted by 1.1–1.5; 15/15 of our
  hand-maintained code-table entries reproduce a JSON file inside the installed wheel.
- (b) our types — **a small real blocker**: one missing `_default_message` slot,
  and `_default_suggestion` populated on 3 of 42 classes.
- (c) transport boundary not singular — **the largest real blocker**: 12 envelope
  constructors, 9 normalize calls, 3 of which rewrite the code, and an MCP wire
  shape that differs from the SDK's by omitting `structuredContent`.
- (d) call sites needing inexpressible text — **true for at most a handful.**
  14 of 210 interpolate untrusted text (all have `internal_detail`); 54
  interpolate identifiers (all have `field`/`details`); the remaining ~155
  interpolate seller-computed values that `details` accepts. How many of those
  ~155 are graded on `message` specifically is **unverified**.
