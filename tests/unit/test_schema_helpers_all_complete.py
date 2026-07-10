"""Guard: every public helper in src/core/schema_helpers.py is exported in __all__.

The module declares an explicit ``__all__`` listing the public conversion
helpers (``to_*``/``coerce_*``/``create_*``) plus re-exported types. A public
helper that is defined but omitted from ``__all__`` is an invisible-API bug:
``from src.core.schema_helpers import *`` skips it and static tooling treats it
as non-public, so callers reach for it inconsistently.

This regressed once already (#1417 re-review): ``to_push_notification_config``
was added to the module but not to ``__all__`` while its sibling ``to_*``
helpers were listed, and ``to_property_list_reference`` was never listed either.

Introspection-based so it stays permanent: any future public module-level
function must be added to ``__all__`` or this test fails.
"""

import inspect

from src.core import schema_helpers


def _public_helper_names() -> set[str]:
    """Public (non-underscore) functions defined IN this module (not imported)."""
    return {
        name
        for name, obj in inspect.getmembers(schema_helpers, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == schema_helpers.__name__
    }


def test_all_public_helpers_are_exported():
    public_helpers = _public_helper_names()
    exported = set(schema_helpers.__all__)
    missing = public_helpers - exported
    assert not missing, (
        f"Public helpers defined in schema_helpers.py but missing from __all__: {sorted(missing)}. Add them to __all__."
    )


def test_all_entries_resolve_to_module_attributes():
    """__all__ must not name symbols the module does not actually expose."""
    unresolved = [name for name in schema_helpers.__all__ if not hasattr(schema_helpers, name)]
    assert not unresolved, f"__all__ lists names not defined in the module: {unresolved}"
