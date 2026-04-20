"""Admin-UI authentication primitives.

``Principal`` (see ``principal.py``) is the canonical detached POJO
stashed by ``UnifiedAuthMiddleware`` on ``request.state.principal``.
Per canonical spec §11.3.1 (B15 mitigation).
"""
