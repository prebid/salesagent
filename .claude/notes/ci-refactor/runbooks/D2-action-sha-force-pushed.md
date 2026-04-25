### D2 — A SHA-pinned action gets force-pushed by upstream (or SHA GC'd)


**Trigger**: `uses: actions/checkout@<sha>` fails to resolve; jobs queue forever or fail with "ref not found"; the action's repository moved or deleted that SHA.
**Severity**: P0 — every CI run is blocked.
**Detection time**: 5-10 min (jobs hang, then time out).
**Affected PR(s)**: all post-PR-1 (every action is SHA-pinned).

**Symptoms**
- Multiple workflows failing with "Could not resolve action".
- The failing actions are ones that worked yesterday.

**Verification**
```bash
# Identify the offending pin
grep -RhoE 'uses: [^@]+@[a-f0-9]{40}' .github/workflows/ | sort -u
# For each, query the upstream
for pin in $(...); do
  ACT="${pin#*: }"; ACT="${ACT%@*}"
  SHA="${pin#*@}"
  curl -fsSL -o /dev/null "https://api.github.com/repos/$ACT/commits/$SHA" || echo "MISSING: $pin"
done
```

**Immediate response (first 15 min)**
1. Identify which action SHA is now unresolvable.
2. Open the action's GitHub repo. Check recent releases. Look for a force-push notice or a blog post.
3. Pick the latest released SHA from a tagged release. Verify it resolves: `curl -fsSL -o /dev/null https://api.github.com/repos/<owner>/<repo>/commits/<new-sha>`.
4. Open an emergency PR updating all references to the new SHA. Update the `# v<tag>` comment.

**Stabilization (next 1-4 hours)**
1. Merge the emergency PR (use `@chrishuie` bypass per ADR-002 — this IS the legitimate emergency case).
2. Verify all workflows recover.

**Recovery (longer-term)**
- Open an upstream issue on the action's repo asking why the SHA was lost (if force-push, if branch deletion, etc.). This is an upstream supply-chain incident — they should respond.
- If the action has a history of force-pushes, consider migrating to a more stable maintainer's fork.

**Post-incident**
- File a security advisory if the force-push could have served malicious code (verify by hash diff against the previous SHA's content).
- Update SECURITY.md if needed.
- Risk register: add a new entry for "upstream action force-push" if not already covered.

**Why this happens (root cause)**
GitHub's API may garbage-collect SHAs that aren't reachable from any branch or tag. If an action repo is deleted, archived, or has its history rewritten, the pinned SHA can vanish. SHA-pinning is correct for security but requires upstream stability.

**Related scenarios**
- See also: F1 (deliberate compromise — investigate diff), D3 (pinact — different tool issue).

---
