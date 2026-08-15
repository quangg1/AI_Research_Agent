from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def open_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=psycopg_url(settings.database_url),
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row, "prepare_threshold": 0},
            open=False,
        )
        _pool.open(wait=True)
    return _pool


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Postgres persistence pool is not initialized")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def health() -> bool:
    try:
        with get_pool().connection() as conn:
            return conn.execute("SELECT 1").fetchone() is not None
    except Exception:
        return False


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_pool().connection() as conn:
        with conn.transaction():
            yield conn
