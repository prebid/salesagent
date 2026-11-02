# tests/unit — pointer

**A unit test is for a pure function. One thing, nothing else.**

Input in, value out. No database, no transport, no time, no identity, no adapter. If the
subject has any of those, the test belongs in BDD — or in integration if BDD genuinely
cannot reach it. The full preference order and the two-part bar for integration are in
[tests/CLAUDE.md](../CLAUDE.md) § Which kind of test; read that before adding a file here.

519 files live here and not all of them obey this. A file that predates the rule is not a
licence to add another.

## What does not belong here, however it is dressed

- **Behavior.** A response field, an error code, a status, a webhook — anything a buyer can
  observe is BDD's, on all four transports. A "unit test" of a controller, the boundary, or
  an error path is a behavioral test that grades mocks it set up itself.
- **A mock of our own code.** If the test needs `get_db_session`, a UoW, a repository or an
  adapter patched to make the subject reachable, the subject is not a pure function.
- **An assertion that a value equals what `CODE_TABLE` says.** `message`, `recovery`,
  `suggestion` and `status_code` are read-only properties over that table, so asserting one
  against the table's own value compares the table to itself. Assert the code, the field,
  the details, the issues.

## The exception, and it is narrow

**Structural guards** live here and are not pure-function tests: they read the tree (AST,
config files, the rule set) and assert an invariant about the codebase. They belong here
because they need no infrastructure, not because they are unit tests. Two rules bind them:

- **A guard must be seen to fail.** Reintroduce the defect it targets and watch it go red
  before trusting it. A guard that has never fired is not known to grade anything.
- **A guard that can be a ruff or ast-grep rule should be one.** A rule refuses at the point
  of writing; a test reports after the fact. `test_ast_grep_test_flag_ban.py` is the shape
  to copy when a rule needs a companion: it shells out to the real rule rather than
  reimplementing the detection, because a second copy of the logic drifts from the first.
