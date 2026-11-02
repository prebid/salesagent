# src/core/errors — pointer

This directory **is** the error taxonomy: `CODE_TABLE` (`codes.py`), the declared details
classes (`details.py`), and the issues projection (`issues.py`). Changing anything here
changes every raise site in the codebase at once.

**Read [docs/design/error-architecture.md](../../../docs/design/error-architecture.md) before
editing.** It is the contract; this file only says where to look and what not to do here.

## What belongs here

- **A new code** needs a row in `CODE_TABLE` and an `_HTTP_STATUS` entry. A code with no
  status row falls to the 500 default, which the 5xx band defines as "the buyer cannot fix
  it" — so a `correctable` code landing there tells the buyer two different things about one
  refusal. `NOT_CANCELLABLE` did exactly that.
- **A new details shape** is a class extending `ErrorDetails`, with declared fields. Borrow
  the pinned schema's own property names when one governs the shape; inventing a synonym
  claims a shape the pin does not associate with that code.
- **A new code also needs an exception subclass** (`src/core/exceptions.py`) binding it. A
  pinned code with no class is a gap: the only way to emit it is naming it on the base, which
  is how a code reaches the wire with nothing bound to it.

## What does not belong here

- **No free-text field on a details class.** `message`, `recovery`, `suggestion` and
  `status_code` are read-only properties resolved from `CODE_TABLE` at every read, so a raise
  site cannot author buyer-facing text and a details block has no slot for a sentence to move
  into. `ErrorProblem` carries structured facts for the same reason.
- **No runtime validation of a details argument.** The exception base is generic in its
  details type and every subclass binds one, so mypy refuses a dict and a foreign class at
  the raise site. A `__new__` check for it was added and removed in a day — see pattern 10 in
  the root [CLAUDE.md](../../../CLAUDE.md).
