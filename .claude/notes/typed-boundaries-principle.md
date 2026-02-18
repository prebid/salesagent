# Typed Boundaries Principle

> Established in PR #1044 ("refactor: enforce typed model boundaries across serialization and data flow").
> Extended here with protocol versioning and library inheritance guidelines.

## Core Rule

Pydantic models are the universal data representation inside the application. Serialization to/from dicts and JSON happens **only at system boundaries** (protocol input, database I/O, API responses). No dicts at internal boundaries. No `model_dump()` between layers.

### Why: encapsulation

The previous pattern of `model_dump()` at every layer boundary violated encapsulation — the third principle of OOP (alongside inheritance and polymorphism). Instead of working with the model's interface (typed attributes, validators, computed properties), callers were cracking it open into a raw `dict[str, Any]` and doing surgery on the internals. At that point the model's invariants, validators, and type safety are all gone. Any caller can inject, rename, or remove fields with no type checking and no validation.

By keeping models intact across layers, consumers are forced to work through the interface. The model owns its data and its rules. GRASP's Information Expert principle also applies: the model that owns the data should be responsible for operations on that data — not an external caller navigating a dict.

```
JSON from client
  → boundary: Pydantic coercion (deserialize once)
  → internal: everything is typed models
  → boundary: model_dump / ToolResult / DB JSONB (serialize once)
```

## Library Type Inheritance

**All buyer-facing models MUST extend the adcp library types.** The library defines the protocol spec. Our extension classes exist only for:

1. **`model_config` override** — the 2jl pattern: `ConfigDict(extra=get_pydantic_extra_mode())`. Forbid extras in dev (catch bugs), ignore in prod (forward compat). This is needed because the library's codegen hardcodes `extra='allow'`.

2. **Codegen gaps** — spec fields the code generator missed (e.g., `creative_ids` on `PackageUpdate`). These should be filed upstream and removed once fixed.

3. **Internal-only fields** — fields not in the spec, needed by salesagent internals. Marked with `Field(exclude=True)` so they never appear in protocol responses.

4. **Normalizing validators** — pre-validators that translate deprecated protocol fields to current structure (e.g., v2 flat geo → v3 structured geo).

**Nothing else.** Do not duplicate fields that exist in the library. Do not duplicate validators that the library already provides. The extension class should be as small as possible — ideally a one-liner `model_config` override.

```python
# CORRECT: minimal extension
class UpdateMediaBuyRequest(LibraryUpdateMediaBuyRequest1):
    model_config = ConfigDict(extra=get_pydantic_extra_mode())

# CORRECT: codegen gap + model_config
class PackageUpdate(LibraryPackageUpdate1):
    model_config = ConfigDict(extra=get_pydantic_extra_mode())
    creative_ids: list[str] | None = None  # spec field, missing from codegen

# WRONG: parallel hierarchy duplicating library fields
class UpdateMediaBuyRequest(SalesAgentBaseModel):
    media_buy_id: str | None = None       # already in library
    packages: list[OurType] | None = None  # already in library
    context: dict[str, Any] | None = None  # library has typed ContextObject
    # ... 30 more lines of duplication
```

## Protocol Versioning

When the protocol evolves (e.g., v3 → v4 field structure change), two concerns are handled separately:

### `extra` mode — unknown field handling
Truly unknown fields (garbage, typos, fields from a different protocol) are handled by the `model_config` extra mode. Dropping them is always correct. This is the 2jl mechanism.

### Version translation — deprecated field normalization
Known deprecated fields from older protocol versions are **defined on the model** as deprecated optional fields with normalizing pre-validators. Pydantic processes them as real fields *before* the `extra` check runs — they don't get stripped.

```
Client sends v2 geo_countries → Pydantic parses it (defined field, deprecated)
                              → pre-validator normalizes to v3 structure
                              → extra='ignore' strips truly unknown junk
                              → model is clean v3 internally
```

The library types should carry the deprecated field aliases and normalizers. Our extension class adds them only if the library doesn't (yet).

### Consequence for consumers (adapters, business logic)
Downstream code works with the **latest typed model**. It does not know or care what protocol version the client spoke. The coercion and normalization happened at the boundary. An adapter receives a `Targeting` instance with structured geo — whether the client sent v2 flat fields or v3 structured fields is invisible.

**Protocol changes do not break internals**, provided the underlying logic doesn't change. The boundary absorbs version differences.

## Change Locality

The extension class is the single point of flexibility:

| Want to add... | Where | Downstream cascade |
|---|---|---|
| Extra mode override | `model_config` on extension class | Zero |
| v2→v3 field normalizer | Pre-validator on extension class | Zero |
| Internal-only field | `Field(exclude=True)` on extension class | Zero |
| Missing spec field | Field on extension class | Zero — remove when upstream fixes it |

Models already following this pattern: `Targeting`, `CreateMediaBuyRequest`, `PackageRequest`, `Product`, `Format`, `Creative`, `FrequencyCap`, and all success/error response types.

## References
- PR #1044: established typed model boundaries, migrated `_create_media_buy_impl` to accept `CreateMediaBuyRequest`
- salesagent-2jl: fixed `extra='allow'` bypass on 7 buyer-facing request models
- salesagent-4xs: applying the same pattern to `UpdateMediaBuyRequest` and `PackageUpdate`
