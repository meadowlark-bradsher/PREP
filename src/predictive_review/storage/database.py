"""Engine + session factory for the storage layer.

The factory is the unit of dependency injection: callers pass a factory
into services, and each service operation opens its own session and
manages its own transaction. This keeps transaction scope visible at the
service-method level rather than implicit in some global state.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    return create_engine(url, future=True)


def make_session_factory(
    database_url: str | None = None,
) -> Callable[[], Session]:
    engine = make_engine(database_url)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
