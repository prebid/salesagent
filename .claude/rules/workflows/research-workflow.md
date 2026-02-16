# Research Workflow

## When Research is Needed

Before implementing a beads task, research is warranted when:
- The task involves unfamiliar code paths
- External library APIs need verification (doc-first rule)
- Architecture decisions need to be made
- The acceptance criteria are ambiguous

## 3-Path Flow

### Path 1: Research Complete
Research answers all questions. Implementation can proceed.
```bash
bd label add <id> research:complete
```

### Path 2: Research Blocked
Research reveals missing information or external blockers.
```bash
bd label add <id> research:blocked
bd update <id> --notes="Blocked because: <reason>"
```

### Path 3: No Research Needed
Task is clear and well-defined. Skip to implementation.

## Research Process

### 1. Explore the Codebase
- Read files involved in the change
- Trace execution paths
- Check existing tests for examples
- Look for similar implementations

### 2. Check Documentation
**Doc-First Rule**: For external libraries, check docs before relying on training data.

Available documentation sources:
- **Ref MCP**: Search library docs (FastMCP, SQLAlchemy, Flask, Pydantic, etc.)
- **DeepWiki MCP**: Ask questions about GitHub repositories
- **CLAUDE.md**: Project patterns and architecture
- **`/docs` directory**: Detailed project documentation

### 3. Record Findings

Create a research artifact:
```
.claude/research/<beads-id>.md
```

Structure:
```markdown
# Research: <task title>

## Task
<beads-id>: <description>

## Findings
- Key finding 1
- Key finding 2

## Relevant Code
- `path/to/file.py:line` — description
- `path/to/other.py:line` — description

## Architecture Decisions
- Decision 1: <rationale>

## Implementation Notes
- Start with: <suggested first step>
- Watch out for: <potential pitfalls>
```

### 4. Update Task
```bash
bd label add <id> research:complete
bd update <id> --notes="Research complete. See .claude/research/<id>.md"
```

## Tips

- Keep research artifacts concise (not exhaustive documentation)
- Focus on what the implementer needs to know
- Include specific file paths and line numbers
- Note any risks or edge cases discovered
- If research takes > 15 minutes, it's probably too broad — narrow scope
