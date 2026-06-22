---
name: feedback-minimal-changes
description: User wants minimal changes per task and confirmation before major/structural changes
metadata:
  type: feedback
---

Make minimal changes for every task. Ask before making major or structural changes.

**Why:** User explicitly stated this preference during /init.

**How to apply:** Default to the smallest diff that solves the problem. If a task seems to require significant refactoring, API changes, or touching many files, pause and describe the plan before proceeding.