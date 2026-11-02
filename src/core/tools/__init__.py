"""AdCP tool implementations, one module per tool domain.

This package used to re-export a ``*_raw`` wrapper per tool -- a transport-agnostic
pass-through that every non-MCP transport imported by name. There are none left. A transport
names a TOOL and calls :func:`src.core.tools._boundary.invoke_tool`, which reads
:data:`src.core.tools.registry.TOOLS` for the implementation, so MCP, A2A and REST reach the
same function by construction rather than by fifteen wrappers agreeing with each other.
"""
