"""One-time script — creates the item_comments table.

Uses asyncpg directly (statement_cache_size=0) to work with Supabase Supavisor
transaction-mode pooler.
"""
import asyncio
import os
import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url, statement_cache_size=0, target_session_attrs="any")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS item_comments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            item_id UUID NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            user_display_name VARCHAR(255) NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_item_comments_item_id ON item_comments (item_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_item_comments_tenant_id ON item_comments (tenant_id)
    """)

    await conn.close()
    print("item_comments table ready.")


asyncio.run(run())
