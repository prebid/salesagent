# Merging Main into Feature Branch — Procedure

## Invariants

- Main passes all tests.
- Our branch passes all tests.
- Every failure after merge is a merge error. There is no "pre-existing."
- Main may have new commits since the last merge attempt. Always `git fetch origin main` and work against the latest.

## Why `git merge` Does Not Work

Git's textual merge is semantically blind. Main's adcp 3.12 migration removed `buyer_ref` from data models (ORM, Pydantic, routes). Our branch references `buyer_ref` in *different lines* of the *same files*. Git sees no conflict, auto-merges them, code compiles, crashes at runtime.

Additionally, "take main's version" for conflicted files loses our branch's additions. "Take our version" reintroduces removed fields. Neither side is correct alone.

## Procedure

### Step 0: Get current main

```bash
git fetch origin main
```

Main may have moved since the last attempt. Always start from the latest.

### For each file our branch changed

Get the 3-way view:

```bash
merge_base=$(git merge-base HEAD origin/main)
git diff ${merge_base}..HEAD -- <file>           # what WE changed
git diff ${merge_base}..origin/main -- <file>    # what MAIN changed
```

**If only we changed the file** (not in main's diff): copy our version. Strip `buyer_ref` if it touches ORM/Pydantic models (see Removals below).

**If only main changed the file**: main's version is already correct. Nothing to do.

**If both changed the file**: start from main's version, then apply our diff hunk by hunk:
- **New function** (exists in our diff, not in main): append it.
- **Modified function** (exists on both branches, our branch changed it, main didn't): take our version of that function.
- **Modified by both**: reconcile manually.
- **Any hunk that references `buyer_ref`, `buyer_refs`, `FormatCategory`, or `type` filter on formats**: adapt or drop per the Removals section.

### Test incrementally

After each batch of files:
```bash
make quality          # unit tests first
tox -e integration    # then integration
tox -e bdd            # then BDD
```

Do NOT run `./run_all_tests.sh` until all three above pass independently.

## adcp 3.12 Removals

From main's commit `0cf4de78`:

| Removed | Was | Replacement |
|---------|-----|-------------|
| `buyer_ref` on `MediaBuy` ORM | Column | Dropped (column removed by migration) |
| `buyer_ref` on `CreateMediaBuyRequest`, `PackageRequest`, `Package`, `UpdateMediaBuyRequest` | Pydantic field | None — field gone, `extra="forbid"` rejects it |
| `buyer_refs` on `GetMediaBuysRequest`, `GetMediaBuyDeliveryRequest` | Filter param | Use `media_buy_ids` only |
| `Format.type` (`FormatCategory` enum) | Enum field | Plain string or removed entirely |
| `BrandManifest` | Type | `Brand` (from `adcp.types.generated_poc.brand`) |
| `UpdateMediaBuyRequest` oneOf (media_buy_id \| buyer_ref) | Pattern | `media_buy_id` required, no alternative |
| `order_name_template` default `{buyer_ref}` | Template var | `{media_buy_id}` |
| `canceled` on `UpdateMediaBuyRequest` | Not present before 3.12 | Added as `Literal[True] = True` — leaks through `model_dump(exclude_none=True)`, must be filtered in harness transport methods |

Strip `buyer_ref` completely — it must not appear anywhere in the final codebase. Remove from kwargs, dict literals, attribute access, field handlers, comments, docstrings, xfail tags, test state dicts. All of it.

## Do Not

1. **Do not use `git merge`.** Auto-merge silently reintroduces removed fields in non-conflicting files.

2. **Do not take main's version of a file wholesale.** This loses our branch's additions — classes like `MediaBuyUpdateIntegrationEnv`, methods like `set_adapter_test_behavior`, modifications to existing functions like `given_account_active` adding `ctx["account_ref"]`.

3. **Do not only check for new functions when merging overlapping files.** Our branch also *modified* 18 existing functions in `uc002_create_media_buy.py`. "Append new, keep main's existing" broke 673 tests. The diff must be checked at function granularity: new, modified-by-us, modified-by-both.

4. **Do not use regex/sed to strip `buyer_ref`.** It breaks elif chains, closing parens, dict structure. Read each occurrence and edit it understanding the surrounding code.

5. **Do not fix guard allowlists or traceability entries before the merge is clean.** Line numbers shift with every file change. Fix them once, at the end, after `make quality` runs to completion.

6. **Do not copy our branch-only files without checking for `buyer_ref`.** Files that only we created still reference `buyer_ref` on models that no longer have it. Every file must be checked.

7. **Do not assume auto-merged files are correct.** 48 of 63 overlapping files auto-merged without conflict. Many of those carried `buyer_ref` code from our branch into main's post-3.12 world.

8. **Do not run `./run_all_tests.sh` as the first check.** Run `make quality` first, then `tox -e integration`, then `tox -e bdd`. Fix each level before moving to the next.

9. **Do not forget `_harness_db.py`.** Our branch imports it at module level in 6+ BDD step files but it was never committed (only on disk as untracked). Must be committed before or during merge.
