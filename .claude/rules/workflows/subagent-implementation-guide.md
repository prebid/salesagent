# Subagent Implementation Guide

## When to Use Subagents

Use the Task tool with subagents when:
- **Parallelizing independent work**: Multiple files to explore, multiple beads tasks to create
- **Protecting context**: Large search results that would clutter the main conversation
- **Specialized agents**: QC validation, deep research, code exploration

## Subagent Types

### Explore Agent (`subagent_type=Explore`)
Use for codebase exploration:
- Finding files by patterns
- Searching for code keywords
- Understanding how features work
- Tracing execution paths

### General Purpose Agent (`subagent_type=general-purpose`)
Use for complex, multi-step tasks:
- Researching questions across multiple files
- Executing multi-step analysis
- When you need all tools available

### QC Validator Agent (`.claude/agents/qc-validator.md`)
Use after completing a beads task:
- Validates acceptance criteria
- Runs quality gates
- Checks AdCP compliance
- Verifies git state

## Implementation Pattern

When a beads task has a design field or detailed acceptance criteria:

### 1. Plan the Work
Read the beads task design/description for implementation guidance:
```bash
bd show <id>
```

### 2. Parallelize Where Possible
Launch independent subagents simultaneously:
```
Task 1: Explore agent — find related code patterns
Task 2: Explore agent — find existing tests
```

### 3. Implement Sequentially
After research, implement in order:
1. Write tests (TDD)
2. Write implementation
3. Run quality gates

### 4. Validate with QC Agent
Before closing the beads task, run the QC validator:
- Checks all acceptance criteria
- Runs `make quality`
- Verifies AdCP compliance if applicable

## Tips

- **Be specific in prompts**: Tell subagents exactly what to find/do
- **Include file paths**: When you know which files to check
- **Set expectations**: Tell the agent whether to write code or just research
- **Use background agents**: For long-running tasks that don't block your work
- **Don't duplicate work**: If you delegate research, don't also search yourself
