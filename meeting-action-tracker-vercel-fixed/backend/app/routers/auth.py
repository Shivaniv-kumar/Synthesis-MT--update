"""Authentication router — re-exports the canonical router from app.api.auth.

The router is defined in app.api.auth; we re-export it here so that
app.main continues to import from app.routers without changes.
"""

from __future__ import annotations

from app.api.auth import router  # noqa: F401
