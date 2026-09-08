"""One-time script — creates the user_project_access table.

Uses asyncpg directly (statement_cache_size=0) to work with Supabase Supavisor
transaction-mode pooler which is incompatible with asyncpg prepared statements.

Run from the backend directory:
    set -a && source .env && set +a
    python create_user_project_access_table.py
"""

import asyncio
import os

import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url, statement_cache_size=0, target_session_attrs="any")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_project_access (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            granted_by  UUID REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT uq_user_project_access UNIQUE (user_id, project_id)
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_project_access_tenant
            ON user_project_access (tenant_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_project_access_user
            ON user_project_access (user_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_project_access_project
            ON user_project_access (project_id)
    """)

    await conn.close()
    print("user_project_access table created (or already existed).")


if __name__ == "__main__":
    asyncio.run(run())
