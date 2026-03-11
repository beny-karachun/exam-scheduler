"""Async SQLAlchemy engine, session factory, and DB lifecycle helpers."""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# ── Engine & Session Factory ──────────────────────────────────

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # SQLite needs this for async; harmless for PostgreSQL.
    connect_args={"check_same_thread": False}
    if "sqlite" in settings.DATABASE_URL
    else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Dependency Injection (FastAPI) ────────────────────────────

async def get_db() -> AsyncSession:  # type: ignore[misc]
    """Yield an async session; auto-close on exit."""
    async with async_session_factory() as session:
        yield session


# ── Lifecycle Helpers ─────────────────────────────────────────

async def init_db() -> None:
    """Create all tables from metadata. For dev/testing only."""
    from app.models import Base  # local import to avoid circular deps

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db() -> None:
    """Drop all tables. For dev/testing only."""
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
