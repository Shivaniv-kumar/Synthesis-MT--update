from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _production_settings(**overrides: str) -> Settings:
    values = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "test-secret-key-that-is-not-the-default",
        "FRONTEND_URL": "https://frontend.example.com",
        "DATABASE_URL": "postgresql://postgres:password@db.example.supabase.co:5432/postgres",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_rejects_supabase_transaction_pooler_in_production() -> None:
    with pytest.raises(ValidationError, match="transaction pooler"):
        _production_settings(
            DATABASE_URL=(
                "postgres://postgres.project-ref:password"
                "@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
            )
        )


def test_allows_supabase_session_pooler_in_production() -> None:
    settings = _production_settings(
        DATABASE_URL=(
            "postgres://postgres.project-ref:password"
            "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
        )
    )

    assert settings.effective_database_url == settings.database_url


def test_direct_database_url_overrides_transaction_pooler() -> None:
    settings = _production_settings(
        DATABASE_URL=(
            "postgres://postgres.project-ref:password"
            "@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
        ),
        DIRECT_DATABASE_URL=(
            "postgresql://postgres:password@db.project-ref.supabase.co:5432/postgres"
        ),
    )

    assert settings.effective_database_url == settings.direct_database_url


def test_frontend_url_without_scheme_is_normalised_for_production() -> None:
    settings = _production_settings(
        FRONTEND_URL="frontend-production-1234.up.railway.app/"
    )

    assert settings.frontend_url == "https://frontend-production-1234.up.railway.app"


def test_rejects_invalid_frontend_origin_in_production() -> None:
    with pytest.raises(ValidationError, match="FRONTEND_URL"):
        _production_settings(FRONTEND_URL="https:///missing-host")
