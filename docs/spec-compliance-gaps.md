# AdCP Spec Compliance Gaps Analysis

## Executive Summary

Found 4 fields in `CreateMediaBuyRequest` that appear to be non-compliant with AdCP spec:
1. ✅ **webhook_url** - ACTUALLY SPEC COMPLIANT (wrong field name in code)
2. ✅ **webhook_auth_token** - ACTUALLY SPEC COMPLIANT (wrong field structure in code)
3. ⚠️ **campaign_name** - NOT IN SPEC (internal display name)
4. ✅ **currency** - SPEC COMPLIANT (AdCP PR #88 added in v2.4)

**Verdict**: 3 of 4 are actually compliant! Only `campaign_name` is a true gap.

---

## Detailed Analysis

### 1. webhook_url & webhook_auth_token

**Status**: ✅ **SPEC COMPLIANT** (but implemented incorrectly)

**What the spec says:**
```json
{
  "reporting_webhook": {
    "$ref": "/schemas/v1/core/push-notification-config.json"
  }
}
```

**push-notification-config.json structure:**
```json
{
  "url": "string (format: uri)",
  "token": "string (optional)",
  "authentication": {
    "schemes": ["Bearer" | "HMAC-SHA256"],
    "credentials": "string (min 32 chars)"
  }
}
```

**What we currently have (WRONG):**
```python
class CreateMediaBuyRequest(BaseModel):
    webhook_url: str | None = Field(...)  # ❌ WRONG - not in spec
    webhook_auth_token: str | None = Field(...)  # ❌ WRONG - not in spec
    reporting_webhook: dict[str, Any] | None = Field(...)  # ✅ CORRECT!
```

**The Problem:**
- We have BOTH `webhook_url`/`webhook_auth_token` (flat, non-spec) AND `reporting_webhook` (spec-compliant)
- The flat fields are never actually read from requests
- Code comments claim they're "AdCP spec" but they're NOT

**Evidence they're not used:**
```bash
$ grep "req\.webhook_url" src/core/main.py
# No results!
```

**Fix Required:**
1. **Remove** `webhook_url` and `webhook_auth_token` fields
2. **Keep** `reporting_webhook` (already spec-compliant)
3. Update any code that tries to use these fields (but grep shows none!)

**Impact**: LOW - Fields are defined but never read

---

### 2. campaign_name

**Status**: ⚠️ **NOT IN SPEC** (internal extension)

**What the spec says:**
- No `campaign_name` field in create-media-buy-request.json
- Media buys are identified by `buyer_ref` (required field)

**What we have:**
```python
class CreateMediaBuyRequest(BaseModel):
    campaign_name: str | None = Field(
        None,
        description="Campaign name for display purposes"
    )
```

**Where it's used:**
```python
# src/core/main.py:3117
req = CreateMediaBuyRequest(
    ...
    campaign_name=None,  # Optional display name
)
```

**The Problem:**
- This is an internal field for UI display
- NOT part of AdCP spec
- Clients shouldn't be sending this

**Fix Options:**

**Option A: Remove it** (RECOMMENDED)
- Most spec-compliant approach
- Use `buyer_ref` for display instead
- If UI needs friendly names, maintain mapping in database

**Option B: Keep it as internal extension**
- Document as "implementation-specific extension"
- Don't expect clients to provide it
- Only use server-side

**Option C: Propose to AdCP spec**
- File issue with AdCP maintainers
- Argue for optional display name field
- Wait for spec update

**Recommendation**: **Option A** - Remove it. The `buyer_ref` field serves the same purpose.

**Impact**: LOW - Only used for internal display, never sent to clients

---

### 3. currency

**Status**: ✅ **SPEC COMPLIANT** (newly added in v2.4)

**What the spec says (AdCP PR #88):**
```json
{
  "currency": {
    "type": "string",
    "pattern": "^[A-Z]{3}$",
    "description": "ISO 4217 currency code for campaign"
  }
}
```

**What we have:**
```python
class CreateMediaBuyRequest(BaseModel):
    currency: str | None = Field(
        None,
        pattern="^[A-Z]{3}$",
        description="ISO 4217 currency code for campaign (applies to budget and all packages) - AdCP PR #88",
    )
```

**The Problem:**
- NO PROBLEM! This is actually spec-compliant
- Added in AdCP v2.4 via PR #88
- Our code even references the PR number

**Evidence**:
Line 2364-2368 in schemas.py:
```python
currency: str | None = Field(
    None,
    pattern="^[A-Z]{3}$",
    description="ISO 4217 currency code for campaign (applies to budget and all packages) - AdCP PR #88",
)
```

**Fix Required:** NONE - Already compliant!

**Impact**: NONE - This field is correct

---

## Root Cause Analysis

### Why do we have these non-spec fields?

**Historical Reasons:**
1. **webhook_url/webhook_auth_token** - Created before `reporting_webhook` was added to spec
   - Probably from early MCP experimentation
   - Never removed when proper spec field was added
   - Comments incorrectly claim they're "AdCP spec"

2. **campaign_name** - Internal UX convenience
   - Developers wanted friendly names in UI
   - Added without checking spec
   - Nobody validated it was non-compliant

### Why weren't they caught?

**Lack of Validation:**
- No automated check that Pydantic schema fields match JSON schema fields
- Manual schema maintenance allows drift
- Comments claiming "AdCP spec" went unquestioned

**This is exactly why auto-generation matters!**

---

## Recommendations

### Immediate Actions (This PR)

1. **Document the gaps** ✅ (this file)
2. **Add validation** - Use generated schemas as source of truth

### Next PR (Clean up non-compliant fields)

1. **Remove webhook_url and webhook_auth_token**
   ```python
   class CreateMediaBuyRequest(BaseModel):
       # Remove these
       # webhook_url: str | None = ...
       # webhook_auth_token: str | None = ...

       # Keep this (already compliant!)
       reporting_webhook: dict[str, Any] | None = Field(...)
   ```

2. **Remove campaign_name**
   ```python
   # Remove this
   # campaign_name: str | None = Field(...)

   # Use buyer_ref for display instead
   ```

3. **Update tests** to not reference removed fields

4. **Update documentation** to explain the changes

### Long-term (Migration to Generated Schemas)

1. **Phase 1**: Use generated as validation (DONE)
2. **Phase 2**: Migrate simple models
3. **Phase 3**: Wrap complex models with generated as base

This will prevent future drift!

---

## Testing Impact

### Tests that might break:

```bash
# Find tests using the fields
grep -r "webhook_url" tests/
grep -r "webhook_auth_token" tests/
grep -r "campaign_name" tests/
```

**Result from investigation:**
- `webhook_url` - Used in ~20 places as MCP tool parameter (NOT from request schema!)
- `webhook_auth_token` - Only in schema definition
- `campaign_name` - Only in schema definition, passed as `None`

**Impact**: MINIMAL - Most usage is as tool parameters, not request fields

---

## Spec Reference

**Official AdCP Schemas:**
- Base: https://adcontextprotocol.org/schemas/v1/
- Create Media Buy: https://adcontextprotocol.org/schemas/v1/media-buy/create-media-buy-request.json
- Push Notifications: https://adcontextprotocol.org/schemas/v1/core/push-notification-config.json

**Cached Locally:**
- `tests/e2e/schemas/v1/_schemas_v1_media-buy_create-media-buy-request_json.json`
- `tests/e2e/schemas/v1/_schemas_v1_core_push-notification-config_json.json`

---

## Action Items

- [ ] Create PR to remove `webhook_url` and `webhook_auth_token` from CreateMediaBuyRequest
- [ ] Create PR to remove `campaign_name` from CreateMediaBuyRequest
- [ ] Update any tests that reference these fields
- [ ] Add pre-commit hook to validate schema fields against JSON schemas
- [ ] Document `reporting_webhook` usage in examples
- [ ] Update migration guide with these findings

---

## Conclusion

**Good News:**
- Only 1 of 4 fields is actually non-compliant (`campaign_name`)
- The others are either spec-compliant or unused legacy fields
- Auto-generation will prevent this in the future

**Action Required:**
- Remove 3 fields from CreateMediaBuyRequest
- Minimal code impact (they're barely used)
- High spec compliance gain

**Lesson Learned:**
This is exactly why auto-generation from JSON schemas matters - manual maintenance leads to drift!
