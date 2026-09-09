"""Alembic migration environment — async SQLAlchemy with asyncpg."""

from __future__ import annotations

import asyncio
import os
import ssl as _ssl
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Alembic Config object (gives access to alembic.ini values)
# ---------------------------------------------------------------------------

config = context.config

# Interpret the config file for Python logging unless overridden.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Resolve the database URL
#
# For migrations we prefer DIRECT_DATABASE_URL (Supabase direct connection,
# port 5432) over DATABASE_URL (Supavisor pooler, port 6543).  The pooler
# runs in transaction mode which reuses physical PG connections across asyncpg
# sessions; that causes DuplicatePreparedStatementError even with
# statement_cache_size=0 because asyncpg still names its internal statements.
# The direct connection bypasses the pooler entirely.
#
# To set DIRECT_DATABASE_URL in Railway:
#   Supabase dashboard → Settings → Database → Connection string (Direct)
# ---------------------------------------------------------------------------

_db_url = (
    os.environ.get("DIRECT_DATABASE_URL")
    or os.environ.get("DATABASE_URL", "")
    or config.get_main_option("sqlalchemy.url", "")
)

if not _db_url:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Export it as an environment variable before running Alembic."
    )

# Normalise the URL for asyncpg:
# 1. Strip ?ssl=... — we pass SSL explicitly via connect_args.
# 2. Rewrite postgres:// / postgresql:// → postgresql+asyncpg:// so that
#    Railway/Supabase URLs work without a separately formatted env var.
_db_url = _db_url.split("?")[0]
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

config.set_main_option("sqlalchemy.url", _db_url)

# ---------------------------------------------------------------------------
# Import all models so Base.metadata is fully populated
# ---------------------------------------------------------------------------

# app.database defines Base; app.models package registers all ORM tables against it.
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401  (side-effect: imports all model classes)

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Offline migrations (generate SQL without a live DB connection)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (against a live DB connection using asyncpg)
# ---------------------------------------------------------------------------


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create the async engine and run migrations."""
    # Use create_async_engine directly (not async_engine_from_config) so we can
    # pass prepared_statement_cache_size as a TOP-LEVEL dialect kwarg.  When
    # placed inside connect_args it is silently ignored by asyncpg; only the
    # dialect-level kwarg is correctly translated to asyncpg's statement_cache_size=0.
    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE

    connectable = create_async_engine(
        _db_url,
        poolclass=pool.NullPool,
        connect_args={"ssl": _ssl_ctx},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode via asyncio."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
