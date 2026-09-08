"""One-time seed script — creates the initial admin user, tenant, and workspace.

Uses asyncpg's simple-query protocol (no-parameter conn.execute) to avoid
DuplicatePreparedStatementError on Supabase Supavisor transaction-mode pooler.
"""
import asyncio
import uuid
import os

import bcrypt
import asyncpg


async def seed() -> None:
    raw_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(
        raw_url,
        statement_cache_size=0,
        target_session_attrs="any",
    )

    tid = str(uuid.uuid4())
    wid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    pwd = bcrypt.hashpw(b"Admin123!", bcrypt.gensalt()).decode()

    # Create tables using simple protocol (no params = no prepared statements)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)

    await conn.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                CREATE TYPE user_role AS ENUM ('Admin', 'Member', 'Viewer');
            END IF;
        END $$
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            email VARCHAR(320) NOT NULL,
            display_name VARCHAR(255) NOT NULL DEFAULT '',
            sso_subject VARCHAR(512),
            hashed_password VARCHAR(255),
            role user_role NOT NULL DEFAULT 'Member',
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, email)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            user_id UUID NOT NULL,
            role TEXT NOT NULL DEFAULT 'Member',
            joined_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)

    print("Tables ready.")

    # Inline values into SQL — all inputs are Python-generated (UUIDs, bcrypt hash,
    # hardcoded strings) so there is no injection risk here.
    await conn.execute(f"""
        INSERT INTO tenants (id, name, plan, is_active, created_at, updated_at)
        VALUES ('{tid}', 'My Organization', 'free', true, now(), now())
    """)

    await conn.execute(f"""
        INSERT INTO workspaces (id, tenant_id, name, is_active, created_at, updated_at)
        VALUES ('{wid}', '{tid}', 'Default Workspace', true, now(), now())
    """)

    await conn.execute(f"""
        INSERT INTO users
            (id, tenant_id, email, display_name, hashed_password, role, is_active, created_at, updated_at)
        VALUES ('{uid}', '{tid}', 'admin@example.com', 'Admin', '{pwd}', 'Admin', true, now(), now())
    """)

    await conn.execute(f"""
        INSERT INTO workspace_members
            (id, workspace_id, user_id, role, joined_at, created_at, updated_at)
        VALUES ('{mid}', '{wid}', '{uid}', 'Admin', now(), now(), now())
    """)

    await conn.close()

    print("Seeded successfully!")
    print("  Email:    admin@example.com")
    print("  Password: Admin123!")


asyncio.run(seed())
