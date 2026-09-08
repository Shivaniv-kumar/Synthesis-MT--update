from __future__ import annotations

import ssl as _ssl
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# Normalise the effective database URL so it always uses the asyncpg driver:
#   - Strip query params (ssl=require etc.) — SSL is handled via connect_args.
#   - Rewrite postgres:// or postgresql:// → postgresql+asyncpg:// so that
#     Supabase / Railway connection strings work without manual reformatting.
#     Without +asyncpg, SQLAlchemy defaults to psycopg2 which is not installed.
_db_url = settings.effective_database_url.split("?")[0]
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Supabase PostgreSQL uses a self-signed certificate chain that is not in the
# system trust store, so CERT_REQUIRED fails on Railway. CERT_NONE here does
# NOT mean "no SSL" — asyncpg still negotiates a TLS connection; we just skip
# CA chain verification because Supabase's cert can't be verified without
# bundling their root CA. The connection is still encrypted in transit.
_ssl_ctx = _ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = _ssl.CERT_NONE

engine = create_async_engine(
    _db_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
    connect_args={"ssl": _ssl_ctx},
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    # Subclasses may define __tablename__, columns, etc.
    pass


async def get_db() -> AsyncGenerator[AsyncSession, Any]:
    """FastAPI dependency that yields a per-request AsyncSession."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_database() -> None:
    """Verify that the configured database accepts a simple query."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await conn.commit()


async def create_tables() -> None:
    """Ensure schema exists and apply any missing column migrations.

    Runs create_all in all environments using checkfirst=True so it is safe
    to run against an already-initialised Supabase database — only tables and
    types that are missing get created; existing ones are left untouched.
    """
    from sqlalchemy import text

    # Side-effect import: registers all ORM models with Base.metadata so that
    # create_all and Alembic can see the full schema.
    import app.models  # noqa: F401

    async with engine.connect() as conn:
        try:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
            await conn.commit()
        except Exception as exc:
            await conn.rollback()
            logger.warning("database.schema_create_skipped", error=str(exc))

        # Verify connectivity works before declaring success.
        await conn.execute(text("SELECT 1"))
        await conn.commit()

        # Idempotent column migrations — safe to run on every startup.
        _migrations = [
            (
                "ALTER TABLE meetings "
                "ADD COLUMN IF NOT EXISTS project_id UUID "
                "REFERENCES projects(id) ON DELETE SET NULL",
                "database.project_id_migration_skipped",
            ),
            (
                "ALTER TABLE action_items "
                "ADD COLUMN IF NOT EXISTS reviewed_by UUID "
                "REFERENCES users(id) ON DELETE SET NULL, "
                "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
                "database.reviewed_fields_migration_skipped",
            ),
            # Add new values to the ENUM so new categories can be saved even while
            # the ENUM column type still exists. ALTER TYPE ADD VALUE must run as
            # the sole statement in its transaction (PostgreSQL restriction), so
            # each is its own migration entry — never wrapped in a DO block.
            # If the ENUM type doesn't exist (fresh DB), the exception is caught.
            (
                "ALTER TYPE knowledge_category ADD VALUE IF NOT EXISTS 'dependency'",
                "database.knowledge_category_add_dependency_skipped",
            ),
            (
                "ALTER TYPE knowledge_category ADD VALUE IF NOT EXISTS 'constraint'",
                "database.knowledge_category_add_constraint_skipped",
            ),
            (
                "ALTER TYPE knowledge_category ADD VALUE IF NOT EXISTS 'blocker'",
                "database.knowledge_category_add_blocker_skipped",
            ),
            (
                "ALTER TYPE knowledge_category ADD VALUE IF NOT EXISTS 'tradeoff'",
                "database.knowledge_category_add_tradeoff_skipped",
            ),
            (
                "ALTER TYPE knowledge_category ADD VALUE IF NOT EXISTS 'principle'",
                "database.knowledge_category_add_principle_skipped",
            ),
            # Convert the column from ENUM to TEXT so the category set can evolve
            # freely. Caught if already TEXT or the table doesn't exist yet.
            (
                "ALTER TABLE knowledge_entries "
                "ALTER COLUMN category TYPE TEXT USING category::text",
                "database.knowledge_category_text_skipped",
            ),
            (
                "DROP TYPE IF EXISTS knowledge_category",
                "database.knowledge_category_enum_drop_skipped",
            ),
            (
                "ALTER TABLE knowledge_entries "
                "ADD COLUMN IF NOT EXISTS latest_update TEXT",
                "database.knowledge_latest_update_skipped",
            ),
            (
                "ALTER TABLE knowledge_entries "
                "ADD COLUMN IF NOT EXISTS is_closed BOOLEAN NOT NULL DEFAULT FALSE",
                "database.knowledge_is_closed_skipped",
            ),
            # Migrate legacy 'context' category rows to 'assumption' now that
            # 'context' has been removed as a standalone category.
            (
                "UPDATE knowledge_entries SET category = 'assumption' WHERE category = 'context'",
                "database.knowledge_context_to_assumption_skipped",
            ),
            # Add sharing_summary to the notification_rule_type ENUM. Must be a
            # plain statement in its own transaction (PostgreSQL restriction).
            (
                "ALTER TYPE notification_rule_type ADD VALUE IF NOT EXISTS 'sharing_summary'",
                "database.notification_sharing_summary_skipped",
            ),
            # Convert notification_rules.rule_type from ENUM to TEXT so new rule
            # types (e.g. sharing_summary) can be added without ENUM migrations.
            # Safe to run repeatedly — IF the column is already TEXT this is a no-op.
            (
                "ALTER TABLE notification_rules "
                "ALTER COLUMN rule_type TYPE TEXT USING rule_type::text",
                "database.notification_rule_type_text_skipped",
            ),
            (
                "DROP TYPE IF EXISTS notification_rule_type",
                "database.notification_rule_type_enum_drop_skipped",
            ),
            # MFA brute-force rate limiting columns
            (
                "ALTER TABLE user_mfa ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0",
                "database.user_mfa_failed_attempts_skipped",
            ),
            (
                "ALTER TABLE user_mfa ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
                "database.user_mfa_locked_until_skipped",
            ),
            # Invite flow: distinguish reset vs invite tokens
            (
                "ALTER TABLE password_reset_tokens ADD COLUMN IF NOT EXISTS "
                "token_type VARCHAR(16) NOT NULL DEFAULT 'reset'",
                "database.password_reset_token_type_skipped",
            ),
        ]
        for stmt, label in _migrations:
            short = stmt[:80].replace("\n", " ")
            try:
                await conn.execute(text(stmt))
                await conn.commit()
                logger.info("database.migration_ok", sql=short)
            except Exception as exc:  # noqa: BLE001
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(label, sql=short, error=str(exc))

    logger.info("database.tables_created")
