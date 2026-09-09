"""One-time migration — adds reviewed_by and reviewed_at to action_items.

Uses asyncpg directly (statement_cache_size=0) to work with Supabase Supavisor
transaction-mode pooler.

Run from the backend directory:
    set -a && source .env && set +a
    python create_review_fields_migration.py
"""

import asyncio
import os

import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url, statement_cache_size=0, target_session_attrs="any")

    await conn.execute("""
        ALTER TABLE action_items
            ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_action_items_reviewed_by
            ON action_items (reviewed_by)
        WHERE reviewed_by IS NOT NULL
    """)

    await conn.close()
    print("reviewed_by / reviewed_at columns added (or already existed).")


if __name__ == "__main__":
    asyncio.run(run())
