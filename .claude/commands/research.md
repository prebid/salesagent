---
name: research
description: Research a beads task before implementation
arguments:
  - name: task_id
    description: The beads task ID to research (e.g., beads-001)
    required: true
---

# Research Task: $ARGUMENTS

## Instructions

You are researching beads task **$ARGUMENTS** before implementation begins.

### Step 1: Read the Task
Run `bd show $ARGUMENTS` to get the full task description, acceptance criteria, and any design notes.

### Step 2: Explore the Codebase
Based on the task requirements:
1. Search for relevant code using Grep and Glob
2. Read the files that will need to be modified
3. Check existing tests for the affected area
4. Look for similar implementations to follow as patterns

### Step 3: Check Documentation (Doc-First Rule)
If the task involves external libraries:
- Use Ref MCP to search library documentation
- Use DeepWiki MCP to ask questions about GitHub repos
- Check CLAUDE.md for project-specific patterns
- Check `/docs` directory for detailed documentation

### Step 4: Engineering Checklist
Run these checks against your findings. Each one should produce a concrete answer, not a shrug.

1. **DRY**: Does similar logic already exist? Search for functions doing comparable work. Extend, don't duplicate.
2. **Library idioms**: How does the primary library (Pydantic, SQLAlchemy, FastMCP, etc.) solve this? Check docs via Ref/DeepWiki before hand-rolling.
3. **Data flow trace**: Walk one concrete example from system boundary (buyer JSON) → Pydantic parsing → logic layer → data layer (DB write/read) → response serialization. Trace both the success path and a failure/rejection path. Note where types change or could break.
4. **Consistency**: How do the language/framework and this codebase solve the same class of problem? Check both — codebase conventions take precedence unless they're a clear antipattern.
5. **Test infrastructure**: What fixtures, factories, helpers already exist in `tests/`? What's reusable vs needs new?

### Step 5: Integrate Findings
Weave the checklist answers into your analysis. They should shape your architecture decisions and implementation plan — not appear as a standalone section in the artifact.

### Step 6: Identify Architecture Decisions
Based on your research:
- What CLAUDE.md patterns apply?
- Are there multiple valid approaches?
- What are the risks or edge cases?

### Step 7: Create Research Artifact
Create a research file at `.claude/research/$ARGUMENTS.md` with:

```markdown
# Research: [task title from bd show]

## Task
$ARGUMENTS: [description]

## Findings
- [Key findings from codebase exploration]

## Relevant Code
- `path/to/file.py:line` — [what it does]

## CLAUDE.md Patterns
- [Which critical patterns apply and how]

## Architecture Decisions
- [Decisions and rationale]

## Implementation Plan
1. [First step]
2. [Second step]
3. [...]

## Risks & Edge Cases
- [Potential issues to watch for]
```

### Step 8: Update the Task
Run `bd label add $ARGUMENTS research:complete` if research is sufficient.
Run `bd label add $ARGUMENTS research:blocked` if there are unresolved questions, and add notes explaining what's blocked.
