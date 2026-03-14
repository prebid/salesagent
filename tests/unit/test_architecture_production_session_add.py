"""Guard moved to test_architecture_repository_pattern.py (unified direct DB access guard).

session.add() scanning is now part of the unified guard that covers both
get_db_session() and session.add() across the entire codebase.
"""
