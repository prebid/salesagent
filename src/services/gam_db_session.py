"""Lazy database session proxy for legacy GAM service modules."""

from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.database import database_session


class LazyScopedSession:
    """Expose a scoped_session-compatible object without creating an engine at import time."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._session_factory = sessionmaker()
        self._scoped_session: scoped_session | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = database_session.get_engine()
        return self._engine

    @property
    def scoped_session(self) -> scoped_session:
        if self._scoped_session is None:
            self._session_factory.configure(bind=self.engine)
            self._scoped_session = scoped_session(self._session_factory)
        return self._scoped_session

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.scoped_session(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.scoped_session, name)
