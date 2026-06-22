---
name: feedback-no-test-changes
description: User does not want test files modified unless explicitly asked
metadata:
  type: feedback
---

Do not modify test files (tests/) unless the user explicitly asks.

**Why:** User stated "I don't want any test or changes in test" during session setup.

**How to apply:** Treat tests/ as read-only by default. If a code change would normally require a test update, implement the code change only and note that tests may need updating — do not touch them.