"""One-time script — creates the user_mfa table added in Phase 2.

Uses asyncpg directly (no prepared statements) to avoid Supabase Supavisor
transaction-pooler incompatibility with Alembic's asyncpg dialect.
"""
import asyncio
import os

import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(
        raw_url,
        statement_cache_size=0,
        target_session_attrs="any",
    )

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_mfa (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            totp_secret VARCHAR(64) NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT false,
            backup_codes TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            UNIQUE (user_id)
        )
    """)

    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_user_mfa_user_id ON user_mfa (user_id)
    """)

    await conn.close()
    print("user_mfa table ready.")


asyncio.run(run())
