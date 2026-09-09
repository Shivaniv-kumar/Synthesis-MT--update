"""One-time migration — adds 'sharing_summary' to the notification_rule_type enum.

Run from the backend directory AFTER create_notification_tables.py has been run:
    set -a && source .env && set +a
    python add_sharing_summary_enum.py
"""

import asyncio
import os

import asyncpg


async def run() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url, statement_cache_size=0, target_session_attrs="any")
    await conn.execute(
        "ALTER TYPE notification_rule_type ADD VALUE IF NOT EXISTS 'sharing_summary'"
    )
    await conn.close()
    print("Done: 'sharing_summary' added to notification_rule_type enum.")


asyncio.run(run())
