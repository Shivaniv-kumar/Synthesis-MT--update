"""One-time script — creates notification_rules and notification_logs tables.

Uses asyncpg directly (statement_cache_size=0) to work with Supabase Supavisor
transaction-mode pooler which is incompatible with asyncpg prepared statements.

Run from the backend directory:
    set -a && source .env && set +a
    python create_notification_tables.py
"""

import asyncio
import os

import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url, statement_cache_size=0, target_session_attrs="any")

    # Create enum types (IF NOT EXISTS not supported for enums — use DO block)
    await conn.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_rule_type AS ENUM ('assignment', 'due_soon', 'overdue', 'digest');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    await conn.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_channel AS ENUM ('email', 'slack', 'teams');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    await conn.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_status AS ENUM ('sent', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # notification_rules
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            workspace_id UUID NOT NULL,
            rule_type notification_rule_type NOT NULL,
            channel notification_channel NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            config JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_rules_workspace_id
        ON notification_rules (workspace_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_rules_tenant_id
        ON notification_rules (tenant_id)
    """)

    # notification_logs
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            item_id UUID REFERENCES action_items(id) ON DELETE SET NULL,
            rule_type VARCHAR(50) NOT NULL,
            channel VARCHAR(20) NOT NULL,
            status notification_status NOT NULL,
            sent_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            error TEXT
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_logs_user_id
        ON notification_logs (user_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_logs_item_id
        ON notification_logs (item_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_logs_sent_at
        ON notification_logs (sent_at)
    """)

    await conn.close()
    print("notification_rules and notification_logs tables ready.")


asyncio.run(run())
