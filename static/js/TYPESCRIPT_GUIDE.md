# TypeScript Type Checking for JavaScript Templates

This guide explains how to use TypeScript type definitions for compile-time safety in our JavaScript code.

## What We Have

1. **`format-utils.js`** - Runtime JavaScript utilities
2. **`format-utils.d.ts`** - TypeScript type definitions
3. **`jsconfig.json`** - VSCode/IDE configuration

## How to Enable Type Checking

### Option 1: Add `@ts-check` to Individual Files

Add this comment at the top of any template's `<script>` block:

```html
<script>
// @ts-check

// Now TypeScript will check this file!
const formats = [];

formats.forEach(format => {
    // IDE will show autocomplete for getFormatId()
    // TypeScript will warn if you pass wrong types
    const id = getFormatId(format);
});
</script>
```

### Option 2: JSDoc Type Annotations

Use JSDoc comments for variable types:

```javascript
// @ts-check

/** @type {import('./static/js/format-utils').Format[]} */
let allFormats = [];

/** @type {Set<string>} */
let selectedFormats = new Set();

allFormats.forEach(format => {
    const id = getFormatId(format);  // TypeScript knows format is Format type
    selectedFormats.add(id);  // TypeScript knows this expects string
});
```

### Option 3: Import Types in Template

```html
<script>
// @ts-check

/**
 * @typedef {import('./static/js/format-utils').Format} Format
 * @typedef {import('./static/js/format-utils').FormatId} FormatId
 */

/** @type {Format[]} */
let allFormats = [];

function renderFormats() {
    allFormats.forEach(format => {
        // IDE autocomplete works!
        const id = getFormatId(format);
        const dimensions = getFormatDimensions(format);
        const agentUrl = getFormatAgentUrl(format);
    });
}
</script>
```

## What Type Checking Catches

### ❌ Before (No Type Checking)

```javascript
// No error, but will fail at runtime!
const id = format.format_id.toLowerCase();  // TypeError if format_id is object

// No error, but wrong key type
selectedFormats.has(format.format_id);  // Wrong if format_id is object

// Typo - no warning
const name = format.naem;  // undefined
```

### ✅ After (With Type Checking)

```javascript
// @ts-check

// TypeScript error: Property 'toLowerCase' does not exist on type 'FormatId | string'
const id = format.format_id.toLowerCase();  // ❌ Error at dev time!

// Fixed with utility
const id = getFormatId(format).toLowerCase();  // ✅ Always safe

// TypeScript error: Property 'naem' does not exist on type 'Format'
const name = format.naem;  // ❌ Typo caught!

// Fixed
const name = format.name;  // ✅ Autocomplete helped!
```

## IDE Setup

### VSCode

1. **Automatic** - VSCode picks up `jsconfig.json` and `.d.ts` files automatically
2. **Enable checking** - Add `// @ts-check` to files you want checked
3. **View errors** - Look for red squiggles, hover for details

### WebStorm/IntelliJ

1. **Automatic** - WebStorm uses TypeScript definitions automatically
2. **Enable inspection** - Settings → Editor → Inspections → JavaScript and TypeScript
3. **Check on save** - Enable "TypeScript" inspection

### Cursor/Other IDEs

Most modern IDEs support TypeScript definitions for JavaScript via JSDoc.

## Incremental Adoption Strategy

**Phase 1: Core Utilities (✅ Done)**
- ✅ `format-utils.js` has `.d.ts` file
- ✅ `jsconfig.json` configured
- ✅ Runtime utilities handle both formats

**Phase 2: Enable Checking in Templates (Optional)**
1. Add `@ts-check` to one template (e.g., `inventory_profile_editor.html`)
2. Fix any type errors found
3. Add JSDoc types for variables: `/** @type {Format[]} */`
4. Repeat for other templates

**Phase 3: Enforce in CI (Optional)**
```bash
# Add to CI pipeline
npx tsc --noEmit --allowJs --checkJs templates/**/*.html
```

## Benefits

### 1. **Autocomplete**
IDE shows available properties and methods as you type.

### 2. **Type Safety**
Catches errors before runtime:
- Wrong property names (typos)
- Wrong function arguments
- Wrong return types
- Null/undefined access

### 3. **Refactoring**
IDE can safely rename properties across all files.

### 4. **Documentation**
Types serve as inline documentation - hover to see what's expected.

## Example: Before and After

### Before (No Types)

```html
<script>
let allFormats = [];  // What type? Unknown!

function loadFormats() {
    fetch('/api/formats/list?tenant_id=' + tenantId)
        .then(r => r.json())
        .then(data => {
            // What structure? Who knows!
            allFormats = data.agents;  // Wrong! Should be flattened
            renderFormats();
        });
}

function renderFormats() {
    allFormats.forEach(fmt => {
        // Is format_id a string or object? Guess!
        const id = fmt.format_id;  // Runtime crash if wrong assumption
    });
}
</script>
```

### After (With Types)

```html
<script>
// @ts-check

/** @type {import('./static/js/format-utils').Format[]} */
let allFormats = [];

function loadFormats() {
    fetch('/api/formats/list?tenant_id=' + tenantId)
        .then(r => r.json())
        .then(data => {
            // TypeScript error: Type 'object' is not assignable to type 'Format[]'
            allFormats = data.agents;  // ❌ IDE shows error immediately!

            // Fixed: Flatten agents object
            allFormats = Object.values(data.agents).flat();  // ✅ Correct!
            renderFormats();
        });
}

function renderFormats() {
    allFormats.forEach(fmt => {
        // TypeScript knows fmt is Format type
        // IDE autocomplete suggests: getFormatId(fmt)
        const id = getFormatId(fmt);  // ✅ Safe, no crashes!
    });
}
</script>
```

## When to Use

**✅ Always use** for:
- Complex templates with lots of JavaScript
- Templates that multiple people edit
- Critical user flows (checkout, configuration)

**Optional** for:
- Simple templates with < 50 lines JS
- One-off admin pages
- Rarely changed code

## Performance Impact

**Zero** - Type checking happens:
- At **development time** in your IDE
- At **build time** if you add CI checks
- **Never** at runtime - `.d.ts` files are not loaded by browser

## Summary

```
templates/static/js/
├── format-utils.js         # Runtime code (loaded in browser)
├── format-utils.d.ts       # Type definitions (dev-time only)
├── jsconfig.json           # VSCode/IDE config
└── TYPESCRIPT_GUIDE.md     # This file
```

**Key Points:**
1. `.d.ts` files live **alongside** `.js` files
2. Add `@ts-check` to enable checking per-file
3. Use JSDoc `@type` annotations for variables
4. Zero runtime overhead
5. Incremental adoption - start with critical templates

**Next Steps:**
1. Try adding `// @ts-check` to one template
2. Fix any type errors
3. Enjoy autocomplete and safety!
