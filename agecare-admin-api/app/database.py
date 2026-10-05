"""Motor async de SQLAlchemy y sesión por petición."""
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        s = get_settings()
        if s.db_nullpool:
            # Serverless: una conexión por invocación y sin caché de sentencias preparadas
            # (compatible con poolers tipo PgBouncer de Neon / Supabase).
            _engine = create_async_engine(
                s.database_url, poolclass=NullPool,
                connect_args={"statement_cache_size": 0,
                              "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4().hex}__"})
        else:
            _engine = create_async_engine(s.database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: una sesión por petición, commit al éxito."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
